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
        assert strat in {"copy", "hardlink-file", "hardlink", "reflink"}
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
        assert used in {"reflink", "hardlink", "hardlink-file", "copy"}
        assert (ghost / "f0.txt").is_file()
    finally:
        discard_ghost_copy(ghost)


def test_hardlink_when_possible(tmp_path: Path):
    """On same filesystem, hardlink-file or hardlink should share inodes."""
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "data.bin"
    src.write_bytes(b"hello-ghost" * 100)
    ghost = create_ghost_copy(root, strategy="hardlink-file")
    try:
        dest = ghost / "data.bin"
        assert dest.is_file()
        # Same device + preferably same inode when hardlinked
        s1, s2 = src.stat(), dest.stat()
        assert s1.st_size == s2.st_size
        strat = (ghost / ".ghost-run-strategy").read_text().strip()
        if strat == "hardlink-file" and s1.st_dev == s2.st_dev:
            assert s1.st_ino == s2.st_ino
    finally:
        discard_ghost_copy(ghost)
