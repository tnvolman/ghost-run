#!/usr/bin/env python3
"""Fast Ghost Run demo check.

Exits 0 when the fixture's keep-files are present in the current directory
(the ghost copy, when Ghost Run runs it). No third-party imports and no
node_modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path.cwd()
NEEDED = ("index.html", "src/app.js", "package.json", "public/styles.css")


def main() -> int:
    missing = [name for name in NEEDED if not (ROOT / name).is_file()]
    if missing:
        print("ghost verify: missing " + ", ".join(missing), file=sys.stderr)
        return 1
    print(f"ghost verify: fixture healthy ({ROOT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
