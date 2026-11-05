"""Apply selected ghost changes onto the real working folder, or discard."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .diff import DiffReport, compute_diff
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

    wanted = {p.strip().lstrip("./") for p in (paths or []) if p and p.strip()}
    if not wanted:
        wanted = {ch.path for ch in report.all_changes}

    actions: List[str] = []
    change_by_path = {ch.path: ch for ch in report.all_changes}

    for rel in sorted(wanted):
        ch = change_by_path.get(rel)
        dest = real_root / rel
        src = ghost_root / rel
        if ch is None:
            # Path named explicitly but not in diff — if it exists in ghost, copy anyway.
            if src.is_file():
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
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            actions.append(f"{ch.kind} → applied {rel}")
        elif ch.kind == "deleted":
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
                actions.append(f"deleted → removed {rel}")
            else:
                actions.append(f"skipped {rel} (already absent on real)")
        else:
            actions.append(f"skipped {rel} (unknown kind {ch.kind})")

    return actions


def discard_session(state: GhostState) -> str:
    """Delete the ghost copy and clear session state."""
    root = state.ghost_root
    discard_ghost_copy(root)
    state.clear_session()
    return f"ghost-run: discarded sandbox {root}" if root else "ghost-run: nothing to discard"
