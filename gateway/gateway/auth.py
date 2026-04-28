"""Bearer token auth on user-facing routes.

V0 model: shared keys from env. Set `GATEWAY_API_KEYS=key1,key2,key3`. Each
client uses one of these in `Authorization: Bearer <key>`.

Auth is *off* (all routes open) when GATEWAY_API_KEYS is empty — that's the
default for local dev / fakeredis tests where wiring auth would just be noise.
For any public deployment, set the env var.

Routes deliberately exempted:
  /health           - liveness probe, no auth ever
  /workers/register - validated by the one-shot registration token
  /workers/heartbeat - validated by machine_id existing in worker_index
"""
from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request


def _load_keys() -> set[str]:
    raw = os.environ.get("GATEWAY_API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def get_keys() -> set[str]:
    """Re-read on each call so tests can mutate env without reloading the module."""
    return _load_keys()


def require_api_key(request: Request) -> None:
    """FastAPI dependency. Raises 401 if Authorization header is missing or
    doesn't carry a valid bearer key.

    No-op when GATEWAY_API_KEYS is empty (dev mode).
    """
    keys = get_keys()
    if not keys:
        return  # auth disabled

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": "missing or malformed Authorization header"},
        )
    presented = header[len("Bearer "):].strip()

    # Constant-time comparison against each known key. Avoids timing oracles
    # for short keys; the cost is O(N) where N = number of keys (small).
    matched = False
    for k in keys:
        if secrets.compare_digest(presented, k):
            matched = True
    if not matched:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid api key"},
        )
