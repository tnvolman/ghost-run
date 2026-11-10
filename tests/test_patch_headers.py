"""Multi-file patch headers must land in the ghost copy, never the real tree."""

from __future__ import annotations

from pathlib import Path

from ghost_run.commands import turn_on
from ghost_run.hooks import on_pre_tool_call
from ghost_run.middleware import (
    _V4A_MOVE_HEADER_RE,
    _V4A_SINGLE_HEADER_RE,
    on_tool_request,
)
from ghost_run.state import get_state


def _snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[rel] = "link:" + os_readlink(path)
        elif path.is_file():
            out[rel] = path.read_text(encoding="utf-8")
        elif path.is_dir():
            out[rel] = "dir"
    return out


def os_readlink(path: Path) -> str:
    return str(path.readlink())


def _header_targets(patch: str, cwd: Path) -> list[tuple[str, Path, Path | None]]:
    """(kind, target, move_dest) resolved the way a cwd-relative tool would."""
    found = []
    for line in patch.splitlines():
        move = _V4A_MOVE_HEADER_RE.match(line.rstrip("\r"))
        if move:
            found.append(("Move", _resolve(move.group(2).strip(), cwd), _resolve(move.group(3).strip(), cwd)))
            continue
        single = _V4A_SINGLE_HEADER_RE.match(line.rstrip("\r"))
        if single:
            found.append((single.group(2), _resolve(single.group(3).strip(), cwd), None))
    return found


