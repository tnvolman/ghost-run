"""Create and tear down the throwaway ghost working copy."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Set

# Directories we never copy into the ghost sandbox (size + noise).
DEFAULT_IGNORE_DIR_NAMES: Set[str] = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ghost-run",
    "dist",
    "build",
    ".next",
    "target",
}

# Skip individual files larger than this when copying (bytes).
MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MiB
# Soft cap on total copied bytes; stop adding more files past this.
MAX_TOTAL_BYTES = 256 * 1024 * 1024  # 256 MiB


def _ignore_names(dir_names: Optional[Iterable[str]] = None) -> Set[str]:
    names = set(DEFAULT_IGNORE_DIR_NAMES)
    if dir_names:
        names.update(dir_names)
    return names


def create_ghost_copy(
    real_root: Path,
    *,
    ignore_dir_names: Optional[Iterable[str]] = None,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> Path:
    """Copy *real_root* into a new temp directory; return the ghost root.

    Uses a filtered full copy (not true CoW) so it works on every filesystem.
    Skips bulky/ignored dirs and oversize files.
    """
    real_root = real_root.resolve()
    if not real_root.is_dir():
        raise NotADirectoryError(f"working folder is not a directory: {real_root}")

    ghost_root = Path(tempfile.mkdtemp(prefix="ghost-run-", suffix="-" + real_root.name))
    ignore = _ignore_names(ignore_dir_names)
    total = 0

    for dirpath, dirnames, filenames in os.walk(real_root):
        # Prune ignored directories in-place so os.walk does not descend.
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
                shutil.copy2(src, dest, follow_symlinks=False)
                total += size
            except OSError:
                # Skip unreadable / special files.
                continue

    # Marker so nested tools can detect they are inside a ghost copy.
    (ghost_root / ".ghost-run-marker").write_text(
        f"real_root={real_root}\n", encoding="utf-8"
    )
    return ghost_root


def discard_ghost_copy(ghost_root: Optional[Path]) -> None:
    """Delete the ghost sandbox directory if it exists."""
    if ghost_root is None:
        return
    path = Path(ghost_root)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
