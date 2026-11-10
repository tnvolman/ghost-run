"""Security boundaries: UI auth, apply containment, copy strategy, verify gate."""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ghost_run.apply import apply_paths
from ghost_run.commands import turn_on
from ghost_run.hooks import on_pre_tool_call
from ghost_run.middleware import on_tool_request
from ghost_run.sandbox import create_ghost_copy, discard_ghost_copy
from ghost_run.state import get_state, pop_pending_block, set_pending_block
from ghost_run.ui_server import DESKTOP_RENDERER_ORIGIN, serve
from ghost_run.verify import (
    NOT_APPROVED,
    prepare_verify_command,
    remember_approval,
    run_verify,
    scrub_child_env,
)


def _py(code: str) -> str:
    return f"{sys.executable} -c {json.dumps(code)}"


def test_ui_requires_token_and_loopback_host(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_RUN_SESSION_FILE", str(tmp_path / "session.json"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    turn_on(root)
    httpd, port = serve(host="127.0.0.1", port=0, block=False)
    token = httpd.ghost_token
    try:
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/diff", timeout=5)
        assert missing.value.code == 401

        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health?token=not-the-token",
        )
        with pytest.raises(urllib.error.HTTPError) as wrong:
            urllib.request.urlopen(bad, timeout=5)
        assert wrong.value.code == 401

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            f"/api/health?token={token}",
            headers={"Host": "evil.example"},
        )
        denied = conn.getresponse()
        raw_headers = {key.lower(): value for key, value in denied.getheaders()}
        assert denied.status == 403
        assert "access-control-allow-origin" not in raw_headers
        denied.read()
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            f"/api/health?token={token}",
            headers={"Host": f"127.0.0.1:{port + 1}"},
        )
        wrong_port = conn.getresponse()
        assert wrong_port.status == 403
        wrong_port.read()
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            "/api/health",
            headers={
                "Host": f"localhost:{port}",
                "X-Ghost-Run-Token": token,
            },
        )
        allowed = conn.getresponse()
        body = allowed.read()
        header_names = {key.lower() for key, _value in allowed.getheaders()}
        assert allowed.status == 200
        assert b"ghost-run-ui" in body
        assert "access-control-allow-origin" not in header_names
        conn.close()

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health?token={token}", timeout=5
        ) as resp:
            payload = json.loads(resp.read().decode())
            names = {key.lower() for key in resp.headers}
        assert payload["ok"] is True
        assert "access-control-allow-origin" not in names
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_ui_refuses_non_loopback_bind():
    with pytest.raises(ValueError, match="127.0.0.1"):
        serve(host="0.0.0.0", port=0, block=False)


