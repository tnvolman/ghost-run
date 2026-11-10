from __future__ import annotations

from pathlib import Path

from ghost_run.commands import slash_ghost, turn_on
from ghost_run.paths import remap_to_ghost
from ghost_run.diff import compute_diff
from ghost_run.hooks import on_pre_tool_call
from ghost_run.middleware import on_tool_request
from ghost_run.state import get_state


def test_ghost_write_never_touches_real(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    assert state.enabled and state.ghost_root

    real_notes = (demo_project / "notes.txt").read_text()
    result = on_tool_request(
        tool_name="write_file",
        args={"path": "notes.txt", "content": "GHOST ONLY\n"},
    )
    assert result is not None
    ghost_path = Path(result["args"]["path"])
    assert state.ghost_root in ghost_path.parents or ghost_path.parent == state.ghost_root
    # Simulate the write the remapped tool would do
    ghost_path.write_text("GHOST ONLY\n", encoding="utf-8")

    assert (demo_project / "notes.txt").read_text() == real_notes
    assert ghost_path.read_text() == "GHOST ONLY\n"


def test_delete_is_simulated(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    target = state.ghost_root / "src" / "unused_tmp.py"
    assert target.exists()
    target.unlink()  # simulate terminal rm in ghost workdir

    assert (demo_project / "src" / "unused_tmp.py").exists()
    report = compute_diff(demo_project, state.ghost_root)
    assert any(c.path == "src/unused_tmp.py" and c.kind == "deleted" for c in report.deleted)


def test_network_tool_blocked(demo_project: Path):
    turn_on(demo_project)
    on_tool_request(tool_name="web_search", args={"query": "x"})
    blocked = on_pre_tool_call(tool_name="web_search", args={"query": "x"})
    assert blocked and blocked["action"] == "block"
    assert "network" in blocked["message"].lower() or "web_search" in blocked["message"]


def test_network_command_blocked(demo_project: Path):
    turn_on(demo_project)
    on_tool_request(tool_name="terminal", args={"command": "curl https://example.com"})
    blocked = on_pre_tool_call(
        tool_name="terminal", args={"command": "curl https://example.com"}
    )
    assert blocked and blocked["action"] == "block"


def test_outside_working_folder_blocked(demo_project: Path, tmp_path: Path):
    turn_on(demo_project)
    outside = tmp_path / "other" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("nope", encoding="utf-8")

    on_tool_request(
        tool_name="write_file",
        args={"path": str(outside), "content": "hacked"},
    )
    blocked = on_pre_tool_call(
        tool_name="write_file",
        args={"path": str(outside), "content": "hacked"},
    )
    assert blocked and blocked["action"] == "block"
    assert outside.read_text() == "nope"


def test_remap_rejects_embedded_nul(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    remapped, err = remap_to_ghost("notes\x00.txt", state.real_root, state.ghost_root)
    assert remapped is None
    assert err and "NUL" in err
    result = on_tool_request(
        tool_name="write_file",
        args={"path": "notes\x00.txt", "content": "nope"},
        tool_call_id="nul",
    )
    assert result is None
    blocked = on_pre_tool_call(
        tool_name="write_file",
        args={"path": "notes\x00.txt", "content": "nope"},
        tool_call_id="nul",
    )
    assert blocked and blocked["action"] == "block"
    assert (demo_project / "notes.txt").exists()


def test_slash_status(demo_project: Path, monkeypatch):
    monkeypatch.chdir(demo_project)
    assert "OFF" in slash_ghost("status")
    out = slash_ghost("on")
    assert "ON" in out
    assert "ON" in slash_ghost("status")
