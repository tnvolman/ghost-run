"""pre_tool_call hook: enforce Ghost Mode blocks."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .network import bypass_tool_reason, is_network_tool, network_command_reason
from .paths import is_within, resolve_under
from .risk import record_block
from .state import get_state, pop_pending_block


def _tool_call_id(kwargs: Dict[str, Any]) -> str:
    raw = kwargs.get("tool_call_id") or ""
    return raw if isinstance(raw, str) else ""


def on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Block network tools/commands and outside-root paths while Ghost Mode is on.

    Consumes the pending slot for this ``tool_call_id`` (or this thread) set by
    tool_request middleware. Each concurrent call has its own slot.
    """
    state = get_state()
    if not state.enabled:
        return None
    result = _decide(state, tool_name, args, tool_call_id=_tool_call_id(kwargs))
    if result and result.get("action") == "block":
        record_block(tool_name, result.get("message", ""))
    return result


def _decide(state, tool_name: str, args: Any, tool_call_id: str = "") -> Optional[Dict[str, str]]:

    pending = pop_pending_block(tool_call_id)
    if pending:
        return {"action": "block", "message": pending}

    if is_network_tool(tool_name):
        return {
            "action": "block",
            "message": (
                f"ghost-run: blocked network tool {tool_name!r} while Ghost Mode is on"
            ),
        }

    bypass = bypass_tool_reason(tool_name)
    if bypass:
        return {"action": "block", "message": bypass}

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
