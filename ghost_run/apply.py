"""Apply selected ghost changes onto the real working folder, or discard."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Sequence

from .diff import compute_diff
from .paths import contained_path, tree_stays_inside
from .sandbox import discard_ghost_copy
from .state import GhostState


def apply_paths(
    state: GhostState,
    paths: Optional[Sequence[str]] = None,
) -> List[str]:
    """Copy selected relative paths from ghost → real.

    * If *paths* is empty/None, apply every change in the current diff.
    * Deleted paths (present in real, absent in ghost) are unlinked on real.
    Returns a list of human-readable action lines.
    """
    if state.real_root is None or state.ghost_root is None:
        raise RuntimeError("ghost-run: no ghost sandbox; nothing to apply (try /ghost on first)")
    if not state.ghost_root.exists():
        raise RuntimeError(f"ghost-run: sandbox missing at {state.ghost_root}")

    real_root = state.real_root.resolve()
    ghost_root = state.ghost_root.resolve()
    report = compute_diff(real_root, ghost_root)

    wanted = {_clean_rel(p) for p in (paths or []) if p and str(p).strip()}
    wanted.discard("")
    if not wanted:
        wanted = {ch.path for ch in report.all_changes}

    actions: List[str] = []
    change_by_path = {ch.path: ch for ch in report.all_changes}

    for rel in sorted(wanted):
        ch = change_by_path.get(rel)
        # Resolve first and refuse escapes. Operate on the lexical path so a
        # symlink is not followed into a different file before the check.
        dest = _io_path(real_root, rel)
        src = _io_path(ghost_root, rel)
        if dest is None or src is None:
            actions.append(f"refused {rel} (outside project root)")
            continue
        if ch is None:
            # Path named explicitly but not in diff — if it exists in ghost, copy anyway.
            if src.is_file() and tree_stays_inside(ghost_root, src):
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                actions.append(f"copied (forced) {rel}")
            else:
                actions.append(f"skipped {rel} (not in diff, not in ghost)")
            continue

        if ch.kind in ("added", "modified"):
            if not src.exists():
                actions.append(f"skipped {rel} (missing in ghost)")
                continue
            if not tree_stays_inside(ghost_root, src):
                actions.append(f"refused {rel} (outside project root)")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            actions.append(f"{ch.kind} → applied {rel}")
        elif ch.kind == "deleted":
            if not tree_stays_inside(real_root, dest):
                actions.append(f"refused {rel} (outside project root)")
                continue
            if dest.exists():
                if dest.is_dir() and not dest.is_symlink():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
                actions.append(f"deleted → removed {rel}")
            else:
                actions.append(f"skipped {rel} (already absent on real)")
        else:
            actions.append(f"skipped {rel} (unknown kind {ch.kind})")

    return actions


def _clean_rel(rel: str) -> str:
    """Strip surrounding space and a leading ``./`` without eating ``.env``."""
    text = str(rel).strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _io_path(root: Path, rel: str) -> Optional[Path]:
    """Lexical path under *root* after ``contained_path`` says it stays inside."""
    if contained_path(root, rel) is None:
        return None
    return root.resolve() / _clean_rel(rel)


def discard_session(state: GhostState) -> str:
    """Delete the ghost copy and clear session state."""
    root = state.ghost_root
    discard_ghost_copy(root)
    state.clear_session()
    return f"ghost-run: discarded sandbox {root}" if root else "ghost-run: nothing to discard"
