"""Review-page selection: Apply must send the paths of the ticked rows.

Regression for path (a): with HIGH (.env), MED (package.json) and LOW
(notes.txt) pending, unticking MED and HIGH then clicking Apply posts
exactly ["notes.txt"] and changes only that real file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ghost_run.commands import turn_on
from ghost_run.state import get_state
from ghost_run.ui_server import serve

playwright = pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

REAL = {
    "notes.txt": "hello\n",
    "package.json": '{"name":"demo","dependencies":{}}\n',
    ".env": "DEMO_MODE=off\n",
}
GHOST = {
    "notes.txt": "hello world\n",
    "package.json": '{"name":"demo","dependencies":{"leftpad":"1.0.0"}}\n',
    ".env": "DEMO_MODE=on\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")


@pytest.fixture
def review_session(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_RUN_SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.delenv("GHOST_RUN_AI", raising=False)
    from ghost_run.state import reset_state

    reset_state()
    root = tmp_path / "proj"
    root.mkdir()
    _write(root, REAL)
    turn_on(root)
    state = get_state()
    _write(Path(state.ghost_root), GHOST)
    import io
    from contextlib import redirect_stdout

    with redirect_stdout(io.StringIO()):
        httpd, port = serve(host="127.0.0.1", port=0, block=False)
    yield root, port, httpd.ghost_token
    httpd.shutdown()
    reset_state()


def _launch(playwright):
    """Start Chromium. Skip only when it is missing on a local machine."""
    try:
        return playwright.chromium.launch(headless=True)
    except Exception as exc:
        if os.environ.get("GITHUB_ACTIONS"):
            raise
        pytest.skip(f"Chromium is not available locally ({exc})")


def _ticks(page) -> dict:
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('.row')].map(r => [
            r.dataset.path, r.querySelector('input').checked
        ]))"""
    )


def _open(page, port: int, token: str) -> None:
    page.goto("about:blank")
    page.goto(f"http://127.0.0.1:{port}/#token={token}", wait_until="domcontentloaded")
    page.wait_for_selector(".row")
    page.wait_for_function("() => document.querySelectorAll('.row').length === 3")


def test_apply_selected_sends_only_ticked_low_path(review_session):
    """Path (a): uncheck MED and HIGH, Apply, body is exactly ['notes.txt']."""
    root, port, token = review_session
    posts: list[list[str]] = []

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()

        def on_request(request):
            if request.method == "POST" and request.url.endswith("/api/apply"):
                body = json.loads(request.post_data or "{}")
                posts.append(body.get("paths"))

        page.on("request", on_request)
        _open(page, port, token)

        rows = page.evaluate(
            """() => [...document.querySelectorAll('.row')].map(r => ({
                path: r.dataset.path,
                risk: r.querySelector('.risk').textContent,
                checked: r.querySelector('input[type=checkbox]').checked,
                dataPath: r.querySelector('input[type=checkbox]').getAttribute('data-path')
            }))"""
        )
        by_risk = {r["risk"]: r["path"] for r in rows}
        assert by_risk["high"] == ".env"
        assert by_risk["med"] == "package.json"
        assert by_risk["low"] == "notes.txt"
        # Each checkbox carries its own path (not a list index).
        assert all(r["dataPath"] == r["path"] for r in rows)

        # Untick MED and HIGH if they are on (HIGH may already be off).
        for path in ("package.json", ".env"):
            loc = page.locator(f'.row[data-path="{path}"] input[type=checkbox]')
            if loc.is_checked():
                loc.click()
        page.locator("#applyBtn").click()
        page.wait_for_timeout(500)
        browser.close()

    assert posts == [["notes.txt"]], posts
    assert (root / "notes.txt").read_text(encoding="utf-8") == "hello world\n"
    assert (root / "package.json").read_text(encoding="utf-8") == REAL["package.json"]
    assert (root / ".env").read_text(encoding="utf-8") == REAL[".env"]


