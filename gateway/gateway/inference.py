"""Inference endpoints — scale-to-zero LLM hosts on PI bare metal.

Architecture: each Inference endpoint is paired with a hidden `apps` row.
The existing /apps queue + autoscaler + worker-agent + reconciler handle
provisioning, scale-to-zero, and request dispatch unchanged. The /inference
API is just a thin facade that:
    - hides GPU model behind a tier_label
    - auto-picks GPU + autoscaler config from a HF repo
    - forwards `/inference/{id}/v1/...` into the existing queue path

`idle_timeout_s == 0` means "never tear down" — the autoscaler skips the
scale-down branch when set to 0.

Auth re-uses gateway sessions (same `current_user` / `require_developer`).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_developer
from .db import App, Model, Request as ReqRow, User, get_session
from .inference_planner import recommend_gpu

logger = logging.getLogger("gateway.inference")
router = APIRouter(prefix="/inference", tags=["inference"])


# ---------- Pydantic schemas ----------

class CreateModelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    hf_repo: str = Field(min_length=1, max_length=255)
    # 0 = never tear down. Otherwise 60..3600s.
    idle_timeout_s: int = Field(default=300, ge=0, le=3600)


class UpdateModelRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    idle_timeout_s: Optional[int] = Field(default=None, ge=0, le=3600)


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
    created_at: str
    # Admin-only fields (None for non-admin)
    gpu_type: Optional[str] = None
    vram_gb: Optional[int] = None
    app_id: Optional[str] = None


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


# ---------- helpers ----------

def _to_record(m: Model, *, is_admin: bool) -> ModelRecord:
    return ModelRecord(
        id=m.id,
        name=m.name,
        hf_repo=m.hf_repo,
        tier_label=m.tier_label,
        idle_timeout_s=m.idle_timeout_s,
        created_at=m.created_at.isoformat() if m.created_at else "",
        gpu_type=m.gpu_type if is_admin else None,
        vram_gb=m.vram_gb if is_admin else None,
        app_id=m.app_id if is_admin else None,
    )


async def _load_owned_model(session: AsyncSession, model_id: str, user: User) -> Model:
    m = await session.get(Model, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="no such model")
    if m.owner_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="not your model")
    return m


def _autoscaler_for(idle_timeout_s: int) -> dict:
    """Inference apps run with one max replica and the user's idle timeout.
    idle_timeout_s == 0 means never tear down (autoscaler honors this)."""
    return {
        "max_containers": 1,
        "tasks_per_container": 30,
        "idle_timeout_s": int(idle_timeout_s),
    }


# ---------- recommend ----------

@router.post("/models/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest, _: User = Depends(require_developer)):
    pick = await recommend_gpu(req.hf_repo)
    return RecommendResponse(vram_gb=pick.vram_gb, tier_label=pick.tier_label)


# ---------- CRUD ----------

@router.post("/models", response_model=ModelRecord)
async def create_model(
    req: CreateModelRequest,
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
):
    pick = await recommend_gpu(req.hf_repo)
    model_id = str(uuid.uuid4())
    # The hidden app uses a fixed prefix so we can identify inference-backed
    # apps later (e.g. for NFS mount, billing tags, etc).
    app_id = f"inf-{model_id[:12]}"

    app = App(
        app_id=app_id,
        owner_id=user.id,
        name=app_id,
        model=req.hf_repo,
        gpu=pick.gpu_type,
        autoscaler=_autoscaler_for(req.idle_timeout_s),
        cpu=2,
        memory="16Gi",
        request_timeout_s=600,
        created_at=datetime.now(timezone.utc),
    )
    model = Model(
        id=model_id,
        owner_id=user.id,
        name=req.name,
        hf_repo=req.hf_repo,
        vram_gb=pick.vram_gb,
        gpu_type=pick.gpu_type,
        tier_label=pick.tier_label,
        idle_timeout_s=req.idle_timeout_s,
        app_id=app_id,
        state="idle",
    )
    session.add(app)
    session.add(model)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail={"error": "name collision; try again"})
    await session.refresh(model)
    logger.info(
        "inference: created model=%s app=%s repo=%s tier=%s by user=%s",
        model_id, app_id, req.hf_repo, pick.tier_label, user.username,
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
        # Mirror to the hidden app's autoscaler so the loop sees it.
        if m.app_id:
            from sqlalchemy.orm.attributes import flag_modified
            app = await session.get(App, m.app_id)
            if app is not None:
                cfg = dict(app.autoscaler or {})
                cfg["idle_timeout_s"] = int(req.idle_timeout_s)
                app.autoscaler = cfg
                flag_modified(app, "autoscaler")
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
    """Drop the inference endpoint, draining its workers via the App path."""
    m = await _load_owned_model(session, model_id, user)
    app_id = m.app_id

    # Best-effort: drain workers and tear down provider pods using the same
    # logic as DELETE /apps/{app_id}, then remove rows.
    if app_id:
        rdb = request.app.state.redis
        provider = getattr(request.app.state, "provider", None)

        tracked = set(await rdb.smembers(f"worker_index:{app_id}"))
        for mid in tracked:
            await rdb.set(f"worker:{mid}:drain", "1", ex=600)

        all_machines = set(tracked)
        if provider is not None:
            try:
                orphans = set(await provider.list_machines_for_app(app_id)) - tracked
                all_machines |= orphans
            except Exception:
                logger.exception("delete inference %s: list_machines_for_app failed", model_id)
            for mid in all_machines:
                try:
                    await provider.terminate(mid)
                except Exception:
                    logger.exception("delete inference %s: provider.terminate(%s) failed", model_id, mid)

        for mid in all_machines:
            await rdb.delete(f"worker:{mid}", f"register_token:{mid}")
        await rdb.delete(
            f"queue:{app_id}",
            f"app:{app_id}:last_request_ts",
            f"worker_index:{app_id}",
        )

        # Drop the hidden app row
        app = await session.get(App, app_id)
        if app is not None:
            await session.delete(app)

    await session.delete(m)
    await session.commit()
    logger.info("inference: deleted model=%s (app=%s)", model_id, app_id)
    return {"ok": True, "model_id": model_id}


# ---------- request history ----------

@router.get("/models/{model_id}/requests", response_model=list[InferenceRequestRecord])
async def list_model_requests(
    model_id: str,
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
):
    m = await _load_owned_model(session, model_id, user)
    # Inference requests carry the model_id; legacy /apps requests on the
    # backing app would carry app_id only, so query by either.
    stmt = (
        select(ReqRow)
        .where((ReqRow.model_id == model_id) | (ReqRow.app_id == m.app_id))
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


# ---------- OpenAI-compatible proxy: enqueue into the hidden app ----------

async def _proxy_to_queue(
    model_id: str,
    payload: dict,
    request: Request,
    user: User,
    vllm_path: str,
):
    """Enqueue against the hidden App and reuse the existing OpenAI bridge.

    Same code path as POST /v1/chat/completions on /apps — just with the
    model id rewritten to the hidden app id, and request rows tagged with
    `model_id` so /inference/{id}/requests works.
    """
    from .main import _admit_and_enqueue, _openai_endpoint_after_admit  # late import: avoid circular at module load
    from .db import session_factory as sf
    import json, time

    async with sf()() as s:
        m = await _load_owned_model(s, model_id, user)
        if not m.app_id:
            raise HTTPException(status_code=503, detail={"error": "inference endpoint not bound to a worker pool"})
        app_id = m.app_id

    # Inject the hidden app id as the model field so the autoscaler queue
    # routes correctly. Keep the user-visible `hf_repo` value out of the
    # payload — vLLM gets called by the worker with --model already pinned.
    forward_payload = dict(payload)
    forward_payload["model"] = app_id

    # _admit_and_enqueue handles capacity check, db row creation, redis push.
    # We pass through the raw dict; it tags the request with app_id but not
    # model_id, so we patch the row after enqueue.
    rdb = request.app.state.redis
    async with sf()() as s:
        is_stream = bool(payload.get("stream"))
        request_id, _t = await _admit_and_enqueue(
            rdb, s, app_id, user, forward_payload,
            stream=is_stream, endpoint=vllm_path,
        )
        # Tag the request with the inference model id for history lookup.
        row = await s.get(ReqRow, request_id)
        if row is not None:
            row.model_id = model_id
            await s.commit()

    # Now block on the result via the same OpenAI bridge logic from main.py.
    return await _openai_endpoint_after_admit(request, request_id, is_stream)


@router.post("/{model_id}/v1/chat/completions")
async def chat_completions(
    model_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_developer),
):
    return await _proxy_to_queue(model_id, payload, request, user, "/v1/chat/completions")


@router.post("/{model_id}/v1/completions")
async def completions(
    model_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_developer),
):
    return await _proxy_to_queue(model_id, payload, request, user, "/v1/completions")


@router.post("/{model_id}/v1/embeddings")
async def embeddings(
    model_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_developer),
):
    payload.pop("stream", None)
    return await _proxy_to_queue(model_id, payload, request, user, "/v1/embeddings")


@router.get("/{model_id}/v1/models")
async def list_vllm_models(
    model_id: str,
    user: User = Depends(require_developer),
    session: AsyncSession = Depends(get_session),
):
    """Pass-through for OpenAI clients that introspect available models."""
    m = await _load_owned_model(session, model_id, user)
    return {"object": "list", "data": [{"id": m.hf_repo, "object": "model"}]}
