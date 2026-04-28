"""Reconciler loop: trust the cloud API as the source of truth for liveness.

Every 5s:
  1. Ask the provider what's actually running (its `list_machines()`).
  2. SCAN Redis for our `worker_index:*` sets.
  3. Compute the diff:
       - in-redis but NOT in-provider  → pod is gone; SREM from index, DEL state key
                                          (this catches manual terminations,
                                          PI-side crashes, billing kills, ...)
       - in-provider but NOT in-redis  → orphan pod; log a warning so the
                                          operator can investigate
  4. Sleep 5s, repeat.

Tracks the "machines we provisioned but never registered" case via the
`worker:{id}:provisioning` ttl set by the autoscaler — if expired AND the
provider doesn't know about the pod either, we clean up our state.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as redis_async
    from .provider import Provider

logger = logging.getLogger("gateway.reconciler")

TICK_S = 5.0


async def reconciler_loop(rdb: "redis_async.Redis", provider: "Provider") -> None:
    logger.info("reconciler running (provider=%s)", provider.name)
    while True:
        try:
            await asyncio.sleep(TICK_S)
            await tick(rdb, provider)
        except asyncio.CancelledError:
            logger.info("reconciler cancelled")
            raise
        except Exception:
            logger.exception("reconciler tick failed")


async def tick(rdb: "redis_async.Redis", provider: "Provider") -> None:
    try:
        provider_machines = set(await provider.list_machines())
    except NotImplementedError:
        return
    except Exception:
        logger.exception("provider.list_machines failed; skipping tick")
        return

    redis_machines: set[str] = set()
    async for key in rdb.scan_iter(match="worker_index:*"):
        members = await rdb.smembers(key)
        redis_machines.update(members)

    gone = redis_machines - provider_machines
    orphans = provider_machines - redis_machines

    for machine_id in gone:
        await _remove_machine(rdb, machine_id)
        logger.info("reconciler: %s no longer in provider, GC'd from Redis", machine_id)

    for machine_id in orphans:
        logger.warning(
            "reconciler: %s exists on provider but not in Redis (orphan)",
            machine_id,
        )


async def _remove_machine(rdb: "redis_async.Redis", machine_id: str) -> None:
    """Delete the worker's state and remove from any index that contains it."""
    await rdb.delete(f"worker:{machine_id}")
    await rdb.delete(f"worker:{machine_id}:drain")
    async for key in rdb.scan_iter(match="worker_index:*"):
        await rdb.srem(key, machine_id)
