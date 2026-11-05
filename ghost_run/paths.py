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
    except ValueError:
        return False


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
    try:
        resolved = resolve_under(real_root, raw_path)
    except (OSError, RuntimeError) as exc:
        return None, f"ghost-run: cannot resolve path {raw_path!r}: {exc}"

    if not is_within(resolved, real_root):
        return None, (
            f"ghost-run: blocked path outside working folder: {raw_path!r} "
            f"(resolved {resolved}, root {real_root})"
        )

    rel = resolved.relative_to(real_root.resolve())
    ghost_path = (ghost_root / rel).resolve()
    # Prefer absolute ghost path so Hermes file tools hit the sandbox regardless
    # of the process cwd.
    return str(ghost_path), None


def ghost_rel_from_abs(abs_path: Path, ghost_root: Path) -> Optional[str]:
    """Return POSIX-ish relative path inside ghost, or None if outside."""
    try:
        return abs_path.resolve().relative_to(ghost_root.resolve()).as_posix()
    except ValueError:
        return None
