"""Ghost Mode session state with disk persistence across CLI/agent processes."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

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
    # Legacy single slot. Concurrent calls use ``set_pending_block`` instead
    # so one tool call cannot clear another's block. Not persisted.
    pending_block: Optional[str] = None
    flags: list[str] = field(default_factory=list)
    # Last thin-Verify result (advisory). Persisted so the UI can reload it.
    last_verify: Optional[dict] = None

    def clear_session(self) -> None:
        self.enabled = False
        self.real_root = None
        self.ghost_root = None
        self.pending_block = None
        self.flags.clear()
        self.last_verify = None
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
            "verify": self.last_verify,
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


_STATE = GhostState()

# One pending block per tool call. ``tool_call_id`` is the slot when Hermes
# supplies it; otherwise the calling thread is the slot. Middleware sets the
# slot and the matching pre_tool_call pops it, so concurrent calls do not
# clobber each other.
_pending_lock = threading.Lock()
_pending_slots: dict[str, str] = {}


def pending_slot_key(tool_call_id: str = "") -> str:
    if tool_call_id:
        return "call:" + tool_call_id
    return "thread:" + str(threading.get_ident())


_MAX_PENDING_SLOTS = 256


def set_pending_block(message: str, tool_call_id: str = "") -> None:
    with _pending_lock:
        key = pending_slot_key(tool_call_id)
        if key not in _pending_slots and len(_pending_slots) >= _MAX_PENDING_SLOTS:
            _pending_slots.pop(next(iter(_pending_slots)))
        _pending_slots[key] = message


def pop_pending_block(tool_call_id: str = "") -> Optional[str]:
    with _pending_lock:
        return _pending_slots.pop(pending_slot_key(tool_call_id), None)


def clear_pending_blocks() -> None:
    with _pending_lock:
        _pending_slots.clear()


def _delete_session_file() -> None:
    path = _default_session_path()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _read_session_payload() -> Optional[dict]:
    path = _default_session_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _apply_verify(state: GhostState, data: dict) -> None:
    if "verify" not in data:
        return
    verify: Any = data.get("verify")
    state.last_verify = verify if isinstance(verify, dict) else None


def _load_from_disk(state: GhostState) -> None:
    data = _read_session_payload()
    if not data:
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
    _apply_verify(state, data)


def get_state() -> GhostState:
    """Return session singleton, loading disk when memory has no roots yet.

    When roots are already in memory, the last Verify result is still refreshed
    from the session file so a CLI ``hermes ghost verify`` is visible to the UI
    process on its next request.
    """
    if _STATE.real_root is None or _STATE.ghost_root is None:
        _load_from_disk(_STATE)
    else:
        data = _read_session_payload()
        if data is not None:
            _apply_verify(_STATE, data)
    return _STATE


def reset_state() -> None:
    """Test helper: wipe singleton and session file."""
    _STATE.enabled = False
    _STATE.real_root = None
    _STATE.ghost_root = None
    _STATE.pending_block = None
    _STATE.flags.clear()
    _STATE.last_verify = None
    clear_pending_blocks()
    _delete_session_file()
