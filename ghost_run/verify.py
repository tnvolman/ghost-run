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

import os
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .state import GhostState

ENV_VAR = "GHOST_RUN_TEST"
CONFIG_FILENAME = ".ghost-run-test"
NO_TEST_COMMAND = "no test command configured"

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


def _run_in_ghost(
    command: str,
    ghost_root: Path,
    timeout: float,
) -> Tuple[Optional[int], str, str, bool]:
    """Run ``command`` in ``ghost_root``. Returns exit, stdout, stderr, timed_out."""
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=str(ghost_root),
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
