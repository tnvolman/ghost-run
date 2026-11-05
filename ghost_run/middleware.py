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
