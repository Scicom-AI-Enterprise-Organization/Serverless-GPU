"""Prometheus metrics for the gateway.

Exposed at GET /metrics (auth-exempt — scrapers don't have keys; protect via
network/ingress allowlist if needed).

Counters/gauges defined:
  - gateway_requests_total{route, status}
  - gateway_inflight_requests          (point-in-time, gauge)
  - gateway_queue_length{app_id}       (sampled per-scrape from Redis)
  - gateway_workers{app_id, status}    (sampled per-scrape from Redis)
  - gateway_provision_total{provider, ok}
  - gateway_terminate_total{provider, ok}
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)

if TYPE_CHECKING:
    import redis.asyncio as redis_async


_registry = CollectorRegistry()


REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total HTTP requests handled by the gateway",
    ["route", "status"],
    registry=_registry,
)

INFLIGHT = Gauge(
    "gateway_inflight_requests",
    "Number of requests currently being handled by the gateway",
    registry=_registry,
)

QUEUE_LENGTH = Gauge(
    "gateway_queue_length",
    "Length of the per-app job queue at scrape time",
    ["app_id"],
    registry=_registry,
)

WORKERS_TOTAL = Gauge(
    "gateway_workers",
    "Number of live workers per app at scrape time",
    ["app_id"],
    registry=_registry,
)

PROVISION_TOTAL = Counter(
    "gateway_provision_total",
    "Worker provision attempts",
    ["provider", "ok"],
    registry=_registry,
)

TERMINATE_TOTAL = Counter(
    "gateway_terminate_total",
    "Worker terminate attempts",
    ["provider", "ok"],
    registry=_registry,
)


async def render(rdb: "redis_async.Redis") -> tuple[bytes, str]:
    """Sample point-in-time gauges from Redis, then serialize the registry."""
    app_ids = await rdb.smembers("apps:index")
    # Reset gauges so deleted apps don't keep stale values forever.
    QUEUE_LENGTH.clear()
    WORKERS_TOTAL.clear()
    for app_id in app_ids:
        QUEUE_LENGTH.labels(app_id=app_id).set(await rdb.llen(f"queue:{app_id}"))
        members = await rdb.smembers(f"worker_index:{app_id}")
        live = 0
        for mid in members:
            if await rdb.exists(f"worker:{mid}"):
                live += 1
        WORKERS_TOTAL.labels(app_id=app_id).set(live)

    return generate_latest(_registry), CONTENT_TYPE_LATEST
