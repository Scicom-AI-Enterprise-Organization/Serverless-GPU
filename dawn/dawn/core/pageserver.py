"""CRIU page-server mode for streaming pages over network.

The page-server runs on the destination, listens on a TCP port, and writes
incoming pages directly to its --images-dir. The source then dumps with
--page-server --address <host>:<port> so pages skip the source disk.

This is most useful for cross-node migration: source dumps to network, dest
receives directly into tmpfs.
"""

import logging
import time
from pathlib import Path
from typing import Optional

from dawn.utils.shell import run, ShellError

logger = logging.getLogger(__name__)

CRIU_BIN = "criu"
DEFAULT_PLUGIN_DIR = "/usr/local/lib/"


class PageServer:
    """A running CRIU page-server. Started in daemon mode."""

    def __init__(self, output_dir: str | Path, port: int, pid: int):
        self.output_dir = Path(output_dir)
        self.port = port
        self.pid = pid

    def is_alive(self) -> bool:
        try:
            import os
            os.kill(self.pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def wait(self, timeout: float = 600.0) -> bool:
        """Block until the page-server exits (i.e. transfer complete).

        Returns True if it exited cleanly, False on timeout.
        """
        deadline = time.monotonic() + timeout
        while self.is_alive():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.5)
        return True


def start(
    output_dir: str | Path,
    *,
    port: int = 27,
    plugin_dir: str = DEFAULT_PLUGIN_DIR,
    pidfile: Optional[str | Path] = None,
) -> PageServer:
    """Start a CRIU page-server in daemon mode.

    Args:
        output_dir: where pages will be written (use /dev/shm for speed)
        port: TCP port to listen on
        plugin_dir: where cuda_plugin.so lives
        pidfile: optional path to write the daemon PID
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    pidfile_path = Path(pidfile) if pidfile else out / ".pageserver.pid"

    cmd = [
        CRIU_BIN,
        "page-server",
        "--images-dir", str(out),
        "--port", str(port),
        "-L", plugin_dir,
        "-d",  # daemonize
        "--pidfile", str(pidfile_path),
        "-v0",
    ]
    run(cmd)

    # Read the daemon's PID
    for _ in range(20):  # up to 1s
        if pidfile_path.exists():
            try:
                pid = int(pidfile_path.read_text().strip())
                return PageServer(output_dir=out, port=port, pid=pid)
            except (ValueError, OSError):
                pass
        time.sleep(0.05)

    raise RuntimeError(f"page-server started but PID file not found at {pidfile_path}")
