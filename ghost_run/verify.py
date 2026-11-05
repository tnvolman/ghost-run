"""Run one configured test command inside the ghost copy (advisory only).

Command resolution order:
  1. Environment variable ``GHOST_RUN_TEST``
  2. First non-empty, non-comment line of ``.ghost-run-test`` at the real root
  3. Otherwise a clear error: ``no test command configured``

The command line is read from the real project (or the environment), then
executed with cwd set to the ghost sandbox. Apply is never blocked on the
result — Pass/Fail is evidence for the human.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Set, Tuple

from .state import GhostState, _default_session_path

ENV_VAR = "GHOST_RUN_TEST"
CONFIG_FILENAME = ".ghost-run-test"
NO_TEST_COMMAND = "no test command configured"
NOT_APPROVED = (
    "verify not approved for this project; refusing to run the repo-chosen command. "
    "Approve once in a terminal (`hermes ghost verify`) or add the project to the "
    "owner allowlist (verify-allow.json next to the Ghost Run session file)."
)

# Child processes get only these names, and never a name that looks like a secret.
_CHILD_ENV_EXACT = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_NUMERIC",
        "LC_TIME",
        "LC_COLLATE",
        "LC_MONETARY",
        "TMPDIR",
        "TMP",
        "TEMP",
        "TERM",
        "TZ",
        "SHELL",
        "PWD",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
        "PYTHONUTF8",
        "PYTHONNOUSERSITE",
        "VIRTUAL_ENV",
        "NODE_PATH",
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
        "WINDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)
_SECRET_ENV_NAME = re.compile(
    r"(API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)",
    re.I,
)
_APPROVED_CHOICES = {"once", "session", "always", "yes", "y"}
_SESSION_APPROVED: Set[Tuple[str, str]] = set()

# Middle of the 60–120s window from the thin-Verify spec.
DEFAULT_TIMEOUT_SEC = 90
# Middle of the 8–16 KiB log cap.
MAX_LOG_BYTES = 12 * 1024


class VerifyError(Exception):
    """Raised when a test command cannot be resolved."""


def _first_command_line(text: str) -> Optional[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped
    return None


def resolve_test_command(
    real_root: Path,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Return the configured test command, or raise ``VerifyError``."""
    env = os.environ if environ is None else environ
    from_env = (env.get(ENV_VAR) or "").strip()
    if from_env:
        return from_env

    path = Path(real_root) / CONFIG_FILENAME
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        from_file = _first_command_line(text)
        if from_file:
            return from_file

    raise VerifyError(NO_TEST_COMMAND)


def truncate_log(text: str, limit: int = MAX_LOG_BYTES) -> Tuple[str, bool]:
    """Cap ``text`` to ``limit`` UTF-8 bytes, keeping a truncation marker."""
    data = (text or "").encode("utf-8", errors="replace")
    if len(data) <= limit:
        return text or "", False
    marker = f"\n…[truncated, {len(data)} bytes total, cap {limit}]\n"
    marker_b = marker.encode("utf-8")
    keep = max(0, limit - len(marker_b))
    clipped = data[:keep].decode("utf-8", errors="ignore")
    return clipped + marker, True


def _combine_output(stdout: str, stderr: str) -> str:
    out = stdout or ""
    err = stderr or ""
    if out and err:
        return out.rstrip("\n") + "\n--- stderr ---\n" + err
    return out or err


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


def approval_store_path() -> Path:
    """Owner allowlist. Lives beside the session file, outside the project."""
    return _default_session_path().parent / "verify-allow.json"


def clear_session_approvals() -> None:
    """Drop in-memory session approvals (tests and process reset)."""
    _SESSION_APPROVED.clear()


