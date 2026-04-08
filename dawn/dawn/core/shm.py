"""/dev/shm semaphore handling for CRIU link-remap.

Background:
    vLLM (via PyTorch/CUDA) creates POSIX named semaphores in /dev/shm/sem.<rand>.
    CRIU dumps these as 'unlinked file' link_remap entries. On restore, CRIU
    tries to recreate the hardlink from /dev/shm/link_remap.N -> /dev/shm/sem.X.
    If the original semaphore file is gone (because the original process is
    dead), the restore fails with:

        Can't link dev/shm/link_remap.N -> dev/shm/sem.X: No such file or directory

    This module pre-creates empty placeholder files for any sem.* names that
    CRIU expects, so the link operation succeeds.
"""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SHM_DIR = Path("/dev/shm")


def list_existing_sem_files() -> list[Path]:
    """Return all sem.* files currently in /dev/shm."""
    if not SHM_DIR.exists():
        return []
    return sorted(SHM_DIR.glob("sem.*"))


def expected_sem_names_from_dump(checkpoint_dir: str | Path) -> set[str]:
    """Scan CRIU dump files for references to /dev/shm/sem.* names.

    Returns the set of bare sem filenames (e.g. {'sem.WyyteS', 'sem.mp-y_49adx8'}).
    """
    ckpt = Path(checkpoint_dir)
    if not ckpt.exists():
        return set()

    pattern = re.compile(rb"sem\.[A-Za-z0-9_-]+")
    found: set[str] = set()
    for img in ckpt.glob("*.img"):
        try:
            data = img.read_bytes()
        except OSError:
            continue
        for match in pattern.findall(data):
            found.add(match.decode("ascii", errors="ignore"))
    return found


def precreate_sem_placeholders(checkpoint_dir: str | Path) -> list[str]:
    """Create empty /dev/shm/sem.* files referenced by the checkpoint.

    Returns the list of paths that were created (or already existed).
    """
    SHM_DIR.mkdir(parents=True, exist_ok=True)

    expected = expected_sem_names_from_dump(checkpoint_dir)
    created: list[str] = []

    for name in expected:
        path = SHM_DIR / name
        if not path.exists():
            try:
                path.touch(mode=0o600, exist_ok=True)
                # POSIX semaphores have a fixed 32-byte header
                with open(path, "wb") as f:
                    f.write(b"\x00" * 32)
                logger.debug("created semaphore placeholder: %s", path)
            except OSError as e:
                logger.warning("failed to create %s: %s", path, e)
                continue
        created.append(str(path))

    return created
