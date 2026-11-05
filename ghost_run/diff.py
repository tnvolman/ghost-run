"""Diff the ghost copy against the real working folder."""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .sandbox import DEFAULT_IGNORE_DIR_NAMES


@dataclass
class FileChange:
    path: str  # relative posix path
    kind: str  # added | modified | deleted
    unified_diff: str = ""


@dataclass
class DiffReport:
    real_root: Path
    ghost_root: Path
    added: List[FileChange] = field(default_factory=list)
    modified: List[FileChange] = field(default_factory=list)
    deleted: List[FileChange] = field(default_factory=list)

    @property
    def all_changes(self) -> List[FileChange]:
        return [*self.added, *self.modified, *self.deleted]

    def summary_lines(self) -> List[str]:
        lines = [
            f"Ghost Run diff — real: {self.real_root}",
            f"                 ghost: {self.ghost_root}",
            f"  +{len(self.added)} added  ~{len(self.modified)} modified  -{len(self.deleted)} deleted",
            "",
        ]
        for ch in self.added:
            lines.append(f"  + {ch.path}")
        for ch in self.modified:
            lines.append(f"  ~ {ch.path}")
        for ch in self.deleted:
            lines.append(f"  - {ch.path}")
        return lines

    def text(self, *, include_unified: bool = True) -> str:
        parts = self.summary_lines()
        if include_unified:
            for ch in self.all_changes:
                if ch.unified_diff:
                    parts.append("")
                    parts.append(ch.unified_diff.rstrip())
        return "\n".join(parts) + "\n"


def _list_rel_files(root: Path, ignore_dirs: Set[str]) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
        for name in filenames:
            if name == ".ghost-run-marker":
                continue
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            out[rel] = full
    return out


def _read_lines(path: Path) -> Optional[List[str]]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None  # binary — skip unified diff body
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except Exception:
            return None
    return text.splitlines(keepends=True)


def compute_diff(
    real_root: Path,
    ghost_root: Path,
    *,
    ignore_dirs: Optional[Iterable[str]] = None,
) -> DiffReport:
    ignore = set(DEFAULT_IGNORE_DIR_NAMES)
    if ignore_dirs:
        ignore.update(ignore_dirs)

    real_files = _list_rel_files(real_root, ignore)
    ghost_files = _list_rel_files(ghost_root, ignore)

    report = DiffReport(real_root=real_root, ghost_root=ghost_root)

    for rel in sorted(set(real_files) | set(ghost_files)):
        in_real = rel in real_files
        in_ghost = rel in ghost_files
        if in_ghost and not in_real:
            g_lines = _read_lines(ghost_files[rel]) or []
            ud = "".join(
                difflib.unified_diff([], g_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}")
            )
            report.added.append(FileChange(path=rel, kind="added", unified_diff=ud))
        elif in_real and not in_ghost:
            r_lines = _read_lines(real_files[rel]) or []
            ud = "".join(
                difflib.unified_diff(r_lines, [], fromfile=f"a/{rel}", tofile=f"b/{rel}")
            )
            report.deleted.append(FileChange(path=rel, kind="deleted", unified_diff=ud))
        else:
            # both exist — compare bytes first for speed
            try:
                same = real_files[rel].read_bytes() == ghost_files[rel].read_bytes()
            except OSError:
                same = False
            if same:
                continue
            r_lines = _read_lines(real_files[rel])
            g_lines = _read_lines(ghost_files[rel])
            if r_lines is None or g_lines is None:
                ud = f"--- a/{rel}\n+++ b/{rel}\n@@ binary or unreadable file changed @@\n"
            else:
                ud = "".join(
                    difflib.unified_diff(
                        r_lines, g_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}"
                    )
                )
            report.modified.append(FileChange(path=rel, kind="modified", unified_diff=ud))

    return report
