"""Inference endpoints — scale-to-zero LLM hosts on PI bare metal.

Self-contained sub-router. Only touches `models` table + `requests.model_id`.
Nothing here mutates `apps`, the autoscaler, or the existing reconciler.

Lifecycle:
    create   →  state=idle, network_volume created on PI
    request  →  cold-start: provision_vllm, wait for ready-checkin, proxy
    quiet    →  inference_reconciler tears down after idle_timeout_s
    delete   →  terminate active machine + delete volume + drop row

Auth re-uses gateway API keys (same `current_user` / `require_developer`).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import current_user, require_developer
from .db import Model, Request as ReqRow, User, get_session
from .inference_planner import recommend_gpu

logger = logging.getLogger("gateway.inference")
router = APIRouter(prefix="/inference", tags=["inference"])

# Per-process locks keyed by model_id so concurrent first-requests don't all
# trigger a cold-start. Lives in memory; if the gateway has multiple replicas
# they may briefly race — first-write-wins on the DB state field is fine.
_cold_start_locks: dict[str, asyncio.Lock] = {}


def _lock_for(model_id: str) -> asyncio.Lock:
    lock = _cold_start_locks.get(model_id)
    if lock is None:
        lock = asyncio.Lock()
        _cold_start_locks[model_id] = lock
    return lock


# ---------- Pydantic schemas ----------

class CreateModelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    hf_repo: str = Field(min_length=1, max_length=255)
    idle_timeout_s: int = Field(default=300, ge=60, le=3600)


class UpdateModelRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    idle_timeout_s: Optional[int] = Field(default=None, ge=60, le=3600)


class RecommendRequest(BaseModel):
    hf_repo: str = Field(min_length=1, max_length=255)


class RecommendResponse(BaseModel):
    vram_gb: int
    tier_label: str
    estimated_cold_start_s: int = 60


class ModelRecord(BaseModel):
    id: str
    name: str
    hf_repo: str
    tier_label: str
    idle_timeout_s: int
    state: str
    last_request_at: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str
    # Admin-only fields (None for non-admin)
    gpu_type: Optional[str] = None
    vram_gb: Optional[int] = None
    active_machine_id: Optional[str] = None
    active_endpoint: Optional[str] = None


class ReadyCheckinRequest(BaseModel):
    model_id: str
    endpoint: str  # http://ip:port


# ---------- helpers ----------

def _to_record(m: Model, *, is_admin: bool) -> ModelRecord:
    return ModelRecord(
        id=m.id,
        name=m.name,
        hf_repo=m.hf_repo,
        tier_label=m.tier_label,
        idle_timeout_s=m.idle_timeout_s,
        state=m.state,
        last_request_at=m.last_request_at.isoformat() if m.last_request_at else None,
        last_error=m.last_error,
        created_at=m.created_at.isoformat() if m.created_at else "",
        gpu_type=m.gpu_type if is_admin else None,
        vram_gb=m.vram_gb if is_admin else None,
        active_machine_id=m.active_machine_id if is_admin else None,
        active_endpoint=m.active_endpoint if is_admin else None,
    )


async def _load_owned_model(session: AsyncSession, model_id: str, user: User) -> Model:
    m = await session.get(Model, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="no such model")
    if m.owner_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="not your model")
    return m


def _worker_token_secret() -> str:
    # Reuse the gateway's session secret; fine since worker tokens are
    # short-lived and only checked at /inference/internal/ready-checkin.
    return os.environ.get("WORKER_TOKEN_SECRET") or os.environ.get("SECRET_KEY") or "dev-secret"


def mint_worker_token(model_id: str, ttl_s: int = 600) -> str:
    """HMAC-signed token: <exp>.<sig>. Verified on ready-checkin."""
    exp = int(time.time()) + ttl_s
    body = f"{model_id}.{exp}".encode()
    sig = hmac.new(_worker_token_secret().encode(), body, hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def verify_worker_token(model_id: str, token: str) -> bool:
    try:
        exp_str, sig = token.split(".", 1)
        exp = int(exp_str)
        if exp < int(time.time()):
            return False
        body = f"{model_id}.{exp}".encode()
        expected = hmac.new(_worker_token_secret().encode(), body, hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


async def _set_state(
    session: AsyncSession,
    model: Model,
    state: str,
    *,
    machine_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    model.state = state
    if machine_id is not None:
        model.active_machine_id = machine_id or None
    if endpoint is not None:
        model.active_endpoint = endpoint or None
    if last_error is not None:
        model.last_error = last_error or None
    model.updated_at = datetime.now(timezone.utc)
    await session.commit()


# ---------- recommend ----------

@router.post("/models/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest, _: User = Depends(require_developer)):
    pick = await recommend_gpu(req.hf_repo)
    return RecommendResponse(vram_gb=pick.vram_gb, tier_label=pick.tier_label)


# ---------- CRUD ----------

@router.post("/models", response_model=ModelRecord)
async def create_model(
    req: CreateModelRequest,
    request: Request,
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
):
    pick = await recommend_gpu(req.hf_repo)
    model = Model(
        id=str(uuid.uuid4()),
        owner_id=user.id,
        name=req.name,
        hf_repo=req.hf_repo,
        vram_gb=pick.vram_gb,
        gpu_type=pick.gpu_type,
        tier_label=pick.tier_label,
        idle_timeout_s=req.idle_timeout_s,
        state="idle",
    )
    # Best-effort network volume create. If the provider doesn't support
    # volumes (or PI errors out), we still create the row so the user can
    # retry — first cold-start will just download to ephemeral storage.
    provider = getattr(request.app.state, "provider", None)
    if provider is not None and hasattr(provider, "create_network_volume"):
        try:
            volume_size = max(20, pick.vram_gb * 2)  # rough: model file ≤ vram*2
            vid = await provider.create_network_volume(
                name=f"sgpu-inf-{model.id[:8]}", size_gb=volume_size,
            )
            model.network_volume_id = vid
        except Exception as e:
            logger.warning("network volume create failed for model=%s: %s", model.id, e)
    session.add(model)
    await session.commit()
    await session.refresh(model)
    logger.info(
        "inference: created model=%s name=%s repo=%s tier=%s by user=%s",
        model.id, model.name, model.hf_repo, model.tier_label, user.username,
    )
    return _to_record(model, is_admin=user.is_admin)


@router.get("/models", response_model=list[ModelRecord])
async def list_models(
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Model).order_by(desc(Model.created_at))
    if not user.is_admin:
        stmt = stmt.where(Model.owner_id == user.id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_record(m, is_admin=user.is_admin) for m in rows]


@router.get("/models/{model_id}", response_model=ModelRecord)
async def get_model(
    model_id: str,
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
):
    m = await _load_owned_model(session, model_id, user)
    return _to_record(m, is_admin=user.is_admin)


@router.patch("/models/{model_id}", response_model=ModelRecord)
async def patch_model(
    model_id: str,
    req: UpdateModelRequest,
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
):
    m = await _load_owned_model(session, model_id, user)
    if req.name is not None:
        m.name = req.name
    if req.idle_timeout_s is not None:
        m.idle_timeout_s = req.idle_timeout_s
    m.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(m)
    return _to_record(m, is_admin=user.is_admin)


@router.delete("/models/{model_id}")
async def delete_model(
    model_id: str,
    request: Request,
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
):
    m = await _load_owned_model(session, model_id, user)
    provider = getattr(request.app.state, "provider", None)
    if m.active_machine_id and provider is not None:
        try:
            await provider.terminate(m.active_machine_id)
        except Exception:
            logger.exception("delete model %s: terminate active machine failed", model_id)
    if m.network_volume_id and provider is not None and hasattr(provider, "delete_network_volume"):
        try:
            await provider.delete_network_volume(m.network_volume_id)
        except Exception:
            logger.exception("delete model %s: delete_network_volume failed", model_id)
    await session.delete(m)
    await session.commit()
    return {"ok": True, "model_id": model_id}


# ---------- requests history ----------

class InferenceRequestRecord(BaseModel):
    request_id: str
    model_id: str
    endpoint: str
    payload: dict
    status: str
    output: Optional[Any] = None
    is_stream: bool
    created_at: str
    completed_at: Optional[str] = None


@router.get("/models/{model_id}/requests", response_model=list[InferenceRequestRecord])
async def list_model_requests(
    model_id: str,
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
):
    await _load_owned_model(session, model_id, user)
    stmt = (
        select(ReqRow)
        .where(ReqRow.model_id == model_id)
        .order_by(desc(ReqRow.created_at))
        .limit(min(limit, 200))
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        InferenceRequestRecord(
            request_id=r.request_id,
            model_id=r.model_id or model_id,
            endpoint=r.endpoint,
            payload=r.payload,
            status=r.status,
            output=r.output,
            is_stream=r.is_stream,
            created_at=r.created_at.isoformat() if r.created_at else "",
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
        )
        for r in rows
    ]


# ---------- worker ready-checkin (no user auth) ----------

@router.post("/internal/ready-checkin")
async def ready_checkin(
    req: ReadyCheckinRequest,
    x_worker_token: str = Header(..., alias="X-Worker-Token"),
    session: AsyncSession = Depends(get_session),
):
    if not verify_worker_token(req.model_id, x_worker_token):
        raise HTTPException(status_code=401, detail="invalid worker token")
    m = await session.get(Model, req.model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="no such model")
    await _set_state(session, m, "ready", endpoint=req.endpoint, last_error="")
    logger.info("inference: model=%s ready at %s", m.id, req.endpoint)
    return {"ok": True}


# ---------- cold-start + proxy ----------

async def _wait_for_ready(session_factory_fn, model_id: str, *, timeout_s: int = 240) -> Model:
    """Poll the DB until the worker checks in (state=ready) or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        async with session_factory_fn()() as s:
            m = await s.get(Model, model_id)
            if m is None:
                raise HTTPException(status_code=410, detail="model deleted while cold-starting")
            if m.state == "ready" and m.active_endpoint:
                return m
            if m.state == "error":
                raise HTTPException(status_code=502, detail={"error": m.last_error or "cold-start failed"})
        await asyncio.sleep(1.5)
    raise HTTPException(
        status_code=504,
        detail={"error": f"cold-start did not complete within {timeout_s}s"},
    )


