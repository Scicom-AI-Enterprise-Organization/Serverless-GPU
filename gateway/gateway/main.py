import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as redis_async
import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .auth import require_api_key
from . import metrics

logger = logging.getLogger("gateway")

WORKER_TTL_S = 30


class AutoscalerSpec(BaseModel):
    max_containers: int = 1
    tasks_per_container: int = 30
    idle_timeout_s: int = 300


class CreateAppRequest(BaseModel):
    name: str
    model: str
    gpu: str
    autoscaler: AutoscalerSpec = Field(default_factory=AutoscalerSpec)
    cpu: int = 2
    memory: str = "16Gi"
    request_timeout_s: int = 600  # per-job ceiling enforced by the worker


class CreateAppResponse(BaseModel):
    app_id: str
    url: str


class AppRecord(BaseModel):
    app_id: str
    name: str
    model: str
    gpu: str
    autoscaler: AutoscalerSpec
    cpu: int = 2
    memory: str = "16Gi"
    request_timeout_s: int = 600
    created_at: str


class RunResponse(BaseModel):
    request_id: str
    poll_url: str


class ResultResponse(BaseModel):
    request_id: str
    status: str
    output: Optional[Any] = None


class WorkerRegisterRequest(BaseModel):
    machine_id: str
    app_id: str
    token: str


class WorkerRegisterResponse(BaseModel):
    ok: bool
    redis_url: str


class WorkerHeartbeatRequest(BaseModel):
    machine_id: str
    app_id: str
    status: str = "ready"


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
    logger.info("connecting to redis at %s", redis_url)
    app.state.redis = redis_async.from_url(redis_url, decode_responses=True)
    await app.state.redis.ping()
    logger.info("redis ready")

    # Provider + autoscaler are opt-in for now. Enabled when AUTOSCALER=1.
    app.state.provider = None
    app.state.autoscaler_task = None

    app.state.reconciler_task = None
    if os.environ.get("AUTOSCALER", "0") == "1":
        from .provider import build_provider
        from .autoscaler import autoscaler_loop
        from .reconciler import reconciler_loop

        provider_name = os.environ.get("PROVIDER", "fake")

        # Fail-fast: when the operator says PROVIDER=primeintellect, refuse to
        # boot if any required PI env is missing or obviously stubbed.
        # Otherwise the autoscaler loops forever calling provision and getting
        # 401/422 from PI, which is hard to debug from logs.
        if provider_name == "primeintellect":
            missing = []
            for var in ("PI_API_KEY", "PI_CUSTOM_TEMPLATE_ID", "GATEWAY_PUBLIC_URL"):
                v = os.environ.get(var, "")
                if not v or v in ("replace-me", "changeme"):
                    missing.append(var)
            if missing:
                raise RuntimeError(
                    f"PROVIDER=primeintellect requires {missing} to be set "
                    f"(or set PROVIDER=fake for local dev with no real GPU)"
                )

        if provider_name == "runpod":
            missing = []
            for var in ("RUNPOD_API_KEY", "RUNPOD_TEMPLATE_ID", "GATEWAY_PUBLIC_URL"):
                v = os.environ.get(var, "")
                if not v or v in ("replace-me", "changeme"):
                    missing.append(var)
            if missing:
                raise RuntimeError(
                    f"PROVIDER=runpod requires {missing} to be set "
                    f"(or set PROVIDER=fake for local dev with no real GPU)"
                )

        app.state.provider = build_provider(provider_name)
        app.state.autoscaler_task = asyncio.create_task(
            autoscaler_loop(app.state.redis, app.state.provider)
        )
        app.state.reconciler_task = asyncio.create_task(
            reconciler_loop(app.state.redis, app.state.provider)
        )
        logger.info("autoscaler + reconciler enabled (provider=%s)", app.state.provider.name)

    try:
        yield
    finally:
        for task_attr in ("autoscaler_task", "reconciler_task"):
            t = getattr(app.state, task_attr, None)
            if t:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, BaseException):
                    pass
        if app.state.provider:
            await app.state.provider.shutdown()
        await app.state.redis.aclose()


