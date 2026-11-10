"""Path remapping between the real working folder and the ghost copy."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple


def resolve_under(root: Path, raw: str) -> Path:
    """Resolve *raw* (absolute, relative, or ~/...) against *root* when relative."""
    expanded = os.path.expanduser(raw)
    p = Path(expanded)
    if not p.is_absolute():
        p = root / p
    return p.resolve()


def is_within(path: Path, root: Path) -> bool:
    """True if *path* is *root* or a descendant (after resolve)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def contained_path(root: Path, rel: str) -> Optional[Path]:
    """Resolve *rel* under *root* only when the target stays inside *root*.

    Absolute paths, NUL bytes, the root itself, and symlink escapes return None.
    A leading ``./`` is stripped; ``..`` is refused when it leaves *root*.
    """
    if not isinstance(rel, str):
        return None
    text = rel.strip()
    while text.startswith("./"):
        text = text[2:]
    if not text or "\x00" in text:
        return None
    raw = Path(text)
    if raw.is_absolute():
        return None
    root_r = root.resolve()
    try:
        candidate = (root_r / raw).resolve()
    except (OSError, RuntimeError):
        return None
    if candidate == root_r or not is_within(candidate, root_r):
        return None
    return candidate


def tree_stays_inside(root: Path, start: Path) -> bool:
    """True when *start* and every symlink under it resolve inside *root*."""
    if not is_within(start, root):
        return False
    if not start.exists() or start.is_symlink() or not start.is_dir():
        return True
    root_r = root.resolve()
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        for name in list(dirnames) + list(filenames):
            child = Path(dirpath) / name
            if child.is_symlink() and not is_within(child, root_r):
                return False
    return True


def remap_to_ghost(
    raw_path: str,
    real_root: Path,
    ghost_root: Path,
) -> Tuple[Optional[str], Optional[str]]:
    """Map a user/tool path into the ghost copy.

    Returns ``(remapped_path_str, error_message)``.
    On success error_message is None. On outside-root, remapped is None and
    error_message explains the block.
    """
    if "\x00" in raw_path:
        return None, "ghost-run: blocked path containing NUL"
    try:
        resolved = resolve_under(real_root, raw_path)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, f"ghost-run: cannot resolve path {raw_path!r}: {exc}"

    if not is_within(resolved, real_root):
        return None, (
            f"ghost-run: blocked path outside working folder: {raw_path!r} "
            f"(resolved {resolved}, root {real_root})"
        )

    rel = resolved.relative_to(real_root.resolve())
    ghost_path = (ghost_root / rel).resolve()
    if not is_within(ghost_path, ghost_root):
        return None, (
            f"ghost-run: blocked path outside ghost copy: {raw_path!r} "
            f"(resolved {ghost_path}, ghost {ghost_root})"
        )
    # Prefer absolute ghost path so Hermes file tools hit the sandbox regardless
    # of the process cwd.
    return str(ghost_path), None


def ghost_rel_from_abs(abs_path: Path, ghost_root: Path) -> Optional[str]:
    """Return POSIX-ish relative path inside ghost, or None if outside."""
    try:
        return abs_path.resolve().relative_to(ghost_root.resolve()).as_posix()
    except ValueError:
        return None
