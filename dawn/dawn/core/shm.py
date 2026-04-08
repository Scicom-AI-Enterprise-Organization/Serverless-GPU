"""/dev/shm semaphore handling for CRIU link-remap.

Background:
    vLLM (via PyTorch/CUDA) creates POSIX named semaphores in /dev/shm/sem.<rand>.
    CRIU dumps these as 'unlinked file' link_remap entries. During dump, CRIU
    creates hardlinks at /dev/shm/link_remap.N pointing to the original semaphore
    inodes. On restore, CRIU re-links them back: link_remap.N -> sem.X.

    The problem: those /dev/shm files are tmpfs and get auto-cleaned by systemd
    when no process holds a reference. So between dump (process killed) and
    restore (new process), they vanish — and restore fails with:

        Can't link dev/shm/link_remap.N -> dev/shm/sem.X: No such file or directory

    Fix: dawn snapshots /dev/shm/link_remap.* and /dev/shm/sem.* into a
    `_dawn_shm/` subdirectory inside the checkpoint after a successful dump.
    Before restore, dawn copies them back to /dev/shm so CRIU finds them.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

SHM_DIR = Path("/dev/shm")
SAVED_SUBDIR = "_dawn_shm"


def list_link_remap_files() -> list[Path]:
    """Return all link_remap.* files currently in /dev/shm."""
    if not SHM_DIR.exists():
        return []
    return sorted(SHM_DIR.glob("link_remap.*"))


def list_sem_files() -> list[Path]:
    """Return all sem.* files currently in /dev/shm."""
    if not SHM_DIR.exists():
        return []
    return sorted(SHM_DIR.glob("sem.*"))


def snapshot_shm_into_checkpoint(checkpoint_dir: str | Path) -> int:
    """Copy /dev/shm/{link_remap.*,sem.*} into <checkpoint_dir>/_dawn_shm/.

    Call this immediately after a successful CRIU dump to preserve the
    link_remap hardlinks before tmpfs cleans them up.

    Returns the number of files copied.
    """
    ckpt = Path(checkpoint_dir)
    saved = ckpt / SAVED_SUBDIR
    saved.mkdir(parents=True, exist_ok=True)

    files = list_link_remap_files() + list_sem_files()
    count = 0
    for src in files:
        if not src.is_file():
            continue
        try:
            shutil.copy2(src, saved / src.name)
            count += 1
        except OSError as e:
            logger.warning("failed to snapshot %s: %s", src, e)

    if count:
        logger.debug("snapshotted %d /dev/shm files into %s", count, saved)
    return count


def restore_shm_from_checkpoint(checkpoint_dir: str | Path) -> int:
    """Copy <checkpoint_dir>/_dawn_shm/* back into /dev/shm.

    Call this immediately before `criu restore` so the link_remap files
    CRIU expects are in place.

    Returns the number of files restored.
    """
    ckpt = Path(checkpoint_dir)
    saved = ckpt / SAVED_SUBDIR

    if not saved.is_dir():
        return 0

    SHM_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in saved.iterdir():
        if not src.is_file():
            continue
        dst = SHM_DIR / src.name
        try:
            shutil.copy2(src, dst)
            count += 1
        except OSError as e:
            logger.warning("failed to restore %s: %s", src, e)

    if count:
        logger.debug("restored %d /dev/shm files from %s", count, saved)
    return count
