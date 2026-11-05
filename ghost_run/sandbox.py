"""Create and tear down the throwaway ghost working copy (CoW / hardlink when possible)."""

from __future__ import annotations

import os
import shutil
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


def _try_cp_al(src: Path, dest: Path) -> bool:
    """Hardlink tree via ``cp -a --link`` (Linux) / ``cp -al``."""
    try:
        # GNU cp: -a archive, --link hardlink files instead of copying
        r = subprocess.run(
            ["cp", "-a", "--link", str(src) + "/.", str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0:
            return True
        # macOS-ish fallback
        r2 = subprocess.run(
            ["cp", "-al", str(src) + "/.", str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r2.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


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
    prefer_hardlink: bool,
) -> str:
    total = 0
    mode = "hardlink-file" if prefer_hardlink else "copy"
    for dirpath, dirnames, filenames in os.walk(real_root):
        dirnames[:] = [d for d in dirnames if d not in ignore]
        rel_dir = Path(dirpath).relative_to(real_root)
        dest_dir = ghost_root / rel_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            if name in ignore:
                continue
            src = Path(dirpath) / name
            try:
                size = src.stat().st_size
            except OSError:
                continue
            if size > max_file_bytes:
                continue
            if total + size > max_total_bytes:
                continue
            dest = dest_dir / name
            try:
                if prefer_hardlink:
                    try:
                        os.link(src, dest)
                        total += size
                        continue
                    except OSError:
                        pass
                shutil.copy2(src, dest, follow_symlinks=False)
                total += size
            except OSError:
                continue
    return mode


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
      1. ``reflink`` — ``cp --reflink=auto``
      2. ``hardlink`` — ``cp -a --link`` then prune ignored dirs
      3. ``hardlink-file`` — per-file ``os.link`` with ignore walk
      4. ``copy`` — filtered ``shutil.copy2``
    """
    real_root = real_root.resolve()
    if not real_root.is_dir():
        raise NotADirectoryError(f"working folder is not a directory: {real_root}")

    ignore = _ignore_names(ignore_dir_names, real_root)
    ghost_root = Path(tempfile.mkdtemp(prefix="ghost-run-", suffix="-" + real_root.name))
    preferred = (strategy or os.environ.get("GHOST_RUN_COPY_STRATEGY") or "auto").lower()

    used = "copy"
    if preferred in ("auto", "reflink"):
        if _try_reflink(real_root, ghost_root):
            _prune_ignored(ghost_root, ignore)
            used = "reflink"
    if used == "copy" and preferred in ("auto", "hardlink", "reflink"):
        # Clear any partial tree
        for child in ghost_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        if _try_cp_al(real_root, ghost_root):
            _prune_ignored(ghost_root, ignore)
            used = "hardlink"
    if used == "copy":
        for child in list(ghost_root.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        used = _filtered_copy(
            real_root,
            ghost_root,
            ignore,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
            prefer_hardlink=preferred in ("auto", "hardlink", "hardlink-file"),
        )

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
