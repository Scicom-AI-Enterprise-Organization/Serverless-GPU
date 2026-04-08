"""lz4 compress/decompress for checkpoint directories.

We use a simple tar+lz4 approach: bundle the directory into a single .tar.lz4
archive. This is what we measured in session 2: 18 GB -> 2.1 GB (~88% reduction)
for vLLM dumps.
"""

import logging
import shutil
from pathlib import Path

from dawn.models.result import CompressionResult
from dawn.utils.shell import run, run_quiet
from dawn.utils.timing import Timer

logger = logging.getLogger(__name__)

LZ4_BIN = "lz4"
TAR_BIN = "tar"


def has_lz4() -> bool:
    return run_quiet([LZ4_BIN, "--version"])


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def compress(
    checkpoint_dir: str | Path,
    output_path: str | Path,
    *,
    level: int = 1,
) -> CompressionResult:
    """Compress a checkpoint directory to a .tar.lz4 archive.

    Args:
        checkpoint_dir: directory created by Checkpoint.dump()
        output_path: destination archive path (typically ends in .tar.lz4)
        level: lz4 compression level (1=fastest, 9=smallest). Default 1.
    """
    src = Path(checkpoint_dir)
    dst = Path(output_path)

    if not src.is_dir():
        raise FileNotFoundError(f"checkpoint dir not found: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    original = _dir_size(src)

    with Timer() as t:
        # tar -c -C parent dirname | lz4 -<level> -q > output.tar.lz4
        # Streaming: no temp file needed
        cmd = (
            f"{TAR_BIN} -c -C {src.parent} {src.name} | "
            f"{LZ4_BIN} -{level} -q -f - {dst}"
        )
        run(["bash", "-c", cmd])

    compressed = dst.stat().st_size
    return CompressionResult(
        path=str(dst),
        original_size=original,
        compressed_size=compressed,
        ratio=compressed / original if original else 0.0,
        duration_ms=t.ms,
    )


def decompress(
    archive_path: str | Path,
    output_dir: str | Path,
) -> CompressionResult:
    """Decompress a .tar.lz4 archive into output_dir.

    The archive is expected to contain a single top-level directory (the original
    checkpoint name). After extraction, the contents are moved up one level if
    output_dir doesn't already match.
    """
    src = Path(archive_path)
    dst = Path(output_dir)

    if not src.exists():
        raise FileNotFoundError(f"archive not found: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir()

    compressed = src.stat().st_size

    with Timer() as t:
        # lz4 -d -q archive | tar -x -C output_dir --strip-components=1
        cmd = (
            f"{LZ4_BIN} -d -q -f {src} - | "
            f"{TAR_BIN} -x -C {dst} --strip-components=1"
        )
        run(["bash", "-c", cmd])

    original = _dir_size(dst)
    return CompressionResult(
        path=str(dst),
        original_size=original,
        compressed_size=compressed,
        ratio=compressed / original if original else 0.0,
        duration_ms=t.ms,
    )
