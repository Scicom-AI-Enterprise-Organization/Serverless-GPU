"""Liveness vs readiness split."""
import httpx
import pytest


@pytest.mark.asyncio
async def test_health_is_liveness_only(gateway_url):
    """/health must always return 200 when the process is alive — no external deps."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{gateway_url}/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_ready_pings_redis(gateway_url):
    """/ready returns 200 only when Redis is reachable."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{gateway_url}/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["redis"] == "ok"


@pytest.mark.asyncio
async def test_ready_503_when_redis_dies(gateway_url, monkeypatch):
    """If Redis disappears, /ready should return 503 so k8s depools the pod."""
    async with httpx.AsyncClient() as c:
        # Patch the gateway's redis client to raise on ping. Reach into the
        # running app's state via an internal HTTP route would be invasive;
        # instead, drop in a stub via the lifespan pattern would be ideal but
        # complex. Pragmatic: monkey-patch the FakeRedis ping method.
        from gateway.main import app as gateway_app

        async def boom():
            raise ConnectionError("simulated redis down")

        rdb = gateway_app.state.redis
        original = rdb.ping
        rdb.ping = boom
        try:
            r = await c.get(f"{gateway_url}/ready")
            assert r.status_code == 503
            assert r.json()["detail"]["redis"] == "unreachable"
        finally:
            rdb.ping = original
