#!/usr/bin/env python3
"""Catalog-style local validator for Ghost Run (works without hermes plugins validate).

Mirrors admission checks from Hermes docs / plugin_validate:
  - plugin.yaml present with name/version/description
  - declared provides_* match what register() actually registers
  - no self-update language
  - __init__.py has register(ctx)
  - no core-override imports in plugin code
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
WARNINGS: list[str] = []


def err(msg: str) -> None:
    ERRORS.append(msg)


def warn(msg: str) -> None:
    WARNINGS.append(msg)


def load_manifest() -> dict:
    path = ROOT / "plugin.yaml"
    if not path.exists():
        err("missing plugin.yaml")
        return {}
    text = path.read_text(encoding="utf-8")
    if yaml:
        return yaml.safe_load(text) or {}
    err("PyYAML required for validate_local")
    return {}


def check_register_matches(manifest: dict) -> None:
    init = ROOT / "__init__.py"
    if not init.exists():
        err("missing __init__.py")
        return
    src = init.read_text(encoding="utf-8")
    if "def register(" not in src:
        err("register(ctx) not found in __init__.py")

    declared_hooks = set(manifest.get("provides_hooks") or [])
    declared_mw = set(manifest.get("provides_middleware") or [])
    registered_hooks = set(re.findall(r'register_hook\(\s*["\']([\w]+)["\']', src))
    registered_mw = set(re.findall(r'register_middleware\(\s*["\']([\w]+)["\']', src))

    if declared_hooks != registered_hooks:
        err(
            f"provides_hooks {sorted(declared_hooks)} != registered {sorted(registered_hooks)}"
        )
    if declared_mw and declared_mw != registered_mw:
        err(
            f"provides_middleware {sorted(declared_mw)} != registered {sorted(registered_mw)}"
        )
    if "register_command" in src and "ghost" not in src:
        warn("register_command present but 'ghost' not found in __init__.py")
    if re.search(r"self.?update|auto.?update|hermes update", src, re.I):
        err("self-update language found in __init__.py")

    skip_parts = {"tests", "scripts", ".venv", ".git", "__pycache__", ".pytest_cache"}
    for py in ROOT.rglob("*.py"):
        if skip_parts.intersection(set(py.parts)):
            continue
        if py.name.startswith("."):
            continue
        body = py.read_text(encoding="utf-8", errors="replace")
        if "allow_tool_override" in body:
            err(f"allow_tool_override referenced in {py.relative_to(ROOT)}")
        if re.search(r"sys\.modules\[.agent\.", body):
            warn(f"possible core poke in {py.relative_to(ROOT)}")


def check_license(manifest: dict) -> None:
    if not (ROOT / "LICENSE").exists():
        err("missing LICENSE")
    lic = str(manifest.get("license") or "")
    if lic and lic.upper() != "MIT":
        warn(f"manifest license is {lic!r}, expected MIT for this scaffold")


def main() -> int:
    manifest = load_manifest()
    if manifest:
        for key in ("name", "version", "description"):
            if not manifest.get(key):
                err(f"plugin.yaml missing {key}")
        if manifest.get("name") != "ghost-run":
            warn(f"name is {manifest.get('name')!r}")
    check_register_matches(manifest)
    check_license(manifest)

    print("Ghost Run local catalog validation")
    print(f"  root: {ROOT}")
    for w in WARNINGS:
        print(f"  WARN: {w}")
    for e in ERRORS:
        print(f"  ERROR: {e}")
    if ERRORS:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
