# Ghost Demo Site

Tiny web project used by Ghost Run's 90-second demo.

**Keep:** `index.html`, `src/app.js`, `public/styles.css`, `package.json`, this README.

**Obvious unused (agent should remove under Ghost Mode):**
- `src/unused_legacy.js`
- `src/old_helper.js`
- `tmp/cache.tmp`

`node_modules/` and `dist/` should be ignored by the ghost copy.

**Verify (advisory):** `.ghost-run-test` runs `python3 scripts/ghost_verify.py` with cwd set to the ghost copy. The script exits 0 when the keep-files above are present. It uses only the Python standard library, so the demo can show **Pass** without installing `node_modules`. Override with `GHOST_RUN_TEST` if you want a different command. A failing result does not block Apply.
