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
# → http://127.0.0.1:8765/
```

Imports are limited to `@hermes/plugin-sdk` and `react` / `react/jsx-runtime`
(see Hermes `plugin_validate_desktop.py`).

## Headless / screen-record fallback

The full Electron Desktop app is not required for demos. Run:

```bash
hermes ghost ui --port 8765
```

Then open or screenshot `http://127.0.0.1:8765/` — same interactive pane.