async def _cold_start(request: Request, model: Model, session: AsyncSession) -> Model:
    """Provision a vLLM pod and block until ready-checkin lands.

    Caller already holds `_lock_for(model.id)` so we won't double-provision.
    """
    provider = getattr(request.app.state, "provider", None)
    if provider is None or not hasattr(provider, "provision_vllm"):
        raise HTTPException(status_code=503, detail={"error": "provider does not support inference"})
    await _set_state(session, model, "cold-starting", endpoint="", last_error="")
    token = mint_worker_token(model.id)
    image = os.environ.get("PI_VLLM_IMAGE")  # optional override
    try:
        machine_id = await provider.provision_vllm(
            model_id=model.id,
            hf_repo=model.hf_repo,
            gpu=model.gpu_type,
            network_volume_id=model.network_volume_id,
            worker_token=token,
            image=image,
        )
        model.active_machine_id = machine_id
        await session.commit()
    except Exception as e:
        await _set_state(session, model, "error", last_error=str(e)[:400])
        raise HTTPException(status_code=502, detail={"error": f"provision failed: {e}"})
    # Drop the session lock while we wait — the worker checks in via a
    # separate request and needs to commit its own update.
    return await _wait_for_ready(
        lambda: __import__("gateway.db", fromlist=["session_factory"]).session_factory(),
        model.id,
        timeout_s=300,
    )


