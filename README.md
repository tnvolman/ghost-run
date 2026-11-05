# Ghost Run

**Dry-run mode for [Hermes Agent](https://hermes-agent.nousresearch.com).** While Ghost Mode is on, file writes/edits/deletes and terminal tools are redirected into a throwaway copy of your working folder. Network-touching tools and shell commands are blocked. Review the diff, then apply only the paths you want — or discard everything. Your real disk stays untouched until you say so.

> Local scaffold for the Nous Research Hermes plugin hackathon (entries due **Wed Nov 11, 2026, 5:00 PM America/New_York**). This repo is **local-only** until Michael says yes to publish.

## 90-second demo script

Automated (deterministic) runner:

```bash
# HERMES_MODEL_API_KEY or OPENROUTER_API_KEY must be set (never printed)
./demo/run_demo.sh
# → demo/pane.png, demo/copy-timing.json, demo/demo-run.log
```

Manual beats:

1. `cd` into `demo/fixture` (small web project with obvious unused files).
2. `/ghost on` — Hermes CoW/hardlink-copies the tree into a temp sandbox (skips `.git`, `node_modules`, venvs, build dirs; configurable via `.ghost-runignore` / `GHOST_RUN_IGNORE`).
3. Ask the agent: **"delete unused files"** (or have it `write_file` / `rm` via `terminal`).
4. `hermes ghost ui` — interactive pane on localhost (checkboxes, badges, struck-through deletions, unified diff, **Verify**). Desktop `desktop/plugin.js` registers the same pane via `ctx.openWorkspace` / `area: 'panes'`.
5. `hermes ghost verify` — or press **Verify**. The demo fixture’s `.ghost-run-test` runs `python3 scripts/ghost_verify.py` in the ghost copy (no `node_modules`). **Pass** / **Fail** is evidence only.
6. Apply selected paths — **or** Discard. A red Verify result does not disable Apply. Real disk stays untouched until Apply.

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
| Slash | `/ghost on\|off\|status\|diff\|verify\|apply [paths...]\|discard\|ui` |
| CLI | `hermes ghost on\|off\|status\|diff\|verify\|apply [paths...]\|discard\|ui` |

## Exact Hermes APIs used

Public `PluginContext` only — **no core overrides**:

| API | Kind | Purpose |
|-----|------|---------|
| `ctx.register_middleware("tool_request", ...)` | middleware | Remap `write_file` / `patch` / `read_file` / `search_files` `path` and `terminal` `workdir`/`command` paths into the ghost copy. Return `{"args": ...}`. |
| `ctx.register_hook("pre_tool_call", ...)` | hook | Block with `{"action":"block","message":...}` for network tools (`web_search`, `web_extract`, `browser_*`, …), network shell (`curl`, `wget`, `ssh`, …), and paths outside the working folder. |
| `ctx.register_command("ghost", ...)` | slash | `/ghost on\|off\|status\|…` |
| `ctx.register_cli_command("ghost", ...)` | CLI | `hermes ghost diff\|apply\|discard` |

Target built-in tools (Hermes 0.19+): `write_file`, `patch`, `read_file`, `search_files`, `terminal`, `process_manage`.

## Desktop pane + localhost fallback

`desktop/plugin.js` registers a real React pane (imports only `@hermes/plugin-sdk` + `react`):

- per-file checkboxes, added/modified badges, struck-through deletions
- unified diff preview
- **Verify** (advisory Pass/Fail) / **Apply selected** / **Discard** wired to `http://127.0.0.1:8765/api/*`

Because the Electron Desktop app may not run headless on the box, ship the same UX via:

```bash
hermes ghost ui          # http://127.0.0.1:8765/
hermes ghost ui --open
```

`hermes ghost diff` still writes a static `ghost-run-diff.html` snapshot.

## AI risk flags

Every pending ghost change gets a risk badge (**high**, **med**, or **low**) and a one-line reason in plain English. The list is sorted with the riskiest changes first, in both the Desktop pane and `hermes ghost ui`.

The heuristics are deterministic and always on (`ghost_run/risk.py`):

| Level | Trigger |
|---|---|
| high | secrets files (`.env`, `*.pem`, keys, credentials) or added lines that look like keys or tokens; DB migrations / `.sql`; mass deletes (5 or more); permission changes to world-writable or newly executable |
| med | single deletes; lockfiles and dependency manifests; CI, build, or deploy config; other mode changes; large diffs (300+ lines) |
| low | everything else (new files, small edits) |

Blocked agent attempts (network tools, network shell commands, workdirs outside the folder) are logged to `blocked.jsonl` next to the session file and listed as high-risk flags.

**Select only low-risk** checks only the changes the heuristics rated low. Nothing applies until you press **Apply selected**.

**Optional model notes:** set `GHOST_RUN_AI=1` together with `HERMES_MODEL_API_KEY` (or `OPENROUTER_API_KEY`). `hermes ghost ui` then asks `openrouter/free` (override with `GHOST_RUN_MODEL`) for one sentence per file plus an overall summary, and caches the answer by diff. The model only adds advisory text. It never changes a risk level, selects a file, or applies anything. On a timeout (8 s), an HTTP error, or bad JSON, the pane quietly falls back to the heuristic flags. The key is sent only in the Authorization header and is never logged or printed.

## Verify (advisory)

Risk badges are path and diff heuristics. **Verify** runs one test command you configure, inside the ghost copy, and shows **Pass** or **Fail** plus a truncated log. It does not choose files and it does not block **Apply selected**. A red result leaves Apply enabled; you still decide what to copy onto the real folder.

Command resolution, in order:

1. Environment variable `GHOST_RUN_TEST` (the whole value is the command line).
2. File `.ghost-run-test` at the **real** project root. The first non-empty line that does not start with `#` is the command. Later lines are ignored.
3. Otherwise Verify reports `no test command configured`.

The command string is read from the environment or the real tree, then executed with **cwd = the ghost sandbox** (90s timeout, stdout/stderr capped at 12 KiB). An agent edit of `.ghost-run-test` inside the ghost copy does not change which command runs. The process itself sees ghost files, so a check script in the sandbox is what actually runs. There is no auto-discovery of pytest or npm, and Ghost Run does not install dependencies for you.

```bash
cd demo/fixture
hermes ghost on
hermes ghost verify
# Verify: Pass
#   command: python3 scripts/ghost_verify.py
#   cwd: <ghost copy>
```

`demo/fixture/.ghost-run-test` points at `scripts/ghost_verify.py`, a stdlib-only check that the fixture’s keep-files (`index.html`, `src/app.js`, `package.json`, `public/styles.css`) exist. It does not need `node_modules` (that directory is ignored by the ghost copy). Delete or break one of those files in the ghost copy and the same command exits 1 so the pane can show **Fail**. Then Apply or Discard as you like.

The same control is in the Desktop pane and `hermes ghost ui` (**Verify** button, Pass/Fail banner, truncated log). `GET /api/diff` and `GET /api/verify` reload the last result from the session file; `POST /api/verify` runs it again. `/ghost verify` matches the CLI.

## Speed

Ghost copies prefer filesystem CoW / hardlinks when possible:

1. `cp --reflink=auto` → 2. `cp -a --link` → 3. per-file `os.link` → 4. filtered `copy2`

Override with `GHOST_RUN_COPY_STRATEGY=reflink|hardlink|hardlink-file|copy`. Ignore patterns: built-in (`.git`, `node_modules`, `.venv`, `dist`, `build`, …) plus `.ghost-runignore` and `GHOST_RUN_IGNORE=dir1,dir2`. See `demo/copy-timing.json` after `./demo/run_demo.sh`.

## Tests

```bash
pytest -q
```

## License

MIT — see `LICENSE`. No self-update. No secrets.

Built by Michael McClanahan (@tnvolman)
