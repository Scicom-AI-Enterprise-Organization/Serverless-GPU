"""API key auth gating."""
import httpx
import pytest


SPEC = {
    "name": "qwen",
    "model": "Qwen",
    "gpu": "H100",
    "autoscaler": {"max_containers": 1, "tasks_per_container": 30, "idle_timeout_s": 300},
}


@pytest.fixture
def auth_env(gateway_env, monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEYS", "key-alpha,key-bravo")


@pytest.mark.asyncio
async def test_health_open_without_auth(auth_env, gateway_url):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{gateway_url}/health")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_apps_requires_auth(auth_env, gateway_url):
    async with httpx.AsyncClient() as c:
        # missing
        assert (await c.get(f"{gateway_url}/apps")).status_code == 401
        # malformed (no Bearer prefix)
        assert (await c.get(f"{gateway_url}/apps", headers={"Authorization": "key-alpha"})).status_code == 401
        # wrong key
        assert (
            await c.get(f"{gateway_url}/apps", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401


@pytest.mark.asyncio
async def test_either_valid_key_works(auth_env, gateway_url):
    """Multi-key support enables zero-downtime rotation."""
    async with httpx.AsyncClient() as c:
        for key in ("key-alpha", "key-bravo"):
            r = await c.get(f"{gateway_url}/apps", headers={"Authorization": f"Bearer {key}"})
            assert r.status_code == 200, f"{key} should work"


@pytest.mark.asyncio
async def test_full_flow_with_auth(auth_env, gateway_url):
    h = {"Authorization": "Bearer key-alpha"}
    async with httpx.AsyncClient() as c:
        await c.post(f"{gateway_url}/apps", headers=h, json=SPEC)
        r = await c.post(f"{gateway_url}/run/qwen", headers=h, json={"prompt": "hi"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_workers_register_exempt_from_gateway_auth(auth_env, gateway_url):
    """Workers use one-shot registration tokens; gateway auth doesn't double-gate."""
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{gateway_url}/workers/register",
            json={"machine_id": "m-x", "app_id": "qwen", "token": "dev"},
        )
        # AUTOSCALER off → registration token validation skipped → accepted
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_metrics_exempt_from_auth(auth_env, gateway_url):
    """/metrics is auth-exempt; protect via network/ingress allowlist if needed."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{gateway_url}/metrics")
        assert r.status_code == 200
        assert "gateway_requests_total" in r.text