app = FastAPI(title="serverless-gpu gateway", lifespan=lifespan)


@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    """Count every request by route + status; track inflight."""
    metrics.INFLIGHT.inc()
    try:
        resp = await call_next(request)
        # Use the matched route template, not the raw path — keeps cardinality
        # bounded (e.g. /run/{app_id} not /run/qwen, /run/llama, ...).
        route_obj = request.scope.get("route")
        route = getattr(route_obj, "path", request.url.path) if route_obj else request.url.path
        metrics.REQUESTS_TOTAL.labels(route=route, status=str(resp.status_code)).inc()
        return resp
    finally:
        metrics.INFLIGHT.dec()


@app.get("/health")
async def health():
    """Liveness: 200 means the gateway process is alive. Does NOT verify
    Redis — if Redis is down, restarting the gateway won't help, and we
    don't want k8s to crashloop. Use /ready for that."""
    return {"ok": True}


@app.get("/ready")
async def ready(request: Request):
    """Readiness: 200 means the gateway can actually serve requests right
    now. Checks Redis. k8s readinessProbe should hit this so a pod that
    can't reach Redis is removed from the Service's endpoints."""
    rdb = request.app.state.redis
    try:
        await rdb.ping()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"redis": "unreachable", "error": str(e)[:200]},
        )
    return {"ok": True, "redis": "ok"}


@app.get("/metrics")
async def metrics_endpoint(request: Request):
    body, ctype = await metrics.render(request.app.state.redis)
    return Response(content=body, media_type=ctype)


