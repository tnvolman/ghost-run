"""CoW / hardlink copy strategies and configurable ignore patterns."""

from __future__ import annotations

import os
from pathlib import Path

from ghost_run.ignore import load_ignore_dirs
from ghost_run.sandbox import create_ghost_copy, discard_ghost_copy, timed_create


def test_ignore_env_and_file(tmp_path: Path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".ghost-runignore").write_text("secret_cache\n# comment\n", encoding="utf-8")
    monkeypatch.setenv("GHOST_RUN_IGNORE", "extra_dir,another")
    names = load_ignore_dirs(root)
    assert "secret_cache" in names
    assert "extra_dir" in names
    assert "another" in names
    assert ".git" in names
    assert "node_modules" in names


def test_copy_skips_ignored_dirs(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("1\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    (root / ".ghost-runignore").write_text("scratch\n", encoding="utf-8")
    (root / "scratch").mkdir()
    (root / "scratch" / "x.bin").write_text("x\n", encoding="utf-8")

    ghost = create_ghost_copy(root, strategy="copy")
    try:
        assert (ghost / "src" / "app.py").is_file()
        assert not (ghost / "node_modules").exists()
        assert not (ghost / ".git").exists()
        assert not (ghost / "scratch").exists()
        strat = (ghost / ".ghost-run-strategy").read_text().strip()
        assert strat in {"copy", "reflink"}
    finally:
        discard_ghost_copy(ghost)


def test_timed_create_reports_strategy(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    for i in range(20):
        (root / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
    ghost, dt, used = timed_create(root, strategy="auto")
    try:
        assert dt >= 0
        assert used in {"reflink", "copy"}
        assert (ghost / "f0.txt").is_file()
    finally:
        discard_ghost_copy(ghost)


def test_hardlink_strategy_is_refused(tmp_path: Path, monkeypatch):
    """Hard links are the real file. Ghost Mode must not start that way."""
    import pytest

    root = tmp_path / "proj"
    root.mkdir()
    (root / "data.bin").write_bytes(b"hello-ghost" * 100)
    for strategy in ("hardlink", "hardlink-file"):
        with pytest.raises(RuntimeError, match="hard link"):
            create_ghost_copy(root, strategy=strategy)
    monkeypatch.setenv("GHOST_RUN_COPY_STRATEGY", "hardlink")
    with pytest.raises(RuntimeError, match="hard link"):
        create_ghost_copy(root)


def test_copy_does_not_share_inodes(tmp_path: Path, monkeypatch):
    """A ghost edit must not change the real file, and os.link is not used."""
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "data.bin"
    src.write_bytes(b"hello-ghost" * 100)

    def _no_link(*_args, **_kwargs):
        raise AssertionError("os.link must not be used for the ghost copy")

    monkeypatch.setattr(os, "link", _no_link)
    ghost = create_ghost_copy(root, strategy="copy")
    try:
        dest = ghost / "data.bin"
        assert dest.is_file()
        s1, s2 = src.stat(), dest.stat()
        assert s1.st_ino != s2.st_ino
        dest.write_bytes(b"changed-in-ghost")
        assert src.read_bytes().startswith(b"hello-ghost")
        strat = (ghost / ".ghost-run-strategy").read_text().strip()
        assert strat == "copy"
    finally:
        discard_ghost_copy(ghost)
