import asyncio
import json
import logging
import os
import uuid
from typing import Any

import httpx
import redis.asyncio as redis_async
from dotenv import load_dotenv

logger = logging.getLogger("worker-agent")


async def register(gateway_url: str, machine_id: str, app_id: str, token: str) -> str:
    url = f"{gateway_url.rstrip('/')}/workers/register"
    body = {"machine_id": machine_id, "app_id": app_id, "token": token}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(1, 31):
            try:
                r = await client.post(url, json=body)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("ok"):
                        return data["redis_url"]
                    raise RuntimeError(f"gateway rejected registration: {data}")
                logger.warning("register attempt %d → status=%s", attempt, r.status_code)
            except httpx.HTTPError as e:
                logger.warning("register attempt %d → %s", attempt, e)
            await asyncio.sleep(1.0)
    raise RuntimeError("gateway never accepted registration after 30 attempts")


async def handle(mode: str, model_id: str, payload: dict, endpoint: str = "/v1/completions") -> Any:
    """Run a unary (non-streaming) request.

    `endpoint` is the path on localhost vLLM to POST to. Defaults to the
    legacy /v1/completions; OpenAI-compat /run uses /v1/chat/completions or
    /v1/embeddings. The body is forwarded verbatim — vLLM is OpenAI-shaped
    natively, so the gateway's job is just queue + auth + autoscale.
    """
    if mode == "fake":
        return {
            "echo": payload,
            "fake": True,
            "model": model_id,
            "endpoint": endpoint,
            "completion": f"[fake response from {model_id}] you sent: {payload}",
        }
    if mode == "vllm":
        url = os.environ.get("VLLM_URL", "http://localhost:8000")
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                r = await client.post(f"{url}{endpoint}", json=payload)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError as e:
                return {"error": str(e)}
    return {"error": f"unknown WORKER_MODE: {mode}"}


