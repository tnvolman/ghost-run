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
    from .ghost_run.risk import bind_llm_complete, unbind_llm_complete
except ImportError:  # pragma: no cover - flat path / test import edge
    from ghost_run.commands import handle_cli, setup_cli, slash_ghost
    from ghost_run.hooks import on_pre_tool_call
    from ghost_run.middleware import on_tool_request
    from ghost_run.risk import bind_llm_complete, unbind_llm_complete


def _bind_host_llm(ctx) -> None:
    """Route advisory notes through ctx.llm.complete when Hermes provides it."""
    llm = getattr(ctx, "llm", None)
    complete = getattr(llm, "complete", None) if llm is not None else None
    if callable(complete):
        bind_llm_complete(complete)
    else:
        unbind_llm_complete()


def register(ctx) -> None:
    """Called by Hermes when the plugin loads."""
    _bind_host_llm(ctx)
    ctx.register_middleware("tool_request", on_tool_request)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_command(
        "ghost",
        slash_ghost,
        description="Ghost Run dry-run mode: on|off|status|diff|verify|apply|discard|ui",
        args_hint="on|off|status|diff|verify|apply [paths...]|discard|ui",
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
