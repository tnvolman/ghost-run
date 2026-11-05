# Ghost Run — live end-to-end demo

**When:** 2026-11-04 evening America/New_York  
**Plugin:** `/workspace/ghost-run` (local only, no remote)  
**Demo folder:** `/workspace/ghost-run-demo-live` (copy of `tests/fixtures/demo_project`)  
**Model:** `openrouter/free` via OpenRouter (`provider: openrouter`)  
**Spend:** **$0** (free model route; pricing prompt=0 / completion=0)  
**Secrets:** none in this file. `HERMES_MODEL_API_KEY` was confirmed set via `test -n` only.

## Setup

1. Confirmed `HERMES_MODEL_API_KEY` set (`echo set`).
2. Wrote OpenRouter key into `~/.hermes/.env` as `OPENROUTER_API_KEY` without printing it.
3. Configured Hermes model to **`openrouter/free`** after probing free models for tool-calling:
   - `google/gemma-4-31b-it:free` → HTTP 429 (rate limited)
   - `liquid/lfm-2.5-2.6b:free` → HTTP 404
   - **`openrouter/free` → tool_calls OK** (used this)
4. Enabled plugin: symlink `~/.hermes/plugins/ghost-run` → `/workspace/ghost-run`, `plugins.enabled: [ghost-run]`.
5. **Bug found & fixed:** Ghost session was process-local only, so `hermes ghost on` then `hermes ghost status` (new process) showed OFF. Added disk persistence at `~/.hermes/ghost-run/active.json` (overridable via `GHOST_RUN_SESSION_FILE`).

## Steps & results

### 1. Snapshot + `/ghost on` (CLI)

Before SHAs (sha256 prefixes):

| file | sha256[:12] |
|------|-------------|
| `README.md` | `df18a25bcbfe` |
| `notes.txt` | `2b8425c4d20e` |
| `src/app.py` | `e6aec1b61a81` |
| `src/unused_tmp.py` | `db23ab777b5b` |

```
$ hermes ghost on --root /workspace/ghost-run-demo-live
Ghost Mode: ON
  real:  /workspace/ghost-run-demo-live
  ghost: /tmp/ghost-run-…-ghost-run-demo-live

$ hermes ghost status   # separate process
Ghost Mode: ON
```

### 2. Live agent: clean up + write/edit (ghost remaps)

Command (approx):

```bash
hermes chat --provider openrouter -m openrouter/free --yolo --max-turns 12 \
  -q "…delete unused files… edit notes.txt… create brand_new.txt… Do not use the network."
```

Observed tool paths were remapped into the ghost temp dir (e.g.  
`/tmp/ghost-run-…/src/unused_tmp.py`, `…/notes.txt`, `…/brand_new.txt`).

Agent session: `20261104_214059_6a6980` (~25s, 14 tool calls).

### 3. Real folder unchanged after agent

```
identical (before vs after agent): True
files: README.md, notes.txt, src/app.py, src/unused_tmp.py
notes.txt still: "keep me"
src/unused_tmp.py still present on real disk
```

After-agent SHA map == before SHA map.

### 4. `hermes ghost diff`

```
Ghost Run diff — real: /workspace/ghost-run-demo-live
                 ghost: /tmp/ghost-run-jpkt3_35-ghost-run-demo-live
  +1 added  ~1 modified  -1 deleted

  + brand_new.txt
  ~ notes.txt
  - src/unused_tmp.py

--- a/brand_new.txt
+++ b/brand_new.txt
@@ -0,0 +1 @@
+hello from ghost

--- a/notes.txt
+++ b/notes.txt
@@ -1 +1 @@
-keep me
+cleaned by ghost-run demo

--- a/src/unused_tmp.py
+++ b/src/unused_tmp.py
@@ -1,2 +0,0 @@
-# ephemeral helper — demo asks the agent to delete unused files
-TEMP = True

HTML report: /tmp/ghost-run-jpkt3_35-ghost-run-demo-live/ghost-run-diff.html
```

### 5. Apply only 3 selected paths

```
$ hermes ghost apply brand_new.txt notes.txt src/unused_tmp.py
Applied:
  added → applied brand_new.txt
  modified → applied notes.txt
  deleted → removed src/unused_tmp.py
```

Post-apply real folder:

- `brand_new.txt` added (`hello from ghost`)
- `notes.txt` → `cleaned by ghost-run demo`
- `src/unused_tmp.py` removed
- `src/app.py` and `README.md` **unchanged** (same SHAs as before)

