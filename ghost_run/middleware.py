"""tool_request middleware: remap file/terminal paths into the ghost sandbox."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Optional, Set

from .network import is_network_tool, network_command_reason
from .paths import remap_to_ghost
from .state import get_state

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


def on_tool_request(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> Optional[Dict[str, Any]]:
    """Rewrite tool args so file/terminal ops hit the ghost copy.

    Returns ``{"args": ..., "source": "ghost-run", "reason": "..."}`` or None.
    Outside-root and network shell commands set ``state.pending_block`` for
    the ``pre_tool_call`` hook (middleware cannot block by itself).
    """
    state = get_state()
    if not state.enabled or state.real_root is None or state.ghost_root is None:
        return None
    if not isinstance(args, dict):
        return None

    # Network tools: leave args alone; pre_tool_call will block.
    if is_network_tool(tool_name):
        state.pending_block = (
            f"ghost-run: blocked network tool {tool_name!r} while Ghost Mode is on"
        )
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
            state.pending_block = err
            return None
        new_args["path"] = remapped
        changed = True

    elif tool_name == "terminal":
        command = new_args.get("command")
        if isinstance(command, str):
            net = network_command_reason(command)
            if net:
                state.pending_block = net
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
                state.pending_block = err
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
