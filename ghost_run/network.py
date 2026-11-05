"""Detect network-touching tools and shell commands during Ghost Mode."""

from __future__ import annotations

import re
from typing import FrozenSet, Optional

# Built-in Hermes tools that reach the network (or a remote browser).
NETWORK_TOOLS: FrozenSet[str] = frozenset(
    {
        "web_search",
        "web_extract",
        "browser_exec",
        "browser_cdp",
        "browser_dialog",
        "browser_vault",
        "x_search",
        "computer_use",  # can drive remote/desktop network surfaces
        "image_generate",
        "video_generate",
        "text_to_speech",
    }
)

# Shell tokens that typically mean "leave the machine / hit the network".
# Matched as whole words at the start of a pipeline segment or after separators.
_NETWORK_CMD_PATTERN = re.compile(
    r"(?i)(?:^|[;&|`(]|\b(?:sudo|env|command|time)\s+)"
    r"(?:"
    r"curl|wget|httpie|http|aria2c|"
    r"nc|ncat|netcat|socat|"
    r"ssh|scp|sftp|rsync|"
    r"ftp|sftp|telnet|"
    r"pip\s+install|npm\s+(?:install|publish|add)|yarn\s+add|pnpm\s+add|"
    r"docker\s+pull|docker\s+push|git\s+push|git\s+fetch|git\s+pull|"
    r"gh\s+(?:pr|issue|api|release)|"
    r"aws|gcloud|az\b|"
    r"ping|traceroute|dig|nslookup|host\b"
    r")\b"
)


# These tools run code, message someone, spawn another agent, or schedule
# work on the host. They are not remapped into the ghost copy, so Ghost Mode
# blocks them instead of pretending the dry run covers them.
BYPASS_TOOLS: FrozenSet[str] = frozenset(
    {
        "execute_code",
        "send_message",
        "delegate_task",
        "cronjob",
    }
)


def bypass_tool_reason(tool_name: str) -> Optional[str]:
    """Block tools that would act outside the ghost copy."""
    if tool_name in BYPASS_TOOLS:
        return (
            f"ghost-run: blocked {tool_name!r} while Ghost Mode is on "
            "(it bypasses the ghost copy)"
        )
    return None


def is_network_tool(tool_name: str) -> bool:
    if not tool_name:
        return False
    if tool_name in NETWORK_TOOLS:
        return True
    # MCP tools that look like web/browser/http.
    lower = tool_name.lower()
    if lower.startswith("mcp__") and any(
        tok in lower for tok in ("web", "http", "browser", "fetch", "search")
    ):
        return True
    return False


def network_command_reason(command: str) -> Optional[str]:
    """Return a block reason if *command* looks network-touching, else None."""
    if not command or not isinstance(command, str):
        return None
    if _NETWORK_CMD_PATTERN.search(command):
        return (
            "ghost-run: blocked network-touching shell command while Ghost Mode "
            f"is on: {command!r}"
        )
    return None