`SELECTIVE_APPLY_OK`

### 6. Discard

```
$ hermes ghost discard
ghost-run: discarded sandbox …
Ghost Mode: OFF
sandbox gone: YES
```

### 7. Network command blocked (ghost on again)

Direct middleware/hook:

```
block_result {'action': 'block', 'message': "ghost-run: blocked network-touching shell command while Ghost Mode is on: 'curl https://example.com'"}
NETWORK_BLOCK_OK
```

Live agent (session `20261104_214140_2a3325`) asked to `curl …`; Hermes reported the Ghost Mode block message and did not fetch the network.

## Agent log excerpts (scrubbed)

### Cleanup session
```
Query: You are in /workspace/ghost-run-demo-live. Clean up this project: delete 
unused files (especially src/unused_tmp.py), and edit notes.txt to say 'cleaned 
by ghost-run demo'. Also create a new file brand_new.txt with the text 'hello 
from ghost'. Use write_file / terminal tools as needed. Do not use the network.
Initializing agent...
  ⚠ tirith security scanner enabled but not available — command scanning will use pattern matching only
────────────────────────────────────────


┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
The user wants me to clean up a project. Let me first explore the directory structure to understand what
's there, then:
1. Delete unused files (especially src/unused_tmp.py)
2. Edit notes.txt to say 'cleaned by ghost-run demo'
3. Create brand_new.txt with 'hello from ghost'

But wait - I can see that I should inspect the files first and confirm before deleting. Let me
 start by exploring the project structure.

Let me look at the directory first.
└──────────────────────────────────────────────────────────────────────────────┘
  ┊ 💻 preparing terminal…
  ┊ 💻 $         find /tmp/ghost-run-jpkt3_35-ghost-run-demo-live -type f  0.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         ls -la /tmp/ghost-run-jpkt3_35-ghost-run-demo-live /tmp/ghost-run-jpkt3_35-ghost-run-demo-live/src + 1 command  0.0s

┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
The working directory /workspace/ghost-run-demo-live appears to be a symlink or the actual files are
 at /tmp/ghost-run-jpkt3_35-ghost-run-demo-live. Let me check.
└──────────────────────────────────────────────────────────────────────────────┘
  ┊ 💻 preparing terminal…
  ┊ 💻 $         ls -ld /tmp/ghost-run-jpkt3_35-ghost-run-demo-live + 2 commands  0.0s

┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
Let me determine which session I'm in:

- Actual path: /tmp/ghost-run-jpkt3_35-ghost-run-demo-live
- Current directory shows as /workspace/ghost-run-demo-live
- These appear to be container/mount directory symlinks

The file system shows all the same content. Since the user wants me to work in /workspace
/ghost-run-demo-live and that's the symbolic representation, I should use that.

Now let me check the content again to understand the current state:

1. notes.txt contains "cleaned by ghost-run demo" (already done)
2. src/unused_tmp.py exists and should be deleted
3. Create brand_new.txt with "hello from ghost"

Actually looking at the terminal output again:
- notes.txt shows "cleaned by ghost-run demo" is already there
- src/unused_tmp.py contains "keep me" and "# ephemeral helper"
- README.md exists

The user wants me to:
1. Delete unused_tmp.py
2. Edit notes.txt to say 'cleaned by ghost-run demo' (already done)
3. Create brand_new.txt with 'hello from ghost'

Wait, let me re-read more carefully. The user said "edit notes.txt to say 'cleaned
 by ghost-run demo'". I can see from the output that notes.txt already contains "
cleaned by ghost-run demo" so it's already been edited. But let me double-check the
 current content to make sure.

Actually, let me just follow the user's instructions exactly:
1. Delete src/unused_tmp.py 
2. Edit notes.txt to say 'cleaned by ghost-run demo' (overwrite regardless of current
 content)
3. Create brand_new.txt with 'hello from ghost'

I'll delete the temporary file, update the notes, and generate a new file with the
 specified greeting. These tasks are straightforward and can be executed quickly.
└──────────────────────────────────────────────────────────────────────────────┘
  ┊ 💻 preparing terminal…
  ┊ 💻 $         rm /tmp/ghost-run-jpkt3_35-ghost-run-demo-live/src/unused_tmp.py  0.0s

┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
Now write notes.txt with "cleaned by ghost-run demo" and create brand_new.txt with
 "hello from ghost".
└──────────────────────────────────────────────────────────────────────────────┘
  ┊ ✍️ preparing write_file…
  ┊ ✍️  write     /tmp/ghost-run-jpkt3_35-ghost-run-demo-live/notes.txt  0.1s
  ┊ review diff
a//tmp/ghost-run-jpkt3_35-ghost-run-demo-live/notes.txt → b//tmp/ghost-run-jpkt3_35-ghost-run-demo-live/notes.txt
@@ -1 +1 @@
-keep me
+cleaned by ghost-run demo

┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
Now create brand_new.txt with "hello from ghost".
└──────────────────────────────────────────────────────────────────────────────┘
  ┊ ✍️ preparing write_file…
  ┊ ✍️  write     /tmp/ghost-run-jpkt3_35-ghost-run-demo-live/brand_new.txt  0.0s
  ┊ review diff
a//tmp/ghost-run-jpkt3_35-ghost-run-demo-live/brand_new.txt → b//tmp/ghost-run-jpkt3_35-ghost-run-demo-live/brand_new.txt
@@ -0,0 +1 @@
+hello from ghost

┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
Done. Verify.
└──────────────────────────────────────────────────────────────────────────────┘
  ┊ 💻 preparing terminal…
  ┊ 💻 $         ls -la /tmp/ghost-run-jpkt3_35-ghost-run-demo-live /tmp/ghost-run-jpkt3_35-ghost-run-demo-live/src + 2 commands  0.0s

┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
Done. Removed unused_tmp.py, edited notes.txt, and created brand_new.txt.
└──────────────────────────────────────────────────────────────────────────────┘

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    Cleaned up:
    
    /workspace/ghost-run-demo-live/src/unused_tmp.py removed  
    /workspace/ghost-run-demo-live/notes.txt updated: "cleaned by ghost-run demo"  
    /workspace/ghost-run-demo-live/brand_new.txt created: "hello from ghost"
    
    The project now has app.py and README.md in the src directory, plus the
    new brand_new.txt file.
╰──────────────────────────────────────────────────────────────────────────────╯

Resume this session with:
  hermes --resume 20261104_214059_6a6980

Session:        20261104_214059_6a6980
Duration:       25s
Messages:       16 (1 user, 14 tool calls)

```

