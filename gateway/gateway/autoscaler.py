"""Per-app autoscaler loop.

Runs at 1Hz. For every registered app:
  - sample queue length, in-flight tasks, current worker count
  - desired = ceil(queue_len / tasks_per_container), capped at max_containers
  - if desired > current: provider.provision()
  - if queue+inflight=0 AND idle > idle_timeout_s: terminate one worker (until 0)

Worker liveness is tracked by Redis TTL on `worker:{machine_id}` keys.
The autoscaler cleans up `worker_index:{app_id}` entries whose underlying
key has expired (= worker died without unregistering).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as redis_async
    from .provider import Provider

logger = logging.getLogger("gateway.autoscaler")

TICK_S = 1.0
REGISTRATION_TOKEN_TTL_S = 300  # 5 min — pod must register within this window


async def autoscaler_loop(rdb: "redis_async.Redis", provider: "Provider") -> None:
    logger.info("autoscaler running")
    while True:
        try:
            await asyncio.sleep(TICK_S)
            await tick(rdb, provider)
        except asyncio.CancelledError:
            logger.info("autoscaler cancelled")
            raise
        except Exception:
            logger.exception("autoscaler tick failed")


async def tick(rdb: "redis_async.Redis", provider: "Provider") -> None:
    app_ids = await rdb.smembers("apps:index")
    for app_id in app_ids:
        await _reconcile_app(rdb, provider, app_id)


async def _reconcile_app(rdb: "redis_async.Redis", provider: "Provider", app_id: str) -> None:
    app_blob = await rdb.get(f"app:{app_id}")
    if app_blob is None:
        await rdb.srem("apps:index", app_id)
        return
    app = json.loads(app_blob)
    autoscaler_cfg = app["autoscaler"]
    max_containers = int(autoscaler_cfg["max_containers"])
    tasks_per_container = int(autoscaler_cfg["tasks_per_container"])
    idle_timeout_s = int(autoscaler_cfg["idle_timeout_s"])

    queue_len = await rdb.llen(f"queue:{app_id}")
    workers = await _live_workers(rdb, app_id)
    current = len(workers)

    if queue_len == 0:
        desired = 0
    else:
        desired = min(max_containers, math.ceil(queue_len / tasks_per_container))

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
                    model=app["model"],
                    gpu=app["gpu"],
                    env={"REGISTRATION_TOKEN": token},
                )
                _metrics.PROVISION_TOTAL.labels(provider=provider.name, ok="true").inc()
            except Exception:
                _metrics.PROVISION_TOTAL.labels(provider=provider.name, ok="false").inc()
                raise
            # Store token AFTER provision so we have machine_id to scope it.
            # Worker has 5 minutes to register or the token + provisioning
            # state expire and the machine is GC'd by the reconciler.
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
                ex=120,  # provisioning grace window
            )
            logger.info("scaled up %s: +1 worker (%s) → %d/%d", app_id, machine_id, current + 1, max_containers)
            current += 1
    elif desired < current and queue_len == 0 and idle_for >= idle_timeout_s:
        # Idle scale-down: remove the oldest live worker.
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
    """Return machine_ids that have a live `worker:{id}` key. GC dead ones."""
    candidates = await rdb.smembers(f"worker_index:{app_id}")
    live: list[str] = []
    for mid in candidates:
        if await rdb.exists(f"worker:{mid}"):
            live.append(mid)
        else:
            await rdb.srem(f"worker_index:{app_id}", mid)
    return live
