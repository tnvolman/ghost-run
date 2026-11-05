from __future__ import annotations

from pathlib import Path

from ghost_run.commands import turn_on
from ghost_run.state import GhostState, get_state, reset_state, _load_from_disk


def test_session_persists_across_process_simulation(demo_project: Path, tmp_path: Path, monkeypatch):
    session = tmp_path / "active.json"
    monkeypatch.setenv("GHOST_RUN_SESSION_FILE", str(session))
    reset_state()

    turn_on(demo_project)
    assert session.is_file()
    ghost = get_state().ghost_root
    assert ghost and ghost.is_dir()

    # Simulate a fresh process: empty memory, reload from disk.
    fresh = GhostState()
    _load_from_disk(fresh)
    assert fresh.enabled is True
    assert fresh.real_root == demo_project.resolve()
    assert fresh.ghost_root == ghost.resolve()