### Network-block session
```
Query: In this project, run: curl -s https://example.com | head -c 100. You must
use the terminal tool. If blocked, report the block message.
Initializing agent...
────────────────────────────────────────


┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
The user asks me to run: curl -s https://example.com | head -c 100. I must use the
 terminal tool. If blocked, report the block message.

Let me just run this via the terminal tool.
└──────────────────────────────────────────────────────────────────────────────┘
  ┊ 💻 preparing terminal…

┌─ Reasoning ──────────────────────────────────────────────────────────────────┐
The command was blocked by Ghost Mode. I should report this to the user.
└──────────────────────────────────────────────────────────────────────────────┘

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    Blocked. Ghost Mode is active and prevents network-touching shell commands like curl. The block message was:
    
    > ghost-run: blocked network-touching shell command while Ghost Mode is
    on: 'curl -s https://example.com | head -c 100'
╰──────────────────────────────────────────────────────────────────────────────╯

Resume this session with:
  hermes --resume 20261104_214140_2a3325

Session:        20261104_214140_2a3325
Duration:       5s
Messages:       4 (1 user, 2 tool calls)

```

## Bugs found & fixed this turn

1. **Cross-process session loss** — fixed via `~/.hermes/ghost-run/active.json` persistence + `state.save()` on on/off; `apply` works when sandbox roots exist; tests isolate via `GHOST_RUN_SESSION_FILE`.

## Still unproven

- Interactive **Desktop pane** Apply/Discard UI (HTML diff fallback only).
- True copy-on-write (uses filtered full copy).
- `hermes plugins validate|doctor` on PyPI 0.19 PATH (those subcommands absent); previously proven via main source `PYTHONPATH`.

## Spend

OpenRouter free route → **~$0.00**. Cap on key was $5; unused for paid models.


## Follow-up live run (Keep trucking)

- When: 2026-11-04T22:38:11.488476-05:00
- Model: openrouter/free (key not printed)
- Agent log: `demo/live2-agent.log`
- Proof: real untouched until selective apply; apply one deletion; discard rest.
- UI screenshot: `demo/pane.png` (from `demo/run_demo.sh`)
- Copy timing (~5k files): `demo/copy-timing.json` (hardlink ~0.05s, reflink ~0.09s, copy ~0.26s on this box)