async def _ensure_ready(request: Request, model_id: str, user: User) -> Model:
    """Return a model in state=ready, cold-starting if needed."""
    from .db import session_factory as sf  # local import: avoid circular at module load
    async with sf()() as s:
        m = await _load_owned_model(s, model_id, user)
        if m.state == "ready" and m.active_endpoint:
            return m
        # Take the per-model lock and re-check.
        async with _lock_for(model_id):
            async with sf()() as s2:
                m2 = await s2.get(Model, model_id)
                if m2 is None:
                    raise HTTPException(status_code=404, detail="no such model")
                if m2.state == "ready" and m2.active_endpoint:
                    return m2
                if m2.state == "cold-starting":
                    # Another worker started it — just wait.
                    return await _wait_for_ready(sf, model_id, timeout_s=300)
                # state in {idle, error, terminating}: provision now.
                return await _cold_start(request, m2, s2)


def _record_request(
    db_session: AsyncSession,
    model: Model,
    *,
    payload: dict,
    is_stream: bool,
    endpoint: str,
) -> ReqRow:
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    row = ReqRow(
        request_id=request_id,
        app_id=None,
        model_id=model.id,
        owner_id=model.owner_id,
        endpoint=endpoint,
        payload=payload,
        is_stream=is_stream,
    )
    db_session.add(row)
    return row


