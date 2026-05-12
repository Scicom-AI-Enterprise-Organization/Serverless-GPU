"""HTTP routes for user-registered cloud providers.

For phase 1 only VM (bare-metal SSH) is implemented. Adding RunPod/PI later
means another `_validate_*_config` branch and a `kind` value the test path
recognises.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit as audit_module
from . import crypto
from .auth import current_user, require_admin
from .db import Provider, User, get_session
from .vm_probe import availability_vm, probe_vm

logger = logging.getLogger("gateway.providers")

router = APIRouter(prefix="/v1/providers", tags=["providers"])

VM_DEFAULT_PORT = 22
SUPPORTED_KINDS = ("vm",)


class VmConfig(BaseModel):
    host: str
    port: int = VM_DEFAULT_PORT
    user: str = "root"
    # Full PEM body. Required on create; on update the client may omit it to
    # keep the existing key — handled by `CreateProviderRequest` validation.
    private_key: Optional[str] = None


class CreateProviderRequest(BaseModel):
    name: str
    kind: str  # "vm" only for now
    vm: Optional[VmConfig] = None


class TestProviderRequest(BaseModel):
    """Test against an arbitrary config without persisting it. The frontend
    calls this from the new-provider form so users can verify SSH before they
    commit to saving the row.

    Alternately, callers may pass `provider_id` to test an already-saved
    provider — useful for the list page's per-row "Re-test" button later.
    """
    kind: str
    vm: Optional[VmConfig] = None
    provider_id: Optional[str] = None


class ProviderRecord(BaseModel):
    """Public shape. Never includes the private key body."""
    id: str
    name: str
    kind: str
    created_at: str
    created_by: str
    # VM-specific summary; absent for other kinds.
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    gpus: Optional[list[str]] = None
    gpu_count: Optional[int] = None


class TestProviderResponse(BaseModel):
    ok: bool
    message: str
    gpus: list[str] = []
    gpu_count: int = 0


class GpuLiveInfo(BaseModel):
    index: int
    name: str
    mem_free_mib: int
    mem_total_mib: int
    util_pct: int


class AvailabilityResponse(BaseModel):
    ok: bool
    message: str
    gpus: list[GpuLiveInfo] = []
    checked_at: float


def _to_record(p: Provider, owner_username: str) -> ProviderRecord:
    cfg = p.config or {}
    return ProviderRecord(
        id=p.id,
        name=p.name,
        kind=p.kind,
        created_at=p.created_at.isoformat() if p.created_at else "",
        created_by=owner_username,
        host=cfg.get("host"),
        port=cfg.get("port"),
        user=cfg.get("user"),
        gpus=cfg.get("gpus"),
        gpu_count=cfg.get("gpu_count"),
    )


def _validate_vm(vm: Optional[VmConfig]) -> VmConfig:
    if vm is None:
        raise HTTPException(status_code=400, detail="vm config required for kind=vm")
    if not vm.host.strip():
        raise HTTPException(status_code=400, detail="vm.host is required")
    if vm.port < 1 or vm.port > 65535:
        raise HTTPException(status_code=400, detail="vm.port must be 1..65535")
    if not vm.user.strip():
        raise HTTPException(status_code=400, detail="vm.user is required")
    return vm


@router.get("", response_model=list[ProviderRecord])
async def list_providers(
    user: User = Depends(current_user),  # noqa: ARG001 — auth-only; list is org-wide
    session: AsyncSession = Depends(get_session),
):
    """All providers are visible to every authenticated user so the resource
    forms (benchmark, serverless) can show the dropdown. Writes (POST/DELETE)
    require admin — see those handlers."""
    # Join to users so we can populate `created_by` without a per-row lookup.
    from sqlalchemy.orm import selectinload
    result = await session.execute(
        select(Provider).order_by(Provider.created_at.desc())
    )
    rows = list(result.scalars().all())
    # Resolve owner usernames in one extra query.
    owner_ids = {p.owner_id for p in rows}
    owner_map: dict[int, str] = {}
    if owner_ids:
        from .db import User as _User
        users = await session.execute(select(_User).where(_User.id.in_(owner_ids)))
        for u in users.scalars().all():
            owner_map[u.id] = u.username
    return [_to_record(p, owner_map.get(p.owner_id, "?")) for p in rows]


@router.post("", response_model=ProviderRecord)
async def create_provider(
    req: CreateProviderRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if req.kind not in SUPPORTED_KINDS:
        raise HTTPException(status_code=400, detail=f"unsupported kind: {req.kind}")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="name is required")

    config: dict
    if req.kind == "vm":
        vm = _validate_vm(req.vm)
        if not vm.private_key or not vm.private_key.strip():
            raise HTTPException(status_code=400, detail="vm.private_key is required")
        config = {
            "host": vm.host.strip(),
            "port": int(vm.port),
            "user": vm.user.strip(),
            "private_key_enc": crypto.encrypt(vm.private_key),
        }
    else:  # pragma: no cover — guarded above
        raise HTTPException(status_code=400, detail=f"kind {req.kind} not implemented")

    pid = f"prov-{secrets.token_hex(4)}"
    row = Provider(
        id=pid,
        owner_id=user.id,
        name=req.name.strip(),
        kind=req.kind,
        config=config,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    await audit_module.record(
        user, "provider.create", "provider", pid, req.name,
        details={"kind": req.kind},
    )
    logger.info("created provider %s (%s) for user=%s", pid, req.kind, user.username)
    return _to_record(row, user.username)


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(Provider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")
    name = row.name
    kind = row.kind
    await session.delete(row)
    await session.commit()
    await audit_module.record(
        user, "provider.delete", "provider", provider_id, name,
        details={"kind": kind},
    )
    return {"ok": True, "id": provider_id}


@router.post("/test", response_model=TestProviderResponse)
async def test_provider(
    req: TestProviderRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if req.kind not in SUPPORTED_KINDS:
        raise HTTPException(status_code=400, detail=f"unsupported kind: {req.kind}")

    # Resolve config: either inline (new-provider form) or from a saved row.
    if req.provider_id:
        row = await session.get(Provider, req.provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="provider not found")
        if row.kind != req.kind:
            raise HTTPException(status_code=400, detail="kind mismatch")
        cfg = row.config or {}
        host = cfg.get("host", "")
        port = int(cfg.get("port") or VM_DEFAULT_PORT)
        ssh_user = cfg.get("user", "root")
        enc = cfg.get("private_key_enc")
        if not enc:
            raise HTTPException(status_code=500, detail="provider missing stored key")
        private_key = crypto.decrypt(enc)
    else:
        vm = _validate_vm(req.vm)
        if not vm.private_key or not vm.private_key.strip():
            raise HTTPException(status_code=400, detail="vm.private_key required for test")
        host = vm.host.strip()
        port = int(vm.port)
        ssh_user = vm.user.strip()
        private_key = vm.private_key

    result = await probe_vm(host=host, port=port, user=ssh_user, private_key=private_key)

    # On a saved provider, persist the probe result so the list view can show
    # the GPU summary without re-running SSH on every page load.
    if req.provider_id and result.ok:
        row = await session.get(Provider, req.provider_id)
        if row is not None:
            cfg = dict(row.config or {})
            cfg["gpus"] = result.gpus
            cfg["gpu_count"] = result.gpu_count
            row.config = cfg
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(row, "config")
            await session.commit()

    return TestProviderResponse(
        ok=result.ok,
        message=result.message,
        gpus=result.gpus,
        gpu_count=result.gpu_count,
    )


@router.get("/{provider_id}/availability", response_model=AvailabilityResponse)
async def provider_availability(
    provider_id: str,
    user: User = Depends(current_user),  # noqa: ARG001 — auth-only; open to all
    session: AsyncSession = Depends(get_session),
):
    """Live SSH probe — returns per-GPU memory + utilisation. Used by the
    benchmark form to surface availability the same way RunPod's API check
    does for cloud runs. Open to all authenticated users so non-admins can
    still see whether a provider has free capacity before picking it."""
    row = await session.get(Provider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")
    if row.kind != "vm":
        raise HTTPException(status_code=400, detail="availability check only supported for kind=vm")
    cfg = row.config or {}
    enc = cfg.get("private_key_enc")
    if not enc:
        raise HTTPException(status_code=500, detail="provider missing stored key")
    private_key = crypto.decrypt(enc)
    result = await availability_vm(
        host=cfg.get("host", ""),
        port=int(cfg.get("port") or VM_DEFAULT_PORT),
        user=cfg.get("user", "root"),
        private_key=private_key,
    )
    return AvailabilityResponse(
        ok=result.ok,
        message=result.message,
        gpus=[GpuLiveInfo(
            index=g.index, name=g.name,
            mem_free_mib=g.mem_free_mib, mem_total_mib=g.mem_total_mib,
            util_pct=g.util_pct,
        ) for g in result.gpus],
        checked_at=result.checked_at,
    )
