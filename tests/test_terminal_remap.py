from __future__ import annotations

from pathlib import Path

from ghost_run.commands import turn_on
from ghost_run.middleware import on_tool_request
from ghost_run.state import get_state


def test_terminal_workdir_remapped(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    result = on_tool_request(
        tool_name="terminal",
        args={"command": "rm -f src/unused_tmp.py"},
    )
    assert result is not None
    assert result["args"]["workdir"] == str(state.ghost_root.resolve())
    # Simulate the delete inside ghost
    ghost_file = state.ghost_root / "src" / "unused_tmp.py"
    assert ghost_file.exists()
    ghost_file.unlink()
    assert (demo_project / "src" / "unused_tmp.py").exists()
