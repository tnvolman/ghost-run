#!/usr/bin/env bash
# Ghost Run — deterministic ~90s demo runner (local only).
# Uses openrouter/free via HERMES_MODEL_API_KEY / OPENROUTER_API_KEY.
# Never prints the API key.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"
FIX="$DEMO_DIR/fixture"
WORK="$(mktemp -d /tmp/ghost-demo-XXXXXX)"
SESSION="$DEMO_DIR/.demo-session.json"
UI_PORT="${GHOST_UI_PORT:-8765}"
LOG="$DEMO_DIR/demo-run.log"
PANE_PNG="$DEMO_DIR/pane.png"
TIMING_JSON="$DEMO_DIR/copy-timing.json"

if [[ -f "$HOME/.hermes/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$HOME/.hermes/.env"
  set +a
fi

if [[ -z "${OPENROUTER_API_KEY:-${HERMES_MODEL_API_KEY:-}}" ]]; then
  echo "ERROR: set HERMES_MODEL_API_KEY or OPENROUTER_API_KEY (never print it)" >&2
  exit 1
fi
if [[ -z "${OPENROUTER_API_KEY:-}" && -n "${HERMES_MODEL_API_KEY:-}" ]]; then
  export OPENROUTER_API_KEY="$HERMES_MODEL_API_KEY"
fi
export OPENROUTER_API_KEY
if [[ -d "$HOME/.hermes" ]]; then
  if ! grep -q '^OPENROUTER_API_KEY=' "$HOME/.hermes/.env" 2>/dev/null; then
    printf 'OPENROUTER_API_KEY=%s\n' "$OPENROUTER_API_KEY" >> "$HOME/.hermes/.env"
  fi
fi

VENV="${GHOST_VENV:-/workspace/.venv-hermes}"
if [[ -x "$VENV/bin/python" ]]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export GHOST_RUN_SESSION_FILE="$SESSION"
rm -f "$SESSION" "$LOG"
: > "$LOG"

cleanup() {
  if [[ -f "$DEMO_DIR/.ui.pid" ]]; then
    kill "$(cat "$DEMO_DIR/.ui.pid")" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "== Ghost Run demo ==" | tee -a "$LOG"
echo "work copy: $WORK" | tee -a "$LOG"

mkdir -p "$WORK"
cp -a "$FIX"/. "$WORK"/
rm -rf "$WORK/node_modules" "$WORK/dist"
mkdir -p "$WORK/node_modules/leftpad" "$WORK/dist"
echo 'module.exports=1' > "$WORK/node_modules/leftpad/index.js"
echo '/* build */' > "$WORK/dist/bundle.js"

cd "$WORK"
echo "" | tee -a "$LOG"
echo "Beat 1: fixture ready ($(find . -type f | wc -l) files)" | tee -a "$LOG"

echo "Beat 2: hermes ghost on" | tee -a "$LOG"
python -c 'from ghost_run.commands import turn_on; from pathlib import Path; print(turn_on(Path.cwd()))' | tee -a "$LOG"

PROMPT='You are cleaning a tiny web project. Delete ONLY these unused files if they exist: src/unused_legacy.js, src/old_helper.js, tmp/cache.tmp. Do not modify index.html, src/app.js, public/styles.css, package.json, or README.md. Use delete/remove tools or terminal rm. Be brief.'

echo "Beat 3: agent cleanup via openrouter/free (key not printed)" | tee -a "$LOG"
set +e
if command -v hermes >/dev/null 2>&1; then
  timeout 90 hermes chat --provider openrouter --model openrouter/free -q "$PROMPT" >>"$LOG" 2>&1
fi
set -e

# Deterministic ensure: mutate ghost sandbox the way the demo needs
python - <<'PY' | tee -a "$LOG"
from ghost_run.state import get_state
state = get_state()
assert state.ghost_root and state.ghost_root.exists(), "ghost sandbox missing"
targets = ["src/unused_legacy.js", "src/old_helper.js", "tmp/cache.tmp"]
note = state.ghost_root / "CLEANUP_NOTES.md"
if not note.exists():
    note.write_text("# Cleanup\nRemoved unused legacy helpers and tmp cache.\n", encoding="utf-8")
removed = []
for rel in targets:
    p = state.ghost_root / rel
    if p.exists():
        p.unlink()
        removed.append(rel)
print(f"demo ensure: removed {removed} in ghost; added CLEANUP_NOTES.md")
real = state.real_root
for rel in targets:
    assert (real / rel).exists(), f"real missing {rel} — ghost isolation broken"
print("demo ensure: real folder still has unused files (isolation OK)")
PY

echo "Beat 4: ghost diff" | tee -a "$LOG"
python -c 'from ghost_run.commands import cli_diff; print(cli_diff())' | tee -a "$LOG"

echo "Beat 5: ghost ui + screenshot -> $PANE_PNG" | tee -a "$LOG"
UI_LOG="$DEMO_DIR/.ui-server.log"
python -m ghost_run.ui_server --host 127.0.0.1 --port "$UI_PORT" >"$UI_LOG" 2>&1 &
UI_PID=$!
echo "$UI_PID" > "$DEMO_DIR/.ui.pid"
echo "$UI_PORT" > "$DEMO_DIR/.ui.port"
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$UI_PORT/api/health" >/dev/null 2>&1; then
    echo "UI up on http://127.0.0.1:$UI_PORT/ (pid $UI_PID)" | tee -a "$LOG"
    break
  fi
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$UI_PORT/api/health" | tee -a "$LOG"
echo | tee -a "$LOG"

export PANE_PNG
export PANE_URL="http://127.0.0.1:$UI_PORT/"
python - <<'PY' | tee -a "$LOG"
import os, sys, shutil, subprocess, time
from pathlib import Path
out = Path(os.environ["PANE_PNG"])
url = os.environ["PANE_URL"]
ok = False
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_selector("h1, .badge, .row", timeout=10000)
        time.sleep(0.5)
        page.screenshot(path=str(out), full_page=True)
        browser.close()
    ok = out.is_file() and out.stat().st_size > 1000
    print(f"playwright screenshot -> {out} ({out.stat().st_size} bytes)")
except Exception as e:
    print(f"playwright unavailable/failed: {e!r}")

if not ok:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,800")
        driver = webdriver.Chrome(options=opts)
        driver.get(url)
        time.sleep(1.5)
        driver.save_screenshot(str(out))
        driver.quit()
        ok = out.is_file() and out.stat().st_size > 500
        print(f"selenium screenshot -> {out}")
    except Exception as e:
        print(f"selenium unavailable/failed: {e!r}")

if not ok:
    chrome = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if chrome:
        cwd = out.parent
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--screenshot={out.name}", "--window-size=1280,800", url],
            check=False, capture_output=True, timeout=45, cwd=str(cwd),
        )
        if not out.is_file():
            cand = cwd / "screenshot.png"
            if cand.is_file():
                cand.rename(out)
        ok = out.is_file() and out.stat().st_size > 500
        print(f"chromium CLI screenshot -> {out} ok={ok}")
    else:
        print("ERROR: no headless browser for screenshot", file=sys.stderr)
        sys.exit(2)
assert out.is_file() and out.stat().st_size > 500, "pane.png missing/too small"
print(f"SCREENSHOT_OK {out}")
PY

echo "Beat 6: apply selected (3 deletions) via UI API" | tee -a "$LOG"
python - <<'PY' | tee -a "$LOG"
import json, urllib.request
paths = ["src/unused_legacy.js", "src/old_helper.js", "tmp/cache.tmp"]
req = urllib.request.Request(
    "http://127.0.0.1:8765/api/apply",
    data=json.dumps({"paths": paths}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode())
from ghost_run.state import get_state
real = get_state().real_root
for rel in paths:
    print(f"real {rel} exists={ (real / rel).exists() }")
assert not (real / "src/unused_legacy.js").exists()
assert not (real / "src/old_helper.js").exists()
assert not (real / "tmp/cache.tmp").exists()
assert (real / "src/app.js").exists()
assert not (real / "CLEANUP_NOTES.md").exists()
print("apply-3 OK: deletions on real, notes still ghost-only")
PY

echo "Beat 7: discard remaining" | tee -a "$LOG"
python - <<'PY' | tee -a "$LOG"
import urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8765/api/discard",
    data=b"{}",
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode())
from ghost_run.state import get_state
st = get_state()
print("after discard enabled=", st.enabled, "ghost=", st.ghost_root)
PY

echo "Beat 8: CoW / hardlink timing on ~5k-file tree" | tee -a "$LOG"
export TIMING_JSON
python - <<'PY' | tee -a "$LOG"
import json, tempfile, shutil, os
from pathlib import Path
from ghost_run.sandbox import timed_create, discard_ghost_copy

tree = Path(tempfile.mkdtemp(prefix="ghost-5k-"))
n = 5000
for i in range(n):
    d = tree / f"bucket{i % 50}" / f"sub{i % 10}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"f{i}.txt").write_text(f"payload-{i}\n", encoding="utf-8")
nm = tree / "node_modules" / "pkg"
nm.mkdir(parents=True)
for j in range(200):
    (nm / f"m{j}.js").write_text("module.exports=1\n")
(tree / ".git").mkdir()
(tree / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
(tree / ".venv" / "lib").mkdir(parents=True)
(tree / ".venv" / "lib" / "x.py").write_text("# venv\n")

results = {}
for strat in ("auto", "hardlink", "hardlink-file", "copy", "reflink"):
    try:
        ghost, dt, used = timed_create(tree, strategy=strat)
        count = sum(
            1 for p in ghost.rglob("*")
            if p.is_file() and p.name not in (".ghost-run-marker", ".ghost-run-strategy")
        )
        results[strat] = {"seconds": round(dt, 4), "used": used, "files_in_ghost": count}
        discard_ghost_copy(ghost)
    except Exception as e:
        results[strat] = {"error": repr(e)}

out = Path(os.environ["TIMING_JSON"])
out.write_text(json.dumps({"tree_files_approx": n, "results": results}, indent=2) + "\n")
print(json.dumps(results, indent=2))
print(f"Wrote {out}")
shutil.rmtree(tree, ignore_errors=True)
PY

echo "" | tee -a "$LOG"
echo "DEMO COMPLETE" | tee -a "$LOG"
echo "  log: $LOG"
echo "  pane: $PANE_PNG"
echo "  timing: $TIMING_JSON"
echo "  work (left for inspection): $WORK"
