#!/usr/bin/env bash
# Ghost Run — deterministic demo runner (local only).
# The live-agent beat is off unless GHOST_RUN_AGENT=1. It is never started
# from a sourced ~/.hermes/.env. GHOST_SKIP_AGENT=1 forces it off.
# Ghost Run does not copy the main model key and never prints a key.
# Thin Verify (README beat 5) runs between the pane screenshot and Apply:
# Pass on the healthy fixture, Fail after deleting ghost index.html, then
# restore. Apply still runs — Verify is advisory. Stdlib fixture command only.
set -euo pipefail

if ! command -v python >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi

# Header file so the session token is not a curl argv entry. Mode 600, removed
# before the function returns. The headless Chrome fallback still receives the
# page URL, fragment included, because the browser has to open that URL.
ui_curl() {
  local hdr rc
  hdr="$(mktemp)"
  chmod 600 "$hdr"
  printf 'X-Ghost-Run-Token: %s\n' "$UI_TOKEN" >"$hdr"
  set +e
  curl -fsS -H @"$hdr" "$@"
  rc=$?
  set -e
  rm -f "$hdr"
  return "$rc"
}

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"
FIX="$DEMO_DIR/fixture"
WORK="$(mktemp -d /tmp/ghost-demo-XXXXXX)"
SESSION="$WORK/session.json"
UI_PORT="${GHOST_UI_PORT:-8765}"
export UI_PORT
LOG="$WORK/demo-run.log"
PANE_PNG="$WORK/pane.png"
TIMING_JSON="$WORK/copy-timing.json"

