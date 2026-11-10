from __future__ import annotations

from pathlib import Path

import sys
import types

from ghost_run.commands import turn_on
from ghost_run.state import GhostState, get_state, reset_state, _default_session_path, _load_from_disk


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


def test_session_path_uses_get_hermes_home(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GHOST_RUN_SESSION_FILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))
    home = tmp_path / "profile-home"
    module = types.ModuleType("hermes_constants")
    module.get_hermes_home = lambda: home
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    assert _default_session_path() == home / "ghost-run" / "active.json"


def test_session_path_falls_back_without_hermes_constants(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GHOST_RUN_SESSION_FILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "from-env"))
    module = types.ModuleType("hermes_constants")
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    assert _default_session_path() == tmp_path / "from-env" / "ghost-run" / "active.json"


def test_session_path_falls_back_when_get_hermes_home_raises(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GHOST_RUN_SESSION_FILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "from-env"))

    def boom():
        raise RuntimeError("profile lookup failed")

    module = types.ModuleType("hermes_constants")
    module.get_hermes_home = boom
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    assert _default_session_path() == tmp_path / "from-env" / "ghost-run" / "active.json"
