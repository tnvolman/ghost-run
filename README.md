# Ghost Run

**Dry-run mode for [Hermes Agent](https://hermes-agent.nousresearch.com).** While Ghost Mode is on, file writes/edits/deletes and terminal tools are redirected into a throwaway copy of your working folder. Network-touching tools and shell commands are blocked. Review the diff, then apply only the paths you want — or discard everything. Your real disk stays untouched until you say so.

> Local scaffold for the Nous Research Hermes plugin hackathon (entries due **Wed Nov 11, 2026, 5:00 PM America/New_York**). This repo is **local-only** until Michael says yes to publish.

## 90-second demo script

1. `cd` into a small project (or `tests/fixtures/demo_project`).
2. `/ghost on` — Hermes copies the tree into a temp sandbox.
3. Ask the agent: **"delete unused files"** (or have it `write_file` / `rm` via `terminal`).
4. Run `hermes ghost diff` — pane/HTML shows deletions with strike-through; `ls` on the real folder shows files still there.
5. `hermes ghost apply src/unused_tmp.py notes.txt brand_new.txt` — only those paths land on disk — **or** `hermes ghost discard` to throw the sandbox away.

## Install (dev)

```bash
# Hermes discovers project plugins from ./.hermes/plugins/<name>/
mkdir -p .hermes/plugins
ln -s /absolute/path/to/ghost-run .hermes/plugins/ghost-run
# Enable in ~/.hermes/config.yaml → plugins.enabled: [ghost-run]
```

Or copy this directory into `~/.hermes/plugins/ghost-run`.

## Commands

| Surface | Usage |
|--------|--------|
| Slash | `/ghost on\|off\|status\|diff\|apply [paths...]\|discard` |
| CLI | `hermes ghost on\|off\|status\|diff\|apply [paths...]\|discard` |

## Exact Hermes APIs used

Public `PluginContext` only — **no core overrides**:

| API | Kind | Purpose |
|-----|------|---------|
| `ctx.register_middleware("tool_request", ...)` | middleware | Remap `write_file` / `patch` / `read_file` / `search_files` `path` and `terminal` `workdir`/`command` paths into the ghost copy. Return `{"args": ...}`. |
| `ctx.register_hook("pre_tool_call", ...)` | hook | Block with `{"action":"block","message":...}` for network tools (`web_search`, `web_extract`, `browser_*`, …), network shell (`curl`, `wget`, `ssh`, …), and paths outside the working folder. |
| `ctx.register_command("ghost", ...)` | slash | `/ghost on\|off\|status\|…` |
| `ctx.register_cli_command("ghost", ...)` | CLI | `hermes ghost diff\|apply\|discard` |

Target built-in tools (Hermes 0.19+): `write_file`, `patch`, `read_file`, `search_files`, `terminal`, `process_manage`.

## Desktop pane

`desktop/plugin.js` attempts `ctx.register({ area: 'panes', ... })`. **Interactive Apply/Discard inside Desktop is unproven** in this local build. Supported fallback: `hermes ghost diff` writes `ghost-run-diff.html` (struck-through deletions).

## Tests

```bash
pytest -q
```

## License

MIT — see `LICENSE`. No self-update. No secrets.
