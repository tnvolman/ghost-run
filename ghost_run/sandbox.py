"""Create and tear down the throwaway ghost working copy.

Copies only. A hard link is the real file, so Ghost Mode refuses to start
rather than fall back to ``cp --link`` or ``os.link``. ``cp --reflink=auto``
is a copy-on-write clone (new inode); when that is unavailable the tree is
copied with ``shutil.copy2``.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple

from .ignore import load_ignore_dirs
from .ignore import DEFAULT_IGNORE_DIR_NAMES  # re-export for callers

__all__ = ["DEFAULT_IGNORE_DIR_NAMES", "create_ghost_copy", "discard_ghost_copy", "timed_create", "MAX_FILE_BYTES", "MAX_TOTAL_BYTES"]

# Skip individual files larger than this when copying (bytes).
MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MiB
# Soft cap on total copied bytes; stop adding more files past this.
MAX_TOTAL_BYTES = 256 * 1024 * 1024  # 256 MiB


def _ignore_names(dir_names: Optional[Iterable[str]] = None, real_root: Optional[Path] = None) -> Set[str]:
    names = load_ignore_dirs(real_root)
    if dir_names:
        names.update(dir_names)
    return names


_HARDLINK_STRATEGIES = {"hardlink", "hardlink-file"}
_MARKER_NAMES = {".ghost-run-marker", ".ghost-run-strategy"}


class GhostCopyError(RuntimeError):
    """Raised when Ghost Mode must not start because the copy is not independent."""


def _try_reflink(src: Path, dest: Path) -> bool:
    """``cp --reflink=auto -a`` when the filesystem supports CoW clones."""
    try:
        r = subprocess.run(
            ["cp", "-a", "--reflink=auto", str(src) + "/.", str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _prune_ignored(root: Path, ignore: Set[str]) -> None:
    """Remove ignored directories/files that a bulk cp may have brought along."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        for d in list(dirnames):
            if d in ignore:
                shutil.rmtree(Path(dirpath) / d, ignore_errors=True)
                dirnames.remove(d)
        for name in filenames:
            if name in ignore or name == ".ghost-run-marker":
                try:
                    (Path(dirpath) / name).unlink()
                except OSError:
                    pass


