"""Prometheus /metrics surface."""
import httpx
import pytest


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_registry(gateway_url):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{gateway_url}/metrics")
        assert r.status_code == 200
        body = r.text
        for metric in (
            "gateway_requests_total",
            "gateway_inflight_requests",
        ):
            assert metric in body, f"missing {metric}"


@pytest.mark.asyncio
async def test_request_template_cardinality_bounded(gateway_url):
    """Routes are reported by template, not by raw path. Otherwise every
    new app_id would create a new time series."""
    async with httpx.AsyncClient() as c:
        # Drive a few /apps/{app_id} requests with different ids
        await c.post(
            f"{gateway_url}/apps",
            json={
                "name": "qwen",
                "model": "x",
                "gpu": "H100",
                "autoscaler": {"max_containers": 1, "tasks_per_container": 30, "idle_timeout_s": 300},
            },
        )
        for app_id in ("qwen", "missing-1", "missing-2"):
            await c.get(f"{gateway_url}/apps/{app_id}")

        body = (await c.get(f"{gateway_url}/metrics")).text
        # The route appears once with template, not once per app_id
        assert 'route="/apps/{app_id}"' in body
        assert 'route="/apps/qwen"' not in body
        assert 'route="/apps/missing-1"' not in body


@pytest.mark.asyncio
async def test_per_app_gauges_appear(gateway_url):
    async with httpx.AsyncClient() as c:
        await c.post(
            f"{gateway_url}/apps",
            json={
                "name": "tiny",
                "model": "x",
                "gpu": "L4",
                "autoscaler": {"max_containers": 1, "tasks_per_container": 30, "idle_timeout_s": 300},
            },
        )
        body = (await c.get(f"{gateway_url}/metrics")).text
        assert 'gateway_queue_length{app_id="tiny"}' in body
        assert 'gateway_workers{app_id="tiny"}' in body
