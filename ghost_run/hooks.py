"""pre_tool_call hook: enforce Ghost Mode blocks."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .network import is_network_tool, network_command_reason
from .paths import is_within, resolve_under
from .risk import record_block
from .state import get_state


def on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    **_: Any,
) -> Optional[Dict[str, str]]:
    """Block network tools/commands and outside-root paths while Ghost Mode is on.

    Also consumes ``state.pending_block`` set by tool_request middleware.
    """
    state = get_state()
    if not state.enabled:
        return None
    result = _decide(state, tool_name, args)
    if result and result.get("action") == "block":
        record_block(tool_name, result.get("message", ""))
    return result


def _decide(state, tool_name: str, args: Any) -> Optional[Dict[str, str]]:

    if state.pending_block:
        msg = state.pending_block
        state.pending_block = None
        return {"action": "block", "message": msg}

    if is_network_tool(tool_name):
        return {
            "action": "block",
            "message": (
                f"ghost-run: blocked network tool {tool_name!r} while Ghost Mode is on"
            ),
        }

    if tool_name == "terminal" and isinstance(args, dict):
        command = args.get("command")
        if isinstance(command, str):
            reason = network_command_reason(command)
            if reason:
                return {"action": "block", "message": reason}

        # Belt-and-suspenders: if workdir somehow points outside real+ghost, block.
        if state.real_root and state.ghost_root:
            wd = args.get("workdir")
            if isinstance(wd, str) and wd:
                try:
                    resolved = resolve_under(state.ghost_root, wd)
                except (OSError, RuntimeError):
                    resolved = None
                if resolved is not None and not (
                    is_within(resolved, state.ghost_root)
                    or is_within(resolved, state.real_root)
                ):
                    return {
                        "action": "block",
                        "message": (
                            f"ghost-run: blocked workdir outside working folder: {wd!r}"
                        ),
                    }

    return None
