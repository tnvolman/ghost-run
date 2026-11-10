"""Thin Verify: one configured command in the ghost copy, advisory only."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from ghost_run.commands import slash_ghost, turn_on
from ghost_run.state import GhostState, _load_from_disk, get_state, reset_state
from ghost_run.ui_server import serve
from ghost_run.verify import (
    MAX_LOG_BYTES,
    NO_TEST_COMMAND,
    format_verify,
    resolve_test_command,
    run_verify,
    truncate_log,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO_FIXTURE = ROOT / "demo" / "fixture"


def _py(code: str) -> str:
    return f"{sys.executable} -c {json.dumps(code)}"


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GHOST_RUN_TEST", raising=False)
    # Existing cases exercise the command itself. The approval gate is covered
    # in test_security.py; here a yes is recorded for the project+command.
    monkeypatch.setattr(
        "ghost_run.verify.prompt_verify_approval",
        lambda root, command: "always",
    )
    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    turn_on(root)
    state = get_state()
    (state.ghost_root / "only-in-ghost.txt").write_text("ghost\n", encoding="utf-8")
    return root, state


def test_resolve_env_then_file_then_missing(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(Exception) as exc:
        resolve_test_command(root, environ={})
    assert str(exc.value) == NO_TEST_COMMAND

    (root / ".ghost-run-test").write_text(
        "\n# comment\n\n  echo from-file\necho ignored\n",
        encoding="utf-8",
    )
    assert resolve_test_command(root, environ={}) == "echo from-file"
    assert resolve_test_command(root, environ={"GHOST_RUN_TEST": "  "}) == "echo from-file"
    assert (
        resolve_test_command(root, environ={"GHOST_RUN_TEST": "echo from-env"})
        == "echo from-env"
    )

    (root / ".ghost-run-test").write_text("# only a comment\n\n", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        resolve_test_command(root, environ={})
    assert str(exc.value) == NO_TEST_COMMAND


def test_truncate_log_caps_bytes():
    raw = "x" * (MAX_LOG_BYTES + 500)
    clipped, truncated = truncate_log(raw)
    assert truncated is True
    assert len(clipped.encode("utf-8")) <= MAX_LOG_BYTES
    assert "truncated" in clipped
    same, flag = truncate_log("short")
    assert flag is False
    assert same == "short"


def test_run_in_ghost_cwd_pass(project, monkeypatch):
    root, state = project
    ghost = state.ghost_root
    code = (
        "import os, pathlib; "
        "p = pathlib.Path('only-in-ghost.txt'); "
        "assert p.read_text() == 'ghost\\n'; "
        "pathlib.Path('ran-in-cwd.txt').write_text(os.getcwd()); "
        "print(os.getcwd())"
    )
    monkeypatch.setenv("GHOST_RUN_TEST", _py(code))
    result = run_verify(state, timeout=20)
    assert result["status"] == "pass"
    assert result["exit_code"] == 0
    assert result["advisory"] is True
    assert result["cwd"] == str(ghost.resolve())
    assert str(ghost.resolve()) in result["log"]
    assert (ghost / "ran-in-cwd.txt").read_text(encoding="utf-8") == str(ghost.resolve())
    assert not (root / "ran-in-cwd.txt").exists()
    assert not (root / "only-in-ghost.txt").exists()


def test_fail_and_missing_config(project, monkeypatch):
    root, state = project
    monkeypatch.delenv("GHOST_RUN_TEST", raising=False)
    missing = run_verify(state, timeout=10)
    assert missing["status"] == "error"
    assert missing["error"] == NO_TEST_COMMAND
    assert format_verify(missing).strip() == NO_TEST_COMMAND

    (root / ".ghost-run-test").write_text(_py("print('nope'); raise SystemExit(3)") + "\n", encoding="utf-8")
    # Command is read from the real root, not a rewritten ghost copy of the file.
    (state.ghost_root / ".ghost-run-test").write_text("echo from-ghost\n", encoding="utf-8")
    failed = run_verify(state, timeout=20)
    assert failed["status"] == "fail"
    assert failed["exit_code"] == 3
    assert "nope" in failed["log"]
    assert "from-ghost" not in failed["command"]
    assert failed["advisory"] is True


def test_env_beats_file(project, monkeypatch):
    root, state = project
    (root / ".ghost-run-test").write_text(_py("raise SystemExit(1)") + "\n", encoding="utf-8")
    monkeypatch.setenv("GHOST_RUN_TEST", _py("print('env-wins')"))
    result = run_verify(state, timeout=20)
    assert result["status"] == "pass"
    assert "env-wins" in result["log"]


def test_timeout(project, monkeypatch):
    _, state = project
    monkeypatch.setenv("GHOST_RUN_TEST", _py("import time; time.sleep(30)"))
    result = run_verify(state, timeout=0.4)
    assert result["status"] == "fail"
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert result["advisory"] is True


def test_log_truncated(project, monkeypatch):
    _, state = project
    monkeypatch.setenv("GHOST_RUN_TEST", _py("print('y' * 30000)"))
    result = run_verify(state, timeout=20)
    assert result["status"] == "pass"
    assert result["truncated"] is True
    assert len(result["log"].encode("utf-8")) <= MAX_LOG_BYTES


def test_result_persists_and_reloads(project, monkeypatch, tmp_path):
    _, state = project
    monkeypatch.setenv("GHOST_RUN_TEST", _py("print('saved')"))
    result = run_verify(state, timeout=20)
    assert result["status"] == "pass"

    session = Path(os_session())
    payload = json.loads(session.read_text(encoding="utf-8"))
    assert payload["verify"]["status"] == "pass"
    assert "saved" in payload["verify"]["log"]

    fresh = GhostState()
    _load_from_disk(fresh)
    assert fresh.last_verify["status"] == "pass"
    assert "saved" in fresh.last_verify["log"]

    payload["verify"] = {
        "status": "fail",
        "exit_code": 1,
        "log": "reloaded",
        "advisory": True,
        "timed_out": False,
        "error": None,
    }
    session.write_text(json.dumps(payload), encoding="utf-8")
    assert get_state().last_verify["status"] == "fail"
    assert get_state().last_verify["log"] == "reloaded"


def os_session() -> str:
    import os

    return os.environ["GHOST_RUN_SESSION_FILE"]


def test_turn_on_clears_previous_verify(project, monkeypatch):
    root, state = project
    monkeypatch.setenv("GHOST_RUN_TEST", _py("print('once')"))
    assert run_verify(state, timeout=20)["status"] == "pass"
    state.enabled = False
    state.save()
    turn_on(root)
    assert get_state().last_verify is None


def test_slash_verify(project, monkeypatch):
    _, state = project
    monkeypatch.setenv("GHOST_RUN_TEST", _py("print('slash-ok')"))
    text = slash_ghost("verify")
    assert "Verify: Pass" in text
    assert "slash-ok" in text
    assert state.last_verify["status"] == "pass"


def test_no_sandbox(monkeypatch):
    monkeypatch.delenv("GHOST_RUN_TEST", raising=False)
    reset_state()
    result = run_verify(get_state(), timeout=5)
    assert result["status"] == "error"
    assert "no active sandbox" in result["error"]


def test_demo_fixture_verifies_without_install(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GHOST_RUN_TEST", raising=False)
    dest = tmp_path / "fixture"
    shutil.copytree(DEMO_FIXTURE, dest)
    turn_on(dest)
    state = get_state()
    from ghost_run.verify import remember_approval

    remember_approval(dest, "python3 scripts/ghost_verify.py")
    # node_modules is ignored by the ghost copy; the check must not need it.
    assert state.ghost_root is not None
    assert not (state.ghost_root / "node_modules").exists()
    result = run_verify(state, timeout=20)
    assert result["status"] == "pass", result
    assert "fixture healthy" in result["log"]
    assert result["command"] == "python3 scripts/ghost_verify.py"
    assert result["cwd"] == str(state.ghost_root.resolve())

    (state.ghost_root / "index.html").unlink()
    failed = run_verify(state, timeout=20)
    assert failed["status"] == "fail"
    assert "missing index.html" in failed["log"]
    assert (dest / "index.html").is_file()


def _post_json(url: str, payload: dict, token: str = ""):
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Ghost-Run-Token"] = token
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def test_ui_verify_is_advisory(project, monkeypatch):
    root, state = project
    (state.ghost_root / "keep.txt").write_text("changed\n", encoding="utf-8")
    monkeypatch.delenv("GHOST_RUN_TEST", raising=False)
    httpd, port = serve(host="127.0.0.1", port=0, block=False)
    try:
        import urllib.request

        token = httpd.ghost_token
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            html = resp.read().decode()
        assert 'id="verifyBtn"' in html
        assert "Apply selected" in html
        assert "Advisory only" in html
        # Apply is not disabled in the pane markup.
        assert 'id="applyBtn" class="primary"' in html
        assert "applyBtn.disabled" not in html

        missing = _post_json(base + "/api/verify", {}, token)
        assert missing["ok"] is False
        assert missing["advisory"] is True
        assert missing["verify"]["error"] == NO_TEST_COMMAND

        monkeypatch.setenv("GHOST_RUN_TEST", _py("raise SystemExit(7)"))
        body = _post_json(base + "/api/verify", {}, token)
        # Fail is a completed advisory run, not an HTTP failure, and not a gate.
        assert body["ok"] is True
        assert body["advisory"] is True
        assert body["verify"]["status"] == "fail"
        assert body["verify"]["exit_code"] == 7

        diff_req = urllib.request.Request(
            base + "/api/diff",
            headers={"X-Ghost-Run-Token": token},
        )
        with urllib.request.urlopen(diff_req, timeout=5) as resp:
            diff = json.loads(resp.read().decode())
        assert diff["verify"]["status"] == "fail"

        last_req = urllib.request.Request(
            base + "/api/verify",
            headers={"X-Ghost-Run-Token": token},
        )
        with urllib.request.urlopen(last_req, timeout=5) as resp:
            last = json.loads(resp.read().decode())
        assert last["verify"]["exit_code"] == 7

        apply_req = urllib.request.Request(
            base + "/api/apply",
            data=json.dumps({"paths": ["keep.txt"]}).encode(),
            headers={"Content-Type": "application/json", "X-Ghost-Run-Token": token},
            method="POST",
        )
        with urllib.request.urlopen(apply_req, timeout=5) as resp:
            applied = json.loads(resp.read().decode())
        assert applied["ok"] is True
        assert (root / "keep.txt").read_text(encoding="utf-8") == "changed\n"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_demo_script_verify_beat_is_advisory_and_before_apply():
    """run_demo.sh runs the CLI verify path, records Pass then Fail, then Apply."""
    script = (ROOT / "demo" / "run_demo.sh").read_text(encoding="utf-8")
    assert 'slash_ghost("verify")' in script
    assert "python3 scripts/ghost_verify.py" in script
    assert "VERIFY_PASS" in script
    assert "VERIFY_FAIL" in script
    assert "index.html" in script
    pass_at = script.index("VERIFY_PASS")
    fail_at = script.index("VERIFY_FAIL")
    apply_at = script.index("/api/apply")
    assert pass_at < fail_at < apply_at
    # Fail does not exit the runner before Apply.
    assert "Apply still allowed after Fail" in script
    assert "apply after Verify Fail" in script
    # Screenshot/log stay the existing artifacts (no node_modules install).
    assert "PANE_PNG" in script
    assert "demo-run.log" in script
    assert "npm install" not in script
    assert "node_modules" not in script.split("Beat 6")[1].split("Beat 7")[0]


def test_desktop_pane_has_advisory_verify():
    page = (ROOT / "ghost_run" / "ui_server.py").read_text(encoding="utf-8")
    assert "Verify" in page
    assert "ghost-verify" in page or 'id="verifyBtn"' in page
    assert "Advisory only" in page
    js = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "host.openWorkspace" in js
    assert "openExternal" in js
    assert "fetch(" not in js
