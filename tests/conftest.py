from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import the library package only (subdirectory ghost_run/), never the plugin
# root __init__.py as a module under test.
from ghost_run.risk import _AI_CACHE, unbind_llm_complete  # noqa: E402
from ghost_run.state import reset_state  # noqa: E402
from ghost_run.verify import clear_session_approvals  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "demo_project"


@pytest.fixture(autouse=True)
def _clean_state(tmp_path_factory, monkeypatch):
    # Isolate persisted sessions so tests never touch ~/.hermes/ghost-run.
    session = tmp_path_factory.mktemp("ghost-session") / "active.json"
    monkeypatch.setenv("GHOST_RUN_SESSION_FILE", str(session))
    reset_state()
    clear_session_approvals()
    unbind_llm_complete()
    _AI_CACHE.clear()
    yield
    clear_session_approvals()
    unbind_llm_complete()
    _AI_CACHE.clear()
    reset_state()


@pytest.fixture
def demo_project(tmp_path: Path) -> Path:
    dest = tmp_path / "demo_project"
    shutil.copytree(FIXTURE, dest)
    return dest
