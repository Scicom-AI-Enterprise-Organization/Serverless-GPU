"""Shared pytest fixtures.

A single FakeServer (one in-memory Redis backing store) is shared across the
session; each consumer that calls `redis.asyncio.from_url(...)` gets its own
client connected to that store. This is the canonical fakeredis pattern for
multi-client tests — sharing one FakeRedis instance breaks under concurrency.

The `gateway_url` fixture spins up a uvicorn server in a daemon thread on a
free port, returns its URL, and tears down at the end of the session.
"""
from __future__ import annotations

import asyncio
import os
import socket
import threading
from contextlib import closing

import fakeredis
import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
import uvicorn


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def fake_redis_server():
    return fakeredis.FakeServer()


@pytest.fixture(autouse=True)
def patch_redis_from_url(fake_redis_server, monkeypatch):
    """Every redis.asyncio.from_url(...) call returns a fresh client connected
    to our in-memory FakeServer. Per-test reset of state happens via fakeredis_clear."""
    def fake_from_url(*a, **k):
        return fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    monkeypatch.setattr(redis_asyncio, "from_url", fake_from_url)
    yield


@pytest_asyncio.fixture(autouse=True)
async def fakeredis_clear(fake_redis_server):
    """Wipe all keys before each test. Without this, state leaks between tests."""
    rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    await rdb.flushdb()
    await rdb.aclose()
    yield


@pytest.fixture
def gateway_env(monkeypatch):
    """Reset all gateway-relevant env vars to a known-clean state per test."""
    for k in (
        "AUTOSCALER", "PROVIDER", "GATEWAY_API_KEYS",
        "GATEWAY_URL_FOR_PROVIDER", "WORKER_REDIS_URL", "REDIS_URL",
        "PI_API_KEY", "PI_CUSTOM_TEMPLATE_ID", "GATEWAY_PUBLIC_URL",
    ):
        monkeypatch.delenv(k, raising=False)


def _start_gateway(port: int) -> threading.Thread:
    # Import inside so module-level state picks up the env vars set by the
    # test fixture before app construction.
    from gateway.main import app as gateway_app

    def run():
        cfg = uvicorn.Config(gateway_app, host="127.0.0.1", port=port, log_level="warning")
        asyncio.run(uvicorn.Server(cfg).serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


@pytest_asyncio.fixture
async def gateway_url(gateway_env):
    port = _free_port()
    _start_gateway(port)
    url = f"http://127.0.0.1:{port}"
    # Wait for /health to come up
    async with httpx.AsyncClient(timeout=2.0) as c:
        for _ in range(50):
            try:
                if (await c.get(f"{url}/health")).status_code == 200:
                    return url
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        pytest.fail(f"gateway never came up on {url}")


@pytest_asyncio.fixture
async def gateway_url_with_autoscaler(monkeypatch, fake_redis_server):
    """Gateway + autoscaler + FakeProvider all on, ready for scale-from-zero."""
    monkeypatch.setenv("AUTOSCALER", "1")
    monkeypatch.setenv("PROVIDER", "fake")
    port = _free_port()
    monkeypatch.setenv("GATEWAY_URL_FOR_PROVIDER", f"http://127.0.0.1:{port}")
    _start_gateway(port)
    url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=2.0) as c:
        for _ in range(50):
            try:
                if (await c.get(f"{url}/health")).status_code == 200:
                    return url
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        pytest.fail(f"gateway never came up on {url}")
