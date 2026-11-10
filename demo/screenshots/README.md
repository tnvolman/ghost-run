# What these images are

Both shots are the loopback review page (`hermes ghost ui`) in headless Chrome on Linux. They are not Hermes Desktop, and they are not the Desktop pane.

- `review-page-load.png` — Load. The page shows one added file and one deletion. Nothing has been applied.
- `review-page-after-apply.png` — After Apply of that deletion only. The deletion is gone from the list. The added file is still pending.

The Desktop pane was not rendered. It has no diff view of its own. It only opens this page in the system browser.
