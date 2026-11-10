# Ghost Run — Desktop half

`plugin.js` opens the loopback review page in the system browser. It does not
fetch `127.0.0.1`.

Registration, matching the Desktop SDK:

- `host.openWorkspace` when `typeof host.openWorkspace === 'function'`
  (`export const host` in `apps/desktop/src/sdk/index.ts`)
- otherwise `ctx.register({ area: 'panes', ... })`

The button calls `ctx.os.openExternal` (`PluginOs` in
`apps/desktop/src/contrib/plugin.ts`) with the URL printed by:

```bash
hermes ghost ui
# → http://127.0.0.1:<port>/#token=<random>
```

That page is same-origin with the review server. Load and Apply run there.
The server still requires a loopback `Host`. API calls send
`X-Ghost-Run-Token`. A query-string token is ignored and does not authenticate. The server sends no
`Access-Control-Allow-Origin` header.

The SDK sandboxed iframe (`apps/desktop/src/components/ui/sandboxed-frame.tsx`)
is not used. Its default sandbox is `allow-scripts` only, so a page embedded
there would execute as an opaque origin.

Imports are limited to `@hermes/plugin-sdk` and `react` / `react/jsx-runtime`
(see Hermes `plugin_validate_desktop.py`).

## Browser page

The full Electron app is not required to review a diff. Run:

```bash
hermes ghost ui
```

Open the printed URL. Same interactive pane: checkboxes, risk badges, Verify,
Apply selected, Discard.
