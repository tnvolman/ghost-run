"""Localhost interactive Apply/Discard pane (web fallback for Desktop).

Serves the same UX the Desktop ``plugin.js`` pane targets:
  - per-file checkboxes
  - struck-through deletions
  - modified / added badges
  - unified diff viewer
  - Apply selected / Discard wired to apply/discard logic

Start with ``hermes ghost ui`` (or ``python -m ghost_run.ui_server``).

The server binds to ``127.0.0.1`` only. Every request must send
``Host: 127.0.0.1:<port>`` or ``Host: localhost:<port>``. The printed URL
puts the session token in the fragment (``#token=``). The page reads that
once, strips it with ``history.replaceState``, and sends
``X-Ghost-Run-Token``. A query-string token is ignored and does not authenticate. The HTML shell is
served after the Host check; diff, apply, discard, and verify require the
header. Responses send no ``Access-Control-Allow-Origin`` header.
Desktop does not fetch this port; it opens the printed URL with
``ctx.os.openExternal``.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .apply import apply_paths, discard_session
from .diff import DiffReport, compute_diff
from .risk import ai_enabled, annotate, blocked_flags, model_notes, summarize_counts
from .state import get_state
from .verify import run_verify

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# ``localhost`` is accepted as a Host header equivalent of 127.0.0.1.
# The socket still binds to 127.0.0.1. Other hosts are rejected.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def report_to_dict(report: DiffReport) -> Dict[str, Any]:
    def row(ch):
        return {
            "path": ch.path,
            "kind": ch.kind,
            "unified_diff": ch.unified_diff,
        }

    return {
        "real_root": str(report.real_root),
        "ghost_root": str(report.ghost_root),
        "added": [row(c) for c in report.added],
        "modified": [row(c) for c in report.modified],
        "deleted": [row(c) for c in report.deleted],
        "counts": {
            "added": len(report.added),
            "modified": len(report.modified),
            "deleted": len(report.deleted),
        },
    }


def current_diff_payload() -> Dict[str, Any]:
    state = get_state()
    if state.real_root is None or state.ghost_root is None:
        return {
            "ok": False,
            "error": "no active sandbox (try hermes ghost on first)",
            "enabled": bool(state.enabled),
            "changes": [],
            "counts": {"added": 0, "modified": 0, "deleted": 0},
        }
    if not state.ghost_root.exists():
        return {
            "ok": False,
            "error": f"sandbox missing at {state.ghost_root}",
            "enabled": bool(state.enabled),
            "changes": [],
            "counts": {"added": 0, "modified": 0, "deleted": 0},
        }
    report = compute_diff(state.real_root, state.ghost_root)
    data = report_to_dict(report)
    changes: List[Dict[str, Any]] = []
    for ch in report.added:
        changes.append({"path": ch.path, "kind": "added", "unified_diff": ch.unified_diff})
    for ch in report.modified:
        changes.append({"path": ch.path, "kind": "modified", "unified_diff": ch.unified_diff})
    for ch in report.deleted:
        changes.append({"path": ch.path, "kind": "deleted", "unified_diff": ch.unified_diff})
    annotate(changes, state.real_root, state.ghost_root)
    ai = model_notes(changes) if ai_enabled() else {"status": "off", "notes": {}, "summary": ""}
    for c in changes:
        note = ai["notes"].get(c["path"])
        if note:
            c["risk"]["ai_note"] = note
    return {
        "ok": True,
        "enabled": bool(state.enabled),
        "risk_counts": summarize_counts(changes),
        "blocked": blocked_flags(),
        "ai": {"status": ai["status"], "summary": ai.get("summary", "")},
        "real_root": data["real_root"],
        "ghost_root": data["ghost_root"],
        "counts": data["counts"],
        "changes": changes,
        "verify": state.last_verify,
    }


PANE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Ghost Run — Apply / Discard</title>
<style>
  :root {
    --bg: #0f1115; --panel: #161a22; --border: #2a2e37;
    --text: #e6e6e6; --muted: #9aa3b2;
    --add: #3dd68c; --mod: #f5a524; --del: #f31260;
    --accent: #6c8cff; --btn: #1e2430; --btn-hover: #2a3344;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
  }
  header {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 14px 18px; border-bottom: 1px solid var(--border);
    background: var(--panel); position: sticky; top: 0; z-index: 5;
  }
  h1 { font-size: 1.05rem; margin: 0; font-weight: 600; letter-spacing: .01em; }
  .meta { color: var(--muted); font-size: .8rem; flex: 1; min-width: 180px; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  button {
    border: 1px solid var(--border); background: var(--btn); color: var(--text);
    border-radius: 8px; padding: 8px 14px; font-size: .85rem; cursor: pointer;
  }
  button:hover { background: var(--btn-hover); }
  button.primary { background: var(--accent); border-color: transparent; color: #0b1020; font-weight: 600; }
  button.danger { background: #3a1520; border-color: #5a2030; color: #ffb4c4; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  main { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.4fr); gap: 0; min-height: calc(100vh - 58px); }
  @media (max-width: 800px) { main { grid-template-columns: 1fr; } }
  .list, .diffpane { padding: 14px 16px; overflow: auto; max-height: calc(100vh - 58px); }
  .list { border-right: 1px solid var(--border); }
  .counts { color: var(--muted); font-size: .85rem; margin-bottom: 10px; }
  .row {
    display: grid; grid-template-columns: 22px 46px 64px 1fr; gap: 8px; align-items: center;
    padding: 8px 6px; border-radius: 8px; cursor: pointer;
  }
  .row:hover, .row.active { background: #1c2230; }
  .row input { accent-color: var(--accent); }
  .badge {
    font-size: .68rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
    padding: 2px 7px; border-radius: 999px; text-align: center; width: fit-content;
  }
  .badge.added { background: #143526; color: var(--add); }
  .badge.modified { background: #3a2a10; color: var(--mod); }
  .badge.deleted { background: #3a1520; color: var(--del); }
  .risk { font-size: .66rem; font-weight: 800; text-transform: uppercase; padding: 2px 6px; border-radius: 6px; text-align: center; }
  .risk.high { background: #5a1020; color: #ff8fa8; box-shadow: 0 0 0 1px #f31260 inset; }
  .risk.med { background: #3a2a10; color: #f5c46a; }
  .risk.low { background: #143526; color: #7be8b0; }
  .reason { grid-column: 3 / -1; color: var(--muted); font-size: .76rem; margin-top: -4px; }
  .reason .ai { color: #b9c6ff; display: block; }
  .riskbar { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; font-size: .8rem; flex-wrap: wrap; }
  .blocked { background: #2a1018; border: 1px solid #5a2030; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; font-size: .78rem; color: #ffb4c4; }
  .aisum { color: #b9c6ff; font-size: .78rem; margin-bottom: 8px; }
  .path { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; word-break: break-all; }
  .path.deleted { color: var(--del); text-decoration: line-through; }
  .empty { color: var(--muted); padding: 24px 8px; }
  .diffpane h2 { font-size: .95rem; margin: 0 0 10px; }
  pre.udiff {
    margin: 0; background: #12151c; border: 1px solid var(--border); border-radius: 10px;
    padding: 12px; overflow: auto; font-size: .78rem; line-height: 1.45;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap;
  }
  .line-add { color: var(--add); }
  .line-del { color: var(--del); }
  .line-hunk { color: var(--accent); }
  .toast {
    position: fixed; bottom: 16px; right: 16px; background: #1e2a44; color: #dce6ff;
    padding: 10px 14px; border-radius: 10px; font-size: .85rem; display: none;
    max-width: min(420px, 90vw); box-shadow: 0 8px 24px rgba(0,0,0,.35);
  }
  .toast.show { display: block; }
  .select-bar { display: flex; gap: 10px; margin-bottom: 8px; font-size: .8rem; color: var(--muted); }
  .select-bar a { color: var(--accent); cursor: pointer; text-decoration: none; }
  .verify {
    border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
    margin-bottom: 8px; font-size: .8rem; background: #1a1e28; color: var(--muted);
  }
  .verify.pass { background: #143526; color: #7be8b0; border-color: #1f6b45; }
  .verify.fail { background: #3a1520; color: #ffb4c4; border-color: #5a2030; }
  .verify.error { background: #3a2a10; color: #f5c46a; border-color: #6a4a16; }
  .verify-log {
    margin: 0 0 10px; max-height: 180px; overflow: auto; white-space: pre-wrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .75rem;
    background: #12151c; border: 1px solid var(--border); border-radius: 8px; padding: 8px;
  }
</style>
</head>
<body>
  <header>
    <h1>Ghost Run</h1>
    <div class="meta" id="meta">Loading…</div>
    <div class="actions">
      <button id="refreshBtn" type="button">Refresh</button>
      <button id="verifyBtn" type="button">Verify</button>
      <button id="applyBtn" class="primary" type="button">Apply selected</button>
      <button id="discardBtn" class="danger" type="button">Discard</button>
    </div>
  </header>
  <main>
    <section class="list">
      <div class="counts" id="counts"></div>
      <div class="verify" id="verifyBanner">Verify not run. Advisory only — Apply stays available either way.</div>
      <pre class="verify-log" id="verifyLog" hidden></pre>
      <div class="riskbar" id="riskbar"></div>
      <div id="blocked"></div>
      <div class="aisum" id="aisum"></div>
      <div class="select-bar">
        <a id="selAll">Select all</a> · <a id="selNone">Select none</a> · <a id="selLow">Select only low-risk</a>
      </div>
      <div id="rows"></div>
    </section>
    <section class="diffpane">
      <h2 id="diffTitle">Unified diff</h2>
      <pre class="udiff" id="diffBody">Select a file to preview its unified diff.</pre>
    </section>
  </main>
  <div class="toast" id="toast"></div>
<script>
const state = { changes: [], selected: new Set(), active: null };
let verifyInFlight = false;
let GHOST_TOKEN = "";
(function takeFragmentToken() {
  const raw = (window.location.hash || "").replace(/^#/, "");
  if (!raw) return;
  GHOST_TOKEN = new URLSearchParams(raw).get("token") || "";
  history.replaceState(null, "", window.location.pathname + window.location.search);
})();
function api(path) { return path; }
function withAuth(init) {
  const headers = Object.assign({}, (init && init.headers) || {});
  if (GHOST_TOKEN) headers["X-Ghost-Run-Token"] = GHOST_TOKEN;
  return Object.assign({}, init || {}, { headers: headers });
}

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 3200);
}

function esc(t) { return String(t || '').replace(/&/g,'&amp;').replace(/</g,'&lt;'); }

function colorize(diff) {
  if (!diff) return '(no unified diff)';
  return diff.split('\n').map(line => {
    const esc = line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    if (line.startsWith('+') && !line.startsWith('+++')) return '<span class="line-add">' + esc + '</span>';
    if (line.startsWith('-') && !line.startsWith('---')) return '<span class="line-del">' + esc + '</span>';
    if (line.startsWith('@@')) return '<span class="line-hunk">' + esc + '</span>';
    return esc;
  }).join('\n');
}

function renderVerify(v) {
  const banner = document.getElementById('verifyBanner');
  const log = document.getElementById('verifyLog');
  if (!v) {
    banner.className = 'verify';
    banner.textContent = 'Verify not run. Advisory only — Apply stays available either way.';
    log.hidden = true;
    log.textContent = '';
    return;
  }
  const cmd = v.command ? ' · ' + v.command : '';
  if (v.status === 'pass') {
    banner.className = 'verify pass';
    banner.textContent = 'Pass — exit ' + v.exit_code + cmd;
  } else if (v.status === 'fail') {
    banner.className = 'verify fail';
    const why = v.timed_out ? 'timed out' : ('exit ' + v.exit_code);
    banner.textContent = 'Fail — ' + why + cmd;
  } else {
    banner.className = 'verify error';
    banner.textContent = v.error || 'Verify error';
  }
  if (v.log) {
    log.hidden = false;
    log.textContent = v.log;
  } else {
    log.hidden = true;
    log.textContent = '';
  }
}

function render() {
  const rows = document.getElementById('rows');
  if (!state.changes.length) {
    rows.innerHTML = '<div class="empty">No pending changes. Turn Ghost Mode on, let the agent edit, then Refresh.</div>';
    document.getElementById('diffBody').textContent = 'Nothing to show.';
    return;
  }
  rows.innerHTML = '';
  state.changes.forEach((ch, i) => {
    const div = document.createElement('div');
    div.className = 'row' + (state.active === ch.path ? ' active' : '');
    div.dataset.path = ch.path;
    const rk = ch.risk || {level: 'low', reason: ''};
    const checked = state.selected.has(ch.path) ? 'checked' : '';
    const pathClass = ch.kind === 'deleted' ? 'path deleted' : 'path';
    div.innerHTML =
      '<input type="checkbox" data-path="' + String(ch.path).replace(/"/g, '&quot;') + '" ' + checked + ' aria-label="select ' + esc(ch.path) + '"/>' +
      '<span class="risk ' + rk.level + '" title="risk">' + rk.level + '</span>' +
      '<span class="badge ' + ch.kind + '">' + ch.kind + '</span>' +
      '<span class="' + pathClass + '">' + esc(ch.path) + '</span>' +
      '<span class="reason">' + esc(rk.reason) +
        (rk.ai_note ? '<span class="ai">AI: ' + esc(rk.ai_note) + '</span>' : '') + '</span>';
    const cb = div.querySelector('input');
    cb.addEventListener('click', (e) => {
      e.stopPropagation();
      const path = cb.getAttribute('data-path') || div.dataset.path;
      if (cb.checked) state.selected.add(path); else state.selected.delete(path);
    });
    div.addEventListener('click', () => {
      state.active = ch.path;
      document.getElementById('diffTitle').textContent = ch.kind + ' — ' + ch.path;
      document.getElementById('diffBody').innerHTML = colorize(ch.unified_diff);
      render();
    });
    rows.appendChild(div);
  });
}

async function loadDiff() {
  const res = await fetch(api('/api/diff'), withAuth());
  const data = await res.json();
  const meta = document.getElementById('meta');
  if (!data.ok) {
    meta.textContent = data.error || 'No sandbox';
    document.getElementById('counts').textContent = '';
    state.changes = [];
    render();
    return;
  }
  meta.innerHTML = 'real: ' + data.real_root + '<br/>ghost: ' + data.ghost_root;
  document.getElementById('counts').textContent =
    '+' + data.counts.added + ' added · ~' + data.counts.modified +
    ' modified · −' + data.counts.deleted + ' deleted';
  state.changes = data.changes || [];
  const rc = data.risk_counts || {high:0, med:0, low:0};
  document.getElementById('riskbar').innerHTML =
    '<span class="risk high">' + rc.high + ' high</span>' +
    '<span class="risk med">' + rc.med + ' med</span>' +
    '<span class="risk low">' + rc.low + ' low</span>' +
    '<span style="color:var(--muted)">sorted by risk · you decide what applies</span>';
  const bl = data.blocked || [];
  document.getElementById('blocked').innerHTML = bl.length
    ? '<div class="blocked"><b>' + bl.length + ' blocked attempt(s)</b><br/>' + bl.slice(-5).map(b => '• ' + esc(b.reason)).join('<br/>') + '</div>'
    : '';
  const ai = data.ai || {};
  document.getElementById('aisum').textContent =
    ai.status === 'ok' && ai.summary ? 'AI summary: ' + ai.summary
    : ai.status === 'fallback' ? 'AI summary unavailable; showing heuristic flags.' : '';
  if (!verifyInFlight) renderVerify(data.verify || null);
  // Keep prior selection when paths still exist. When empty (first load
  // and after Apply), default-select everything except HIGH.
  if (state.selected.size === 0) {
    state.changes.forEach(c => {
      if (((c.risk || {}).level || 'low') !== 'high') state.selected.add(c.path);
    });
  } else {
    const alive = new Set(state.changes.map(c => c.path));
    [...state.selected].forEach(p => { if (!alive.has(p)) state.selected.delete(p); });
  }
  if (!state.active && state.changes[0]) {
    state.active = state.changes[0].path;
    document.getElementById('diffTitle').textContent =
      state.changes[0].kind + ' — ' + state.changes[0].path;
    document.getElementById('diffBody').innerHTML = colorize(state.changes[0].unified_diff);
  }
  render();
}

document.getElementById('refreshBtn').onclick = () => loadDiff().catch(e => toast(String(e)));
document.getElementById('verifyBtn').onclick = async () => {
  // Advisory only: do not disable Apply selected while this runs or on Fail.
  const btn = document.getElementById('verifyBtn');
  verifyInFlight = true;
  btn.disabled = true;
  document.getElementById('verifyBanner').className = 'verify';
  document.getElementById('verifyBanner').textContent = 'Running verify…';
  try {
    const res = await fetch(api('/api/verify'), withAuth({ method: 'POST' }));
    const data = await res.json();
    if (data.verify) renderVerify(data.verify);
    else {
      document.getElementById('verifyBanner').className = 'verify error';
      document.getElementById('verifyBanner').textContent = data.error || 'verify failed';
    }
  } catch (e) {
    toast(String(e));
  } finally {
    verifyInFlight = false;
    btn.disabled = false;
  }
};
document.getElementById('selAll').onclick = () => {
  state.changes.forEach(c => state.selected.add(c.path)); render();
};
document.getElementById('selLow').onclick = () => {
  state.selected.clear();
  state.changes.forEach(c => { if ((c.risk || {}).level === 'low') state.selected.add(c.path); });
  render();
};
document.getElementById('selNone').onclick = () => { state.selected.clear(); render(); };

function selectedPathsFromDom() {
  return [...document.querySelectorAll('.row input[type=checkbox][data-path]:checked')]
    .map(cb => cb.getAttribute('data-path'))
    .filter(Boolean);
}

document.getElementById('applyBtn').onclick = async () => {
  // Trust the visible ticks, not a possibly stale Set.
  const paths = selectedPathsFromDom();
  state.selected = new Set(paths);
  if (!paths.length) { toast('Select at least one path'); return; }
  const highPaths = paths.filter(p => {
    const ch = state.changes.find(c => c.path === p);
    return ch && (ch.risk || {}).level === 'high';
  });
  if (highPaths.length && !confirm('Apply high-risk change(s): ' + highPaths.join(', ') + '?')) return;
  const res = await fetch(api('/api/apply'), withAuth({
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ paths })
  }));
  const data = await res.json();
  toast(data.ok ? ('Applied ' + (data.actions || []).length + ' path(s)') : (data.error || 'apply failed'));
  state.selected.clear();
  state.active = null;
  await loadDiff();
};

document.getElementById('discardBtn').onclick = async () => {
  if (!confirm('Discard the entire ghost sandbox?')) return;
  const res = await fetch(api('/api/discard'), withAuth({ method: 'POST' }));
  const data = await res.json();
  toast(data.message || (data.ok ? 'Discarded' : 'discard failed'));
  state.selected.clear();
  state.active = null;
  await loadDiff();
};

loadDiff().catch(e => toast(String(e)));
setInterval(() => loadDiff().catch(() => {}), 4000);
</script>
</body>
</html>
"""


