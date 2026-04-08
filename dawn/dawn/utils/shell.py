"""Subprocess wrapper with logging and consistent error handling."""

import logging
import shlex
import subprocess
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


class ShellError(RuntimeError):
    """Raised when a shell command exits non-zero."""

    def __init__(self, cmd: Sequence[str], returncode: int, stdout: str, stderr: str):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"command failed (exit {returncode}): {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"stderr: {stderr.strip()}"
        )


def run(
    cmd: Sequence[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: Optional[float] = None,
    input: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run a command, capturing stdout/stderr.

    Raises ShellError on non-zero exit if check=True.
    """
    logger.debug("running: %s", " ".join(shlex.quote(c) for c in cmd))
    result = subprocess.run(
        list(cmd),
        capture_output=capture,
        text=True,
        timeout=timeout,
        input=input,
    )
    if check and result.returncode != 0:
        raise ShellError(cmd, result.returncode, result.stdout or "", result.stderr or "")
    return result


def run_quiet(cmd: Sequence[str], **kwargs) -> bool:
    """Run a command and return True if it succeeded, False otherwise.

    Never raises. Useful for capability checks.
    """
    try:
        run(cmd, check=True, **kwargs)
        return True
    except (ShellError, FileNotFoundError, subprocess.TimeoutExpired):
        return False
