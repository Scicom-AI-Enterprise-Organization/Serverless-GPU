"""SSH probe for VM-type cloud providers.

Opens SSH to a bare-metal box and runs `nvidia-smi` to return the GPU
inventory. Paramiko is already a transitive dep via benchmaq, so we use it
in a worker thread rather than pulling in asyncssh.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass

import paramiko

logger = logging.getLogger("gateway.vm_probe")

CONNECT_TIMEOUT_S = 10
COMMAND_TIMEOUT_S = 15


@dataclass
class VmProbeResult:
    ok: bool
    message: str
    gpus: list[str]
    gpu_count: int


@dataclass
class GpuInfo:
    index: int
    name: str
    mem_free_mib: int
    mem_total_mib: int
    util_pct: int


@dataclass
class VmAvailabilityResult:
    ok: bool
    message: str
    gpus: list[GpuInfo]
    checked_at: float


def _load_pkey(private_key: str) -> paramiko.PKey:
    """Try OpenSSH key formats until one parses. Paramiko can't auto-detect."""
    buf = private_key.strip() + "\n"
    # Build the candidate list dynamically — paramiko 4.0 dropped DSSKey, so a
    # static tuple referencing it raises AttributeError at module-load time
    # before any key gets tried.
    candidates: list[type[paramiko.PKey]] = []
    for attr in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey"):
        cls = getattr(paramiko, attr, None)
        if cls is not None:
            candidates.append(cls)
    last_err: Exception | None = None
    for cls in candidates:
        try:
            return cls.from_private_key(io.StringIO(buf))
        except paramiko.SSHException as e:
            last_err = e
            continue
    raise RuntimeError(f"unsupported private key format: {last_err}")


def _probe_sync(host: str, port: int, user: str, private_key: str) -> VmProbeResult:
    try:
        pkey = _load_pkey(private_key)
    except Exception as e:
        return VmProbeResult(ok=False, message=f"key parse failed: {e}", gpus=[], gpu_count=0)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            pkey=pkey,
            timeout=CONNECT_TIMEOUT_S,
            banner_timeout=CONNECT_TIMEOUT_S,
            auth_timeout=CONNECT_TIMEOUT_S,
            look_for_keys=False,
            allow_agent=False,
        )
    except paramiko.AuthenticationException:
        return VmProbeResult(ok=False, message="authentication failed — check user + private key", gpus=[], gpu_count=0)
    except Exception as e:
        return VmProbeResult(ok=False, message=f"SSH connect failed: {e}", gpus=[], gpu_count=0)

    try:
        cmd = "nvidia-smi --query-gpu=name --format=csv,noheader"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=COMMAND_TIMEOUT_S)
        rc = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if rc != 0:
            return VmProbeResult(
                ok=False,
                message=f"nvidia-smi exited {rc}: {err or 'no GPU detected'}",
                gpus=[],
                gpu_count=0,
            )
        gpus = [line.strip() for line in out.splitlines() if line.strip()]
        if not gpus:
            return VmProbeResult(ok=False, message="nvidia-smi returned no GPUs", gpus=[], gpu_count=0)
        return VmProbeResult(
            ok=True,
            message=f"connected · {len(gpus)} GPU{'s' if len(gpus) != 1 else ''} detected",
            gpus=gpus,
            gpu_count=len(gpus),
        )
    finally:
        try:
            client.close()
        except Exception:
            pass


async def probe_vm(host: str, port: int, user: str, private_key: str) -> VmProbeResult:
    return await asyncio.to_thread(_probe_sync, host, port, user, private_key)


def _availability_sync(host: str, port: int, user: str, private_key: str) -> VmAvailabilityResult:
    """Like _probe_sync but returns per-GPU memory + utilisation so the UI can
    show a runpod-style availability badge for VM providers."""
    import time as _time
    try:
        pkey = _load_pkey(private_key)
    except Exception as e:
        return VmAvailabilityResult(ok=False, message=f"key parse failed: {e}", gpus=[], checked_at=_time.time())

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host, port=port, username=user, pkey=pkey,
            timeout=CONNECT_TIMEOUT_S, banner_timeout=CONNECT_TIMEOUT_S,
            auth_timeout=CONNECT_TIMEOUT_S, look_for_keys=False, allow_agent=False,
        )
    except paramiko.AuthenticationException:
        return VmAvailabilityResult(ok=False, message="authentication failed", gpus=[], checked_at=_time.time())
    except Exception as e:
        return VmAvailabilityResult(ok=False, message=f"SSH connect failed: {e}", gpus=[], checked_at=_time.time())

    try:
        cmd = "nvidia-smi --query-gpu=index,name,memory.free,memory.total,utilization.gpu --format=csv,noheader,nounits"
        _, stdout, stderr = client.exec_command(cmd, timeout=COMMAND_TIMEOUT_S)
        rc = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if rc != 0:
            return VmAvailabilityResult(
                ok=False,
                message=f"nvidia-smi exited {rc}: {err or 'no GPU detected'}",
                gpus=[], checked_at=_time.time(),
            )
        gpus: list[GpuInfo] = []
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                gpus.append(GpuInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    mem_free_mib=int(parts[2]),
                    mem_total_mib=int(parts[3]),
                    util_pct=int(parts[4]),
                ))
            except ValueError:
                continue
        if not gpus:
            return VmAvailabilityResult(ok=False, message="nvidia-smi returned no parseable GPUs", gpus=[], checked_at=_time.time())
        return VmAvailabilityResult(
            ok=True,
            message=f"{len(gpus)} GPU{'s' if len(gpus) != 1 else ''} reachable",
            gpus=gpus,
            checked_at=_time.time(),
        )
    finally:
        try:
            client.close()
        except Exception:
            pass


async def availability_vm(host: str, port: int, user: str, private_key: str) -> VmAvailabilityResult:
    return await asyncio.to_thread(_availability_sync, host, port, user, private_key)