async def handle_stream(mode: str, model_id: str, payload: dict, endpoint: str = "/v1/completions"):
    """Async generator yielding chunks. Final chunk is `{"done": True}`."""
    if mode == "fake":
        # Simulate token-by-token output from a real LLM.
        words = ["[fake", "stream", "from", model_id + "]", "you", "sent:", str(payload)]
        for i, word in enumerate(words):
            yield {"index": i, "delta": word + " "}
            await asyncio.sleep(0.02)
        yield {"done": True}
        return

    if mode == "vllm":
        url = os.environ.get("VLLM_URL", "http://localhost:8000")
        body = {**payload, "stream": True}
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream("POST", f"{url}{endpoint}", json=body) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[len("data: "):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            yield {"raw": data}
                yield {"done": True}
                return
            except httpx.HTTPError as e:
                yield {"error": str(e), "done": True}
                return

    yield {"error": f"unknown WORKER_MODE: {mode}", "done": True}


async def heartbeat_loop(gateway_url: str, machine_id: str, app_id: str, drain_event: asyncio.Event) -> None:
    """Heartbeat to gateway every 5s. Set drain_event if gateway tells us to drain."""
    url = f"{gateway_url.rstrip('/')}/workers/heartbeat"
    body = {"machine_id": machine_id, "app_id": app_id, "status": "ready"}
    async with httpx.AsyncClient(timeout=5.0) as client:
        while not drain_event.is_set():
            try:
                r = await client.post(url, json=body)
                if r.status_code == 200 and r.json().get("drain"):
                    logger.info("drain signal received from gateway")
                    drain_event.set()
                    return
            except httpx.HTTPError as e:
                logger.warning("heartbeat error: %s", e)
            try:
                await asyncio.wait_for(drain_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass


async def poll_loop(rdb, queue_key: str, machine_id: str, mode: str, model_id: str, drain_event: asyncio.Event) -> None:
    logger.info("ready, polling %s", queue_key)
    while not drain_event.is_set():
        try:
            res = await rdb.brpop(queue_key, timeout=2)
            if res is None:
                continue
            _key, blob = res
            job = json.loads(blob)
            request_id = job["request_id"]
            payload = job.get("payload", {})
            stream = bool(job.get("stream"))
            timeout_s = float(job.get("timeout_s", 600))
            endpoint = job.get("endpoint", "/v1/completions")
            logger.info("picked up %s (stream=%s endpoint=%s timeout=%ss)", request_id, stream, endpoint, timeout_s)

            if stream:
                await _run_stream(rdb, request_id, machine_id, mode, model_id, payload, timeout_s, endpoint)
            else:
                await _run_unary(rdb, request_id, machine_id, mode, model_id, payload, timeout_s, endpoint)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("loop error, sleeping 1s")
            await asyncio.sleep(1.0)


async def _run_unary(rdb, request_id, machine_id, mode, model_id, payload, timeout_s, endpoint="/v1/completions"):
    try:
        output = await asyncio.wait_for(handle(mode, model_id, payload, endpoint), timeout=timeout_s)
        result = {"status": "completed", "output": output, "machine_id": machine_id}
    except asyncio.TimeoutError:
        logger.warning("%s timed out after %ss", request_id, timeout_s)
        result = {
            "status": "timeout",
            "output": {"error": f"request exceeded timeout_s={timeout_s}"},
            "machine_id": machine_id,
        }
    await rdb.set(f"result:{request_id}", json.dumps(result), ex=3600)
    logger.info("wrote result for %s status=%s", request_id, result["status"])


async def _run_stream(rdb, request_id, machine_id, mode, model_id, payload, timeout_s, endpoint="/v1/completions"):
    """Stream with both per-request timeout AND mid-stream cancel."""
    channel = f"stream:{request_id}"
    cancel_key = f"cancel:{request_id}"
    last = None
    cancelled = False
    timed_out = False
    deadline = asyncio.get_event_loop().time() + timeout_s

    try:
        gen = handle_stream(mode, model_id, payload, endpoint)
        while True:
            now = asyncio.get_event_loop().time()
            remaining = deadline - now
            if remaining <= 0:
                timed_out = True
                break
            try:
                chunk = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                timed_out = True
                break

            if await rdb.exists(cancel_key):
                logger.info("client cancelled %s, stopping mid-stream", request_id)
                cancelled = True
                chunk = {"cancelled": True, "done": True}
                last = chunk
                await rdb.publish(channel, json.dumps(chunk))
                break
            last = chunk
            await rdb.publish(channel, json.dumps(chunk))
    finally:
        if timed_out:
            chunk = {"timeout": True, "timeout_s": timeout_s, "done": True}
            last = chunk
            await rdb.publish(channel, json.dumps(chunk))

    if timed_out:
        status = "timeout"
    elif cancelled:
        status = "cancelled"
    else:
        status = "completed"
    final = {"status": status, "output": last, "machine_id": machine_id, "streamed": True}
    await rdb.set(f"result:{request_id}", json.dumps(final), ex=3600)
    logger.info("streamed %s status=%s", request_id, status)


async def main_async() -> None:
    app_id = os.environ.get("APP_ID")
    if not app_id:
        raise SystemExit("APP_ID env var required")

    machine_id = os.environ.get("MACHINE_ID") or f"m-{uuid.uuid4().hex[:8]}"
    token = os.environ.get("REGISTRATION_TOKEN", "dev-token")
    gateway_url = os.environ.get("GATEWAY_URL", "http://gateway:8080")
    mode = os.environ.get("WORKER_MODE", "fake")
    model_id = os.environ.get("MODEL_ID", "fake-model")

    logger.info(
        "worker booting: app=%s machine=%s mode=%s model=%s gateway=%s",
        app_id, machine_id, mode, model_id, gateway_url,
    )

    redis_url = await register(gateway_url, machine_id, app_id, token)
    logger.info("registered with gateway, redis=%s", redis_url)

    rdb = redis_async.from_url(redis_url, decode_responses=True)
    drain_event = asyncio.Event()
    try:
        await rdb.ping()
        hb_task = asyncio.create_task(
            heartbeat_loop(gateway_url, machine_id, app_id, drain_event)
        )
        try:
            await poll_loop(rdb, f"queue:{app_id}", machine_id, mode, model_id, drain_event)
        finally:
            drain_event.set()
            hb_task.cancel()
            try:
                await hb_task
            except (asyncio.CancelledError, BaseException):
                pass
    finally:
        await rdb.aclose()


def run() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main_async())


if __name__ == "__main__":
    run()