def _filtered_copy(
    real_root: Path,
    ghost_root: Path,
    ignore: Set[str],
    *,
    max_file_bytes: int,
    max_total_bytes: int,
) -> str:
    """Byte-copy the tree. Never calls ``os.link``."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(real_root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in ignore]
        rel_dir = Path(dirpath).relative_to(real_root)
        dest_dir = ghost_root / rel_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            if name in ignore:
                continue
            src = Path(dirpath) / name
            try:
                size = src.lstat().st_size
            except OSError:
                continue
            if size > max_file_bytes:
                continue
            if total + size > max_total_bytes:
                continue
            dest = dest_dir / name
            try:
                shutil.copy2(src, dest, follow_symlinks=False)
                total += size
            except OSError:
                continue
    return "copy"


def _has_copyable_file(
    real_root: Path,
    ignore: Set[str],
    max_file_bytes: int,
) -> bool:
    for dirpath, dirnames, filenames in os.walk(real_root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in ignore]
        for name in filenames:
            if name in ignore:
                continue
            try:
                size = (Path(dirpath) / name).lstat().st_size
            except OSError:
                continue
            if size <= max_file_bytes:
                return True
    return False


def _payload_file_count(root: Path) -> int:
    count = 0
    for dirpath, _, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            if name in _MARKER_NAMES:
                continue
            count += 1
    return count


def _shared_inode(real_root: Path, ghost_root: Path) -> Optional[str]:
    """Return a relative path if a ghost file is a hard link of the real file."""
    for dirpath, _, filenames in os.walk(ghost_root, followlinks=False):
        for name in filenames:
            if name in _MARKER_NAMES:
                continue
            ghost_file = Path(dirpath) / name
            try:
                gst = ghost_file.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(gst.st_mode) or not stat.S_ISREG(gst.st_mode):
                continue
            rel = ghost_file.relative_to(ghost_root)
            real_file = real_root / rel
            try:
                rst = real_file.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(rst.st_mode) or not stat.S_ISREG(rst.st_mode):
                continue
            if gst.st_dev == rst.st_dev and gst.st_ino == rst.st_ino:
                return rel.as_posix()
    return None


def _empty_ghost(ghost_root: Path) -> None:
    for child in list(ghost_root.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _refuse_hardlink(real_root: Path, ghost_root: Path) -> None:
    shared = _shared_inode(real_root, ghost_root)
    if shared is None:
        return
    shutil.rmtree(ghost_root, ignore_errors=True)
    raise GhostCopyError(
        "ghost-run: refusing to start Ghost Mode; the ghost copy hard-linked "
        f"{shared!r} to the real file. Copy only, or do not start."
    )


def create_ghost_copy(
    real_root: Path,
    *,
    ignore_dir_names: Optional[Iterable[str]] = None,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    strategy: Optional[str] = None,
) -> Path:
    """Copy *real_root* into a new temp directory; return the ghost root.

    Strategy order (override with *strategy* or ``GHOST_RUN_COPY_STRATEGY``):
      1. ``reflink`` — ``cp --reflink=auto`` (copy-on-write clone, new inode)
      2. ``copy`` — filtered ``shutil.copy2``

    ``hardlink`` and ``hardlink-file`` raise ``GhostCopyError``. A hard link
    is the real file, so a write in the ghost would change the project.
    After a successful copy, any shared inode aborts startup and deletes the
    partial ghost tree.
    """
    real_root = real_root.resolve()
    if not real_root.is_dir():
        raise NotADirectoryError(f"working folder is not a directory: {real_root}")

    ignore = _ignore_names(ignore_dir_names, real_root)
    preferred = (strategy or os.environ.get("GHOST_RUN_COPY_STRATEGY") or "auto").lower()
    if preferred in _HARDLINK_STRATEGIES:
        raise GhostCopyError(
            "ghost-run: refusing hard links for the ghost copy "
            "(a hard link is the real file). Use copy or reflink, or leave "
            "GHOST_RUN_COPY_STRATEGY unset."
        )

    ghost_root = Path(tempfile.mkdtemp(prefix="ghost-run-", suffix="-" + real_root.name))
    try:
        used = "copy"
        if preferred in ("auto", "reflink"):
            if _try_reflink(real_root, ghost_root):
                _prune_ignored(ghost_root, ignore)
                used = "reflink"
        if used == "copy":
            _empty_ghost(ghost_root)
            used = _filtered_copy(
                real_root,
                ghost_root,
                ignore,
                max_file_bytes=max_file_bytes,
                max_total_bytes=max_total_bytes,
            )
        if _has_copyable_file(real_root, ignore, max_file_bytes) and _payload_file_count(ghost_root) == 0:
            raise GhostCopyError(
                "ghost-run: refusing to start Ghost Mode; could not copy the working folder."
            )
        _refuse_hardlink(real_root, ghost_root)
    except Exception:
        shutil.rmtree(ghost_root, ignore_errors=True)
        raise

    (ghost_root / ".ghost-run-marker").write_text(
        f"real_root={real_root}\ncopy_strategy={used}\n", encoding="utf-8"
    )
    # Record strategy for demos/benchmarks
    (ghost_root / ".ghost-run-strategy").write_text(used + "\n", encoding="utf-8")
    return ghost_root


def discard_ghost_copy(ghost_root: Optional[Path]) -> None:
    """Delete the ghost sandbox directory if it exists."""
    if ghost_root is None:
        return
    path = Path(ghost_root)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def timed_create(
    real_root: Path, **kwargs
) -> Tuple[Path, float, str]:
    """Helper for benchmarks: return (ghost_root, seconds, strategy)."""
    t0 = time.perf_counter()
    ghost = create_ghost_copy(real_root, **kwargs)
    dt = time.perf_counter() - t0
    strat = "unknown"
    marker = ghost / ".ghost-run-strategy"
    if marker.is_file():
        strat = marker.read_text(encoding="utf-8").strip()
    return ghost, dt, strat
