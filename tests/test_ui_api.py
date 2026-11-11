"""Interactive UI server API (Apply selected / Discard)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ghost_run.commands import turn_on
from ghost_run.state import get_state, reset_state
from ghost_run.ui_server import serve


@pytest.fixture
def ui_session(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_RUN_SESSION_FILE", str(tmp_path / "session.json"))
    reset_state()
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "keep.js").write_text("keep\n", encoding="utf-8")
    (root / "src" / "gone.js").write_text("gone\n", encoding="utf-8")
    turn_on(root)
    state = get_state()
    # mutate ghost: delete gone.js, add note.md, modify keep.js
    (state.ghost_root / "src" / "gone.js").unlink()
    (state.ghost_root / "src" / "keep.js").write_text("keep-v2\n", encoding="utf-8")
    (state.ghost_root / "NOTE.md").write_text("added\n", encoding="utf-8")
    httpd, port = serve(host="127.0.0.1", port=0, block=False)
    yield root, state, port, httpd.ghost_token
    httpd.shutdown()
    reset_state()


def _open(port: int, token: str, path: str, payload: dict | None = None):
    headers = {"X-Ghost-Run-Token": token}
    data = None
    method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
        method = "POST"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode()) if path.startswith("/api/") else resp.read().decode()


def test_diff_lists_badges_kinds(ui_session):
    root, state, port, token = ui_session
    data = _open(port, token, "/api/diff")
    assert data["ok"] is True
    kinds = {c["path"]: c["kind"] for c in data["changes"]}
    assert kinds["src/gone.js"] == "deleted"
    assert kinds["src/keep.js"] == "modified"
    assert kinds["NOTE.md"] == "added"
    # unified diffs present
    by = {c["path"]: c for c in data["changes"]}
    assert "@@" in by["src/keep.js"]["unified_diff"] or by["src/keep.js"]["unified_diff"]


def test_apply_selected_and_discard(ui_session):
    root, state, port, token = ui_session
    # Apply only the deletion
    out = _open(port, token, "/api/apply", {"paths": ["src/gone.js"]})
    assert out["ok"] is True
    assert not (root / "src" / "gone.js").exists()
    assert (root / "src" / "keep.js").read_text() == "keep\n"  # not applied
    assert not (root / "NOTE.md").exists()

    # Discard tears down sandbox
    out = _open(port, token, "/api/discard", {})
    assert out["ok"] is True
    st = get_state()
    assert st.enabled is False
    assert st.ghost_root is None or not Path(st.ghost_root).exists()


def _post(port: int, token: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/apply",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Ghost-Run-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _snapshot(root: Path) -> dict:
    return {
        "gone": (root / "src" / "gone.js").read_text(encoding="utf-8"),
        "keep": (root / "src" / "keep.js").read_text(encoding="utf-8"),
        "note": (root / "NOTE.md").exists(),
    }


def test_apply_rejects_missing_and_empty_paths(ui_session):
    root, _state, port, token = ui_session
    before = _snapshot(root)
    for payload in ({}, {"paths": []}):
        code, body = _post(port, token, payload)
        assert code == 400
        assert body["ok"] is False
        assert _snapshot(root) == before


def test_apply_rejects_path_not_in_diff(ui_session):
    root, state, port, token = ui_session
    same = root / "src" / "same.js"
    same.write_text("same\n", encoding="utf-8")
    (state.ghost_root / "src" / "same.js").write_text("same\n", encoding="utf-8")
    before = _snapshot(root)
    before["same"] = same.read_text(encoding="utf-8")

    code, body = _post(port, token, {"paths": ["src/same.js"]})
    assert code == 400
    assert body["ok"] is False
    assert same.read_text(encoding="utf-8") == "same\n"
    assert _snapshot(root) == {k: before[k] for k in ("gone", "keep", "note")}

    code, body = _post(port, token, {"paths": ["src/gone.js", "src/same.js"]})
    assert code == 400
    assert body["ok"] is False
    assert (root / "src" / "gone.js").read_text(encoding="utf-8") == before["gone"]
    assert (root / "src" / "keep.js").read_text(encoding="utf-8") == before["keep"]
    assert same.read_text(encoding="utf-8") == "same\n"
    assert not (root / "NOTE.md").exists()


def test_pane_html_has_controls(ui_session):
    _, _, port, token = ui_session
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        html = resp.read().decode()
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
    assert "history.replaceState" in html
    assert "X-Ghost-Run-Token" in html
    assert "?token=" not in html
    assert "Apply selected" in html
    assert "Discard" in html
    assert "checkbox" in html
    assert "badge" in html