def test_high_starts_unticked_and_needs_confirm(review_session):
    root, port, token = review_session
    posts: list[list[str]] = []
    dialogs: list[str] = []

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        page.on(
            "dialog",
            lambda d: (dialogs.append(d.message), d.dismiss()),
        )
        page.on(
            "request",
            lambda r: posts.append(json.loads(r.post_data or "{}").get("paths"))
            if r.method == "POST" and r.url.endswith("/api/apply")
            else None,
        )
        _open(page, port, token)
        ticks = page.evaluate(
            """() => Object.fromEntries([...document.querySelectorAll('.row')].map(r => [
                r.dataset.path, r.querySelector('input').checked
            ]))"""
        )
        assert ticks[".env"] is False
        assert ticks["package.json"] is True
        assert ticks["notes.txt"] is True

        # Select none then Refresh must not re-tick HIGH.
        page.locator("#selNone").click()
        page.locator("#refreshBtn").click()
        page.wait_for_timeout(300)
        ticks2 = page.evaluate(
            """() => Object.fromEntries([...document.querySelectorAll('.row')].map(r => [
                r.dataset.path, r.querySelector('input').checked
            ]))"""
        )
        assert ticks2[".env"] is False

        # Ticking HIGH and Apply must prompt; dismissing cancels the POST.
        page.locator('.row[data-path=".env"] input[type=checkbox]').click()
        page.locator("#applyBtn").click()
        page.wait_for_timeout(400)
        browser.close()

    assert dialogs and "high-risk" in dialogs[0] and ".env" in dialogs[0]
    assert posts == []
    assert (root / ".env").read_text(encoding="utf-8") == REAL[".env"]


def test_high_stays_unticked_after_apply(review_session):
    """After Apply, the reload must not tick a remaining HIGH file."""
    root, port, token = review_session
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        _open(page, port, token)
        assert _ticks(page)[".env"] is False
        med = page.locator('.row[data-path="package.json"] input[type=checkbox]')
        if med.is_checked():
            med.click()
        page.locator("#applyBtn").click()
        page.wait_for_function(
            "() => !document.querySelector('.row[data-path=\"notes.txt\"]')"
        )
        assert _ticks(page)[".env"] is False
        browser.close()
    assert (root / "notes.txt").read_text(encoding="utf-8") == GHOST["notes.txt"]
    assert (root / ".env").read_text(encoding="utf-8") == REAL[".env"]


def test_sel_low_apply_sends_only_low(review_session):
    root, port, token = review_session
    posts: list[list[str]] = []
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()

        def on_request(request):
            if request.method == "POST" and request.url.endswith("/api/apply"):
                body = json.loads(request.post_data or "{}")
                posts.append(body.get("paths"))

        page.on("request", on_request)
        _open(page, port, token)
        page.locator("#selLow").click()
        ticks = _ticks(page)
        assert ticks[".env"] is False
        assert ticks["package.json"] is False
        assert ticks["notes.txt"] is True
        page.locator("#applyBtn").click()
        page.wait_for_timeout(500)
        browser.close()
    assert posts == [["notes.txt"]], posts
    assert (root / "notes.txt").read_text(encoding="utf-8") == GHOST["notes.txt"]
    assert (root / "package.json").read_text(encoding="utf-8") == REAL["package.json"]
    assert (root / ".env").read_text(encoding="utf-8") == REAL[".env"]


def test_high_confirm_accepted_posts_the_high_path(review_session):
    root, port, token = review_session
    posts: list[list[str]] = []
    dialogs: list[str] = []
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.on(
            "request",
            lambda r: posts.append(json.loads(r.post_data or "{}").get("paths"))
            if r.method == "POST" and r.url.endswith("/api/apply")
            else None,
        )
        _open(page, port, token)
        page.locator("#selNone").click()
        page.locator('.row[data-path=".env"] input[type=checkbox]').click()
        page.locator("#applyBtn").click()
        page.wait_for_timeout(500)
        browser.close()
    assert dialogs and "high-risk" in dialogs[0] and ".env" in dialogs[0]
    assert posts and ".env" in posts[0]
    assert (root / ".env").read_text(encoding="utf-8") == GHOST[".env"]
    assert (root / "notes.txt").read_text(encoding="utf-8") == REAL["notes.txt"]
    assert (root / "package.json").read_text(encoding="utf-8") == REAL["package.json"]


def test_interval_reload_does_not_tick_high(review_session):
    _root, port, token = review_session
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        _open(page, port, token)
        assert _ticks(page)[".env"] is False
        # The page reloads the diff on a 4s timer.
        page.wait_for_timeout(4500)
        assert _ticks(page)[".env"] is False
        page.locator("#selNone").click()
        page.wait_for_timeout(4500)
        assert _ticks(page)[".env"] is False
        browser.close()
