"""Process discovery and inspection."""

import logging
import os
import signal
from typing import Optional

import psutil

logger = logging.getLogger(__name__)


def find_pid_by_cmdline(needle: str) -> Optional[int]:
    """Return the first PID whose cmdline contains needle."""
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
            if needle in cmdline:
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def find_vllm_pid() -> Optional[int]:
    """Find the main vLLM serve process (NOT the EngineCore subprocess).

    The main process is the one that runs `vllm serve ...`. The EngineCore is its
    child and is the one with the CUDA context.
    """
    return find_pid_by_cmdline("vllm serve")


def find_vllm_engine_pid() -> Optional[int]:
    """Find the vLLM EngineCore subprocess (the one with CUDA context)."""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if "VLLM::EngineCor" in (proc.info["name"] or ""):
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def is_alive(pid: int) -> bool:
    """Check if a PID is still alive."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def kill_tree(pid: int, sig: int = signal.SIGKILL) -> None:
    """Kill a process and all of its descendants."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    children = parent.children(recursive=True)
    for child in children:
        try:
            child.send_signal(sig)
        except psutil.NoSuchProcess:
            pass
    try:
        parent.send_signal(sig)
    except psutil.NoSuchProcess:
        pass


def process_tree_pids(pid: int) -> list[int]:
    """Return [pid, *descendant_pids]."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []
    return [pid] + [p.pid for p in parent.children(recursive=True)]
