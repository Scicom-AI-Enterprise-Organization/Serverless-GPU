"""App lifecycle: deploy / list / show / delete."""
import asyncio
import json

import httpx
import pytest


SPEC = {
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "gpu": "H100",
    "autoscaler": {"max_containers": 2, "tasks_per_container": 30, "idle_timeout_s": 300},
}


@pytest.mark.asyncio
async def test_empty_list_initially(gateway_url):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{gateway_url}/apps")
        assert r.status_code == 200
        assert r.json() == []


@pytest.mark.asyncio
async def test_deploy_list_show_delete_roundtrip(gateway_url):
    async with httpx.AsyncClient() as c:
        # deploy
        r = await c.post(f"{gateway_url}/apps", json={"name": "qwen", **SPEC})
        assert r.status_code == 200
        assert r.json() == {"app_id": "qwen", "url": "/run/qwen"}

        # list
        r = await c.get(f"{gateway_url}/apps")
        names = {a["name"] for a in r.json()}
        assert names == {"qwen"}

        # show
        r = await c.get(f"{gateway_url}/apps/qwen")
        rec = r.json()
        assert rec["model"] == SPEC["model"]
        assert rec["autoscaler"]["max_containers"] == 2

        # delete
        r = await c.delete(f"{gateway_url}/apps/qwen")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # list empty
        r = await c.get(f"{gateway_url}/apps")
        assert r.json() == []


@pytest.mark.asyncio
async def test_delete_idempotency_returns_404_after_first_call(gateway_url):
    async with httpx.AsyncClient() as c:
        await c.post(f"{gateway_url}/apps", json={"name": "qwen", **SPEC})
        assert (await c.delete(f"{gateway_url}/apps/qwen")).status_code == 200
        assert (await c.delete(f"{gateway_url}/apps/qwen")).status_code == 404


@pytest.mark.asyncio
async def test_delete_isolates_other_apps(gateway_url, fake_redis_server):
    import fakeredis.aioredis
    async with httpx.AsyncClient() as c:
        await c.post(f"{gateway_url}/apps", json={"name": "qwen", **SPEC})
        await c.post(f"{gateway_url}/apps", json={"name": "llama", **SPEC})

        # Inject ghost state for qwen to confirm full cleanup
        rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        try:
            await rdb.lpush("queue:qwen", json.dumps({"x": 1}))
            await rdb.set("worker:m-001", '{"app_id":"qwen"}')
            await rdb.sadd("worker_index:qwen", "m-001")
        finally:
            await rdb.aclose()

        r = await c.delete(f"{gateway_url}/apps/qwen")
        assert r.json()["drained_workers"] == 1

        rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        try:
            assert await rdb.exists("app:qwen") == 0
            assert await rdb.exists("queue:qwen") == 0
            assert await rdb.exists("worker_index:qwen") == 0
            # llama untouched
            assert await rdb.exists("app:llama") == 1
        finally:
            await rdb.aclose()


@pytest.mark.asyncio
async def test_run_on_deleted_app_returns_404(gateway_url):
    async with httpx.AsyncClient() as c:
        await c.post(f"{gateway_url}/apps", json={"name": "qwen", **SPEC})
        await c.delete(f"{gateway_url}/apps/qwen")
        r = await c.post(f"{gateway_url}/run/qwen", json={"prompt": "hi"})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_admission_429_when_queue_at_cap(gateway_url):
    async with httpx.AsyncClient() as c:
        # cap = 1 * 2 = 2
        await c.post(
            f"{gateway_url}/apps",
            json={
                "name": "tiny",
                "model": "test",
                "gpu": "H100",
                "autoscaler": {"max_containers": 1, "tasks_per_container": 2, "idle_timeout_s": 300},
            },
        )
        # Fill the queue exactly to cap (autoscaler is OFF, no workers drain).
        for _ in range(2):
            r = await c.post(f"{gateway_url}/run/tiny", json={})
            assert r.status_code == 200
        # 3rd → 429
        r = await c.post(f"{gateway_url}/run/tiny", json={})
        assert r.status_code == 429
        body = r.json()
        assert body["detail"]["cap"] == 2