def test_apply_refuses_paths_outside_project(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "keep.txt").write_text("keep\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    turn_on(root)
    state = get_state()
    assert state.ghost_root is not None
    (state.ghost_root / "src" / "keep.txt").write_text("ghost\n", encoding="utf-8")
    (state.ghost_root / "escape.txt").symlink_to(outside)

    actions = apply_paths(
        state,
        ["../outside.txt", str(outside), "src/../../outside.txt", "escape.txt", "src/keep.txt"],
    )
    assert outside.read_text(encoding="utf-8") == "secret\n"
    assert not (root / "escape.txt").exists()
    assert (root / "src" / "keep.txt").read_text(encoding="utf-8") == "ghost\n"
    refused = [line for line in actions if line.startswith("refused")]
    assert len(refused) >= 3
    assert any("src/keep.txt" in line and "applied" in line for line in actions)

    # A deletion path must not unlink a file outside the project.
    actions = apply_paths(state, ["../outside.txt"])
    assert outside.exists()
    assert any("refused" in line for line in actions)


def test_copy_never_calls_cp_link(tmp_path: Path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    calls = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    ghost = create_ghost_copy(root, strategy="auto")
    try:
        for cmd in calls:
            assert "--link" not in cmd
            assert "-al" not in cmd
        src = (root / "a.txt").stat()
        copied = (ghost / "a.txt").stat()
        assert src.st_ino != copied.st_ino
    finally:
        discard_ghost_copy(ghost)


def test_verify_blocks_until_approved_and_scrubs_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GHOST_RUN_TEST", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-secret")
    monkeypatch.setenv("HERMES_MODEL_API_KEY", "sk-hermes-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    turn_on(root)
    state = get_state()
    assert state.ghost_root is not None
    code = (
        "import json, os, pathlib; "
        "pathlib.Path('env.json').write_text(json.dumps({"
        "'openrouter': os.environ.get('OPENROUTER_API_KEY'),"
        "'hermes': os.environ.get('HERMES_MODEL_API_KEY'),"
        "'aws': os.environ.get('AWS_SECRET_ACCESS_KEY'),"
        "'gh': os.environ.get('GITHUB_TOKEN'),"
        "'path': os.environ.get('PATH')"
        "}))"
    )
    command = _py(code)
    monkeypatch.setenv("GHOST_RUN_TEST", command)

    denied = run_verify(state, timeout=20)
    assert denied["status"] == "error"
    assert denied["error"] == NOT_APPROVED
    assert not (state.ghost_root / "env.json").exists()
    assert not (root / "env.json").exists()

    remember_approval(root, command)
    seen = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        seen["shell"] = kwargs.get("shell")
        seen["env"] = kwargs.get("env")
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    passed = run_verify(state, timeout=20)
    assert passed["status"] == "pass", passed
    assert seen["shell"] is False
    assert "OPENROUTER_API_KEY" not in seen["env"]
    assert "HERMES_MODEL_API_KEY" not in seen["env"]
    dumped = json.loads((state.ghost_root / "env.json").read_text(encoding="utf-8"))
    assert dumped["openrouter"] is None
    assert dumped["hermes"] is None
    assert dumped["aws"] is None
    assert dumped["gh"] is None
    assert dumped["path"]

    monkeypatch.setenv("GHOST_RUN_TEST", _py("print('other')"))
    changed = run_verify(state, timeout=20)
    assert changed["status"] == "error"
    assert "not approved" in changed["error"]


def test_hermes_approval_is_once_per_project_command(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GHOST_RUN_TEST", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.txt").write_text("k\n", encoding="utf-8")
    turn_on(root)
    state = get_state()
    monkeypatch.setenv("GHOST_RUN_TEST", _py("print('ok')"))
    calls = []

    def fake_hermes(command, description):
        calls.append((command, description))
        return "always"

    monkeypatch.setattr("ghost_run.verify._hermes_prompt", fake_hermes)
    first = run_verify(state, timeout=20)
    assert first["status"] == "pass", first
    assert calls and "project" in calls[0][1].lower()
    second = run_verify(state, timeout=20)
    assert second["status"] == "pass"
    assert len(calls) == 1


def test_verify_once_does_not_stick(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GHOST_RUN_TEST", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.txt").write_text("k\n", encoding="utf-8")
    turn_on(root)
    state = get_state()
    monkeypatch.setenv("GHOST_RUN_TEST", _py("print('once')"))
    monkeypatch.setattr("ghost_run.verify.prompt_verify_approval", lambda root, command: "once")
    assert run_verify(state, timeout=20)["status"] == "pass"
    monkeypatch.setattr("ghost_run.verify.prompt_verify_approval", lambda root, command: "deny")
    again = run_verify(state, timeout=20)
    assert again["status"] == "error"
    assert "not approved" in again["error"]


def test_shell_operators_still_require_a_shell():
    argv, use_shell = prepare_verify_command("python3 scripts/ghost_verify.py")
    assert use_shell is False
    assert argv[0] == "python3"
    _argv, piped = prepare_verify_command("echo a && echo b")
    assert piped is True


def test_scrub_child_env_drops_secret_names():
    cleaned = scrub_child_env(
        {
            "PATH": "/usr/bin",
            "OPENROUTER_API_KEY": "secret",
            "HOME": "/home/me",
            "CUSTOM_TOKEN": "nope",
            "GHOST_RUN_TEST": "echo hi",
        }
    )
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["HOME"] == "/home/me"
    assert "OPENROUTER_API_KEY" not in cleaned
    assert "CUSTOM_TOKEN" not in cleaned
    assert "GHOST_RUN_TEST" not in cleaned


def test_bypass_tools_are_blocked(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    turn_on(root)
    for name in ("execute_code", "send_message", "delegate_task", "cronjob"):
        blocked = on_pre_tool_call(tool_name=name, args={"code": "print(1)"})
        assert blocked and blocked["action"] == "block"
        assert name in blocked["message"]


def test_concurrent_tool_calls_keep_their_own_block(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "notes.txt").write_text("real\n", encoding="utf-8")
    outside_a = tmp_path / "a.txt"
    outside_b = tmp_path / "b.txt"
    outside_a.write_text("a\n", encoding="utf-8")
    outside_b.write_text("b\n", encoding="utf-8")
    turn_on(root)
    on_tool_request(
        tool_name="write_file",
        args={"path": str(outside_a), "content": "nope"},
        tool_call_id="call-a",
    )
    on_tool_request(
        tool_name="write_file",
        args={"path": str(outside_b), "content": "nope"},
        tool_call_id="call-b",
    )
    block_a = on_pre_tool_call(
        tool_name="write_file",
        args={"path": str(outside_a), "content": "nope"},
        tool_call_id="call-a",
    )
    block_b = on_pre_tool_call(
        tool_name="write_file",
        args={"path": str(outside_b), "content": "nope"},
        tool_call_id="call-b",
    )
    assert block_a and block_a["action"] == "block"
    assert block_b and block_b["action"] == "block"
    assert str(outside_a) in block_a["message"]
    assert str(outside_b) in block_b["message"]
    assert outside_a.read_text(encoding="utf-8") == "a\n"
    assert outside_b.read_text(encoding="utf-8") == "b\n"

    import threading

    results = {}

    def worker(name: str) -> None:
        set_pending_block("msg-" + name)
        results[name] = pop_pending_block()

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == {"one": "msg-one", "two": "msg-two"}


def _raw(port: int, method: str, path: str, headers: dict, body: bytes = b""):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    payload = resp.read()
    header_map = {key.lower(): value for key, value in resp.getheaders()}
    conn.close()
    return resp.status, header_map, payload


def test_desktop_origin_cors_load_and_apply(tmp_path, monkeypatch):
    """Simulate the Desktop renderer's Origin and run Load (GET diff) plus Apply."""
    monkeypatch.setenv("GHOST_RUN_SESSION_FILE", str(tmp_path / "session.json"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    turn_on(root)
    state = get_state()
    assert state.ghost_root is not None
    (state.ghost_root / "keep.txt").write_text("ghost\n", encoding="utf-8")
    httpd, port = serve(host="127.0.0.1", port=0, block=False)
    token = httpd.ghost_token
    origin = DESKTOP_RENDERER_ORIGIN
    assert origin == "null"
    try:
        status, headers, payload = _raw(
            port,
            "GET",
            f"/api/diff?token={token}",
            {"Host": f"127.0.0.1:{port}", "Origin": origin},
        )
        assert status == 200
        assert headers.get("access-control-allow-origin") == origin
        assert "access-control-allow-credentials" not in headers
        diff = json.loads(payload)
        assert diff["ok"] is True
        assert any(row["path"] == "keep.txt" for row in diff["changes"])

        pre_status, pre_headers, _pre_body = _raw(
            port,
            "OPTIONS",
            f"/api/apply?token={token}",
            {
                "Host": f"127.0.0.1:{port}",
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert pre_status == 204
        assert pre_headers.get("access-control-allow-origin") == origin
        assert "POST" in pre_headers.get("access-control-allow-methods", "")
        assert "content-type" in pre_headers.get("access-control-allow-headers", "").lower()

        denied, denied_headers, _denied_body = _raw(
            port,
            "OPTIONS",
            "/api/apply",
            {
                "Host": f"127.0.0.1:{port}",
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert denied == 401

        apply_body = json.dumps({"paths": ["keep.txt"]}).encode()
        applied, apply_headers, apply_payload = _raw(
            port,
            "POST",
            f"/api/apply?token={token}",
            {
                "Host": f"127.0.0.1:{port}",
                "Origin": origin,
                "Content-Type": "application/json",
            },
            apply_body,
        )
        assert applied == 200
        assert apply_headers.get("access-control-allow-origin") == origin
        assert json.loads(apply_payload)["ok"] is True
        assert (root / "keep.txt").read_text(encoding="utf-8") == "ghost\n"

        missing, _missing_headers, _missing_body = _raw(
            port,
            "POST",
            "/api/apply",
            {
                "Host": f"127.0.0.1:{port}",
                "Origin": origin,
                "Content-Type": "application/json",
            },
            apply_body,
        )
        assert missing == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_non_desktop_origins_get_no_cors_header(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_RUN_SESSION_FILE", str(tmp_path / "session.json"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    turn_on(root)
    httpd, port = serve(host="127.0.0.1", port=0, block=False)
    token = httpd.ghost_token
    try:
        for origin in ("https://evil.example", "http://127.0.0.1:47891", "file:///opt/hermes/index.html", "*"):
            status, headers, _payload = _raw(
                port,
                "GET",
                f"/api/health?token={token}",
                {"Host": f"127.0.0.1:{port}", "Origin": origin},
            )
            assert status == 200
            assert "access-control-allow-origin" not in headers
            pre_status, pre_headers, _pre_body = _raw(
                port,
                "OPTIONS",
                f"/api/apply?token={token}",
                {
                    "Host": f"127.0.0.1:{port}",
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert pre_status == 204
            assert "access-control-allow-origin" not in pre_headers
            assert "access-control-allow-private-network" not in pre_headers
    finally:
        httpd.shutdown()
        httpd.server_close()
