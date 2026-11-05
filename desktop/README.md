# Ghost Run Desktop half

Optional. The agent half does not depend on Desktop.

- **Proven locally:** text diff + `ghost-run-diff.html` (struck-through deletions).
- **Unproven:** live interactive pane with Apply/Discard buttons inside Hermes Desktop.
  `plugin.js` attempts `ctx.register({ area: 'panes', ... })` per the Desktop plugin SDK docs.
  If your Desktop build rejects that shape, keep using `hermes ghost diff|apply|discard`.
