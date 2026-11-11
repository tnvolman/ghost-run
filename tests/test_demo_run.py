"""The demo script must not touch the repo or a developer's Hermes install."""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in {".git", "__pycache__", ".pytest_cache"}
        ]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix().encode()
            digest.update(rel)
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_demo_script_is_hermetic(tmp_path: Path):
    python3 = shutil.which("python3")
    assert python3
    home = tmp_path / "home"
    hermes_home = home / ".hermes"
    hermes_home.mkdir(parents=True)
    (hermes_home / ".env").write_text(
        "OPENROUTER_API_KEY=sk-from-env-file\n"
        "HERMES_MODEL_API_KEY=sk-main-model\n"
        "GHOST_DEMO_MARKER=leaked\n"
        "GHOST_RUN_AGENT=1\n",
        encoding="utf-8",
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").symlink_to(python3)
    invoked = tmp_path / "hermes-invoked"
    dummy = bindir / "hermes"
    dummy.write_text(
        "#!/bin/sh\n"
        f"echo invoked > {invoked}\n"
        "echo 'ghost isolation broken' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    dummy.chmod(0o755)
    copy = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        copy,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc"),
        symlinks=True,
    )
    before = _fingerprint(ROOT)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["HERMES_HOME"] = str(hermes_home)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(copy)
    env["GHOST_UI_PORT"] = str(_free_port())
    env["GHOST_SKIP_AGENT"] = "1"
    env["GHOST_DEMO_SKIP_SCREENSHOT"] = "1"
    for name in (
        "GHOST_RUN_AGENT",
        "GHOST_VENV",
        "GHOST_DEMO_SOURCE_HERMES_ENV",
        "OPENROUTER_API_KEY",
        "GHOST_DEMO_MARKER",
    ):
        env.pop(name, None)
    proc = subprocess.run(
        ["bash", str(copy / "demo" / "run_demo.sh")],
        cwd=copy,
        env=env,
        timeout=180,
        capture_output=True,
        text=True,
        check=False,
    )
    work = ""
    for line in proc.stdout.splitlines():
        if line.startswith("work copy:"):
            work = line.split(":", 1)[1].strip()
    log = ""
    if work:
        log_path = Path(work) / "demo-run.log"
        if log_path.is_file():
            log = log_path.read_text(encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr + "\n" + log
    assert "hermes-env-marker=unset" in log
    assert "leaked" not in log
    assert "agent beat skipped: GHOST_SKIP_AGENT=1" in log
    assert "VERIFY_PASS" in log
    assert "VERIFY_FAIL" in log
    assert "apply-3 OK" in log
    assert "?token=" not in log
    assert not invoked.exists()
    assert _fingerprint(ROOT) == before
