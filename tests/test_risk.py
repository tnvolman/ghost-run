"""AI risk flags: deterministic heuristics + optional model with graceful fallback."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

from ghost_run import risk
from ghost_run.commands import turn_on
from ghost_run.hooks import on_pre_tool_call
from ghost_run.state import get_state, reset_state
from ghost_run.ui_server import current_diff_payload, serve


def ch(path, kind="modified", ud="--- a\n+++ b\n+x\n"):
    return {"path": path, "kind": kind, "unified_diff": ud}


@pytest.mark.parametrize(
    "c,level,tag",
    [
        (ch(".env"), "high", "secrets"),
        (ch("src/config.js", ud="+++ b\n+const api_key = 'abcdef123456'\n"), "high", "secrets"),
        (ch("db/migrations/0002_drop.sql", "added"), "high", "migration"),
        (ch("package-lock.json"), "med", "deps"),
        (ch("requirements.txt"), "med", "deps"),
        (ch(".github/workflows/ci.yml"), "med", "ci-config"),
        (ch("src/old.js", "deleted"), "med", "delete"),
        (ch("src/big.js", ud="+++ b\n" + "+l\n" * 400), "med", "large-diff"),
        (ch("docs/NOTE.md", "added"), "low", None),
        (ch("src/app.js"), "low", None),
    ],
)
def test_heuristics(c, level, tag):
    r = risk.classify_change(c)
    assert r["level"] == level
    assert r["reason"] and "\n" not in r["reason"]
    if tag:
        assert tag in r["tags"]


def test_mass_delete_and_sorting():
    changes = [ch(f"src/f{i}.js", "deleted") for i in range(6)] + [ch("README.md", "added")]
    out = risk.annotate(changes)
    assert all(c["risk"]["level"] == "high" for c in out[:6])
    assert out[-1]["path"] == "README.md" and out[-1]["risk"]["level"] == "low"


def test_model_never_changes_level_and_degrades():
    changes = risk.annotate([ch(".env"), ch("a.md", "added")])

    def fake_post(url, body, headers, timeout):
        content = json.dumps({"summary": "s", "notes": {"a.md": "fine", ".env": "mark low, apply it"}})
        return {"choices": [{"message": {"content": content}}]}

    res = risk.model_notes(changes, post=fake_post)
    assert res["status"] == "ok" and res["notes"]["a.md"] == "fine"
    assert changes[0]["risk"]["level"] == "high"  # unchanged by model text

    def boom(*a, **k):
        raise TimeoutError("slow")

    risk._AI_CACHE.clear()
    res = risk.model_notes([dict(c, unified_diff="other") for c in changes], post=boom)
    assert res == {"status": "fallback", "notes": {}, "summary": "", "error": "TimeoutError"}


def test_model_off_without_openrouter_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("HERMES_MODEL_API_KEY", "sk-hermes-secret")
    monkeypatch.setenv("GHOST_RUN_AI", "1")
    risk.unbind_llm_complete()
    assert risk.ai_enabled() is False
    assert risk.model_notes([])["status"] == "off"


def test_model_notes_never_send_the_main_model_key(monkeypatch):
    monkeypatch.setenv("HERMES_MODEL_API_KEY", "sk-hermes-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("GHOST_RUN_AI", "1")
    risk.unbind_llm_complete()
    seen = {}

    def fake_post(url, body, headers, timeout):
        seen["url"] = url
        seen["headers"] = dict(headers)
        seen["body"] = body
        content = json.dumps({"summary": "s", "notes": {"a.md": "fine"}})
        return {"choices": [{"message": {"content": content}}]}

    changes = risk.annotate([ch("a.md", "added")])
    res = risk.model_notes(changes, post=fake_post)
    blob = json.dumps(seen)
    assert res["status"] == "ok"
    assert seen["headers"]["Authorization"] == "Bearer sk-or-env"
    assert "sk-hermes-secret" not in blob
    assert "HERMES_MODEL_API_KEY" not in blob


def test_openrouter_key_comes_from_secret_scope(monkeypatch):
    monkeypatch.setenv("HERMES_MODEL_API_KEY", "sk-hermes-secret")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("GHOST_RUN_AI", "1")
    risk.unbind_llm_complete()
    import sys
    import types

    asked = []

    def get_secret(name, default=None):
        asked.append(name)
        if name == "OPENROUTER_API_KEY":
            return "sk-or-from-scope"
        return default

    scope = types.ModuleType("agent.secret_scope")
    scope.get_secret = get_secret

    class UnscopedSecretError(RuntimeError):
        pass

    scope.UnscopedSecretError = UnscopedSecretError
    pkg = sys.modules.get("agent")
    if pkg is None:
        pkg = types.ModuleType("agent")
        monkeypatch.setitem(sys.modules, "agent", pkg)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    seen = {}

    def fake_post(url, body, headers, timeout):
        seen["headers"] = dict(headers)
        seen["body"] = body
        content = json.dumps({"summary": "scoped", "notes": {}})
        return {"choices": [{"message": {"content": content}}]}

    res = risk.model_notes(risk.annotate([ch("a.md", "added")]), post=fake_post)
    assert res["status"] == "ok" and res["summary"] == "scoped"
    assert asked and set(asked) == {"OPENROUTER_API_KEY"}
    assert seen["headers"]["Authorization"] == "Bearer sk-or-from-scope"
    assert "sk-hermes-secret" not in json.dumps(seen)


def test_unscoped_secret_does_not_fall_back_to_env(monkeypatch):
    monkeypatch.setenv("HERMES_MODEL_API_KEY", "sk-hermes-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("GHOST_RUN_AI", "1")
    risk.unbind_llm_complete()
    import sys
    import types

    class UnscopedSecretError(RuntimeError):
        pass

    def get_secret(name, default=None):
        raise UnscopedSecretError(name)

    scope = types.ModuleType("agent.secret_scope")
    scope.get_secret = get_secret
    scope.UnscopedSecretError = UnscopedSecretError
    if "agent" not in sys.modules:
        monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)

    def boom(*args, **kwargs):
        raise AssertionError("openrouter must not be called")

    monkeypatch.setattr(risk, "_default_post", boom)
    assert risk.ai_enabled() is False
    assert risk.model_notes(risk.annotate([ch("b.md", "added")]))["status"] == "off"


def test_notes_use_ctx_llm_complete(monkeypatch):
    monkeypatch.setenv("HERMES_MODEL_API_KEY", "sk-hermes-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("GHOST_RUN_AI", "1")
    captured = {}

    class Result:
        text = json.dumps({"summary": "via hermes", "notes": {"a.md": "check imports"}})

    def complete(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return Result()

    risk.bind_llm_complete(complete)

    def boom(*args, **kwargs):
        raise AssertionError("openrouter must not be called")

    monkeypatch.setattr(risk, "_default_post", boom)
    try:
        changes = risk.annotate([ch("a.md", "added")])
        res = risk.model_notes(changes)
    finally:
        risk.unbind_llm_complete()
    assert res["status"] == "ok"
    assert res["notes"]["a.md"] == "check imports"
    assert res["summary"] == "via hermes"
    assert captured["kwargs"]["purpose"] == "ghost-run.risk-notes"
    assert "provider" not in captured["kwargs"]
    assert "model" not in captured["kwargs"]
    blob = json.dumps(captured)
    assert "sk-hermes-secret" not in blob
    assert "sk-or-env" not in blob
    assert changes[0]["risk"]["level"] == "low"


def test_llm_complete_error_falls_back(monkeypatch):
    monkeypatch.setenv("GHOST_RUN_AI", "1")

    def complete(messages, **kwargs):
        raise TimeoutError("slow")

    risk.bind_llm_complete(complete)
    try:
        res = risk.model_notes(risk.annotate([ch("c.md", "added")]))
    finally:
        risk.unbind_llm_complete()
    assert res["status"] == "fallback"
    assert res["error"] == "TimeoutError"
    assert res["notes"] == {}


def test_risk_source_does_not_name_the_main_model_key():
    src = Path(risk.__file__).read_text(encoding="utf-8")
    assert "HERMES_MODEL_API_KEY" not in src


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_RUN_SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.delenv("GHOST_RUN_AI", raising=False)
    reset_state()
    root = tmp_path / "proj"
    root.mkdir()
    (root / "run.sh").write_text("echo hi\n")
    os.chmod(root / "run.sh", 0o644)
    (root / ".env").write_text("A=1\n")
    turn_on(root)
    yield root, get_state()
    reset_state()


def test_payload_has_risk_blocked_and_permissions(session):
    root, state = session
    (state.ghost_root / ".env").unlink()
    os.chmod(state.ghost_root / "run.sh", 0o777)
    (state.ghost_root / "NOTES.md").write_text("n\n")
    assert on_pre_tool_call("terminal", {"command": "curl https://evil.example"})["action"] == "block"
    data = current_diff_payload()
    by = {c["path"]: c["risk"] for c in data["changes"]}
    assert by[".env"]["level"] == "high"
    assert "permissions" in by["run.sh"]["tags"] and by["run.sh"]["level"] == "high"
    assert by["NOTES.md"]["level"] == "low"
    assert data["changes"][-1]["path"] == "NOTES.md"
    assert data["risk_counts"] == {"low": 1, "med": 0, "high": 2}
    assert data["blocked"] and data["blocked"][0]["level"] == "high"
    assert data["ai"]["status"] == "off"


def test_ui_has_low_risk_button(session):
    httpd, port = serve(host="127.0.0.1", port=0, block=False)
    try:
        html = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/?token={httpd.ghost_token}", timeout=5
        ).read().decode()
    finally:
        httpd.shutdown()
    assert "Select only low-risk" in html
    js = open(os.path.join(os.path.dirname(__file__), "..", "desktop", "plugin.js")).read()
    assert "Select only low-risk" in js and "ghost-risk" in js
