# Ghost Run

**Dry-run mode for [Hermes Agent](https://hermes-agent.nousresearch.com).** While Ghost Mode is on, file writes/edits/deletes and terminal tools are redirected into a throwaway copy of your working folder. Network-touching tools and shell commands are blocked. Review the diff, then apply only the paths you want — or discard everything. Your real disk stays untouched until you say so.

**Opt-in outbound.** With `GHOST_RUN_AI` unset, Ghost Run does not send file contents anywhere. Set `GHOST_RUN_AI=1` and advisory notes are requested. Inside Hermes they go through `ctx.llm.complete` (your current provider; the plugin never sees the key). Otherwise the only read is `agent.secret_scope.get_secret("OPENROUTER_API_KEY")`. If that module is missing, notes stay off. The main model key is never sent.

> Local scaffold for the Nous Research Hermes plugin hackathon (entries due **Wed Nov 11, 2026, 5:00 PM America/New_York**). This repo is **local-only** until Michael says yes to publish.

## 90-second demo script

Automated (deterministic) runner:

```bash
# Does not source ~/.hermes/.env and does not run a live agent.
# Opt in with GHOST_RUN_AGENT=1 (and your own OPENROUTER_API_KEY).
# GHOST_SKIP_AGENT=1 forces the agent beat off.
./demo/run_demo.sh
# Writes a temp work directory (printed at the end): demo-run.log,
# pane.png, and copy-timing.json. The log records Verify Fail, then Apply.
```

Committed review-page shots, from headless Chrome against the loopback page, not from Hermes Desktop:

- [Load, diff list](demo/screenshots/review-page-load.png)
- [After Apply](demo/screenshots/review-page-after-apply.png)

Manual beats:

1. `cd` into `demo/fixture` (small web project with obvious unused files).
2. `/ghost on` — Hermes copies the tree into a temp sandbox (copy-on-write reflink when the filesystem supports it, otherwise a real copy; never a hard link). Skips `.git`, `node_modules`, venvs, build dirs; configurable via `.ghost-runignore` / `GHOST_RUN_IGNORE`.
3. Ask the agent: **"delete unused files"** (or have it `write_file` / `rm` via `terminal`).
4. `hermes ghost ui` — interactive pane on `127.0.0.1`. The command prints `http://127.0.0.1:<port>/#token=<random>`. Open that exact URL. The page checks `Host` is `127.0.0.1:<port>` or `localhost:<port>`. The fragment is read once, removed from the address bar, and sent as `X-Ghost-Run-Token`. A token in the query string is rejected. The Desktop pane does not fetch this port. Paste the printed URL into it and it opens that page with `ctx.os.openExternal`.
5. `hermes ghost verify` — or press **Verify**. The first run for a project asks you to approve the command (Hermes approval prompt, or a yes/no in the terminal). The demo fixture’s `.ghost-run-test` runs `python3 scripts/ghost_verify.py` in the ghost copy (no `node_modules`). **Pass** / **Fail** is evidence only.
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
| `ctx.register_middleware("tool_request", ...)` | middleware | Remap `write_file` / `patch` / `read_file` / `search_files` `path`, every V4A `patch(mode="patch")` header (`Update` / `Add` / `Delete` / both ends of `Move File`), and `terminal` `workdir`/`command` paths into the ghost copy. Return `{"args": ...}`. A header that leaves the project blocks the call. |
| `ctx.register_hook("pre_tool_call", ...)` | hook | Block with `{"action":"block","message":...}` for network tools (`web_search`, `web_extract`, `browser_*`, …), network shell (`curl`, `wget`, `ssh`, …), and paths outside the working folder. |
| `ctx.register_command("ghost", ...)` | slash | `/ghost on\|off\|status\|…` |
| `ctx.register_cli_command("ghost", ...)` | CLI | `hermes ghost diff\|apply\|discard` |
| `ctx.llm.complete(...)` | host LLM | Advisory risk notes when `GHOST_RUN_AI=1`. Uses the user's Hermes provider. The plugin does not read or forward provider keys. |

Target built-in tools (Hermes 0.19+): `write_file`, `patch`, `read_file`, `search_files`, `terminal`, `process_manage`.

## Desktop pane + localhost fallback

`desktop/plugin.js` registers a real React pane (imports only `@hermes/plugin-sdk` + `react`):

- per-file checkboxes, added/modified badges, struck-through deletions
- unified diff preview
- **Verify** (advisory Pass/Fail) / **Apply selected** / **Discard** wired to the loopback UI

Because the Electron Desktop app may not run headless on the box, ship the same UX via:

```bash
hermes ghost ui          # prints http://127.0.0.1:<port>/#token=<random>
hermes ghost ui --open   # opens that exact URL
```

The UI binds to `127.0.0.1` only (`--host localhost` is accepted and bound as `127.0.0.1`; any other host is refused). The printed URL carries the session token in the fragment (`#token=`), which the browser does not send. The page copies it into `X-Ghost-Run-Token` and strips the fragment with `history.replaceState`. A token in the query string is rejected. Responses send no `Access-Control-Allow-Origin` header. The printed page is same-origin with the server, so a normal browser loads, reviews, and applies without one.

`desktop/plugin.js` is a thin pane. It calls `host.openWorkspace` when that function exists (`apps/desktop/src/sdk/index.ts` exports it on `host`), and otherwise `ctx.register({ area: 'panes' })`. The button calls `ctx.os.openExternal` with the pasted loopback URL. The pane does not fetch the review server. The SDK sandboxed iframe is not used: its sandbox is scripts only, so the page inside it would run as an opaque origin and its own fetches would send `Origin: null`.

A live Desktop click-through is in [docs/desktop-check.md](docs/desktop-check.md). It has not been run here.

`hermes ghost diff` still writes a static `ghost-run-diff.html` snapshot.

## AI risk flags

Every pending ghost change gets a risk badge (**high**, **med**, or **low**) and a one-line reason in plain English. The list is sorted with the riskiest changes first on the `hermes ghost ui` page. The Desktop pane opens that page in the browser.

The heuristics are deterministic and always on (`ghost_run/risk.py`):

| Level | Trigger |
|---|---|
| high | secrets files (`.env`, `*.pem`, keys, credentials) or added lines that look like keys or tokens; DB migrations / `.sql`; mass deletes (5 or more); permission changes to world-writable or newly executable |
| med | single deletes; lockfiles and dependency manifests; CI, build, or deploy config; other mode changes; large diffs (300+ lines) |
| low | everything else (new files, small edits) |

Blocked agent attempts (network tools, network shell commands, workdirs outside the folder) are logged to `blocked.jsonl` next to the session file and listed as high-risk flags.

**Select only low-risk** checks only the changes the heuristics rated low. Nothing applies until you press **Apply selected**.

**Optional model notes (opt-in outbound):** set `GHOST_RUN_AI=1`. This is the only Ghost Run path that leaves the machine.

- Inside Hermes, `register(ctx)` binds `ctx.llm.complete`. Notes use that call (purpose `ghost-run.risk-notes`) and the provider you already configured. The plugin does not read a provider key, does not choose a provider or model, and does not send the main model key anywhere.
- Outside that call, notes POST pending diffs to OpenRouter (`https://openrouter.ai/api/v1/chat/completions`, model `openrouter/free`, override with `GHOST_RUN_MODEL`) only when `agent.secret_scope.get_secret` returns `OPENROUTER_API_KEY`. If that module cannot be imported, notes stay off. A multiplexed gateway with no secret scope fails closed. The process environment is not a fallback, and the main model key is never read.

The answer is cached by diff. Leave `GHOST_RUN_AI` unset and nothing is uploaded. The model only adds advisory text. It never changes a risk level, selects a file, or applies anything. On a timeout (8 s), an HTTP error, or bad JSON, the pane quietly falls back to the heuristic flags. A key used on the OpenRouter fallback is sent only in the Authorization header and is never logged or printed.

## Verify (advisory)

Risk badges are path and diff heuristics. **Verify** runs one test command you configure, inside the ghost copy, and shows **Pass** or **Fail** plus a truncated log. It does not choose files and it does not block **Apply selected**. A red result leaves Apply enabled; you still decide what to copy onto the real folder.

Command resolution, in order:

1. Environment variable `GHOST_RUN_TEST` (the whole value is the command line).
2. File `.ghost-run-test` at the **real** project root. The first non-empty line that does not start with `#` is the command. Later lines are ignored.
3. Otherwise Verify reports `no test command configured`.

The command string is read from the environment or the real tree, then executed with **cwd = the ghost sandbox** (90s timeout, stdout/stderr capped at 12 KiB). An agent edit of `.ghost-run-test` inside the ghost copy does not change which command runs. The process itself sees ghost files, so a check script in the sandbox is what actually runs. There is no auto-discovery of pytest or npm, and Ghost Run does not install dependencies for you.

**Approval, once per project and command.** Verify does not run a repo-chosen command until you approve that exact command for that project. The ask goes through Hermes' approval prompt when Hermes is installed (`once` runs this time, `session` remembers for this process, `always` is saved). Otherwise a terminal yes/no is used when stdin is a TTY; a yes is saved. Non-interactive runs with no saved approval fail closed and do not start the command. Saved approvals live in `verify-allow.json` next to the session file. That directory is under `hermes_constants.get_hermes_home()` (context-local profile, then `HERMES_HOME`, then the platform default). If that module cannot be imported, Ghost Run uses `HERMES_HOME` or `~/.hermes`. `GHOST_RUN_SESSION_FILE` still overrides the session file itself. The approval file stays outside the project, so a repo cannot approve itself. A changed command asks again.

The child process does not inherit provider API keys or other secrets. It receives a short allowlist (PATH, HOME, USER, locale, temp, and the usual Python/Node interpreter variables). Names that look like keys, tokens, or passwords are removed. Simple commands run without a shell; a command that uses pipes or redirects still uses a shell, and only after the same approval.

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

Ghost copies are independent files. A hard link is the real file, so Ghost Mode will not create one and will not start if a copy comes out hard-linked.

1. `cp --reflink=auto` (copy-on-write clone, new inode) → 2. filtered `copy2`

`GHOST_RUN_COPY_STRATEGY=hardlink` and `hardlink-file` are refused. `reflink`, `copy`, and the default `auto` are allowed. Ignore patterns: built-in (`.git`, `node_modules`, `.venv`, `dist`, `build`, …) plus `.ghost-runignore` and `GHOST_RUN_IGNORE=dir1,dir2`. See `demo/copy-timing.json` after `./demo/run_demo.sh`.

## Tools that bypass the copy

`execute_code`, `send_message`, `delegate_task`, and `cronjob` are blocked while Ghost Mode is on. They are not remapped into the ghost copy; blocking them is the dry-run boundary. Each concurrent tool call keeps its own pending block slot (`tool_call_id`, or the calling thread when Hermes does not pass one), so one call cannot clear another’s block.

## Tests

```bash
pytest -q
```

## License

MIT — see `LICENSE`. No self-update. No secrets.

Built by Michael McClanahan (@tnvolman)