async def _bump_last_request(model: Model, session: AsyncSession) -> None:
    model.last_request_at = datetime.now(timezone.utc)
    await session.commit()


async def _proxy_openai(
    model: Model,
    payload: dict,
    *,
    vllm_path: str,
    request_id: str,
    db_session_factory,
) -> Any:
    """Forward to vLLM and persist the result. Streams via SSE."""
    is_stream = bool(payload.get("stream"))
    target = f"{model.active_endpoint.rstrip('/')}{vllm_path}"

    if not is_stream:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(target, json=payload)
        try:
            output = r.json()
        except Exception:
            output = {"raw": r.text}
        # Persist completion
        async with db_session_factory()() as s:
            row = await s.get(ReqRow, request_id)
            if row is not None:
                row.status = "completed" if r.status_code < 400 else "error"
                row.output = output
                row.completed_at = datetime.now(timezone.utc)
                await s.commit()
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=output)
        return output

    # Streaming path
    async def gen():
        chunks: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", target, json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        chunks.append(line)
                        yield f"{line}\n\n"
        finally:
            async with db_session_factory()() as s:
                row = await s.get(ReqRow, request_id)
                if row is not None:
                    row.status = "completed"
                    row.output = {"chunks": chunks[-50:]}  # keep tail only
                    row.completed_at = datetime.now(timezone.utc)
                    await s.commit()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _handle_proxy(
    model_id: str,
    payload: dict,
    request: Request,
    user: User,
    vllm_path: str,
):
    from .db import session_factory as sf
    model = await _ensure_ready(request, model_id, user)
    async with sf()() as s:
        row = _record_request(s, model, payload=payload, is_stream=bool(payload.get("stream")), endpoint=vllm_path)
        await s.commit()
        request_id = row.request_id
        # Bump last_request_at on the model row.
        m_live = await s.get(Model, model.id)
        if m_live is not None:
            await _bump_last_request(m_live, s)
    return await _proxy_openai(model, payload, vllm_path=vllm_path, request_id=request_id, db_session_factory=sf)


@router.post("/{model_id}/v1/chat/completions")
async def chat_completions(
    model_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_developer),
):
    return await _handle_proxy(model_id, payload, request, user, "/v1/chat/completions")


@router.post("/{model_id}/v1/completions")
async def completions(
    model_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_developer),
):
    return await _handle_proxy(model_id, payload, request, user, "/v1/completions")


@router.post("/{model_id}/v1/embeddings")
async def embeddings(
    model_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_developer),
):
    payload.pop("stream", None)
    return await _handle_proxy(model_id, payload, request, user, "/v1/embeddings")


@router.get("/{model_id}/v1/models")
async def list_vllm_models(
    model_id: str,
    request: Request,
    user: User = Depends(require_developer),
):
    """Pass-through for OpenAI clients that introspect available models."""
    from .db import session_factory as sf
    async with sf()() as s:
        m = await _load_owned_model(s, model_id, user)
    if m.state != "ready" or not m.active_endpoint:
        # Don't cold-start just for a model list — return what we know.
        return {"object": "list", "data": [{"id": m.hf_repo, "object": "model"}]}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{m.active_endpoint.rstrip('/')}/v1/models")
    return r.json()
