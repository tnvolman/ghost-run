/**
 * Ghost Run — Desktop half (optional).
 *
 * Hermes Desktop plugins register UI surfaces via `@hermes/plugin-sdk`.
 * A live "panes" area that can render an interactive apply/discard UI is
 * desirable for the 90s demo, but the exact pane host API varies by Desktop
 * build. This file registers a pane when the SDK exposes `area: 'panes'`;
 * otherwise the agent half still writes `ghost-run-diff.html` (struck-through
 * deletions, modified files) via `hermes ghost diff`.
 *
 * Gap note (see README): interactive Apply/Discard buttons inside Desktop
 * are UNPROVEN against a live Desktop host in this local build. The HTML
 * fallback + CLI apply/discard are the supported path.
 */

export default {
  name: 'ghost-run',
  version: '0.1.0',
  async register(ctx) {
    const hasPanes =
      ctx &&
      typeof ctx.register === 'function' &&
      // Heuristic: docs describe ctx.register({ area: 'panes', ... })
      true;

    if (!hasPanes) {
      console.info(
        '[ghost-run] Desktop SDK register() unavailable; use hermes ghost diff HTML fallback'
      );
      return;
    }

    try {
      ctx.register({
        area: 'panes',
        id: 'ghost-run-diff',
        title: 'Ghost Run',
        // Placeholder component: Desktop hosts that support panes should
        // replace/render this with the HTML report or a native file list.
        render: async () => ({
          kind: 'html-url-or-inline',
          note:
            'Open the path printed by `hermes ghost diff` (ghost-run-diff.html). ' +
            'Apply: hermes ghost apply [paths...]  Discard: hermes ghost discard',
        }),
      });
      console.info('[ghost-run] registered Desktop pane ghost-run-diff');
    } catch (err) {
      console.warn(
        '[ghost-run] Desktop pane registration failed; HTML/CLI fallback remains:',
        err
      );
    }
  },
};
