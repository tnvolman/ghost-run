# Ghost Run — Desktop half

`plugin.js` registers a Hermes Desktop **panes** surface (via `ctx.openWorkspace`
when available, else `ctx.register({ area: 'panes', ... })`) that renders:

- per-file checkboxes
- struck-through deletions
- added / modified badges
- unified diff preview
- **Verify** (advisory Pass/Fail + truncated log; does not block Apply)
- **Apply selected** / **Discard** buttons

The pane talks to the local agent-half API started by:

```bash
hermes ghost ui
# → http://127.0.0.1:<port>/?token=<random>
```

Paste that full URL (token included) into the pane’s API base. Packaged Desktop
loads `file://` with web security on, so Load (`GET /api/diff`) and Apply
(`POST /api/apply`) are cross-origin and send `Origin: null`. The UI echoes
`Access-Control-Allow-Origin` only for that origin. The token and the loopback
Host check still apply. Any other `Origin` gets no CORS header.

Imports are limited to `@hermes/plugin-sdk` and `react` / `react/jsx-runtime`
(see Hermes `plugin_validate_desktop.py`).

## Headless / screen-record fallback

The full Electron Desktop app is not required for demos. Run:

```bash
hermes ghost ui --port 8765
```

Then open or screenshot `http://127.0.0.1:8765/` — same interactive pane.
