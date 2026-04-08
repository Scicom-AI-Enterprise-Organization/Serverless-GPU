"""Installs CRIU 4.0 + cuda-checkpoint and configures the kernel.

Equivalent of the bash setup we did manually in session 1/2. Idempotent —
safe to run repeatedly.
"""

import logging
import os
import shutil
from pathlib import Path

from dawn.utils.shell import run, run_quiet, ShellError

logger = logging.getLogger(__name__)

CRIU_VERSION = "v4.0"
CUDA_CHECKPOINT_REPO = "https://github.com/NVIDIA/cuda-checkpoint.git"
CRIU_REPO = "https://github.com/checkpoint-restore/criu.git"

APT_DEPS = [
    "build-essential",
    "git",
    "pkg-config",
    "libprotobuf-dev",
    "libprotobuf-c-dev",
    "protobuf-c-compiler",
    "protobuf-compiler",
    "python3-protobuf",
    "libnl-3-dev",
    "libcap-dev",
    "libaio-dev",
    "libnet1-dev",
    "uuid-dev",
    "lz4",
]


def install_all(
    *,
    skip_apt: bool = False,
    skip_criu: bool = False,
    skip_cuda_checkpoint: bool = False,
    skip_sysctl: bool = False,
) -> None:
    """One-shot install of everything dawn needs. Requires sudo/root."""
    if os.geteuid() != 0:
        raise PermissionError("dawn setup must be run as root (use sudo)")

    if not skip_apt:
        _install_apt_deps()

    if not skip_cuda_checkpoint:
        _install_cuda_checkpoint()

    if not skip_criu:
        _install_criu()

    if not skip_sysctl:
        _configure_sysctls()


def _install_apt_deps() -> None:
    logger.info("installing apt dependencies")
    run(["apt-get", "update", "-qq"], check=False)
    run(["apt-get", "install", "-y", "-qq", *APT_DEPS])


def _install_cuda_checkpoint() -> None:
    """Clone NVIDIA/cuda-checkpoint and copy the prebuilt binary into PATH."""
    if shutil.which("cuda-checkpoint"):
        logger.info("cuda-checkpoint already installed, skipping")
        return

    logger.info("installing cuda-checkpoint")
    work = Path("/tmp/dawn-cuda-checkpoint")
    if work.exists():
        shutil.rmtree(work)
    run(["git", "clone", "--depth", "1", CUDA_CHECKPOINT_REPO, str(work)])

    binary = work / "bin" / "x86_64_Linux" / "cuda-checkpoint"
    if not binary.exists():
        raise FileNotFoundError(f"prebuilt cuda-checkpoint not found at {binary}")
    shutil.copy(binary, "/usr/local/bin/cuda-checkpoint")
    os.chmod("/usr/local/bin/cuda-checkpoint", 0o755)


def _install_criu() -> None:
    """Build CRIU 4.0 from source and install criu + cuda_plugin.so."""
    if shutil.which("criu") and Path("/usr/local/lib/cuda_plugin.so").exists():
        logger.info("criu + cuda plugin already installed, skipping")
        return

    logger.info("building CRIU %s from source", CRIU_VERSION)
    work = Path("/tmp/dawn-criu")
    if work.exists():
        shutil.rmtree(work)
    run(["git", "clone", "--depth", "1", "--branch", CRIU_VERSION, CRIU_REPO, str(work)])

    nproc = run(["nproc"]).stdout.strip()
    run(["make", "-j", nproc], cwd=str(work))

    # Move binaries into place
    shutil.copy(work / "criu" / "criu", "/usr/local/bin/criu")
    os.chmod("/usr/local/bin/criu", 0o755)

    plugin = work / "plugins" / "cuda" / "cuda_plugin.so"
    if not plugin.exists():
        raise FileNotFoundError(f"cuda_plugin.so not built at {plugin}")
    shutil.copy(plugin, "/usr/local/lib/cuda_plugin.so")


def _configure_sysctls() -> None:
    """Disable io_uring (CRIU v4 cannot handle it)."""
    logger.info("configuring sysctls")
    try:
        run(["sysctl", "kernel.io_uring_disabled=2"])
    except ShellError as e:
        logger.warning("could not set kernel.io_uring_disabled=2: %s", e.stderr)