# Opt in to read ~/.hermes/.env. Off by default so a developer key file
# cannot change this run.
if [[ "${GHOST_DEMO_SOURCE_HERMES_ENV:-}" == "1" && -f "${HOME}/.hermes/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${HOME}/.hermes/.env"
  set +a
fi

# Opt in to a virtualenv. There is no default path.
if [[ -n "${GHOST_VENV:-}" && -x "${GHOST_VENV}/bin/python" ]]; then
  # shellcheck disable=SC1091
  source "${GHOST_VENV}/bin/activate"
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

echo "Beat 3: optional live agent (off unless GHOST_RUN_AGENT=1)" | tee -a "$LOG"
echo "hermes-env-marker=${GHOST_DEMO_MARKER:-unset}" | tee -a "$LOG"
set +e
if [[ "${GHOST_SKIP_AGENT:-}" == "1" ]]; then
  echo "agent beat skipped: GHOST_SKIP_AGENT=1" | tee -a "$LOG"
elif [[ "${GHOST_RUN_AGENT:-}" != "1" ]]; then
  echo "agent beat skipped: set GHOST_RUN_AGENT=1 to opt in" | tee -a "$LOG"
elif [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "agent beat skipped: OPENROUTER_API_KEY is not set" | tee -a "$LOG"
elif ! command -v hermes >/dev/null 2>&1; then
  echo "agent beat skipped: hermes is not on PATH" | tee -a "$LOG"
else
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

# Headless pane screenshot. Requires PANE_PNG and PANE_URL.
# Playwright honors PANE_WAIT_SELECTOR when set (default: pane content).
capture_pane() {
  python - <<'PY'
import os, sys, shutil, subprocess, time
from pathlib import Path
out = Path(os.environ["PANE_PNG"])
url = os.environ["PANE_URL"]
wait_sel = os.environ.get("PANE_WAIT_SELECTOR") or "h1, .badge, .row"
ok = False
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_selector(wait_sel, timeout=10000)
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
        try:
            subprocess.run(
                [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--disable-dev-shm-usage", "--virtual-time-budget=4000",
                 f"--screenshot={out.name}", "--window-size=1280,800", url],
                check=False, capture_output=True, timeout=20, cwd=str(cwd),
            )
        except subprocess.TimeoutExpired:
            # Do not print the command: the page URL carries the session token.
            print("chromium CLI screenshot timed out")
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
}

echo "Beat 5: ghost ui + screenshot -> $PANE_PNG" | tee -a "$LOG"
UI_LOG="$DEMO_DIR/.ui-server.log"
python -m ghost_run.ui_server --host 127.0.0.1 --port "$UI_PORT" >"$UI_LOG" 2>&1 &
UI_PID=$!
echo "$UI_PID" > "$DEMO_DIR/.ui.pid"
echo "$UI_PORT" > "$DEMO_DIR/.ui.port"
UI_TOKEN=""
for i in $(seq 1 40); do
  if [[ -s "$UI_LOG" ]]; then
    UI_TOKEN=$(sed -n 's/.*[#]token=\([^[:space:]]\{1,\}\).*/\1/p' "$UI_LOG" | head -1)
  fi
  if [[ -n "$UI_TOKEN" ]] && ui_curl "http://127.0.0.1:$UI_PORT/api/health" >/dev/null 2>&1; then
    echo "UI up on http://127.0.0.1:$UI_PORT/ (token redacted, pid $UI_PID)" | tee -a "$LOG"
    break
  fi
  sleep 0.25
done
if [[ -z "${UI_TOKEN}" ]]; then
  echo "ERROR: UI did not print a session token" | tee -a "$LOG" >&2
  exit 1
fi
export UI_TOKEN
ui_curl "http://127.0.0.1:$UI_PORT/api/health" | tee -a "$LOG"
echo | tee -a "$LOG"

export PANE_PNG
export PANE_URL="http://127.0.0.1:$UI_PORT/#token=${UI_TOKEN}"
if [[ "${GHOST_DEMO_SKIP_SCREENSHOT:-}" == "1" ]]; then
  echo "screenshot skipped" | tee -a "$LOG"
else
  capture_pane | tee -a "$LOG"
fi

# README manual beat 5. Same path as `/ghost verify` / `hermes ghost verify`:
# slash_ghost("verify") -> run_verify. Fixture `.ghost-run-test` is stdlib-only
# (`python3 scripts/ghost_verify.py`); no node_modules. GHOST_RUN_TEST is ignored
# here so the demo always runs that fixture command. Thin Verify is advisory.
echo "Beat 6: thin Verify, Pass on a healthy ghost copy (README beat 5)" | tee -a "$LOG"
python - <<'PY' | tee -a "$LOG"
import os
os.environ.pop("GHOST_RUN_TEST", None)
from ghost_run.commands import slash_ghost
from ghost_run.state import get_state
from ghost_run.verify import remember_approval

# Owner approval for this demo's known fixture command. The runner is
# non-interactive, so it records the allowlist instead of prompting.
remember_approval(get_state().real_root, "python3 scripts/ghost_verify.py")
text = slash_ghost("verify")
print(text, end="" if text.endswith("\n") else "\n")
passed = get_state().last_verify or {}
assert passed.get("status") == "pass", passed
assert passed.get("advisory") is True
assert passed.get("exit_code") == 0
assert passed.get("command") == "python3 scripts/ghost_verify.py"
assert "fixture healthy" in (passed.get("log") or "")
print("VERIFY_PASS")
PY
echo "GET /api/verify after Pass" | tee -a "$LOG"
ui_curl "http://127.0.0.1:$UI_PORT/api/verify" | tee -a "$LOG"
echo | tee -a "$LOG"
echo "recapture pane.png with Verify Pass banner" | tee -a "$LOG"
if [[ "${GHOST_DEMO_SKIP_SCREENSHOT:-}" == "1" ]]; then
  echo "screenshot skipped" | tee -a "$LOG"
else
  export PANE_WAIT_SELECTOR="#verifyBanner.pass"
  capture_pane | tee -a "$LOG"
  unset PANE_WAIT_SELECTOR
fi

echo "Beat 6b: break ghost index.html, expect Fail, restore (Apply stays allowed)" | tee -a "$LOG"
python - <<'PY' | tee -a "$LOG"
import os
os.environ.pop("GHOST_RUN_TEST", None)
from ghost_run.state import get_state
from ghost_run.verify import format_verify, run_verify

state = get_state()
ghost = state.ghost_root
real = state.real_root
assert ghost is not None and real is not None
target = ghost / "index.html"
assert target.is_file(), "ghost index.html missing before break"
backup = target.read_bytes()
target.unlink()
try:
    failed = run_verify(state)
    print(format_verify(failed), end="")
    assert failed.get("status") == "fail", failed
    assert failed.get("advisory") is True
    assert failed.get("exit_code") not in (0, None)
    assert "missing index.html" in (failed.get("log") or "")
    assert (real / "index.html").is_file(), "real index.html was modified"
    print("VERIFY_FAIL")
finally:
    target.write_bytes(backup)
assert target.is_file()
assert get_state().last_verify["status"] == "fail"
print("restored ghost index.html; Apply still allowed after Fail")
PY
echo "GET /api/verify after Fail" | tee -a "$LOG"
ui_curl "http://127.0.0.1:$UI_PORT/api/verify" | tee -a "$LOG"
echo | tee -a "$LOG"

echo "Beat 7: apply selected (3 deletions) via UI API after Verify Fail" | tee -a "$LOG"
python - <<'PY' | tee -a "$LOG"
import json, os, urllib.request
from ghost_run.state import get_state
verify = get_state().last_verify or {}
assert verify.get("status") == "fail", verify
assert verify.get("advisory") is True
print("apply after Verify Fail (advisory; Apply stays available)")
paths = ["src/unused_legacy.js", "src/old_helper.js", "tmp/cache.tmp"]
req = urllib.request.Request(
    f"http://127.0.0.1:{os.environ.get('UI_PORT', '8765')}/api/apply",
    data=json.dumps({"paths": paths}).encode(),
    headers={
        "Content-Type": "application/json",
        "X-Ghost-Run-Token": os.environ["UI_TOKEN"],
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode())
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

echo "Beat 8: discard remaining" | tee -a "$LOG"
python - <<'PY' | tee -a "$LOG"
import os, urllib.request
req = urllib.request.Request(
    f"http://127.0.0.1:{os.environ.get('UI_PORT', '8765')}/api/discard",
    data=b"{}",
    headers={
        "Content-Type": "application/json",
        "X-Ghost-Run-Token": os.environ["UI_TOKEN"],
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode())
from ghost_run.state import get_state
st = get_state()
print("after discard enabled=", st.enabled, "ghost=", st.ghost_root)
PY

echo "Beat 9: copy / reflink timing on ~5k-file tree (hard links refused)" | tee -a "$LOG"
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
for strat in ("auto", "copy", "reflink", "hardlink", "hardlink-file"):
    try:
        ghost, dt, used = timed_create(tree, strategy=strat)
        count = sum(
            1 for p in ghost.rglob("*")
            if p.is_file() and p.name not in (".ghost-run-marker", ".ghost-run-strategy")
        )
        results[strat] = {"seconds": round(dt, 4), "used": used, "files_in_ghost": count}
        discard_ghost_copy(ghost)
    except Exception as e:
        results[strat] = {"error": repr(e), "refused": "hard link" in str(e).lower()}

out = Path(os.environ["TIMING_JSON"])
out.write_text(json.dumps({"tree_files_approx": n, "results": results}, indent=2) + "\n")
print(json.dumps(results, indent=2))
print(f"Wrote {out}")
shutil.rmtree(tree, ignore_errors=True)
PY

echo "" | tee -a "$LOG"
echo "DEMO COMPLETE" | tee -a "$LOG"
echo "  log: $LOG"
echo "  pane: $PANE_PNG (recaptured after Verify Pass)"
echo "  verify: Pass, then Fail with index.html restored; Apply ran after Fail"
echo "  timing: $TIMING_JSON"
echo "  work (left for inspection): $WORK"
