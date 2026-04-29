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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import metrics
from .auth import (
    create_session,
    current_user,
    hash_password,
    revoke_session,
    verify_password,
)
from .db import App, Request as ReqRow, User, get_session, init_db, get_user_by_username, list_all_apps, seed_admin_user, session_factory, shutdown_db

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
    request_timeout_s: int = 600


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
    owner: str


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


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    username: str


class WhoamiResponse(BaseModel):
    user_id: int
    username: str
    is_admin: bool = False


def _to_app_record(app: App) -> AppRecord:
    return AppRecord(
        app_id=app.app_id,
        name=app.name,
        model=app.model,
        gpu=app.gpu,
        autoscaler=AutoscalerSpec(**app.autoscaler),
        cpu=app.cpu,
        memory=app.memory,
        request_timeout_s=app.request_timeout_s,
        created_at=app.created_at.isoformat() if app.created_at else "",
        owner=app.owner.username if app.owner else "",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
    logger.info("connecting to redis at %s", redis_url)
    app.state.redis = redis_async.from_url(redis_url, decode_responses=True)
    await app.state.redis.ping()
    logger.info("redis ready")

    logger.info("initializing postgres")
    await init_db()
    await seed_admin_user()
    logger.info("postgres ready")

    app.state.provider = None
    app.state.autoscaler_task = None
    app.state.reconciler_task = None
    if os.environ.get("AUTOSCALER", "0") == "1":
        from .provider import build_provider
        from .autoscaler import autoscaler_loop
        from .reconciler import reconciler_loop

        provider_name = os.environ.get("PROVIDER", "fake")

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
            autoscaler_loop(app.state.redis, app.state.provider, session_factory())
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
        await shutdown_db()


app = FastAPI(title="serverless-gpu gateway", lifespan=lifespan)


@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    metrics.INFLIGHT.inc()
    try:
        resp = await call_next(request)
        route_obj = request.scope.get("route")
        route = getattr(route_obj, "path", request.url.path) if route_obj else request.url.path
        metrics.REQUESTS_TOTAL.labels(route=route, status=str(resp.status_code)).inc()
        return resp
    finally:
        metrics.INFLIGHT.dec()


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/ready")
async def ready(request: Request):
    rdb = request.app.state.redis
    try:
        await rdb.ping()
    except Exception as e:
        raise HTTPException(status_code=503, detail={"redis": "unreachable", "error": str(e)[:200]})
    return {"ok": True, "redis": "ok"}


@app.get("/metrics")
async def metrics_endpoint(request: Request):
    body, ctype = await metrics.render(request.app.state.redis)
    return Response(content=body, media_type=ctype)


# ----- auth -----

@app.post("/auth/register", response_model=TokenResponse)
async def register(req: RegisterRequest, request: Request, session: AsyncSession = Depends(get_session)):
    user = User(username=req.username, password_hash=hash_password(req.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail={"error": "username already taken"})
    await session.refresh(user)
    token = await create_session(request.app.state.redis, user.id)
    logger.info("registered user %s (id=%d)", user.username, user.id)
    return TokenResponse(token=token, username=user.username)


@app.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request, session: AsyncSession = Depends(get_session)):
    user = await get_user_by_username(session, req.username)
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail={"error": "invalid credentials"})
    token = await create_session(request.app.state.redis, user.id)
    return TokenResponse(token=token, username=user.username)


@app.post("/auth/logout")
async def logout(request: Request, user: User = Depends(current_user)):
    header = request.headers.get("authorization", "")
    token = header[len("Bearer "):].strip()
    await revoke_session(request.app.state.redis, token)
    return {"ok": True}


@app.get("/auth/me", response_model=WhoamiResponse)
async def whoami(user: User = Depends(current_user)):
    return WhoamiResponse(user_id=user.id, username=user.username, is_admin=user.is_admin)


# ----- apps (owner-scoped) -----

