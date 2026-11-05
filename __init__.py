"""Ghost Run Hermes plugin entrypoint.

Registers public PluginContext APIs only (no core overrides):
  - ctx.register_middleware("tool_request", ...)
  - ctx.register_hook("pre_tool_call", ...)
  - ctx.register_command("ghost", ...)          # /ghost
  - ctx.register_cli_command("ghost", ...)      # hermes ghost
"""

from __future__ import annotations

try:
    from .ghost_run.commands import handle_cli, setup_cli, slash_ghost
    from .ghost_run.hooks import on_pre_tool_call
    from .ghost_run.middleware import on_tool_request
except ImportError:  # pragma: no cover - flat path / test import edge
    from ghost_run.commands import handle_cli, setup_cli, slash_ghost
    from ghost_run.hooks import on_pre_tool_call
    from ghost_run.middleware import on_tool_request


def register(ctx) -> None:
    """Called by Hermes when the plugin loads."""
    ctx.register_middleware("tool_request", on_tool_request)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_command(
        "ghost",
        slash_ghost,
        description="Ghost Run dry-run mode: on|off|status|diff|verify|apply|discard",
        args_hint="on|off|status|diff|verify|apply [paths...]|discard",
    )
    ctx.register_cli_command(
        "ghost",
        help="Ghost Run dry-run sandbox (diff / verify / apply / discard)",
        setup_fn=setup_cli,
        handler_fn=handle_cli,
        description=(
            "Redirect file and terminal tools into a throwaway copy of the "
            "working folder, review the diff, optionally verify with one configured "
            "test, then apply selected paths or discard."
        ),
    )
