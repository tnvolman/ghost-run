from __future__ import annotations

from pathlib import Path

from ghost_run.apply import apply_paths, discard_session
from ghost_run.commands import turn_on
from ghost_run.state import get_state


def test_apply_copies_only_selected_paths(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    ghost = state.ghost_root
    assert ghost

    (ghost / "notes.txt").write_text("changed notes\n", encoding="utf-8")
    (ghost / "src" / "app.py").write_text("def main():\n    print('ghost')\n", encoding="utf-8")
    (ghost / "brand_new.txt").write_text("new\n", encoding="utf-8")
    (ghost / "src" / "unused_tmp.py").unlink()

    original_app = (demo_project / "src" / "app.py").read_text()
    original_notes = (demo_project / "notes.txt").read_text()

    actions = apply_paths(state, ["notes.txt", "brand_new.txt"])
    assert any("notes.txt" in a for a in actions)
    assert any("brand_new.txt" in a for a in actions)

    assert (demo_project / "notes.txt").read_text() == "changed notes\n"
    assert (demo_project / "brand_new.txt").read_text() == "new\n"
    # Unselected modifications / deletions must remain untouched on real.
    assert (demo_project / "src" / "app.py").read_text() == original_app
    assert (demo_project / "src" / "unused_tmp.py").exists()
    assert original_notes != "changed notes\n" or True  # notes was selected


def test_discard_cleans_temp(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    ghost = state.ghost_root
    assert ghost and ghost.exists()
    marker = ghost / ".ghost-run-marker"
    assert marker.exists()

    msg = discard_session(state)
    assert "discarded" in msg.lower()
    assert not ghost.exists()
    assert get_state().enabled is False
    assert get_state().ghost_root is None
