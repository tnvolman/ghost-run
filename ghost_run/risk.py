"""AI risk flags for pending ghost changes.

Deterministic heuristics first: every change gets ``level`` (low|med|high)
and a one-line plain-English ``reason``. An optional model summary may add
an ``ai_note`` per file, but it never changes the level and never selects
or applies anything. Any model timeout or error degrades silently to the
heuristics.

Opt-in with ``GHOST_RUN_AI=1``. Inside Hermes the notes go through
``ctx.llm.complete`` so the host keeps the user's provider credentials.
Outside Hermes, the only key read is ``OPENROUTER_API_KEY`` via
``agent.secret_scope.get_secret`` (or that env var when Hermes is not
installed). The main model credential is never read and never sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

LEVELS = {"low": 0, "med": 1, "high": 2}
MASS_DELETE_THRESHOLD = 5
LARGE_DIFF_LINES = 300

_SECRET_NAMES = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*|\.npmrc|\.pypirc|"
    r"credentials(\.json)?|secrets?\.(ya?ml|json|toml)|\.netrc)$",
    re.I,
)
_SECRET_CONTENT = re.compile(
    r"^\+.*(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|sk-[A-Za-z0-9_-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{30,}|(api[_-]?key|secret|password|token)\s*[:=]\s*\S{6,})",
    re.I | re.M,
)
_LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "Gemfile.lock", "composer.lock", "go.sum", "uv.lock",
}
_DEPS = {
    "package.json", "requirements.txt", "pyproject.toml", "Pipfile", "Cargo.toml",
    "Gemfile", "go.mod", "composer.json", "setup.py", "setup.cfg",
}
_CI = re.compile(
    r"(^|/)(\.github/|\.gitlab-ci\.yml|\.circleci/|Jenkinsfile|azure-pipelines|"
    r"Dockerfile|docker-compose|\.?vercel\.json|netlify\.toml|wrangler\.toml|"
    r"Makefile$|\.pre-commit-config|tsconfig.*\.json$|\.eslintrc|plugin\.yaml$|"
    r".*\.(tf|tfvars)$|k8s/|helm/)",
    re.I,
)
_MIGRATION = re.compile(r"(^|/)(migrations?|alembic|db/migrate|prisma/migrations)/|\.sql$", re.I)


def _mode(p: Path) -> Optional[int]:
    try:
        return stat.S_IMODE(p.stat().st_mode)
    except OSError:
        return None


def _changed_lines(ud: str) -> int:
    return sum(
        1 for ln in (ud or "").splitlines()
        if (ln.startswith("+") and not ln.startswith("+++"))
        or (ln.startswith("-") and not ln.startswith("---"))
    )


def classify_change(
    ch: Dict[str, Any],
    *,
    real_root: Optional[Path] = None,
    ghost_root: Optional[Path] = None,
    mass_delete: bool = False,
) -> Dict[str, Any]:
    """Return ``{"level", "reason", "tags"}`` for one change dict."""
    path = ch["path"]
    kind = ch["kind"]
    ud = ch.get("unified_diff") or ""
    name = PurePosixPath(path).name
    hits: List[tuple] = []  # (level, tag, reason)

    if _SECRET_NAMES.search(path):
        hits.append(("high", "secrets", f"Touches a secrets file ({name}); could leak or wipe credentials."))
    elif kind != "deleted" and _SECRET_CONTENT.search(ud):
        hits.append(("high", "secrets", "Adds what looks like a key, token, or password."))
    if _MIGRATION.search(path):
        hits.append(("high", "migration", "Database migration; can change or drop real data."))
    if kind == "deleted" and mass_delete:
        hits.append(("high", "mass-delete", "Part of a mass delete; many files removed at once."))
    if name in _LOCKFILES:
        hits.append(("med", "deps", f"Lockfile {name} changed; installed versions will shift."))
    elif name in _DEPS:
        hits.append(("med", "deps", f"Dependency manifest {name} changed."))
    if _CI.search(path):
        hits.append(("med", "ci-config", "CI, build, or deploy config; affects how code ships."))
    if real_root and ghost_root and kind == "modified":
        a, b = _mode(real_root / path), _mode(ghost_root / path)
        if a is not None and b is not None and a != b:
            lvl = "high" if (b & 0o002) or (b & 0o111 and not a & 0o111) else "med"
            hits.append((lvl, "permissions", f"Permissions change {oct(a)} → {oct(b)}."))
    n = _changed_lines(ud)
    if n >= LARGE_DIFF_LINES:
        hits.append(("med", "large-diff", f"Large change ({n} lines); hard to review by eye."))
    if kind == "deleted" and not mass_delete:
        hits.append(("med", "delete", f"Deletes {name}; check nothing still imports it."))

    if not hits:
        reason = {
            "added": "New file; nothing existing is overwritten.",
            "modified": f"Small edit ({n} lines) to an ordinary file.",
            "deleted": "Deletes a file.",
        }.get(kind, "Ordinary change.")
        return {"level": "low", "reason": reason, "tags": []}
    hits.sort(key=lambda h: -LEVELS[h[0]])
    return {"level": hits[0][0], "reason": hits[0][2], "tags": [h[1] for h in hits]}


def blocked_log_path() -> Path:
    from .state import _default_session_path

    return _default_session_path().parent / "blocked.jsonl"


def record_block(tool_name: str, message: str) -> None:
    """Append one blocked tool attempt (network/terminal/outside-root)."""
    try:
        p = blocked_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "tool": tool_name, "message": message}) + "\n")
    except OSError:
        pass


def clear_blocks() -> None:
    try:
        blocked_log_path().unlink()
    except OSError:
        pass


def read_blocks() -> List[Dict[str, Any]]:
    p = blocked_log_path()
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def blocked_flags() -> List[Dict[str, Any]]:
    flags = []
    for b in read_blocks():
        msg = str(b.get("message", ""))
        flags.append({
            "level": "high",
            "tool": b.get("tool", ""),
            "reason": "Agent tried a blocked action: " + msg.replace("ghost-run: ", "", 1),
        })
    return flags


def annotate(changes: List[Dict[str, Any]], real_root=None, ghost_root=None) -> List[Dict[str, Any]]:
    """Add risk fields in place and return changes sorted high → low, then path."""
    deletes = sum(1 for c in changes if c["kind"] == "deleted")
    mass = deletes >= MASS_DELETE_THRESHOLD
    for c in changes:
        c["risk"] = classify_change(c, real_root=real_root, ghost_root=ghost_root, mass_delete=mass)
    changes.sort(key=lambda c: (-LEVELS[c["risk"]["level"]], c["path"]))
    return changes


def summarize_counts(changes: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"low": 0, "med": 0, "high": 0}
    for c in changes:
        out[c["risk"]["level"]] += 1
    return out


# ---------------------------------------------------------------- optional model
_AI_CACHE: Dict[str, Dict[str, Any]] = {}
DEFAULT_MODEL = "openrouter/free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_LLM_PURPOSE = "ghost-run.risk-notes"

# Bound from register(ctx) when Hermes exposes ctx.llm.complete.
_llm_complete: Optional[Any] = None


def bind_llm_complete(complete: Any) -> None:
    """Use the host LLM for advisory notes. The callable must not need a key."""
    global _llm_complete
    _llm_complete = complete


def unbind_llm_complete() -> None:
    global _llm_complete
    _llm_complete = None


def _ai_flag() -> bool:
    return os.environ.get("GHOST_RUN_AI", "").lower() in ("1", "true", "yes")


def _openrouter_api_key() -> Optional[str]:
    """Return only the OpenRouter key. Never the main Hermes model key.

    Hermes installs resolve the name through ``agent.secret_scope.get_secret``
    so a multiplexed profile cannot borrow another profile's env. With no
    Hermes install, the same name is read from the environment.
    """
    try:
        from agent.secret_scope import UnscopedSecretError, get_secret
    except ImportError:
        value = os.environ.get("OPENROUTER_API_KEY")
        return value.strip() if isinstance(value, str) and value.strip() else None
    try:
        value = get_secret("OPENROUTER_API_KEY")
    except UnscopedSecretError:
        return None
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def ai_enabled() -> bool:
    if not _ai_flag():
        return False
    if _llm_complete is not None:
        return True
    return bool(_openrouter_api_key())


def _completion_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(result, dict):
        raw = result.get("text")
        if isinstance(raw, str):
            return raw
    raise TypeError("llm complete returned no text")


def _call_llm(messages: List[Dict[str, str]], timeout: float) -> str:
    complete = _llm_complete
    if complete is None:
        raise RuntimeError("no llm complete")
    kwargs: Dict[str, Any] = {
        "messages": messages,
        "temperature": 0,
        "timeout": timeout,
        "purpose": _LLM_PURPOSE,
    }
    try:
        result = complete(**kwargs)
    except TypeError:
        kwargs.pop("purpose", None)
        result = complete(**kwargs)
    return _completion_text(result)


def _default_post(url: str, body: Dict[str, Any], headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def model_notes(
    changes: List[Dict[str, Any]],
    *,
    timeout: float = 8.0,
    post=None,
) -> Dict[str, Any]:
    """Ask the model for one-line notes per path. Never raises.

    Returns ``{"status": "ok"|"off"|"fallback", "notes": {path: str}, "summary": str}``.
    The model output is advisory text only; levels and selection stay heuristic.
    """
    if not ai_enabled() and post is None:
        return {"status": "off", "notes": {}, "summary": ""}
    digest = hashlib.sha256(
        json.dumps([(c["path"], c["kind"], c.get("unified_diff", "")[:2000]) for c in changes]).encode()
    ).hexdigest()
    if digest in _AI_CACHE:
        return _AI_CACHE[digest]
    items = [
        {"path": c["path"], "kind": c["kind"], "heuristic": c["risk"]["level"],
         "diff": (c.get("unified_diff") or "")[:1200]}
        for c in changes[:40]
    ]
    prompt = (
        "You review file changes an AI agent made in a sandbox. For each path, write one short "
        "plain-English sentence on what could go wrong if applied. Reply with JSON only: "
        '{"summary": "<one sentence>", "notes": {"<path>": "<sentence>"}}.\n\n' + json.dumps(items)
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        if _llm_complete is not None and post is None:
            text = _call_llm(messages, timeout)
        else:
            key = _openrouter_api_key()
            if post is None and not key:
                return {"status": "off", "notes": {}, "summary": ""}
            body = {
                "model": os.environ.get("GHOST_RUN_MODEL", DEFAULT_MODEL),
                "messages": messages,
                "temperature": 0,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + (key or ""),
            }
            data = (post or _default_post)(OPENROUTER_URL, body, headers, timeout)
            text = data["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(match.group(0) if match else text)
        notes = {
            str(k): str(v)[:240]
            for k, v in (parsed.get("notes") or {}).items()
            if any(c["path"] == k for c in changes)
        }
        result = {"status": "ok", "notes": notes, "summary": str(parsed.get("summary", ""))[:300]}
    except Exception as exc:  # timeout, HTTP, parse — degrade to heuristics
        return {"status": "fallback", "notes": {}, "summary": "", "error": type(exc).__name__}
    _AI_CACHE[digest] = result
    return result
