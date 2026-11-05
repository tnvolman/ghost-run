"""Configurable ignore patterns for Ghost Run copies and diffs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Set

# Built-in defaults (dirs + file names).
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
    ".turbo",
    ".cache",
    "coverage",
    ".idea",
    ".vscode",
}

DEFAULT_IGNORE_FILE_NAMES: Set[str] = {
    ".ghost-run-marker",
    "ghost-run-diff.html",
}


def load_ignore_dirs(real_root: Path | None = None) -> Set[str]:
    """Merge defaults with env + optional ``.ghost-runignore`` beside the project."""
    names = set(DEFAULT_IGNORE_DIR_NAMES)
    env = os.environ.get("GHOST_RUN_IGNORE", "")
    if env.strip():
        for part in env.split(","):
            part = part.strip().strip("/")
            if part:
                names.add(part)
    if real_root is not None:
        cfg = Path(real_root) / ".ghost-runignore"
        if cfg.is_file():
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Treat bare names as directory names to prune.
                names.add(line.strip("/").split("/")[0])
    return names