@app.post("/apps", response_model=CreateAppResponse)
async def create_app(
    req: CreateAppRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    record = App(
        app_id=req.name,
        owner_id=user.id,
        name=req.name,
        model=req.model,
        gpu=req.gpu,
        autoscaler=req.autoscaler.model_dump(),
        cpu=req.cpu,
        memory=req.memory,
        request_timeout_s=req.request_timeout_s,
        created_at=datetime.now(timezone.utc),
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail={"error": "app name already taken"})
    logger.info("created app %s by user=%s (%s on %s)", req.name, user.username, req.model, req.gpu)
    return CreateAppResponse(app_id=req.name, url=f"/run/{req.name}")


@app.get("/apps", response_model=list[AppRecord])
async def list_apps(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    stmt = select(App).options(selectinload(App.owner))
    if not user.is_admin:
        stmt = stmt.where(App.owner_id == user.id)
    result = await session.execute(stmt)
    apps = result.scalars().all()
    return [_to_app_record(a) for a in apps]


async def _load_owned_app(session: AsyncSession, app_id: str, user: User) -> App:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    result = await session.execute(
        select(App).where(App.app_id == app_id).options(selectinload(App.owner))
    )
    app = result.scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="no such app")
    if app.owner_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="not your app")
    return app


@app.get("/apps/{app_id}", response_model=AppRecord)
async def get_app_endpoint(
    app_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    app = await _load_owned_app(session, app_id, user)
    return _to_app_record(app)


@app.delete("/apps/{app_id}")
async def delete_app(
    app_id: str,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    rdb = request.app.state.redis
    app = await _load_owned_app(session, app_id, user)

    machine_ids = await rdb.smembers(f"worker_index:{app_id}")
    for mid in machine_ids:
        await rdb.set(f"worker:{mid}:drain", "1", ex=600)
    logger.info("delete app %s: marked %d workers for drain", app_id, len(machine_ids))

    provider = getattr(request.app.state, "provider", None)
    if provider is not None:
        for mid in machine_ids:
            try:
                await provider.terminate(mid)
            except Exception:
                logger.exception("delete app %s: provider.terminate(%s) failed", app_id, mid)

    await rdb.delete(
        f"queue:{app_id}",
        f"app:{app_id}:last_request_ts",
        f"worker_index:{app_id}",
    )
    await session.delete(app)
    await session.commit()

    return {"ok": True, "app_id": app_id, "drained_workers": len(machine_ids)}


# ----- run / result / stream -----

async def _admit_and_enqueue(
    rdb,
    db_session: AsyncSession,
    app_id: str,
    user: User,
    payload: dict,
    *,
    stream: bool,
    endpoint: str = "/v1/completions",
) -> tuple[str, int]:
    app = await _load_owned_app(db_session, app_id, user)
    cfg = app.autoscaler
    cap = int(cfg["max_containers"]) * int(cfg["tasks_per_container"])
    queue_len = await rdb.llen(f"queue:{app_id}")
    if queue_len >= cap:
        raise HTTPException(
            status_code=429,
            detail={"error": "capacity exceeded", "queue_length": queue_len, "cap": cap, "retry_after_s": 5},
        )
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    timeout_s = int(app.request_timeout_s)
    job = {
        "request_id": request_id,
        "payload": payload,
        "timeout_s": timeout_s,
        "endpoint": endpoint,
    }
    if stream:
        job["stream"] = True
    db_session.add(ReqRow(
        request_id=request_id,
        app_id=app_id,
        owner_id=app.owner_id,
        endpoint=endpoint,
        payload=payload,
        is_stream=stream,
    ))
    await db_session.commit()
    await rdb.lpush(f"queue:{app_id}", json.dumps(job))
    await rdb.set(f"result:{request_id}", json.dumps({"status": "pending"}), ex=3600)
    await rdb.set(f"app:{app_id}:last_request_ts", str(time.time()))
    return request_id, timeout_s


@app.post("/run/{app_id}", response_model=RunResponse)
async def run(
    app_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    rdb = request.app.state.redis
    request_id, _ = await _admit_and_enqueue(rdb, session, app_id, user, payload, stream=False)
    logger.info("enqueued %s on %s (user=%s)", request_id, app_id, user.username)
    return RunResponse(request_id=request_id, poll_url=f"/result/{request_id}")


@app.post("/stream/{app_id}")
async def stream(
    app_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    rdb = request.app.state.redis
    app = await _load_owned_app(session, app_id, user)
    cfg = app.autoscaler
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

    timeout_s = int(app.request_timeout_s)
    job = {"request_id": request_id, "payload": payload, "stream": True, "timeout_s": timeout_s}
    await rdb.lpush(f"queue:{app_id}", json.dumps(job))
    await rdb.set(f"result:{request_id}", json.dumps({"status": "pending"}), ex=3600)
    await rdb.set(f"app:{app_id}:last_request_ts", str(time.time()))
    logger.info("enqueued stream %s on %s (timeout=%ss)", request_id, app_id, timeout_s)

    async def gen():
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


@app.get("/result/{request_id}", response_model=ResultResponse)
async def get_result(
    request_id: str,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Result lookup. Lazily mirrors completed Redis state into the requests
    table so the UI can show the result long after Redis TTL expires."""
    rdb = request.app.state.redis
    blob = await rdb.get(f"result:{request_id}")
    if blob is None:
        # Fall back to Postgres — Redis result key may have expired.
        row = await session.get(ReqRow, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        if row.owner_id != user.id and not user.is_admin:
            raise HTTPException(status_code=403, detail="not your request")
        return ResultResponse(request_id=request_id, status=row.status, output=row.output)

    raw = json.loads(blob)
    status = raw.get("status", "unknown")
    output = raw.get("output")

    row = await session.get(ReqRow, request_id)
    if row is not None:
        if row.owner_id != user.id and not user.is_admin:
            raise HTTPException(status_code=403, detail="not your request")
        # Mirror redis -> postgres so the row reflects current state, surviving Redis TTL.
        if row.status != status or row.output != output:
            row.status = status
            row.output = output
            if status != "pending" and row.completed_at is None:
                from datetime import datetime, timezone
                row.completed_at = datetime.now(timezone.utc)
            await session.commit()

    return ResultResponse(request_id=request_id, status=status, output=output)


class RequestRecord(BaseModel):
    request_id: str
    app_id: str
    endpoint: str
    payload: dict
    status: str
    output: Optional[Any] = None
    is_stream: bool
    created_at: str
    completed_at: Optional[str] = None


def _to_request_record(r: ReqRow) -> RequestRecord:
    return RequestRecord(
        request_id=r.request_id,
        app_id=r.app_id,
        endpoint=r.endpoint,
        payload=r.payload,
        status=r.status,
        output=r.output,
        is_stream=r.is_stream,
        created_at=r.created_at.isoformat() if r.created_at else "",
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
    )


@app.get("/requests/{request_id}", response_model=RequestRecord)
async def get_request(
    request_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(ReqRow, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.owner_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="not your request")
    return _to_request_record(row)


@app.get("/apps/{app_id}/requests", response_model=list[RequestRecord])
async def list_app_requests(
    app_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
    status_filter: Optional[str] = None,
):
    """Recent requests for an app, newest first. Owner-scoped (admin sees all)."""
    await _load_owned_app(session, app_id, user)
    from sqlalchemy import select, desc
    stmt = select(ReqRow).where(ReqRow.app_id == app_id).order_by(desc(ReqRow.created_at)).limit(min(limit, 200))
    if status_filter:
        stmt = stmt.where(ReqRow.status == status_filter)
    result = await session.execute(stmt)
    return [_to_request_record(r) for r in result.scalars().all()]


async def _openai_endpoint(
    request: Request,
    db_session: AsyncSession,
    user: User,
    payload: dict,
    vllm_path: str,
):
    rdb = request.app.state.redis
    app_id = payload.get("model")
    if not app_id:
        raise HTTPException(status_code=400, detail={"error": "missing 'model' field in request body"})

    is_stream = bool(payload.get("stream"))
    request_id, _timeout_s = await _admit_and_enqueue(
        rdb, db_session, app_id, user, payload, stream=is_stream, endpoint=vllm_path
    )

    if not is_stream:
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
            yield "data: [DONE]\n\n"
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


@app.post("/v1/chat/completions")
async def openai_chat_completions(
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _openai_endpoint(request, session, user, payload, "/v1/chat/completions")


@app.post("/v1/completions")
async def openai_completions(
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _openai_endpoint(request, session, user, payload, "/v1/completions")


@app.post("/v1/embeddings")
async def openai_embeddings(
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    payload.pop("stream", None)
    return await _openai_endpoint(request, session, user, payload, "/v1/embeddings")


# ----- workers (machine auth, not user auth) -----

@app.post("/workers/register", response_model=WorkerRegisterResponse)
async def register_worker(req: WorkerRegisterRequest, request: Request):
    rdb = request.app.state.redis

    if os.environ.get("AUTOSCALER", "0") == "1":
        token_key = f"register_token:{req.machine_id}"
        expected = await rdb.get(token_key)
        if expected is None or expected != req.token:
            logger.warning(
                "register rejected: machine=%s token=%s (expected=%s)",
                req.machine_id, req.token[:8] + "...", "<set>" if expected else "<missing>",
            )
            raise HTTPException(status_code=401, detail="invalid or expired token")
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