def _resolve(raw: str, cwd: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    return path


def _apply_like_patch_tool(patch: str, cwd: Path) -> None:
    """Mutate whatever paths the rewritten headers name.

    Relative headers resolve against *cwd* (the real project). Absolute
    headers are used as given, which is where a ghost remap has to point.
    """
    for kind, target, dest in _header_targets(patch, cwd):
        if kind == "Move":
            assert dest is not None
            dest.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.rename(dest)
            else:
                dest.write_text("moved\n", encoding="utf-8")
            continue
        if kind == "Delete":
            if target.is_symlink() or target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ghost-write\n", encoding="utf-8")


def _patch_body() -> str:
    return "\n".join(
        [
            "*** Begin Patch",
            "***Update File: notes.txt",
            "@@",
            "-old",
            "+new",
            "*** Add File: src/brand_new.txt",
            "+hello",
            "*** Delete File: src/unused_tmp.py",
            "*** Move File: README.md -> archive/README.md",
            "*** End Patch",
            "",
        ]
    )


def test_multifile_patch_leaves_real_tree_untouched(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    assert state.ghost_root is not None
    real_before = _snapshot(demo_project)
    result = on_tool_request(
        tool_name="patch",
        args={"mode": "patch", "patch": _patch_body()},
        tool_call_id="patch-ok",
    )
    assert result is not None
    rewritten = result["args"]["patch"]
    assert "path" not in result["args"]
    blocked = on_pre_tool_call(
        tool_name="patch",
        args={"mode": "patch", "patch": rewritten},
        tool_call_id="patch-ok",
    )
    assert blocked is None

    ghost = state.ghost_root.resolve()
    targets = _header_targets(rewritten, demo_project)
    kinds = [kind for kind, _target, _dest in targets]
    assert kinds == ["Update", "Add", "Delete", "Move"]
    for kind, target, dest in targets:
        assert target.is_absolute()
        assert ghost in target.parents or target.parent == ghost
        assert demo_project.resolve() not in target.parents
        if dest is not None:
            assert dest.is_absolute()
            assert ghost in dest.parents
            assert "archive/README.md" in dest.as_posix()
            assert demo_project.resolve() not in dest.parents
    # Both ends of the move were rewritten, not only the source.
    move_line = next(line for line in rewritten.splitlines() if "Move File:" in line)
    assert str(ghost) in move_line
    assert "README.md -> archive/README.md" not in move_line
    assert str(demo_project.resolve()) not in rewritten

    _apply_like_patch_tool(rewritten, demo_project)
    assert _snapshot(demo_project) == real_before
    assert (ghost / "notes.txt").read_text(encoding="utf-8") == "ghost-write\n"
    assert (ghost / "src" / "brand_new.txt").read_text(encoding="utf-8") == "ghost-write\n"
    assert not (ghost / "src" / "unused_tmp.py").exists()
    assert not (ghost / "README.md").exists()
    assert (ghost / "archive" / "README.md").is_file()
    assert (demo_project / "README.md").is_file()
    assert (demo_project / "src" / "unused_tmp.py").is_file()
    assert not (demo_project / "src" / "brand_new.txt").exists()


def test_patch_headers_escape_is_blocked(demo_project: Path, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    moved = tmp_path / "moved-out.txt"
    (demo_project / "leak.txt").symlink_to(outside)
    turn_on(demo_project)
    state = get_state()
    assert state.ghost_root is not None
    real_before = _snapshot(demo_project)
    ghost_before = _snapshot(state.ghost_root)

    attempts = [
        "*** Begin Patch\n*** Update File: " + str(outside) + "\n@@\n-secret\n+nope\n*** End Patch\n",
        "*** Begin Patch\n*** Add File: ../escaped.txt\n+pwned\n*** End Patch\n",
        "*** Begin Patch\n*** Move File: README.md -> " + str(moved) + "\n*** End Patch\n",
        "*** Begin Patch\n*** Update File: leak.txt\n@@\n-secret\n+nope\n*** End Patch\n",
        (
            "*** Begin Patch\n"
            "*** Update File: notes.txt\n@@\n-a\n+b\n"
            "*** Move File: README.md -> "
            + str(moved)
            + "\n*** End Patch\n"
        ),
    ]
    for index, body in enumerate(attempts):
        call_id = f"escape-{index}"
        result = on_tool_request(
            tool_name="patch",
            args={"mode": "patch", "patch": body},
            tool_call_id=call_id,
        )
        assert result is None
        blocked = on_pre_tool_call(
            tool_name="patch",
            args={"mode": "patch", "patch": body},
            tool_call_id=call_id,
        )
        assert blocked and blocked["action"] == "block"
        assert "ghost-run:" in blocked["message"]

    assert _snapshot(demo_project) == real_before
    assert _snapshot(state.ghost_root) == ghost_before
    assert outside.read_text(encoding="utf-8") == "secret\n"
    assert not moved.exists()
    assert (demo_project / "notes.txt").read_text(encoding="utf-8") == real_before["notes.txt"]
    assert (demo_project / "README.md").is_file()


def test_absolute_project_path_in_header_is_remapped(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    assert state.ghost_root is not None
    body = (
        "*** Begin Patch\n"
        f"*** Update File: {demo_project / 'notes.txt'}\n"
        "@@\n-a\n+b\n"
        "*** End Patch\n"
    )
    result = on_tool_request(tool_name="patch", args={"mode": "patch", "patch": body})
    assert result is not None
    rewritten = result["args"]["patch"]
    assert str(demo_project.resolve() / "notes.txt") not in rewritten
    assert str(state.ghost_root.resolve() / "notes.txt") in rewritten
    real_before = (demo_project / "notes.txt").read_text(encoding="utf-8")
    _apply_like_patch_tool(rewritten, demo_project)
    assert (demo_project / "notes.txt").read_text(encoding="utf-8") == real_before
    assert (state.ghost_root / "notes.txt").read_text(encoding="utf-8") == "ghost-write\n"


def test_replace_mode_does_not_rewrite_patch_text(demo_project: Path):
    turn_on(demo_project)
    state = get_state()
    old = "*** Update File: notes.txt\n"
    result = on_tool_request(
        tool_name="patch",
        args={
            "mode": "replace",
            "path": "notes.txt",
            "old_string": old,
            "new_string": "x",
        },
    )
    assert result is not None
    assert result["args"]["old_string"] == old
    assert result["args"]["path"] == str(state.ghost_root.resolve() / "notes.txt")


def test_patch_mode_without_headers_is_blocked(demo_project: Path):
    turn_on(demo_project)
    on_tool_request(
        tool_name="patch",
        args={"mode": "patch", "patch": "*** Begin Patch\n*** End Patch\n"},
        tool_call_id="empty-patch",
    )
    blocked = on_pre_tool_call(
        tool_name="patch",
        args={"mode": "patch", "patch": "*** Begin Patch\n*** End Patch\n"},
        tool_call_id="empty-patch",
    )
    assert blocked and blocked["action"] == "block"
    assert "no Update/Add/Delete/Move" in blocked["message"]
