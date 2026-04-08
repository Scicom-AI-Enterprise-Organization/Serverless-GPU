"""System readiness checks for dawn.

These checks confirm that everything required by `Checkpoint.dump/restore` is
in place: kernel features, capabilities, binaries, GPU driver. Run via
`dawn doctor` or `preflight.check()` from a daemon at startup.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from dawn.core import gpu
from dawn.models.result import PreflightIssue
from dawn.utils.shell import run, run_quiet, ShellError

logger = logging.getLogger(__name__)

CRIU_BIN = "criu"
CUDA_PLUGIN_PATH = "/usr/local/lib/cuda_plugin.so"
IO_URING_DISABLED_PATH = "/proc/sys/kernel/io_uring_disabled"


def check() -> list[PreflightIssue]:
    """Run all preflight checks. Returns a list of issues (empty = ready)."""
    issues: list[PreflightIssue] = []

    issues.extend(_check_root())
    issues.extend(_check_arch())
    issues.extend(_check_capabilities())
    issues.extend(_check_io_uring())
    issues.extend(_check_criu())
    issues.extend(_check_cuda_checkpoint())
    issues.extend(_check_cuda_plugin())
    issues.extend(_check_nvidia_driver())

    return issues


def fix(*, disable_io_uring: bool = True) -> list[str]:
    """Apply auto-fixable issues. Returns the list of actions taken."""
    actions: list[str] = []

    if disable_io_uring:
        if _read_io_uring_state() != "2":
            try:
                run(["sysctl", "kernel.io_uring_disabled=2"])
                actions.append("set kernel.io_uring_disabled=2")
            except ShellError as e:
                logger.warning("could not disable io_uring: %s", e.stderr)

    return actions


# ─── individual checks ──────────────────────────────────────────────────


def _check_root() -> list[PreflightIssue]:
    if os.geteuid() != 0:
        return [
            PreflightIssue(
                severity="warning",
                component="caps",
                message="Not running as root. CRIU requires root for dump/restore.",
                fix_command="sudo dawn ...",
            )
        ]
    return []


def _check_arch() -> list[PreflightIssue]:
    import platform
    if platform.machine() != "x86_64":
        return [
            PreflightIssue(
                severity="error",
                component="kernel",
                message=f"cuda-checkpoint only supports x86_64, found {platform.machine()}",
            )
        ]
    return []


def _check_capabilities() -> list[PreflightIssue]:
    """Check that the process has the capabilities CRIU needs."""
    required = ["cap_sys_ptrace", "cap_checkpoint_restore", "cap_sys_admin"]
    issues: list[PreflightIssue] = []

    try:
        out = run(["capsh", "--print"], check=False).stdout
        bounding = ""
        for line in out.splitlines():
            if line.startswith("Bounding set"):
                bounding = line.lower()
                break

        for cap in required:
            if cap not in bounding:
                issues.append(
                    PreflightIssue(
                        severity="error",
                        component="caps",
                        message=f"missing capability {cap.upper()} (CRIU will fail)",
                        fix_command="use a bare-metal provider, not RunPod-style containers",
                    )
                )
    except (ShellError, FileNotFoundError):
        # capsh not installed; we can't check, but don't error out
        pass

    return issues


def _read_io_uring_state() -> Optional[str]:
    try:
        return Path(IO_URING_DISABLED_PATH).read_text().strip()
    except OSError:
        return None


def _check_io_uring() -> list[PreflightIssue]:
    state = _read_io_uring_state()
    if state is None:
        # kernel doesn't even have the toggle; might be old, but unlikely a problem
        return []
    if state != "2":
        return [
            PreflightIssue(
                severity="error",
                component="kernel",
                message="io_uring is enabled — CRIU v4.0 cannot dump processes that use io_uring (vLLM does)",
                fix_command="sudo sysctl kernel.io_uring_disabled=2",
            )
        ]
    return []


def _check_criu() -> list[PreflightIssue]:
    if not run_quiet([CRIU_BIN, "--version"]):
        return [
            PreflightIssue(
                severity="error",
                component="criu",
                message="criu binary not found in PATH",
                fix_command="dawn setup",
            )
        ]
    return []


def _check_cuda_checkpoint() -> list[PreflightIssue]:
    if not gpu.has_cuda_checkpoint():
        return [
            PreflightIssue(
                severity="error",
                component="cuda",
                message="cuda-checkpoint binary not found",
                fix_command="dawn setup",
            )
        ]
    return []


def _check_cuda_plugin() -> list[PreflightIssue]:
    if not Path(CUDA_PLUGIN_PATH).exists():
        return [
            PreflightIssue(
                severity="error",
                component="criu",
                message=f"CRIU CUDA plugin not found at {CUDA_PLUGIN_PATH}",
                fix_command="dawn setup",
            )
        ]
    return []


def _check_nvidia_driver() -> list[PreflightIssue]:
    if not gpu.has_nvidia_smi():
        return [
            PreflightIssue(
                severity="error",
                component="gpu",
                message="nvidia-smi not found — no NVIDIA driver?",
            )
        ]

    version = gpu.driver_version()
    if version is None:
        return [
            PreflightIssue(
                severity="warning",
                component="gpu",
                message="could not read NVIDIA driver version",
            )
        ]

    try:
        major = int(version.split(".")[0])
        if major < 550:
            return [
                PreflightIssue(
                    severity="error",
                    component="gpu",
                    message=f"NVIDIA driver {version} too old (need >= 550 for cuda-checkpoint)",
                )
            ]
    except (ValueError, IndexError):
        pass

    return []
