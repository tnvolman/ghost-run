# Changelog

## Unreleased

- Multi-file `patch(mode="patch")` headers are remapped into the ghost copy. A path that leaves the project blocks the call.
- Advisory notes use `ctx.llm.complete` inside Hermes. The OpenRouter fallback reads `OPENROUTER_API_KEY` only through `agent.secret_scope.get_secret`, and stays off when that module is missing. The main model key is not read.
- The loopback review page sends no CORS header. The session token is in the URL fragment and then the `X-Ghost-Run-Token` header; a query-string token is ignored. The Desktop pane opens the printed URL with `ctx.os.openExternal` via `host.openWorkspace` (or `ctx.register({ area: 'panes' })` when that host method is absent).