@app.post("/apps", response_model=CreateAppResponse, dependencies=[Depends(require_api_key)])
async def create_app(req: CreateAppRequest, request: Request):
    rdb = request.app.state.redis
    record = AppRecord(
        app_id=req.name,
        name=req.name,
        model=req.model,
        gpu=req.gpu,
        autoscaler=req.autoscaler,
        cpu=req.cpu,
        memory=req.memory,
        request_timeout_s=req.request_timeout_s,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await rdb.set(f"app:{req.name}", record.model_dump_json())
    await rdb.sadd("apps:index", req.name)
    logger.info("created app %s (%s on %s)", req.name, req.model, req.gpu)
    return CreateAppResponse(app_id=req.name, url=f"/run/{req.name}")


@app.get("/apps", response_model=list[AppRecord], dependencies=[Depends(require_api_key)])
async def list_apps(request: Request):
    """All deployed apps. Backed by `apps:index` Redis set."""
    rdb = request.app.state.redis
    app_ids = sorted(await rdb.smembers("apps:index"))
    out: list[AppRecord] = []
    for app_id in app_ids:
        blob = await rdb.get(f"app:{app_id}")
        if blob is None:
            # Index drift: remove and continue.
            await rdb.srem("apps:index", app_id)
            continue
        out.append(AppRecord(**json.loads(blob)))
    return out


@app.get("/apps/{app_id}", response_model=AppRecord, dependencies=[Depends(require_api_key)])
async def get_app(app_id: str, request: Request):
    rdb = request.app.state.redis
    blob = await rdb.get(f"app:{app_id}")
    if blob is None:
        raise HTTPException(status_code=404, detail="not found")
    return AppRecord(**json.loads(blob))


@app.delete("/apps/{app_id}", dependencies=[Depends(require_api_key)])
async def delete_app(app_id: str, request: Request):
    """Tear down an app cleanly:
       1. Mark all its workers for drain (worker:{id}:drain set; heartbeat
          response tells them to exit).
       2. Wait briefly for workers to leave; let the autoscaler/reconciler GC.
       3. Delete the queue, app record, last_request_ts, apps:index entry.
          (In-flight result keys auto-expire via their existing TTL.)

    Idempotent: deleting a non-existent app returns 404 only on first try.
    """
    rdb = request.app.state.redis
    if not await rdb.exists(f"app:{app_id}"):
        raise HTTPException(status_code=404, detail="no such app")

    # Step 1: signal workers to drain. Each one's heartbeat picks this up
    # within the heartbeat interval (~5s) and exits.
    machine_ids = await rdb.smembers(f"worker_index:{app_id}")
    for mid in machine_ids:
        await rdb.set(f"worker:{mid}:drain", "1", ex=600)
    logger.info("delete app %s: marked %d workers for drain", app_id, len(machine_ids))

    # Step 2: provider-level termination, if a provider is wired (autoscaler on).
    provider = getattr(request.app.state, "provider", None)
    if provider is not None:
        for mid in machine_ids:
            try:
                await provider.terminate(mid)
            except Exception:
                logger.exception("delete app %s: provider.terminate(%s) failed", app_id, mid)

    # Step 3: blow away app state. Workers that haven't drained yet will
    # see their queue is gone and cleanly exit; the reconciler GCs leftovers.
    await rdb.delete(
        f"app:{app_id}",
        f"queue:{app_id}",
        f"app:{app_id}:last_request_ts",
        f"worker_index:{app_id}",
    )
    await rdb.srem("apps:index", app_id)

    return {"ok": True, "app_id": app_id, "drained_workers": len(machine_ids)}


async def _admit_and_enqueue(rdb, app_id: str, payload: dict, *, stream: bool, endpoint: str = "/v1/completions") -> tuple[str, int]:
    """Shared logic for /run, /stream, and /v1/* OpenAI routes.

    Returns (request_id, timeout_s). Raises HTTPException on 404 / 429.
    """
    app_blob = await rdb.get(f"app:{app_id}")
    if app_blob is None:
        raise HTTPException(status_code=404, detail="no such app")
    app_record = json.loads(app_blob)
    cfg = app_record["autoscaler"]
    cap = int(cfg["max_containers"]) * int(cfg["tasks_per_container"])
    queue_len = await rdb.llen(f"queue:{app_id}")
    if queue_len >= cap:
        raise HTTPException(
            status_code=429,
            detail={"error": "capacity exceeded", "queue_length": queue_len, "cap": cap, "retry_after_s": 5},
        )
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    timeout_s = int(app_record.get("request_timeout_s", 600))
    job = {
        "request_id": request_id,
        "payload": payload,
        "timeout_s": timeout_s,
        "endpoint": endpoint,
    }
    if stream:
        job["stream"] = True
    await rdb.lpush(f"queue:{app_id}", json.dumps(job))
    await rdb.set(f"result:{request_id}", json.dumps({"status": "pending"}), ex=3600)
    await rdb.set(f"app:{app_id}:last_request_ts", str(time.time()))
    return request_id, timeout_s


@app.post("/run/{app_id}", response_model=RunResponse, dependencies=[Depends(require_api_key)])
async def run(app_id: str, payload: dict, request: Request):
    rdb = request.app.state.redis
    request_id, _ = await _admit_and_enqueue(rdb, app_id, payload, stream=False)
    logger.info("enqueued %s on %s", request_id, app_id)
    return RunResponse(request_id=request_id, poll_url=f"/result/{request_id}")


@app.post("/stream/{app_id}", dependencies=[Depends(require_api_key)])
async def stream(app_id: str, payload: dict, request: Request):
    """Server-Sent Events: open a long-lived connection, get token chunks live.

    Pipe shape: client SSE ↔ gateway ↔ Redis pub/sub channel ↔ worker.

    The gateway subscribes BEFORE enqueueing to avoid a race where the worker
    publishes the first chunk before we've subscribed.
    """
    rdb = request.app.state.redis
    app_blob = await rdb.get(f"app:{app_id}")
    if app_blob is None:
        raise HTTPException(status_code=404, detail="no such app")

    app_record = json.loads(app_blob)
    cfg = app_record["autoscaler"]
    cap = int(cfg["max_containers"]) * int(cfg["tasks_per_container"])
    queue_len = await rdb.llen(f"queue:{app_id}")
    if queue_len >= cap:
        raise HTTPException(
            status_code=429,
            detail={"error": "capacity exceeded", "queue_length": queue_len, "cap": cap, "retry_after_s": 5},
        )

    request_id = f"req-{uuid.uuid4().hex[:12]}"
    channel = f"stream:{request_id}"

    pubsub = rdb.pubsub()
    await pubsub.subscribe(channel)

    timeout_s = int(app_record.get("request_timeout_s", 600))
    job = {"request_id": request_id, "payload": payload, "stream": True, "timeout_s": timeout_s}
    await rdb.lpush(f"queue:{app_id}", json.dumps(job))
    await rdb.set(f"result:{request_id}", json.dumps({"status": "pending"}), ex=3600)
    await rdb.set(f"app:{app_id}:last_request_ts", str(time.time()))
    logger.info("enqueued stream %s on %s (timeout=%ss)", request_id, app_id, timeout_s)

    async def gen():
        # Send the request id up front so clients can correlate / cancel.
        yield f"event: meta\ndata: {json.dumps({'request_id': request_id})}\n\n"
        finished_normally = False
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg["data"]
                yield f"data: {data}\n\n"
                try:
                    parsed = json.loads(data)
                    if parsed.get("done") or parsed.get("error"):
                        finished_normally = True
                        break
                except json.JSONDecodeError:
                    continue
        finally:
            # If the client disconnected, FastAPI cancels this generator and
            # we land here without a `done` chunk. Tell the worker to stop
            # generating so we don't burn GPU cycles on a vanished client.
            if not finished_normally:
                try:
                    await rdb.set(f"cancel:{request_id}", "1", ex=60)
                    logger.info("client disconnected from %s, signaled cancel", request_id)
                except Exception:
                    pass
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-Id": request_id,
        },
    )


