# What these images are

macOS Chrome shots, opened by Hermes Desktop v0.23.1 at 54badf24. The review page was served by `hermes ghost ui` on test folder `/tmp/ghost-risk`. They are the loopback review page, not a rendering of the Desktop pane itself. The pane only opens this page in the system browser.

- `desktop-macos-01-review-load.png` — First load. `.env` (HIGH) is unticked; the MED and LOW files are ticked.
- `desktop-macos-02-high-confirm.png` — Confirm dialog after ticking `.env` and pressing Apply selected: "Apply high-risk change(s): .env?".
- `desktop-macos-03-after-cancel.png` — After Cancel. All three files are still pending. On disk, `notes.txt` still read `notes v2`, `.env` still read `DEMO_MODE=on`, and `package-lock.json` was not created.

Older headless Chrome shots on Linux, against the same loopback page, not from Hermes Desktop:

- `review-page-load.png` — Load. The page shows one added file and one deletion. Nothing has been applied.
- `review-page-after-apply.png` — After Apply of that deletion only. The deletion is gone from the list. The added file is still pending.
