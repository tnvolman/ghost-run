"""Process-local Ghost Mode session state."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class GhostState:
    enabled: bool = False
    real_root: Optional[Path] = None
    ghost_root: Optional[Path] = None
    # Set by tool_request when a call should be blocked by pre_tool_call.
    pending_block: Optional[str] = None
    # Soft flags (non-blocking notices) collected during a run.
    flags: list[str] = field(default_factory=list)

    def clear_session(self) -> None:
        self.enabled = False
        self.real_root = None
        self.ghost_root = None
        self.pending_block = None
        self.flags.clear()


_STATE = GhostState()


def get_state() -> GhostState:
    return _STATE


def reset_state() -> None:
    """Test helper: wipe singleton."""
    _STATE.clear_session()
