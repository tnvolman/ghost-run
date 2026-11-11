"""Plugin surface: attribution lock and slash args_hint includes ui."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _plugin_module():
    spec = importlib.util.spec_from_file_location(
        "ghost_run_plugin_entry", ROOT / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ctx:
    def __init__(self) -> None:
        self.command = None

    def register_middleware(self, *args, **kwargs) -> None:
        return None

    def register_hook(self, *args, **kwargs) -> None:
        return None

    def register_command(self, name, handler, description="", args_hint="") -> None:
        self.command = {
            "name": name,
            "description": description,
            "args_hint": args_hint,
        }

    def register_cli_command(self, *args, **kwargs) -> None:
        return None


def test_register_binds_host_llm_complete():
    from ghost_run import risk

    ctx = _Ctx()

    class LLM:
        def complete(self, messages, **kwargs):
            return "ok"

    ctx.llm = LLM()
    _plugin_module().register(ctx)
    try:
        bound = risk._llm_complete
        assert bound is not None
        assert bound.__self__ is ctx.llm
        assert bound.__func__ is LLM.complete
    finally:
        risk.unbind_llm_complete()


def test_args_hint_includes_ui_with_existing_subcommands():
    ctx = _Ctx()
    _plugin_module().register(ctx)
    assert ctx.command is not None
    hint = ctx.command["args_hint"]
    for token in ("on", "off", "status", "diff", "verify", "apply", "discard", "ui"):
        assert token in hint
    assert ctx.command["description"].rstrip().endswith("ui")


def test_attribution_and_homepage():
    plugin = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert 'author: "Michael McClanahan (@tnvolman)"' in plugin
    assert 'homepage: "https://github.com/tnvolman/ghost-run"' in plugin
    assert 'name = "Michael McClanahan (@tnvolman)"' in project
    assert "Copyright (c) 2026 Michael McClanahan" in license_text
    for banned in ("Journey Digital", "TNVOLMAN Holdings"):
        assert banned not in plugin
        assert banned not in license_text


def test_demo_run_leftovers_are_untracked():
    gitignore = (ROOT / "demo" / ".gitignore").read_text(encoding="utf-8")
    assert "*.log" in gitignore
    assert "pane.png" in gitignore
    assert "*-sha.json" in gitignore
    tracked = subprocess.check_output(
        ["git", "ls-files", "demo"], cwd=ROOT, text=True
    ).splitlines()
    leftovers = {
        "demo/pane.png",
        "demo/demo-run.log",
        "demo/agent-raw.log",
        "demo/live2-agent.log",
        "demo/network-agent.log",
        "demo/before-sha.json",
        "demo/after-agent-sha.json",
        "demo/after-apply-sha.json",
    }
    assert leftovers.isdisjoint(tracked)
    # Historical write-up and the fixture stay tracked.
    assert "demo/live-run.md" in tracked
    assert "demo/fixture/scripts/ghost_verify.py" in tracked
    assert "demo/run_demo.sh" in tracked
