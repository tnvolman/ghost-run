"""Run demo/run_demo.sh so the review-page token cannot drift back to the query string."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_script_completes_with_header_token(tmp_path: Path):
    python3 = shutil.which("python3")
    assert python3
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").symlink_to(python3)
    env = os.environ.copy()
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["GHOST_UI_PORT"] = "18765"
    env.pop("OPENROUTER_API_KEY", None)
    proc = subprocess.run(
        ["bash", str(ROOT / "demo" / "run_demo.sh")],
        cwd=ROOT,
        env=env,
        timeout=180,
        capture_output=True,
        text=True,
        check=False,
    )
    log_path = ROOT / "demo" / "demo-run.log"
    log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr + "\n" + log
    assert "VERIFY_PASS" in log
    assert "VERIFY_FAIL" in log
    assert "apply-3 OK" in log
    assert "?token=" not in log
    assert "X-Ghost-Run-Token" not in log