class GhostUIHandler(BaseHTTPRequestHandler):
    server_version = "GhostRunUI/0.1"

    def log_message(self, fmt: str, *args) -> None:  # quieter
        pass

    def _send(self, code: int, body: bytes, content_type: str, *, close: bool = False) -> None:
        if close:
            self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _drain_body(self) -> None:
        """Read a rejected request body so it is not parsed as the next request."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return
        remaining = min(length, 1024 * 1024)
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except Exception:
            return

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _is_shell(self) -> bool:
        """The HTML shell carries no diff. The token arrives in the URL fragment."""
        return self.command == "GET" and urlparse(self.path).path in ("/", "/index.html", "/pane")

    def _presented_token(self) -> str:
        # Query-string tokens are ignored. They show up in logs and Referer.
        return (self.headers.get("X-Ghost-Run-Token") or "").strip()

    def _unauthorized(self) -> bool:
        """Send 403/401 and return True when the request must not proceed."""
        if not self._host_allowed():
            self._drain_body()
            self._json(403, {"ok": False, "error": "forbidden host"}, close=True)
            return True
        if self._is_shell():
            return False
        expected = getattr(self.server, "ghost_token", "") or ""
        if not _token_match(self._presented_token(), expected):
            self._drain_body()
            self._json(401, {"ok": False, "error": "unauthorized"}, close=True)
            return True
        return False

    def _json(self, code: int, payload: Dict[str, Any], *, close: bool = False) -> None:
        raw = json.dumps(payload, indent=2).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8", close=close)

    def do_OPTIONS(self) -> None:  # noqa: N802
        # No CORS headers. A same-origin call that already carries the token
        # gets an empty 204. Every other caller is rejected like any request.
        if self._unauthorized():
            return
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        if self._unauthorized():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/pane"):
            self._send(200, PANE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/diff":
            self._json(200, current_diff_payload())
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "service": "ghost-run-ui"})
            return
        if path == "/api/verify":
            state = get_state()
            self._json(200, {"ok": True, "verify": state.last_verify, "advisory": True})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self._unauthorized():
            return
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "invalid JSON"})
            return

        if path == "/api/apply":
            paths = payload.get("paths") if isinstance(payload, dict) else None
            if paths is not None and not isinstance(paths, list):
                self._json(400, {"ok": False, "error": "paths must be a list"})
                return
            try:
                actions = apply_paths(get_state(), paths)
            except RuntimeError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"ok": True, "actions": actions})
            return

        if path == "/api/discard":
            msg = discard_session(get_state())
            self._json(200, {"ok": True, "message": msg})
            return

        if path == "/api/verify":
            result = run_verify(get_state())
            # Pass and Fail are both successful advisory runs. A red result
            # does not block Apply. ok is false when the command did not run.
            self._json(
                200,
                {
                    "ok": result.get("status") in ("pass", "fail"),
                    "verify": result,
                    "advisory": True,
                    "error": result.get("error"),
                },
            )
            return

        self._json(404, {"ok": False, "error": "not found"})


def write_port_file(port: int) -> Path:
    from .state import _default_session_path

    path = _default_session_path().parent / "ui.port"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(port) + "\n", encoding="utf-8")
    return path


def _token_match(presented: str, expected: str) -> bool:
    if not presented or not expected:
        return False
    left = presented.encode("utf-8")
    right = expected.encode("utf-8")
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = False,
    block: bool = True,
) -> Tuple[ThreadingHTTPServer, int]:
    """Bind and optionally block forever. Returns (server, bound_port).

    The server object carries ``ghost_token``. The printed URL includes it.
    ``host`` must be ``127.0.0.1`` or ``localhost`` (bound as 127.0.0.1).
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            "ghost-run: the review UI only binds to 127.0.0.1 "
            "(localhost is accepted and bound as 127.0.0.1). "
            f"Refusing host {host!r}."
        )
    httpd = ThreadingHTTPServer(("127.0.0.1", port), GhostUIHandler)
    token = secrets.token_urlsafe(32)
    httpd.ghost_token = token  # type: ignore[attr-defined]
    bound = httpd.server_address[1]
    write_port_file(bound)
    url = f"http://127.0.0.1:{bound}/#token={token}"
    print(f"Ghost Run UI: {url}", flush=True)
    print(
        "  Host must be 127.0.0.1:<port> or localhost:<port>. Other hosts are rejected.",
        flush=True,
    )
    print(
        "  API calls need header X-Ghost-Run-Token. A query-string token is ignored and does not authenticate.",
        flush=True,
    )
    print(
        "  No Access-Control-Allow-Origin header is sent. Open this URL in a browser.",
        flush=True,
    )
    print("  GET  /           interactive Verify / Apply / Discard pane", flush=True)
    print("  GET  /api/diff   JSON change list (includes last verify)", flush=True)
    print("  GET  /api/verify last verify result", flush=True)
    print("  POST /api/verify run configured test in the ghost copy (advisory)", flush=True)
    print("  POST /api/apply  {\"paths\":[...]}  — empty/omit = all", flush=True)
    print("  POST /api/discard", flush=True)
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
    if block:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down Ghost Run UI")
        finally:
            httpd.server_close()
    else:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
    return httpd, bound


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="ghost-run-ui", description="Ghost Run interactive pane")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--open", action="store_true", help="Open a browser tab")
    args = p.parse_args(argv)
    try:
        serve(host=args.host, port=args.port, open_browser=args.open, block=True)
    except ValueError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
