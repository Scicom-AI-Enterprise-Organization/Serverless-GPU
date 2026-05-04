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
import os
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
PROVISION_COOLDOWN_S = 60  # back off this long after a provider provision failure
PROVISION_ERROR_TTL_S = 600  # how long the UI keeps showing the last error

# Worker events ring: per-worker timeline of lifecycle events the UI shows
# under "gateway events" — provisioned, registered, scaled, terminated, etc.
WORKER_EVENTS_CAP = 200
WORKER_EVENTS_TTL_S = 3600


def build_metrics_env(app_id: str, provider_name: str) -> dict[str, str]:
    """Per-worker observability env: tells the worker entrypoint to run
    ansible-pull on the gpu-metrics-exporter playbook so it self-installs the
    DCGM/node/vLLM exporter stack and ships metrics to VictoriaMetrics under
    an `endpoint=<app_id>` label.

    Returns an empty dict (= disabled) when the gateway pod is missing the
    METRICS_REMOTE_WRITE_URL/USERNAME/PASSWORD secret triple — keeps local
    dev installs working without forcing the secret to exist."""
    url = (os.environ.get("METRICS_REMOTE_WRITE_URL") or "").strip()
    user = (os.environ.get("METRICS_USERNAME") or "").strip()
    pw = (os.environ.get("METRICS_PASSWORD") or "").strip()
    if not (url and user and pw):
        return {}
    repo = (os.environ.get("METRICS_REPO_URL") or "https://github.com/AIES-Infra/gpu-metrics-exporter.git").strip()
    branch = (os.environ.get("METRICS_REPO_BRANCH") or "main").strip()
    return {
        "ENABLE_METRICS": "true",
        "METRICS_REPO_URL": repo,
        "METRICS_REPO_BRANCH": branch,
        "METRICS_REMOTE_WRITE_URL": url,
        "METRICS_USERNAME": user,
        "METRICS_PASSWORD": pw,
        "METRICS_DATACENTER": provider_name,
        "METRICS_ENDPOINT": app_id,
    }


async def emit_worker_event(
    rdb: "redis_async.Redis",
    machine_id: str,
    app_id: str,
    level: str,
    msg: str,
) -> None:
    """Append a lifecycle event to the worker's capped Redis ring.

    Also stamps `worker_app:{mid}` so the read endpoint can authorize a
    request even after the worker pod has been torn down (worker:{mid}
    expires when the worker stops heartbeating)."""
    if not machine_id:
        return
    try:
        entry = json.dumps({"ts": time.time(), "level": level, "msg": msg})
        key = f"worker_events:{machine_id}"
        await rdb.lpush(key, entry)
        await rdb.ltrim(key, 0, WORKER_EVENTS_CAP - 1)
        await rdb.expire(key, WORKER_EVENTS_TTL_S)
        if app_id:
            await rdb.set(f"worker_app:{machine_id}", app_id, ex=WORKER_EVENTS_TTL_S)
    except Exception:
        logger.exception("emit_worker_event failed for %s", machine_id)


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
    # Reconcile per-app inside a per-app try/except so one bad app (provider
    # rejecting its GPU spec, etc.) doesn't starve the others on this tick.
    for app in apps:
        try:
            await _reconcile_app(rdb, provider, app)
        except Exception:
            logger.exception("autoscaler: reconcile failed for app=%s", app.app_id)


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
        # Cooldown: skip the provision attempt entirely if the last try failed
        # recently. Otherwise we spam the upstream provider every tick when
        # there's no inventory or our spec is wrong, which burns API quota
        # and noses-up the gateway logs.
        cooldown_until_blob = await rdb.get(f"app:{app_id}:provision_cooldown_until")
        if cooldown_until_blob:
            try:
                if time.time() < float(cooldown_until_blob):
                    return
            except (TypeError, ValueError):
                pass
        n_to_add = desired - current
        for _ in range(n_to_add):
            token = secrets.token_urlsafe(24)
            env: dict[str, str] = {"REGISTRATION_TOKEN": token}
            extra = (getattr(app, "vllm_args", "") or "").strip()
            if extra:
                env["VLLM_EXTRA_ARGS"] = extra
            if bool(getattr(app, "enable_metrics", True)):
                env.update(build_metrics_env(app_id, provider.name))
            try:
                machine_id = await provider.provision(
                    app_id=app_id,
                    model=app.model,
                    gpu=app.gpu,
                    env=env,
                    gpu_count=int(getattr(app, "gpu_count", 1) or 1),
                )
                _metrics.PROVISION_TOTAL.labels(provider=provider.name, ok="true").inc()
                await emit_worker_event(
                    rdb, machine_id, app_id, "info",
                    f"provisioned on {provider.name} (gpu={app.gpu}x{int(getattr(app, 'gpu_count', 1) or 1)})",
                )
                # Clear any stale error/cooldown after a successful provision.
                await rdb.delete(
                    f"app:{app_id}:last_provision_error",
                    f"app:{app_id}:last_provision_error_at",
                    f"app:{app_id}:provision_cooldown_until",
                )
            except Exception as e:
                _metrics.PROVISION_TOTAL.labels(provider=provider.name, ok="false").inc()
                error_msg = (str(e) or repr(e))[:1000]
                cooldown_until = time.time() + PROVISION_COOLDOWN_S
                await rdb.set(
                    f"app:{app_id}:provision_cooldown_until",
                    str(cooldown_until),
                    ex=PROVISION_COOLDOWN_S + 30,
                )
                await rdb.set(
                    f"app:{app_id}:last_provision_error",
                    error_msg,
                    ex=PROVISION_ERROR_TTL_S,
                )
                await rdb.set(
                    f"app:{app_id}:last_provision_error_at",
                    str(time.time()),
                    ex=PROVISION_ERROR_TTL_S,
                )
                logger.warning(
                    "provision failed for app=%s gpu=%sx%d: %s — cooldown %ds",
                    app_id, app.gpu,
                    int(getattr(app, "gpu_count", 1) or 1),
                    error_msg[:200], PROVISION_COOLDOWN_S,
                )
                return  # don't try the next slot in n_to_add this tick
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
            await emit_worker_event(
                rdb, machine_id, app_id, "info",
                f"scaled up: +1 worker → {current + 1}/{max_containers}",
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
                await emit_worker_event(
                    rdb, victim, app_id, "info",
                    f"terminated (idle for {int(idle_for)}s)",
                )
            except Exception:
                _metrics.TERMINATE_TOTAL.labels(provider=provider.name, ok="false").inc()
                await emit_worker_event(rdb, victim, app_id, "error", "terminate failed (provider error)")
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