@app.get("/result/{request_id}", response_model=ResultResponse, dependencies=[Depends(require_api_key)])
async def get_result(request_id: str, request: Request):
    rdb = request.app.state.redis
    blob = await rdb.get(f"result:{request_id}")
    if blob is None:
        raise HTTPException(status_code=404, detail="not found")
    raw = json.loads(blob)
    return ResultResponse(
        request_id=request_id,
        status=raw.get("status", "unknown"),
        output=raw.get("output"),
    )


async def _openai_endpoint(request: Request, payload: dict, vllm_path: str):
    """OpenAI-compatible facade. Reads `model` from the request body, treats
    it as our app_id, enqueues with `endpoint=vllm_path` so the worker forwards
    to the right vLLM endpoint. Supports `stream: true` via SSE pubsub.
    """
    rdb = request.app.state.redis
    app_id = payload.get("model")
    if not app_id:
        raise HTTPException(status_code=400, detail={"error": "missing 'model' field in request body"})

    is_stream = bool(payload.get("stream"))
    request_id, _timeout_s = await _admit_and_enqueue(rdb, app_id, payload, stream=is_stream, endpoint=vllm_path)

    if not is_stream:
        # Sync path: poll for the result up to 60s — covers warm-worker case
        # but not cold start. Caller can re-issue if they want longer.
        deadline = time.time() + 60
        while time.time() < deadline:
            blob = await rdb.get(f"result:{request_id}")
            if blob:
                raw = json.loads(blob)
                if raw.get("status") == "completed":
                    return raw.get("output", {})
                if raw.get("status") in ("timeout", "cancelled"):
                    raise HTTPException(status_code=504, detail=raw.get("output"))
            await asyncio.sleep(0.2)
        raise HTTPException(
            status_code=504,
            detail={
                "error": "no completion in 60s — worker probably cold-starting; retry or use stream:true",
                "request_id": request_id,
            },
        )

    # Streaming path: same SSE pubsub forwarding as /stream/{app_id}
    channel = f"stream:{request_id}"
    pubsub = rdb.pubsub()
    await pubsub.subscribe(channel)

    async def gen():
        finished = False
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg["data"]
                yield f"data: {data}\n\n"
                try:
                    parsed = json.loads(data)
                    if parsed.get("done") or parsed.get("error"):
                        finished = True
                        break
                except json.JSONDecodeError:
                    continue
            yield "data: [DONE]\n\n"  # OpenAI's SSE terminator convention
        finally:
            if not finished:
                try:
                    await rdb.set(f"cancel:{request_id}", "1", ex=60)
                except Exception:
                    pass
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-Id": request_id,
        },
    )


