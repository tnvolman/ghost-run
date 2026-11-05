"""Text and HTML renderers for Ghost Run diffs (Desktop pane fallback)."""

from __future__ import annotations

import html
from pathlib import Path

from .diff import DiffReport


def render_html(report: DiffReport) -> str:
    """Self-contained HTML page: struck-through deletions, colored adds/mods."""
    rows = []
    for ch in report.added:
        rows.append(
            f'<tr class="added"><td class="kind">+</td>'
            f"<td>{html.escape(ch.path)}</td></tr>"
        )
    for ch in report.modified:
        rows.append(
            f'<tr class="modified"><td class="kind">~</td>'
            f"<td>{html.escape(ch.path)}</td></tr>"
        )
    for ch in report.deleted:
        rows.append(
            f'<tr class="deleted"><td class="kind">−</td>'
            f'<td><del>{html.escape(ch.path)}</del></td></tr>'
        )

    diff_blocks = []
    for ch in report.all_changes:
        if not ch.unified_diff:
            continue
        diff_blocks.append(
            f"<h3>{html.escape(ch.kind)} {html.escape(ch.path)}</h3>"
            f"<pre class='udiff'>{html.escape(ch.unified_diff)}</pre>"
        )

    body_rows = "\n".join(rows) or "<tr><td colspan=2>No changes</td></tr>"
    blocks = "\n".join(diff_blocks)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Ghost Run Diff</title>
<style>
  body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 1.5rem; background: #0f1115; color: #e6e6e6; }}
  h1 {{ font-size: 1.25rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }}
  td, th {{ padding: 0.35rem 0.6rem; border-bottom: 1px solid #2a2e37; text-align: left; }}
  .kind {{ width: 2rem; font-weight: 700; }}
  tr.added .kind {{ color: #3dd68c; }}
  tr.modified .kind {{ color: #f5a524; }}
  tr.deleted .kind, tr.deleted del {{ color: #f31260; }}
  tr.deleted del {{ text-decoration: line-through; }}
  pre.udiff {{ background: #1a1d24; padding: 0.75rem; overflow: auto; font-size: 0.85rem; border-radius: 6px; }}
  .meta {{ color: #9aa3b2; font-size: 0.9rem; margin-bottom: 1rem; }}
  .note {{ margin-top: 1.5rem; color: #9aa3b2; font-size: 0.85rem; }}
</style>
</head>
<body>
  <h1>Ghost Run — pending changes</h1>
  <div class="meta">
    real: {html.escape(str(report.real_root))}<br/>
    ghost: {html.escape(str(report.ghost_root))}<br/>
    +{len(report.added)} added · ~{len(report.modified)} modified · −{len(report.deleted)} deleted
  </div>
  <table>
    <thead><tr><th></th><th>path</th></tr></thead>
    <tbody>
      {body_rows}
    </tbody>
  </table>
  {blocks}
  <p class="note">
    Prefer the interactive pane: <code>hermes ghost ui</code> (localhost) or the
    Desktop <code>plugin.js</code> pane (checkboxes, badges, Apply selected / Discard).
    CLI: <code>hermes ghost apply [paths...]</code> / <code>hermes ghost discard</code>.
  </p>
</body>
</html>
"""


def write_html_report(report: DiffReport, dest: Path) -> Path:
    dest = Path(dest)
    dest.write_text(render_html(report), encoding="utf-8")
    return dest