def scrub_child_env(source: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Environment for the verify child: interpreter basics, no provider secrets.

    Names matching API keys, tokens, passwords, or other credentials are dropped
    even if they would otherwise be on the allowlist. ``GHOST_RUN_TEST`` is not
    passed through.
    """
    env = os.environ if source is None else source
    cleaned: Dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if _SECRET_ENV_NAME.search(key):
            continue
        if key not in _CHILD_ENV_EXACT:
            continue
        cleaned[key] = value
    return cleaned


def _read_approval_store() -> Dict[str, Any]:
    path = approval_store_path()
    if not path.is_file():
        return {"projects": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"projects": {}}
    if not isinstance(data, dict):
        return {"projects": {}}
    projects = data.get("projects")
    if not isinstance(projects, dict):
        data["projects"] = {}
    return data


def _remembered_command(real_root: Path) -> Optional[str]:
    projects = _read_approval_store().get("projects") or {}
    entry = projects.get(str(Path(real_root).resolve()))
    if isinstance(entry, dict):
        command = entry.get("command")
        if isinstance(command, str) and command.strip():
            return command
    return None


def is_project_approved(real_root: Path, command: str) -> bool:
    """True when this project+command was approved for the process or on disk."""
    root = str(Path(real_root).resolve())
    if (root, command) in _SESSION_APPROVED:
        return True
    return _remembered_command(real_root) == command


def remember_approval(real_root: Path, command: str) -> None:
    """Persist an owner approval for this project and this exact command."""
    path = approval_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_approval_store()
    projects = data.setdefault("projects", {})
    projects[str(Path(real_root).resolve())] = {
        "command": command,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = json.dumps(data, indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _hermes_prompt(command: str, description: str) -> Optional[str]:
    """Ask Hermes' approval prompt when the host provides it.

    Returns None when Hermes is not installed so the caller can fall back.
    A prompt that raises is a deny (fail closed).
    """
    try:
        from tools.approval_prompt import prompt_dangerous_approval
    except Exception:
        return None
    try:
        choice = prompt_dangerous_approval(
            command,
            description,
            allow_permanent=True,
            allow_session=True,
            title="Run Ghost Run verify command?",
        )
    except Exception:
        return "deny"
    if choice is None:
        return "deny"
    return str(choice)


def _local_yes_no(root: str, command: str) -> str:
    print(
        "\nGhost Run verify\n"
        f"  project: {root}\n"
        f"  command: {command}\n"
        "This runs on the host (cwd = the ghost copy). Provider API keys are not passed in.\n"
        "Approve this project for this command from now on? [y/N] ",
        flush=True,
    )
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        return "deny"
    if answer in ("y", "yes"):
        return "always"
    return "deny"


def prompt_verify_approval(root: str, command: str) -> str:
    """Ask once. Hermes approval prompt, then a terminal yes/no, else deny.

    Returns ``once``, ``session``, ``always``, ``yes``/``y``, or ``deny``
    (``timeout`` and ``cancelled`` are not approvals).
    """
    description = (
        f"Ghost Run verify wants to run a command chosen by this project ({root}). "
        "It runs on this machine with cwd set to the ghost copy. "
        "Provider API keys are not passed to the child. "
        "Approve once for this project and this command?"
    )
    hermes = _hermes_prompt(command, description)
    if hermes in ("once", "session", "always", "deny", "timeout"):
        return hermes
    if sys.stdin is not None and sys.stdin.isatty():
        return _local_yes_no(root, command)
    return "deny"


def ensure_verify_approval(real_root: Path, command: str) -> Optional[str]:
    """Return None when this command may run, else an error string. May prompt."""
    root = str(Path(real_root).resolve())
    if is_project_approved(real_root, command):
        return None
    choice = prompt_verify_approval(root, command)
    if choice in ("always", "yes", "y"):
        remember_approval(real_root, command)
        return None
    if choice == "session":
        _SESSION_APPROVED.add((root, command))
        return None
    if choice == "once":
        return None
    return NOT_APPROVED


def _unquoted_shell_ops(command: str) -> bool:
    """True when shell operators appear outside quotes (pipes, redirects, ``$()``)."""
    i = 0
    in_single = False
    in_double = False
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_single:
            i += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if not in_single and not in_double:
            if char in "|&;<>`$" or char == "\n":
                return True
        i += 1
    return False


def prepare_verify_command(command: str) -> Tuple[Any, bool]:
    """Return ``(argv_or_string, shell)``.

    Simple commands run with ``shell=False``. Shell operators outside quotes
    still use the shell, and only after the approval gate. Unbalanced quotes
    are refused.
    """
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise OSError(f"invalid verify command quoting: {exc}") from exc
    if not argv:
        raise OSError("empty verify command")
    if _unquoted_shell_ops(command):
        return command, True
    return argv, False


def _run_in_ghost(
    command: str,
    ghost_root: Path,
    timeout: float,
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[Optional[int], str, str, bool]:
    """Run ``command`` in ``ghost_root``. Returns exit, stdout, stderr, timed_out."""
    argv, use_shell = prepare_verify_command(command)
    child_env = scrub_child_env(env)
    proc = subprocess.Popen(
        argv,
        shell=use_shell,
        cwd=str(ghost_root),
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout or "", stderr or "", False
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return None, stdout or "", stderr or "", True


def _result(
    *,
    status: str,
    command: Optional[str] = None,
    cwd: Optional[str] = None,
    exit_code: Optional[int] = None,
    timed_out: bool = False,
    log: str = "",
    truncated: bool = False,
    error: Optional[str] = None,
    timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "command": command,
        "cwd": cwd,
        "exit_code": exit_code,
        "timed_out": bool(timed_out),
        "log": log,
        "truncated": bool(truncated),
        "error": error,
        "timeout_sec": timeout_sec,
        # Advisory only: a red result must not block Apply.
        "advisory": True,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


def format_verify(result: Mapping[str, Any]) -> str:
    """Human-readable CLI / slash output."""
    status = result.get("status")
    if status == "error":
        err = str(result.get("error") or "verify failed")
        if err == NO_TEST_COMMAND:
            return NO_TEST_COMMAND
        return f"ghost-run: {err}"

    label = "Pass" if status == "pass" else "Fail"
    exit_code = result.get("exit_code")
    exit_shown = "timeout" if result.get("timed_out") else exit_code
    lines = [
        f"Verify: {label}",
        f"  command: {result.get('command')}",
        f"  cwd: {result.get('cwd')}",
        f"  exit: {exit_shown}",
    ]
    if result.get("timed_out"):
        lines.append(f"  timed out after {result.get('timeout_sec')}s")
    log = str(result.get("log") or "")
    if log:
        lines.append("")
        lines.append(log.rstrip("\n"))
    return "\n".join(lines) + "\n"


def _persist(state: GhostState, result: Dict[str, Any]) -> Dict[str, Any]:
    state.last_verify = result
    if state.real_root is not None and state.ghost_root is not None:
        state.save()
    return result


def run_verify(
    state: GhostState,
    *,
    timeout: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Resolve and run the configured test in the ghost sandbox.

    Persists the result on ``state`` (and the session file) so the UI can
    reload it. Never raises for a failed or missing test — callers display
    ``status``.
    """
    timeout_sec = DEFAULT_TIMEOUT_SEC if timeout is None else float(timeout)
    if timeout_sec <= 0:
        timeout_sec = DEFAULT_TIMEOUT_SEC

    if state.real_root is None or state.ghost_root is None:
        return _persist(
            state,
            _result(status="error", error="no active sandbox (try hermes ghost on first)"),
        )
    if not state.ghost_root.exists():
        return _persist(
            state,
            _result(
                status="error",
                error=f"sandbox missing at {state.ghost_root}",
                cwd=str(state.ghost_root),
            ),
        )

    try:
        command = resolve_test_command(state.real_root, environ)
    except VerifyError as exc:
        return _persist(
            state,
            _result(
                status="error",
                error=str(exc),
                cwd=str(state.ghost_root),
                timeout_sec=timeout_sec,
            ),
        )

    cwd = str(state.ghost_root.resolve())
    denied = ensure_verify_approval(state.real_root, command)
    if denied:
        return _persist(
            state,
            _result(
                status="error",
                command=command,
                cwd=cwd,
                error=denied,
                timeout_sec=timeout_sec,
            ),
        )
    try:
        exit_code, stdout, stderr, timed_out = _run_in_ghost(
            command, state.ghost_root, timeout_sec
        )
    except OSError as exc:
        return _persist(
            state,
            _result(
                status="error",
                command=command,
                cwd=cwd,
                error=str(exc),
                timeout_sec=timeout_sec,
            ),
        )

    log, truncated = truncate_log(_combine_output(stdout, stderr))
    if timed_out:
        status = "fail"
    elif exit_code == 0:
        status = "pass"
    else:
        status = "fail"
    return _persist(
        state,
        _result(
            status=status,
            command=command,
            cwd=cwd,
            exit_code=exit_code,
            timed_out=timed_out,
            log=log,
            truncated=truncated,
            timeout_sec=timeout_sec,
        ),
    )
