"""Reconciler: GC dead machines + log orphans."""
import io
import logging

import fakeredis.aioredis
import pytest

from gateway.reconciler import tick
from gateway.provider import Provider


class _StubProvider(Provider):
    name = "stub"

    def __init__(self, machines):
        self._machines = list(machines)

    async def provision(self, app_id, model, gpu, env):
        raise NotImplementedError

    async def terminate(self, machine_id):
        if machine_id in self._machines:
            self._machines.remove(machine_id)

    async def list_machines(self):
        return list(self._machines)


@pytest.mark.asyncio
async def test_reconciler_logs_orphans(fake_redis_server, caplog):
    rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    provider = _StubProvider(["m-orphan-001", "m-orphan-002"])

    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setLevel(logging.WARNING)
    rec_logger = logging.getLogger("gateway.reconciler")
    rec_logger.addHandler(handler)
    rec_logger.setLevel(logging.WARNING)

    try:
        await tick(rdb, provider)
    finally:
        rec_logger.removeHandler(handler)
        await rdb.aclose()

    output = log_buf.getvalue()
    assert "m-orphan-001" in output
    assert "m-orphan-002" in output
    assert "orphan" in output.lower()


@pytest.mark.asyncio
async def test_reconciler_gcs_dead_machines(fake_redis_server):
    rdb = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    provider = _StubProvider([])  # provider has nothing

    await rdb.sadd("worker_index:qwen", "m-dead-001")
    await rdb.set("worker:m-dead-001", '{"app_id":"qwen"}')

    await tick(rdb, provider)

    members = await rdb.smembers("worker_index:qwen")
    assert "m-dead-001" not in members
    assert await rdb.exists("worker:m-dead-001") == 0
    await rdb.aclose()
