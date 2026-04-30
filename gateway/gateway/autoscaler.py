"""Per-app autoscaler loop.

Runs at 1Hz. For every registered app (loaded from Postgres):
  - sample queue length, in-flight tasks, current worker count
  - desired = ceil(queue_len / tasks_per_container), capped at max_containers
  - if desired > current: provider.provision()
  - if queue+inflight=0 AND idle > idle_timeout_s: terminate one worker (until 0)
  - idle_timeout_s == 0 disables teardown entirely (always-on)

Worker liveness is tracked by Redis TTL on `worker:{machine_id}` keys.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import time
from typing import TYPE_CHECKING

from sqlalchemy import select

from .db import App

if TYPE_CHECKING:
    import redis.asyncio as redis_async
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from .provider import Provider

logger = logging.getLogger("gateway.autoscaler")

TICK_S = 1.0
REGISTRATION_TOKEN_TTL_S = 1800  # 30 min — covers slow ECR pulls + vLLM model load


async def autoscaler_loop(
    rdb: "redis_async.Redis",
    provider: "Provider",
    sm: "async_sessionmaker[AsyncSession]",
) -> None:
    logger.info("autoscaler running")
    while True:
        try:
            await asyncio.sleep(TICK_S)
            await tick(rdb, provider, sm)
        except asyncio.CancelledError:
            logger.info("autoscaler cancelled")
            raise
        except Exception:
            logger.exception("autoscaler tick failed")


async def tick(
    rdb: "redis_async.Redis",
    provider: "Provider",
    sm: "async_sessionmaker[AsyncSession]",
) -> None:
    async with sm() as session:
        result = await session.execute(select(App))
        apps = list(result.scalars().all())
    for app in apps:
        await _reconcile_app(rdb, provider, app)


async def _reconcile_app(rdb: "redis_async.Redis", provider: "Provider", app: App) -> None:
    app_id = app.app_id
    autoscaler_cfg = app.autoscaler
    max_containers = int(autoscaler_cfg["max_containers"])
    tasks_per_container = int(autoscaler_cfg["tasks_per_container"])
    idle_timeout_s = int(autoscaler_cfg["idle_timeout_s"])

    queue_len = await rdb.llen(f"queue:{app_id}")
    workers = await _live_workers(rdb, app_id)
    current = len(workers)

    # idle_timeout_s == 0 also means "always-on": keep at least one worker
    # warm so the first request doesn't pay cold-start, and respawn if the
    # worker dies.
    always_on = idle_timeout_s == 0
    if queue_len == 0:
        desired = 1 if always_on else 0
    else:
        desired = math.ceil(queue_len / tasks_per_container)
        if always_on:
            desired = max(desired, 1)
    desired = min(max_containers, desired)

    last_request_blob = await rdb.get(f"app:{app_id}:last_request_ts")
    last_request_ts = float(last_request_blob) if last_request_blob else 0.0
    idle_for = time.time() - last_request_ts if last_request_ts else float("inf")

    if desired > current:
        from . import metrics as _metrics
        n_to_add = desired - current
        for _ in range(n_to_add):
            token = secrets.token_urlsafe(24)
            try:
                machine_id = await provider.provision(
                    app_id=app_id,
                    model=app.model,
                    gpu=app.gpu,
                    env={"REGISTRATION_TOKEN": token},
                )
                _metrics.PROVISION_TOTAL.labels(provider=provider.name, ok="true").inc()
            except Exception:
                _metrics.PROVISION_TOTAL.labels(provider=provider.name, ok="false").inc()
                raise
            await rdb.set(
                f"register_token:{machine_id}",
                token,
                ex=REGISTRATION_TOKEN_TTL_S,
            )
            await rdb.sadd(f"worker_index:{app_id}", machine_id)
            await rdb.set(
                f"worker:{machine_id}",
                json.dumps({
                    "machine_id": machine_id,
                    "app_id": app_id,
                    "status": "provisioning",
                    "last_seen": time.time(),
                }),
                ex=REGISTRATION_TOKEN_TTL_S,
            )
            logger.info("scaled up %s: +1 worker (%s) → %d/%d", app_id, machine_id, current + 1, max_containers)
            current += 1
    elif idle_timeout_s > 0 and desired < current and queue_len == 0 and idle_for >= idle_timeout_s:
        if workers:
            from . import metrics as _metrics
            victim = workers[0]
            try:
                await provider.terminate(victim)
                _metrics.TERMINATE_TOTAL.labels(provider=provider.name, ok="true").inc()
            except Exception:
                _metrics.TERMINATE_TOTAL.labels(provider=provider.name, ok="false").inc()
                raise
            await rdb.delete(f"worker:{victim}")
            await rdb.srem(f"worker_index:{app_id}", victim)
            logger.info("scaled down %s: -1 worker (%s, idle %.0fs)", app_id, victim, idle_for)


async def _live_workers(rdb: "redis_async.Redis", app_id: str) -> list[str]:
    candidates = await rdb.smembers(f"worker_index:{app_id}")
    live: list[str] = []
    for mid in candidates:
        if await rdb.exists(f"worker:{mid}"):
            live.append(mid)
        else:
            await rdb.srem(f"worker_index:{app_id}", mid)
    return live
