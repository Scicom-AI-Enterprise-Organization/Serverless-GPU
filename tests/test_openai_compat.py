"""OpenAI-compatible /v1 routes.

Verifies:
  - POST /v1/chat/completions extracts `model` from body, enqueues with
    endpoint=/v1/chat/completions
  - 400 if `model` missing
  - 404 if model isn't a deployed app
  - 429 admission applied
  - Stream mode returns SSE
  - Sync mode polls and returns vllm response directly
"""
import asyncio
import json

import fakeredis.aioredis
import httpx
import pytest


SPEC = {
    "name": "qwen",
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "gpu": "H100",
    "autoscaler": {"max_containers": 1, "tasks_per_container": 30, "idle_timeout_s": 300},
}


async def _fake_worker_response(fake_redis_server, app_id="qwen", response=None):
    """Mimic a worker that pulls a job and writes a completed result."""
    rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    try:
        res = await rdb.brpop(f"queue:{app_id}", timeout=5)
        if res is None:
            return None
        _, blob = res
        job = json.loads(blob)
        request_id = job["request_id"]
        endpoint = job.get("endpoint")
        payload = job.get("payload", {})
        # Mimic vLLM's actual OpenAI response shape
        result = response or {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": payload.get("model", "unknown"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "hello back"},
                "finish_reason": "stop",
            }],
            "_endpoint_seen": endpoint,
        }
        await rdb.set(
            f"result:{request_id}",
            json.dumps({"status": "completed", "output": result, "machine_id": "m-test"}),
            ex=60,
        )
        return request_id, endpoint
    finally:
        await rdb.aclose()


@pytest.mark.asyncio
async def test_v1_chat_completions_sync(gateway_url, fake_redis_server):
    """Sync chat-completions: client → gateway → enqueue → worker → result body."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(f"{gateway_url}/apps", json=SPEC)

        worker_task = asyncio.create_task(_fake_worker_response(fake_redis_server))

        r = await c.post(
            f"{gateway_url}/v1/chat/completions",
            json={"model": "qwen", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
        body = r.json()
        # OpenAI-shape response
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["content"] == "hello back"
        # Worker saw the right vLLM endpoint hint
        assert body["_endpoint_seen"] == "/v1/chat/completions"

        await worker_task


@pytest.mark.asyncio
async def test_v1_completions_routes_to_legacy_endpoint(gateway_url, fake_redis_server):
    """Legacy /v1/completions routes to vllm /v1/completions (not chat)."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(f"{gateway_url}/apps", json=SPEC)
        worker_task = asyncio.create_task(_fake_worker_response(fake_redis_server))

        r = await c.post(
            f"{gateway_url}/v1/completions",
            json={"model": "qwen", "prompt": "hi"},
        )
        assert r.status_code == 200
        assert r.json()["_endpoint_seen"] == "/v1/completions"
        await worker_task


@pytest.mark.asyncio
async def test_v1_embeddings(gateway_url, fake_redis_server):
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(f"{gateway_url}/apps", json=SPEC)
        # Different shaped response for embeddings
        worker_task = asyncio.create_task(
            _fake_worker_response(fake_redis_server, response={
                "object": "list",
                "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}],
                "_endpoint_seen": "/v1/embeddings",
            })
        )

        r = await c.post(
            f"{gateway_url}/v1/embeddings",
            json={"model": "qwen", "input": "hello"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"][0]["embedding"] == [0.1, 0.2]
        await worker_task


@pytest.mark.asyncio
async def test_v1_missing_model_field(gateway_url):
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{gateway_url}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},  # no model
        )
        assert r.status_code == 400
        assert "model" in r.json()["detail"]["error"]


@pytest.mark.asyncio
async def test_v1_unknown_model(gateway_url):
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{gateway_url}/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_v1_admission_429(gateway_url, fake_redis_server):
    """OpenAI route shares the same admission cap as /run."""
    async with httpx.AsyncClient() as c:
        # cap = 1 * 1 = 1
        await c.post(f"{gateway_url}/apps", json={
            "name": "tiny",
            "model": "x",
            "gpu": "H100",
            "autoscaler": {"max_containers": 1, "tasks_per_container": 1, "idle_timeout_s": 300},
        })
        rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        try:
            await rdb.lpush("queue:tiny", json.dumps({"request_id": "filler", "payload": {}}))
        finally:
            await rdb.aclose()

        r = await c.post(
            f"{gateway_url}/v1/chat/completions",
            json={"model": "tiny", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 429


@pytest.mark.asyncio
async def test_v1_chat_completions_streaming(gateway_url, fake_redis_server):
    """stream:true returns SSE; OpenAI-style [DONE] terminator at the end."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(f"{gateway_url}/apps", json=SPEC)

        async def fake_stream_worker():
            rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
            try:
                res = await rdb.brpop("queue:qwen", timeout=5)
                _, blob = res
                job = json.loads(blob)
                channel = f"stream:{job['request_id']}"
                # Mimic vllm's chat-completion delta chunks
                for i in range(3):
                    chunk = {
                        "id": "chatcmpl-fake",
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"content": f"tok{i} "}}],
                    }
                    await rdb.publish(channel, json.dumps(chunk))
                    await asyncio.sleep(0.02)
                await rdb.publish(channel, json.dumps({"done": True}))
            finally:
                await rdb.aclose()

        worker_task = asyncio.create_task(fake_stream_worker())

        chunks = []
        saw_done_terminator = False
        async with c.stream(
            "POST",
            f"{gateway_url}/v1/chat/completions",
            json={"model": "qwen", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            buf = ""
            async for raw in r.aiter_text():
                buf += raw
                while "\n\n" in buf:
                    evt, buf = buf.split("\n\n", 1)
                    for line in evt.split("\n"):
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                saw_done_terminator = True
                            else:
                                try:
                                    chunks.append(json.loads(data))
                                except json.JSONDecodeError:
                                    pass
                if saw_done_terminator:
                    break

        await worker_task

        delta_chunks = [c for c in chunks if "delta" in c.get("choices", [{}])[0]]
        assert len(delta_chunks) == 3
        assert saw_done_terminator, "OpenAI SSE convention: terminate with `data: [DONE]`"
