# Desktop check (about five minutes)

This is for a real Hermes Desktop window. It was verified on macOS in Hermes Desktop v0.23.1 at 54badf24. The shots `demo/screenshots/desktop-macos-01-review-load.png`, `desktop-macos-02-high-confirm.png`, and `desktop-macos-03-after-cancel.png` are from that check. The older `review-page-load.png` and `review-page-after-apply.png` shots are headless Chrome on Linux. Windows is untested.

Use the commit named in the pull request. Do not use an older copy already sitting in `~/.hermes`.

## 1. Install this commit

Quit Hermes Desktop first.

Remove every older Ghost Run install:

```bash
rm -rf ~/.hermes/plugins/ghost-run
rm -rf ~/.hermes/desktop-plugins/ghost-run
rm -rf ~/.hermes/profiles/*/plugins/ghost-run
rm -rf ~/.hermes/profiles/*/desktop-plugins/ghost-run
```

Clone this branch and check out the pull-request commit. The full hash is in the PR body. Call it `SHA` below:

```bash
git clone https://github.com/tnvolman/ghost-run.git
cd ghost-run
git fetch origin cursor/ghost-review-blockers-1b18
git checkout SHA
git rev-parse HEAD
```

`git rev-parse HEAD` must print that same SHA. If it does not, stop. You are on the wrong commit.

Link this folder in and make sure `ghost-run` is enabled:

```bash
mkdir -p ~/.hermes/plugins
ln -s "$(pwd)" ~/.hermes/plugins/ghost-run
```

In `~/.hermes/config.yaml`, under `plugins.enabled`, include `ghost-run`. If a profile has its own `config.yaml`, enable it there too.

Start Hermes Desktop again (quit it fully, then open it). In the app, confirm the plugin row is this install, not a leftover.

## 2. Throwaway project

```bash
mkdir -p /tmp/ghost-try
printf 'keep\n' > /tmp/ghost-try/keep.txt
cd /tmp/ghost-try
hermes ghost on
hermes ghost status
```

Status prints a ghost path. Change only that copy:

```bash
printf 'ghost\n' > /PATH/FROM/STATUS/keep.txt
```

Leave `/tmp/ghost-try/keep.txt` as `keep`.

## 3. Clicks

In another terminal, from `/tmp/ghost-try`:

```bash
hermes ghost ui
```

The command prints a `http://127.0.0.1:<port>/#token=...` URL.

In Desktop:

1. Open the Ghost Run pane (workspace tab, or the panes list).
2. Paste that whole URL into the box.
3. Click **Open review page**.

The system browser should open the review page.

## 4. What a pass looks like

- **Open.** A normal browser tab shows the Ghost Run page (file list and diff). The address bar no longer shows `#token=` after the page loads.
- **Reload.** Reloading the review page (Cmd+R) shows `unauthorized`, because the page removes the token from the address bar after load. Reopen the URL that `hermes ghost ui` printed.
- **Load.** The page lists `keep.txt` as modified. You did not press Apply.
- **Before Apply.** `/tmp/ghost-try/keep.txt` still reads `keep`.
- **Apply.** Select `keep.txt` and press **Apply selected**. The file then reads `ghost`. Nothing else in the folder changes.

## 5. If something fails

Send back:

- A screenshot of the Desktop pane and, if the browser opened, of the page. Crop or cover the address bar. Do not send the printed URL, anything after `#`, or the token.
- The Desktop version from Help → About, or the output of `hermes --version`.
- The exact error text, with everything after `#` deleted.
- The path of the terminal where `hermes ghost ui` is running. Do not paste log lines that contain `#token=` or the token.

Stop there. Do not retry against an older plugin folder.
