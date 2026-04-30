"""Idle teardown loop for inference endpoints.

Separate from the existing serverless `reconciler.py` so the two features
can't interfere. Tick every 15s:

- state=ready and now - last_request_at > idle_timeout_s   → terminate
- state=cold-starting older than 5min                      → mark error
- state=terminating older than 5min                        → drop, set idle
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .db import Model
from .provider import Provider

logger = logging.getLogger("gateway.inference_reconciler")

TICK_S = 15
STUCK_COLD_START_S = 300       # ~5min
STUCK_TERMINATING_S = 300


async def _tick(session_factory: async_sessionmaker, provider: Provider) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        rows = (await session.execute(select(Model))).scalars().all()
        for m in rows:
            try:
                if m.state == "ready":
                    if m.last_request_at is None:
                        # never been hit since boot — treat created_at as the clock
                        ref = m.updated_at or m.created_at
                    else:
                        ref = m.last_request_at
                    if (now - ref).total_seconds() > m.idle_timeout_s:
                        logger.info("inference-recon: tearing down idle model=%s (idle for %ds)",
                                    m.id, int((now - ref).total_seconds()))
                        m.state = "terminating"
                        await session.commit()
                        try:
                            if m.active_machine_id:
                                await provider.terminate(m.active_machine_id)
                        except Exception:
                            logger.exception("inference-recon: terminate failed for %s", m.id)
                        m.active_machine_id = None
                        m.active_endpoint = None
                        m.state = "idle"
                        m.updated_at = now
                        await session.commit()
                elif m.state == "cold-starting":
                    age = (now - (m.updated_at or m.created_at)).total_seconds()
                    if age > STUCK_COLD_START_S:
                        logger.warning("inference-recon: cold-start stuck for model=%s (%ds), marking error", m.id, int(age))
                        if m.active_machine_id:
                            try:
                                await provider.terminate(m.active_machine_id)
                            except Exception:
                                logger.exception("inference-recon: stuck-cold-start terminate failed")
                        m.active_machine_id = None
                        m.active_endpoint = None
                        m.state = "error"
                        m.last_error = "cold-start timed out"
                        m.updated_at = now
                        await session.commit()
                elif m.state == "terminating":
                    age = (now - (m.updated_at or m.created_at)).total_seconds()
                    if age > STUCK_TERMINATING_S:
                        m.state = "idle"
                        m.active_machine_id = None
                        m.active_endpoint = None
                        m.updated_at = now
                        await session.commit()
            except Exception:
                logger.exception("inference-recon: unexpected error on model=%s", m.id)


async def inference_reconciler_loop(session_factory: async_sessionmaker, provider: Provider) -> None:
    logger.info("inference reconciler started (tick=%ds)", TICK_S)
    while True:
        try:
            await _tick(session_factory, provider)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("inference-recon: tick failed")
        await asyncio.sleep(TICK_S)
