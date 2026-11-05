"""Ghost Mode session state with disk persistence across CLI/agent processes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_SESSION_ENV = "GHOST_RUN_SESSION_FILE"


def _default_session_path() -> Path:
    override = os.environ.get(_SESSION_ENV)
    if override:
        return Path(override)
    home = os.environ.get("HERMES_HOME")
    base = Path(home) if home else Path.home() / ".hermes"
    return base / "ghost-run" / "active.json"


@dataclass
class GhostState:
    enabled: bool = False
    real_root: Optional[Path] = None
    ghost_root: Optional[Path] = None
    # Set by tool_request when a call should be blocked by pre_tool_call.
    # Memory-only; not persisted (per tool-call within one agent process).
    pending_block: Optional[str] = None
    flags: list[str] = field(default_factory=list)

    def clear_session(self) -> None:
        self.enabled = False
        self.real_root = None
        self.ghost_root = None
        self.pending_block = None
        self.flags.clear()
        _delete_session_file()

    def save(self) -> None:
        """Persist real/ghost roots so other processes see them."""
        path = _default_session_path()
        if self.real_root is None or self.ghost_root is None:
            _delete_session_file()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": bool(self.enabled),
            "keep": not bool(self.enabled),
            "real_root": str(self.real_root.resolve()),
            "ghost_root": str(self.ghost_root.resolve()),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


_STATE = GhostState()


def _delete_session_file() -> None:
    path = _default_session_path()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _load_from_disk(state: GhostState) -> None:
    path = _default_session_path()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    real = Path(str(data.get("real_root") or ""))
    ghost = Path(str(data.get("ghost_root") or ""))
    if not real.is_dir() or not ghost.is_dir():
        _delete_session_file()
        return
    state.real_root = real
    state.ghost_root = ghost
    # Remapping only when enabled=true; keep sessions still allow diff/apply.
    state.enabled = bool(data.get("enabled"))


def get_state() -> GhostState:
    """Return session singleton, loading disk when memory has no roots yet."""
    if _STATE.real_root is None or _STATE.ghost_root is None:
        _load_from_disk(_STATE)
    return _STATE


def reset_state() -> None:
    """Test helper: wipe singleton and session file."""
    _STATE.enabled = False
    _STATE.real_root = None
    _STATE.ghost_root = None
    _STATE.pending_block = None
    _STATE.flags.clear()
    _delete_session_file()