@app.post("/v1/chat/completions", dependencies=[Depends(require_api_key)])
async def openai_chat_completions(payload: dict, request: Request):
    """OpenAI-compatible chat completions. Use `model: "<app_id>"` in body.

    Drop-in for any OpenAI client:
      openai.OpenAI(base_url="https://your-gw/v1", api_key="...").chat.completions.create(model="qwen", ...)
    """
    return await _openai_endpoint(request, payload, "/v1/chat/completions")


@app.post("/v1/completions", dependencies=[Depends(require_api_key)])
async def openai_completions(payload: dict, request: Request):
    """Legacy OpenAI completions endpoint."""
    return await _openai_endpoint(request, payload, "/v1/completions")


@app.post("/v1/embeddings", dependencies=[Depends(require_api_key)])
async def openai_embeddings(payload: dict, request: Request):
    """OpenAI-compatible embeddings (sync only, no stream)."""
    payload.pop("stream", None)
    return await _openai_endpoint(request, payload, "/v1/embeddings")


@app.post("/workers/register", response_model=WorkerRegisterResponse)
async def register_worker(req: WorkerRegisterRequest, request: Request):
    rdb = request.app.state.redis

    # Token validation (only enforced when autoscaler is on; otherwise
    # docker-compose's manually-started workers register with a static token).
    if os.environ.get("AUTOSCALER", "0") == "1":
        token_key = f"register_token:{req.machine_id}"
        expected = await rdb.get(token_key)
        if expected is None or expected != req.token:
            logger.warning(
                "register rejected: machine=%s token=%s (expected=%s)",
                req.machine_id, req.token[:8] + "...", "<set>" if expected else "<missing>",
            )
            raise HTTPException(status_code=401, detail="invalid or expired token")
        # Burn the token: registrations are one-shot.
        await rdb.delete(token_key)

    state = {
        "machine_id": req.machine_id,
        "app_id": req.app_id,
        "status": "registered",
        "last_seen": time.time(),
    }
    await rdb.set(f"worker:{req.machine_id}", json.dumps(state), ex=WORKER_TTL_S)
    await rdb.sadd(f"worker_index:{req.app_id}", req.machine_id)
    logger.info("worker registered: machine=%s app=%s", req.machine_id, req.app_id)
    redis_url = os.environ.get(
        "WORKER_REDIS_URL",
        os.environ.get("REDIS_URL", "redis://redis:6379"),
    )
    return WorkerRegisterResponse(ok=True, redis_url=redis_url)


@app.post("/workers/heartbeat")
async def heartbeat(req: WorkerHeartbeatRequest, request: Request):
    rdb = request.app.state.redis
    state = {
        "machine_id": req.machine_id,
        "app_id": req.app_id,
        "status": req.status,
        "last_seen": time.time(),
    }
    await rdb.set(f"worker:{req.machine_id}", json.dumps(state), ex=WORKER_TTL_S)
    await rdb.sadd(f"worker_index:{req.app_id}", req.machine_id)
    drain = await rdb.exists(f"worker:{req.machine_id}:drain")
    return {"ok": True, "drain": bool(drain)}


def run():
    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host, port = os.environ.get("GATEWAY_BIND", "0.0.0.0:8080").rsplit(":", 1)
    uvicorn.run("gateway.main:app", host=host, port=int(port), log_level="info")


if __name__ == "__main__":
    run()
