"""Streaming SSE end-to-end + cancellation.

Strategy: instead of spawning the full worker_agent module (which has caused
event-loop ownership headaches), spin up a *fake worker* asyncio task that
mimics what the real worker does:
  1. BRPOPs the queue
  2. PUBLISHes chunks to stream:{request_id}
  3. SETEX final result key

This isolates the gateway's streaming machinery (subscribe-before-enqueue,
SSE forwarding, cancel signaling) and tests it against fakeredis pubsub.
"""
import asyncio
import json

import fakeredis.aioredis
import httpx
import pytest


SPEC = {
    "name": "qwen",
    "model": "Qwen",
    "gpu": "H100",
    "autoscaler": {"max_containers": 1, "tasks_per_container": 30, "idle_timeout_s": 300},
}


async def _read_sse(stream):
    """Yield parsed JSON chunks from the gateway's SSE response."""
    buf = ""
    async for raw in stream.aiter_text():
        buf += raw
        while "\n\n" in buf:
            evt, buf = buf.split("\n\n", 1)
            for line in evt.split("\n"):
                if line.startswith("data: "):
                    try:
                        yield json.loads(line[6:])
                    except json.JSONDecodeError:
                        pass


async def _fake_worker(fake_redis_server, n_chunks=4, delay_s=0.05, app_id="qwen"):
    """BRPOP one job, publish n_chunks, then publish a {done: true}."""
    rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    try:
        res = await rdb.brpop(f"queue:{app_id}", timeout=5)
        assert res is not None, "fake worker timed out waiting for a job"
        _, blob = res
        job = json.loads(blob)
        request_id = job["request_id"]
        channel = f"stream:{request_id}"

        for i in range(n_chunks):
            # Stop early if the client cancelled (mirrors real worker behavior)
            if await rdb.exists(f"cancel:{request_id}"):
                await rdb.publish(channel, json.dumps({"cancelled": True, "done": True}))
                await rdb.set(f"result:{request_id}", json.dumps({"status": "cancelled"}), ex=60)
                return request_id, "cancelled"
            await rdb.publish(channel, json.dumps({"index": i, "delta": f"tok{i} "}))
            await asyncio.sleep(delay_s)

        await rdb.publish(channel, json.dumps({"done": True}))
        await rdb.set(f"result:{request_id}", json.dumps({"status": "completed"}), ex=60)
        return request_id, "completed"
    finally:
        await rdb.aclose()


@pytest.mark.asyncio
async def test_stream_pipes_pubsub_to_sse_client(gateway_url, fake_redis_server):
    """Gateway's POST /stream subscribes pubsub, enqueues, and forwards chunks."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(f"{gateway_url}/apps", json=SPEC)

        # Start the fake worker BEFORE opening the stream so it's ready to BRPOP.
        worker_task = asyncio.create_task(_fake_worker(fake_redis_server, n_chunks=4))

        chunks = []
        async with c.stream(
            "POST",
            f"{gateway_url}/stream/qwen",
            json={"prompt": "stream me"},
        ) as r:
            assert r.status_code == 200
            request_id = r.headers["X-Request-Id"]
            async for chunk in _read_sse(r):
                chunks.append(chunk)
                if chunk.get("done") or chunk.get("error"):
                    break

        worker_request_id, worker_status = await worker_task
        assert worker_request_id == request_id
        assert worker_status == "completed"

        delta_chunks = [c for c in chunks if "delta" in c]
        assert len(delta_chunks) == 4
        assert chunks[-1].get("done") is True
        # tokens arrived in order
        assert [c["index"] for c in delta_chunks] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_stream_meta_event_carries_request_id(gateway_url, fake_redis_server):
    """First SSE event is `event: meta` with the request_id, useful for debugging."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(f"{gateway_url}/apps", json=SPEC)
        worker_task = asyncio.create_task(_fake_worker(fake_redis_server, n_chunks=1))

        async with c.stream("POST", f"{gateway_url}/stream/qwen", json={}) as r:
            request_id_header = r.headers["X-Request-Id"]
            buf = ""
            saw_meta = False
            async for raw in r.aiter_text():
                buf += raw
                if "event: meta" in buf and "request_id" in buf:
                    saw_meta = True
                if "\"done\":" in buf or "\"done\": true" in buf:
                    break
            assert saw_meta, "first SSE event should be the meta event"
            # the meta event contains the same request_id as the header
            assert request_id_header in buf

        await worker_task


@pytest.mark.asyncio
async def test_stream_admits_429_at_cap(gateway_url, fake_redis_server):
    """Streaming admission shares the same cap as /run."""
    async with httpx.AsyncClient(timeout=5.0) as c:
        await c.post(
            f"{gateway_url}/apps",
            json={
                "name": "tiny",
                "model": "x",
                "gpu": "H100",
                "autoscaler": {"max_containers": 1, "tasks_per_container": 1, "idle_timeout_s": 300},
            },
        )
        # Fill the queue
        rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        try:
            await rdb.lpush("queue:tiny", json.dumps({"request_id": "filler", "payload": {}}))
        finally:
            await rdb.aclose()

        # Streaming endpoint should also reject with 429
        r = await c.post(f"{gateway_url}/stream/tiny", json={})
        assert r.status_code == 429


@pytest.mark.asyncio
async def test_stream_404_on_unknown_app(gateway_url):
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.post(f"{gateway_url}/stream/nope", json={"prompt": "hi"})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_stream_cancel_signals_worker_via_redis(gateway_url, fake_redis_server):
    """When the client closes the stream early, the gateway sets cancel:{id}.

    A real worker would observe this and stop generating; here we just verify
    the gateway side emits the signal correctly.
    """
    async with httpx.AsyncClient(timeout=5.0) as c:
        await c.post(f"{gateway_url}/apps", json=SPEC)

        # A fake worker that pumps chunks slowly, never sending {done}.
        # Gateway's SSE generator will stay open; we close from the client side.
        async def slow_worker():
            rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
            try:
                res = await rdb.brpop("queue:qwen", timeout=5)
                _, blob = res
                request_id = json.loads(blob)["request_id"]
                channel = f"stream:{request_id}"
                for i in range(20):
                    if await rdb.exists(f"cancel:{request_id}"):
                        return request_id, True
                    await rdb.publish(channel, json.dumps({"index": i, "delta": f"tok{i}"}))
                    await asyncio.sleep(0.1)
                return request_id, False
            finally:
                await rdb.aclose()

        worker_task = asyncio.create_task(slow_worker())

        # Read 2 chunks, then bail by exiting the context manager early.
        chunks_seen = 0
        async with c.stream("POST", f"{gateway_url}/stream/qwen", json={}) as r:
            request_id = r.headers["X-Request-Id"]
            async for chunk in _read_sse(r):
                if "delta" in chunk:
                    chunks_seen += 1
                    if chunks_seen >= 2:
                        break

        # Give the gateway's `finally` a tick to set the cancel key
        await asyncio.sleep(0.3)

        rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        try:
            cancel_set = await rdb.exists(f"cancel:{request_id}")
        finally:
            await rdb.aclose()
        assert cancel_set, "gateway should set cancel:{request_id} on client disconnect"

        worker_rid, worker_saw_cancel = await worker_task
        assert worker_rid == request_id
        assert worker_saw_cancel, "fake worker should observe the cancel flag"
