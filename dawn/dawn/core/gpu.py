"""GPU state inspection via nvidia-smi and cuda-checkpoint."""

import logging
from typing import Optional

from dawn.utils.shell import run, run_quiet, ShellError

logger = logging.getLogger(__name__)

CUDA_CHECKPOINT_BIN = "cuda-checkpoint"


def has_nvidia_smi() -> bool:
    return run_quiet(["nvidia-smi", "--version"])


def has_cuda_checkpoint() -> bool:
    return run_quiet([CUDA_CHECKPOINT_BIN, "--help"])


def driver_version() -> Optional[str]:
    """Return the NVIDIA driver version string, or None if nvidia-smi is missing."""
    try:
        out = run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]).stdout
        return out.strip().splitlines()[0] if out.strip() else None
    except (ShellError, FileNotFoundError):
        return None


def vram_used_mb() -> int:
    """Total VRAM used across all GPUs in MiB."""
    try:
        out = run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]
        ).stdout
        return sum(int(line.strip()) for line in out.splitlines() if line.strip())
    except (ShellError, FileNotFoundError):
        return 0


def find_cuda_pids() -> list[int]:
    """Return all PIDs that have an active CUDA context."""
    try:
        out = run(
            ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"]
        ).stdout
        return [int(line.strip()) for line in out.splitlines() if line.strip()]
    except (ShellError, FileNotFoundError):
        return []


def find_cuda_pid() -> Optional[int]:
    """Return the first CUDA PID, or None.

    For vLLM, this is typically the EngineCore subprocess (not the API server PID).
    """
    pids = find_cuda_pids()
    return pids[0] if pids else None


def get_state(pid: int) -> str:
    """Return cuda-checkpoint state for a PID.

    Possible values: 'running', 'locked', 'checkpointed', 'failed', 'unknown'.
    """
    try:
        out = run([CUDA_CHECKPOINT_BIN, "--get-state", "--pid", str(pid)]).stdout
        state = out.strip().splitlines()[0] if out.strip() else "unknown"
        return state
    except (ShellError, FileNotFoundError):
        return "unknown"


def lock(pid: int) -> None:
    """cuda-checkpoint --action lock --pid <pid>"""
    run([CUDA_CHECKPOINT_BIN, "--action", "lock", "--pid", str(pid)])


def checkpoint(pid: int) -> None:
    """cuda-checkpoint --action checkpoint --pid <pid> (VRAM -> host RAM)."""
    run([CUDA_CHECKPOINT_BIN, "--action", "checkpoint", "--pid", str(pid)])


def restore(pid: int) -> None:
    """cuda-checkpoint --action restore --pid <pid> (host RAM -> VRAM)."""
    run([CUDA_CHECKPOINT_BIN, "--action", "restore", "--pid", str(pid)])


def unlock(pid: int) -> None:
    """cuda-checkpoint --action unlock --pid <pid>"""
    run([CUDA_CHECKPOINT_BIN, "--action", "unlock", "--pid", str(pid)])
