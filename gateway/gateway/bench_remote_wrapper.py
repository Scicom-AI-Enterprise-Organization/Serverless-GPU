"""Subprocess entrypoint used by VM benchmark runs.

Installs the pyremote reconnect-per-command shim, then dispatches to
benchmaq's CLI. Invoke as:

    python -m gateway.bench_remote_wrapper <subcmd> <args...>

sys.argv layout matches benchmaq's own entrypoint, so its argparse parses
correctly.
"""
from __future__ import annotations

import sys


def _main() -> int:
    from .pyremote_shim import install
    install()
    from benchmaq.cli import main  # type: ignore
    return int(main() or 0)


if __name__ == "__main__":
    sys.exit(_main())
