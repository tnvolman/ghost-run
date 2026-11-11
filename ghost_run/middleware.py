"""tool_request middleware: remap file/terminal paths into the ghost sandbox."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Optional, Set

from .network import bypass_tool_reason, is_network_tool, network_command_reason
from .paths import remap_to_ghost
from .state import get_state, set_pending_block

# Tools whose `path` argument is remapped into the ghost copy.
PATH_TOOLS: Set[str] = {"write_file", "patch", "read_file", "search_files"}

# Absolute paths inside the real root that appear in a shell command.
_ABS_PATH_IN_CMD = re.compile(r"(?P<path>/(?:[^;\s|&<>'\"]+))")

# V4A headers, same shapes Hermes' patch tool accepts (including ``***Update File:``).
# Move carries two paths: ``*** Move File: <src> -> <dst>`` (source and move-to).
_V4A_SINGLE_HEADER_RE = re.compile(
    r"^(\*\*\*\s*(Update|Add|Delete)\s+File:\s*)(.+)$"
)
_V4A_MOVE_HEADER_RE = re.compile(
    r"^(\*\*\*\s*Move\s+File:\s*)(.+?)\s*->\s*(.+)$"
)


def _remap_command_paths(command: str, real_root, ghost_root) -> str:
    """Rewrite absolute paths under real_root to their ghost counterparts."""
    real_s = str(real_root.resolve())
    ghost_s = str(ghost_root.resolve())

    def repl(match: re.Match) -> str:
        p = match.group("path")
        if p == real_s or p.startswith(real_s + "/"):
            return ghost_s + p[len(real_s) :]
        return p

    # Also do a simple prefix replace for the real root string.
    out = command.replace(real_s, ghost_s)
    return _ABS_PATH_IN_CMD.sub(repl, out)


def _tool_call_id(kwargs: Dict[str, Any]) -> str:
    raw = kwargs.get("tool_call_id") or ""
    return raw if isinstance(raw, str) else ""


def _queue_block(message: str, tool_call_id: str) -> None:
    """Record a block for this call only. The matching pre_tool_call pops it."""
    set_pending_block(message, tool_call_id)


def _remap_header_path(raw: str, real_root, ghost_root) -> tuple[Optional[str], Optional[str]]:
    text = raw.strip()
    if not text:
        return None, "ghost-run: blocked empty path in a multi-file patch header"
    return remap_to_ghost(text, real_root, ghost_root)


def rewrite_v4a_patch(patch: str, real_root, ghost_root) -> tuple[Optional[str], Optional[str]]:
    """Rewrite every V4A file header onto the ghost copy.

    Update, Add, Delete, and both ends of Move are remapped. One escaping
    path fails the whole patch: ``(None, error)``. A patch-mode body with no
    file headers is rejected too, because those targets would stay on the
    real working directory.
    """
    found = False
    rewritten: list[str] = []
    for line in patch.split("\n"):
        cr = "\r" if line.endswith("\r") else ""
        body = line[:-1] if cr else line
        move = _V4A_MOVE_HEADER_RE.match(body)
        if move:
            found = True
            src, src_err = _remap_header_path(move.group(2), real_root, ghost_root)
            if src_err:
                return None, src_err
            dst, dst_err = _remap_header_path(move.group(3), real_root, ghost_root)
            if dst_err:
                return None, dst_err
            rewritten.append(f"{move.group(1)}{src} -> {dst}{cr}")
            continue
        single = _V4A_SINGLE_HEADER_RE.match(body)
        if single:
            found = True
            mapped, err = _remap_header_path(single.group(3), real_root, ghost_root)
            if err:
                return None, err
            rewritten.append(f"{single.group(1)}{mapped}{cr}")
            continue
        rewritten.append(line)
    if not found:
        return None, (
            "ghost-run: blocked multi-file patch with no Update/Add/Delete/Move "
            "file headers while Ghost Mode is on"
        )
    return "\n".join(rewritten), None


def _remap_patch_mode(new_args: Dict[str, Any], state) -> Optional[str]:
    """Rewrite ``mode="patch"`` headers. Return an error message, or None.

    On error the caller queues a block and must not return rewritten args.
    """
    if "path" in new_args and new_args.get("path") not in (None, ""):
        raw_path = new_args.get("path")
        if not isinstance(raw_path, str):
            return "ghost-run: blocked non-string path on a multi-file patch"
        remapped, err = remap_to_ghost(raw_path, state.real_root, state.ghost_root)
        if err:
            return err
        new_args["path"] = remapped
    patch_text = new_args.get("patch")
    if not isinstance(patch_text, str) or not patch_text.strip():
        return "ghost-run: blocked patch mode without patch content while Ghost Mode is on"
    rewritten, err = rewrite_v4a_patch(patch_text, state.real_root, state.ghost_root)
    if err:
        return err
    new_args["patch"] = rewritten
    return None


def on_tool_request(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Rewrite tool args so file/terminal ops hit the ghost copy.

    Returns ``{"args": ..., "source": "ghost-run", "reason": "..."}`` or None.
    Outside-root and network shell commands set a per-call pending slot for
    the ``pre_tool_call`` hook (middleware cannot block by itself).
    """
    state = get_state()
    if not state.enabled or state.real_root is None or state.ghost_root is None:
        return None
    if not isinstance(args, dict):
        return None

    call_id = _tool_call_id(kwargs)

    # Network tools: leave args alone; pre_tool_call will block.
    if is_network_tool(tool_name):
        _queue_block(
            f"ghost-run: blocked network tool {tool_name!r} while Ghost Mode is on",
            call_id,
        )
        return None

    bypass = bypass_tool_reason(tool_name)
    if bypass:
        _queue_block(bypass, call_id)
        return None

    new_args = copy.deepcopy(args)
    changed = False
    reason = "ghost mode path remap"

    if tool_name in PATH_TOOLS:
        # mode=patch names its targets in the patch body, not in `path`.
        # Those headers resolve against the real working directory unless
        # every one of them is rewritten onto the ghost copy.
        if tool_name == "patch" and new_args.get("mode") == "patch":
            err = _remap_patch_mode(new_args, state)
            if err:
                _queue_block(err, call_id)
                return None
            changed = True
            reason = "ghost mode patch header remap"
        else:
            raw = new_args.get("path", ".")
            if not isinstance(raw, str):
                raw = "."
            remapped, err = remap_to_ghost(raw, state.real_root, state.ghost_root)
            if err:
                _queue_block(err, call_id)
                return None
            new_args["path"] = remapped
            changed = True

    elif tool_name == "terminal":
        command = new_args.get("command")
        if isinstance(command, str):
            net = network_command_reason(command)
            if net:
                _queue_block(net, call_id)
                return None
            rewritten = _remap_command_paths(command, state.real_root, state.ghost_root)
            if rewritten != command:
                new_args["command"] = rewritten
                changed = True

        # Force workdir into the ghost sandbox.
        workdir = new_args.get("workdir")
        if workdir:
            remapped, err = remap_to_ghost(str(workdir), state.real_root, state.ghost_root)
            if err:
                _queue_block(err, call_id)
                return None
            new_args["workdir"] = remapped
        else:
            new_args["workdir"] = str(state.ghost_root.resolve())
        changed = True

    elif tool_name == "process_manage":
        # Background jobs started under ghost should stay there; no path remap
        # needed beyond whatever the original terminal call already did.
        return None

    if not changed:
        return None
    return {"args": new_args, "source": "ghost-run", "reason": reason}
