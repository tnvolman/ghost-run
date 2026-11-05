"""Slash command (/ghost) and CLI (hermes ghost ...) handlers."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from .apply import apply_paths, discard_session
from .diff import compute_diff
from .report import write_html_report
from .sandbox import create_ghost_copy, discard_ghost_copy
from .state import get_state
from .ui_server import DEFAULT_HOST, DEFAULT_PORT, serve
from .verify import format_verify, run_verify


def _status_text() -> str:
    state = get_state()
    if not state.enabled:
        base = "Ghost Mode: OFF"
    else:
        base = (
            f"Ghost Mode: ON\n"
            f"  real:  {state.real_root}\n"
            f"  ghost: {state.ghost_root}"
        )
    verify = state.last_verify or {}
    status = verify.get("status")
    if status == "pass":
        base += "\n  verify: Pass"
    elif status == "fail":
        base += "\n  verify: Fail"
    elif status == "error" and verify.get("error"):
        base += f"\n  verify: {verify.get('error')}"
    return base


def turn_on(real_root: Optional[Path] = None) -> str:
    state = get_state()
    if state.enabled and state.ghost_root and state.ghost_root.exists():
        return _status_text() + "\n(already on)"

    root = (real_root or Path.cwd()).resolve()
    # Replace any stale sandbox.
    if state.ghost_root:
        discard_ghost_copy(state.ghost_root)

    ghost = create_ghost_copy(root)
    state.enabled = True
    state.real_root = root
    state.ghost_root = ghost
    state.pending_block = None
    state.flags.clear()
    state.last_verify = None
    from .risk import clear_blocks

    clear_blocks()
    state.save()
    return (
        f"Ghost Mode: ON\n"
        f"  real:  {root}\n"
        f"  ghost: {ghost}\n"
        f"File/terminal tools now write into the ghost copy. "
        f"Network tools/commands are blocked. "
        f"Use /ghost status, hermes ghost diff|verify|apply|discard when done."
    )


def turn_off(*, discard: bool = True) -> str:
    state = get_state()
    if not state.enabled:
        return "Ghost Mode: OFF (already)"
    msg = ""
    if discard:
        msg = discard_session(state)
    else:
        # Keep sandbox on disk for later apply, but stop remapping.
        state.enabled = False
        state.save()
        msg = (
            f"Ghost Mode: OFF (sandbox kept at {state.ghost_root}; "
            f"run hermes ghost apply|discard next)"
        )
    return msg


def slash_ghost(raw_args: str) -> str:
    """Handler for ``/ghost on|off|status`` (and ``diff`` convenience)."""
    parts = (raw_args or "").strip().split()
    cmd = (parts[0].lower() if parts else "status")

    if cmd in ("on", "start", "enable"):
        return turn_on()
    if cmd in ("off", "stop", "disable"):
        # Default off discards; pass "keep" to retain sandbox.
        keep = len(parts) > 1 and parts[1].lower() in ("keep", "retain")
        return turn_off(discard=not keep)
    if cmd in ("status", "stat", ""):
        return _status_text()
    if cmd == "diff":
        return cli_diff()
    if cmd == "verify":
        return format_verify(run_verify(get_state()))
    if cmd == "discard":
        return discard_session(get_state())
    if cmd == "apply":
        paths = parts[1:]
        try:
            actions = apply_paths(get_state(), paths or None)
        except RuntimeError as exc:
            return str(exc)
        return "Applied:\n" + "\n".join(f"  {a}" for a in actions)
    if cmd == "ui":
        # Non-blocking hint — full server via CLI `hermes ghost ui`
        return (
            "Start the interactive pane with: hermes ghost ui\n"
            "Then open http://127.0.0.1:8765/ (Verify / Apply selected / Discard)."
        )
    return (
        "Usage: /ghost on|off|status|diff|verify|apply [paths...]|discard|ui\n"
        f"Unknown subcommand: {cmd!r}"
    )


def cli_diff() -> str:
    state = get_state()
    if state.real_root is None or state.ghost_root is None:
        return "ghost-run: no active sandbox (try /ghost on first)"
    report = compute_diff(state.real_root, state.ghost_root)
    html_path = state.ghost_root / "ghost-run-diff.html"
    write_html_report(report, html_path)
    return report.text(include_unified=True) + f"\nHTML report: {html_path}\n"


def setup_cli(subparser: argparse.ArgumentParser) -> None:
    """argparse setup_fn for ``ctx.register_cli_command('ghost', ...)``."""
    sub = subparser.add_subparsers(dest="ghost_action", required=True)

    sub.add_parser("status", help="Show Ghost Mode status")
    sub.add_parser("diff", help="Show ghost vs real diff (+ write HTML)")
    sub.add_parser(
        "verify",
        help="Run the configured test in the ghost copy (advisory; does not block apply)",
    )
    sub.add_parser("discard", help="Delete the ghost sandbox and turn off")

    apply_p = sub.add_parser(
        "apply", help="Copy selected (or all) ghost changes onto the real folder"
    )
    apply_p.add_argument(
        "paths",
        nargs="*",
        help="Relative paths to apply (default: every change in the diff)",
    )

    on_p = sub.add_parser("on", help="Enable Ghost Mode for the current directory")
    on_p.add_argument(
        "--root",
        type=str,
        default=None,
        help="Working folder to sandbox (default: cwd)",
    )
    sub.add_parser("off", help="Disable Ghost Mode and discard the sandbox")

    ui_p = sub.add_parser(
        "ui",
        help="Serve interactive Apply/Discard pane on localhost (Desktop fallback)",
    )
    ui_p.add_argument("--host", default=DEFAULT_HOST, help="Bind host (default 127.0.0.1)")
    ui_p.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default 8765)")
    ui_p.add_argument(
        "--open",
        action="store_true",
        help="Open a browser tab to the pane",
    )


def handle_cli(args: argparse.Namespace) -> None:
    """handler_fn for ``hermes ghost ...``."""
    action = getattr(args, "ghost_action", None)
    if action == "on":
        root = Path(args.root).resolve() if getattr(args, "root", None) else None
        print(turn_on(root))
        return
    if action == "off":
        print(turn_off(discard=True))
        return
    if action == "status":
        print(_status_text())
        return
    if action == "diff":
        print(cli_diff())
        return
    if action == "verify":
        print(format_verify(run_verify(get_state())), end="")
        return
    if action == "discard":
        print(discard_session(get_state()))
        return
    if action == "apply":
        paths = getattr(args, "paths", None) or []
        try:
            actions = apply_paths(get_state(), paths or None)
        except RuntimeError as exc:
            print(exc)
            return
        print("Applied:")
        for a in actions:
            print(f"  {a}")
        return
    if action == "ui":
        serve(
            host=getattr(args, "host", DEFAULT_HOST),
            port=int(getattr(args, "port", DEFAULT_PORT) or DEFAULT_PORT),
            open_browser=bool(getattr(args, "open", False)),
            block=True,
        )
        return
    print(f"ghost-run: unknown action {action!r}")
