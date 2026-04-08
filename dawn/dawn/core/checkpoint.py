"""The Checkpoint class — dump and restore primitives.

This module wraps `criu dump` and `criu restore` with the exact flag set we
validated in session 1/2:

    --shell-job
    --tcp-established
    --file-locks
    --ext-unix-sk
    --link-remap
    --timeout 300
    -L /usr/local/lib/      (path to cuda_plugin.so)

The CRIU CUDA plugin handles cuda-checkpoint internally during dump/restore.
We do NOT call cuda-checkpoint manually before dump — that double-checkpoints
and fails.
"""

import logging
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Optional

from dawn.core import compression, gpu, pageserver, shm
from dawn.models.result import CheckpointResult, RestoreResult, CompressionResult
from dawn.utils.shell import run, ShellError
from dawn.utils.timing import Timer

logger = logging.getLogger(__name__)

CRIU_BIN = "criu"
DEFAULT_PLUGIN_DIR = "/usr/local/lib/"

# The validated flag set from session 1/2
_BASE_FLAGS = [
    "--shell-job",
    "--tcp-established",
    "--file-locks",
    "--ext-unix-sk",
    "--link-remap",
]


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class Checkpoint:
    """Static class wrapping CRIU dump/restore + cuda-checkpoint."""

    @staticmethod
    def dump(
        pid: int,
        output_dir: str | Path,
        *,
        timeout: int = 300,
        plugin_dir: str = DEFAULT_PLUGIN_DIR,
        page_server: Optional[str] = None,
        compress: bool = False,
        compress_path: Optional[str | Path] = None,
        verbose: int = 0,
    ) -> CheckpointResult:
        """Checkpoint a running process tree.

        Args:
            pid: the root PID to dump (e.g. the vLLM main process)
            output_dir: directory where CRIU images will be written
            timeout: CRIU timeout in seconds (300 = our tested default)
            plugin_dir: path containing cuda_plugin.so
            page_server: optional "host:port" — stream pages to a remote server
            compress: if True, lz4-compress the dump after it completes
            compress_path: where to write the .tar.lz4 (default: <output_dir>.tar.lz4)
            verbose: CRIU -v level (0-4)

        Returns:
            CheckpointResult with size, file count, duration, etc.

        Raises:
            ShellError if CRIU dump fails.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Capture GPU state before
        cuda_pid = gpu.find_cuda_pid()
        gpu_state_before = gpu.get_state(cuda_pid) if cuda_pid else "unknown"

        cmd = [
            CRIU_BIN,
            "dump",
            "--tree", str(pid),
            "--images-dir", str(out),
            *_BASE_FLAGS,
            "--timeout", str(timeout),
            "-L", plugin_dir,
            f"-v{verbose}",
        ]

        if page_server:
            try:
                host, port = page_server.split(":")
            except ValueError as e:
                raise ValueError(f"page_server must be 'host:port', got {page_server!r}") from e
            cmd.extend(["--page-server", "--address", host, "--port", port])

        with Timer() as t:
            try:
                run(cmd)
            except ShellError as e:
                logger.error("CRIU dump failed: %s", e.stderr)
                raise

        # Snapshot /dev/shm link_remap+sem files BEFORE tmpfs cleans them up
        # (orphan POSIX semaphores get auto-removed once their owning process dies)
        snapshotted = shm.snapshot_shm_into_checkpoint(out)
        if snapshotted:
            logger.debug("snapshotted %d /dev/shm files into checkpoint", snapshotted)

        # Re-check the GPU state after
        gpu_state_after = gpu.get_state(cuda_pid) if cuda_pid else "killed"

        result = CheckpointResult(
            path=str(out),
            size_bytes=_dir_size(out),
            file_count=sum(1 for _ in out.iterdir() if _.is_file()),
            duration_ms=t.ms,
            pid=pid,
            gpu_state_before=gpu_state_before,
            gpu_state_after=gpu_state_after,
            metadata_dir=str(out) if page_server else None,
            pages_dir=None,  # caller knows where the page-server wrote them
        )

        if compress:
            archive_path = Path(compress_path) if compress_path else out.with_suffix(".tar.lz4")
            comp = compression.compress(out, archive_path)
            logger.info(
                "compressed %s -> %s (%.1f%% of original)",
                out, archive_path, comp.ratio * 100,
            )

        return result

    @staticmethod
    def restore(
        checkpoint_dir: str | Path,
        *,
        timeout: int = 300,
        plugin_dir: str = DEFAULT_PLUGIN_DIR,
        pre_warm: bool = False,
        wait_port: Optional[int] = None,
        wait_timeout: float = 60.0,
        verify_request: bool = False,
        verbose: int = 0,
    ) -> RestoreResult:
        """Restore a checkpoint dumped by Checkpoint.dump().

        Args:
            checkpoint_dir: directory containing CRIU images. Can also be a
                .tar.lz4 archive — it will be auto-decompressed first.
            timeout: CRIU timeout in seconds
            plugin_dir: path containing cuda_plugin.so
            pre_warm: read all dump files into the OS page cache before restore
                (32% faster on slow disks per our session 2 measurements)
            wait_port: if set, block until this TCP port is reachable on localhost
            wait_timeout: how long to wait for the port (seconds)
            verify_request: if wait_port is set, hit /v1/models on it after the
                port is up to confirm vLLM is actually serving
            verbose: CRIU -v level

        Returns:
            RestoreResult with duration, restored PID, GPU state, VRAM, etc.

        Raises:
            ShellError if CRIU restore fails.
        """
        ckpt = Path(checkpoint_dir)

        # Auto-decompress if given an archive
        if ckpt.is_file() and ckpt.suffix in (".lz4", ".tar.lz4") or str(ckpt).endswith(".tar.lz4"):
            decomp_dir = ckpt.parent / ckpt.name.replace(".tar.lz4", "").replace(".lz4", "")
            logger.info("auto-decompressing %s -> %s", ckpt, decomp_dir)
            compression.decompress(ckpt, decomp_dir)
            ckpt = decomp_dir

        if not ckpt.is_dir():
            raise FileNotFoundError(f"checkpoint dir not found: {ckpt}")

        # Restore /dev/shm link_remap and sem files that we snapshotted at dump time.
        # CRIU needs these hardlinks to exist on disk so it can re-link them into
        # the restored process. Without this step, restore fails with:
        #   Can't link dev/shm/link_remap.N -> dev/shm/sem.X: No such file or directory
        restored_shm = shm.restore_shm_from_checkpoint(ckpt)
        if restored_shm:
            logger.debug("restored %d /dev/shm files from checkpoint", restored_shm)

        # Optionally pre-warm page cache
        if pre_warm:
            logger.info("pre-warming page cache from %s", ckpt)
            with Timer() as pwt:
                run(["bash", "-c", f"cat {ckpt}/*.img > /dev/null"])
            logger.info("pre-warm took %dms", pwt.ms)

        cmd = [
            CRIU_BIN,
            "restore",
            "--images-dir", str(ckpt),
            *_BASE_FLAGS,
            "--timeout", str(timeout),
            "-L", plugin_dir,
            "--restore-detached",
            f"-v{verbose}",
        ]

        with Timer() as t:
            try:
                run(cmd)
            except ShellError as e:
                logger.error("CRIU restore failed: %s", e.stderr)
                raise

        # Allow a moment for the restored process to settle
        time.sleep(1)

        cuda_pid = gpu.find_cuda_pid()
        gpu_state = gpu.get_state(cuda_pid) if cuda_pid else "unknown"
        vram_mb = gpu.vram_used_mb()

        port_up: Optional[bool] = None
        verified: Optional[bool] = None

        if wait_port is not None:
            port_up = _wait_for_port(wait_port, wait_timeout)
            if port_up and verify_request:
                verified = _verify_models_endpoint(wait_port)

        return RestoreResult(
            duration_ms=t.ms,
            pid=cuda_pid or 0,
            gpu_state=gpu_state,
            vram_mb=vram_mb,
            port_up=port_up,
            verified=verified,
        )

    @staticmethod
    def compress(
        checkpoint_dir: str | Path,
        output_path: str | Path,
        *,
        level: int = 1,
    ) -> CompressionResult:
        """Compress a checkpoint directory to a .tar.lz4 archive."""
        return compression.compress(checkpoint_dir, output_path, level=level)

    @staticmethod
    def decompress(
        archive_path: str | Path,
        output_dir: str | Path,
    ) -> CompressionResult:
        """Decompress a .tar.lz4 archive into a directory."""
        return compression.decompress(archive_path, output_dir)

    @staticmethod
    def start_page_server(
        output_dir: str | Path,
        *,
        port: int = 27,
        plugin_dir: str = DEFAULT_PLUGIN_DIR,
    ) -> pageserver.PageServer:
        """Start a CRIU page-server in daemon mode for receiving streamed pages."""
        return pageserver.start(output_dir, port=port, plugin_dir=plugin_dir)


def _wait_for_port(port: int, timeout: float) -> bool:
    """Block until tcp://localhost:<port> accepts connections, or timeout."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.5)
    return False


def _verify_models_endpoint(port: int) -> bool:
    """Hit /v1/models on the local port and return True if it returns valid JSON."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("verify_request failed: %s", e)
        return False
