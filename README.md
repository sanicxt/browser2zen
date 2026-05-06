# Arc → Zen

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20arm64-lightgrey?logo=apple)](https://github.com/rafcabezas/arc2zen/releases/latest)
[![Python](https://img.shields.io/badge/python-3.7%2B-yellow?logo=python&logoColor=white)](https://www.python.org/)
[![PyWebView](https://img.shields.io/badge/GUI-PyWebView-5e86ff)](https://pywebview.flowrl.com/)
[![Latest release](https://img.shields.io/github/v/release/rafcabezas/arc2zen?display_name=tag&label=release)](./releases/latest)

Move your Arc browser setup to [Zen Browser](https://zen-browser.app/)
in under a minute. Workspaces, pinned tabs, folders, bookmarks, browsing
history, and login state, all carried across.

A polished single-window app handles the whole migration for you. No
terminal, no Python, no install steps to follow.

---

## Install (macOS, Apple Silicon)

1. Open the [latest release](./releases/latest) and download
   `Arc2Zen-x.y.z-arm64.dmg`.
2. Double-click the DMG to mount it, then double-click `Arc2Zen.app`.
3. macOS will show a dialog ("damaged" or "cannot verify"). Click
   **Done**. Do **not** click "Move to Trash".
4. Open  → **System Settings** → **Privacy & Security**.
5. Scroll the right pane until you see *"Arc2Zen was blocked to
   protect your Mac."* Click **Open Anyway**.
6. Enter your Mac password if prompted.
7. macOS shows one final confirmation. Click **Open Anyway**.

The app launches and walks you through detection, preview, and
migration. Steps 3 to 7 only happen the first time. After that you can
double-click `Arc2Zen.app` normally.

When you're done, drag the DMG to the Trash. Arc2Zen runs from inside
the DMG and doesn't install itself anywhere.

> **Why all the steps?** Apple charges $99/year for the developer
> certificate that removes this prompt. Arc2Zen is free open-source
> software and that fee isn't worth passing on. The bundle is ad-hoc
> codesigned, so you can verify its contents have not been tampered
> with at any time:
>
> ```
> codesign --verify --deep --strict /Volumes/Arc2Zen/Arc2Zen.app
> ```

### Don't have Zen yet?

If Zen Browser isn't installed yet, the detection screen offers a
**Download Zen** button. Install Zen, launch it once so it creates
your profile, then click **Recheck** in Arc2Zen and continue.

---

## What gets migrated

| | |
| --- | --- |
| **Spaces** | Each Arc space becomes a Zen workspace, with its emoji icon and colour theme. |
| **Pinned tabs** | All pinned tabs land on the matching workspace, in their original order. |
| **Folders** | The full nested folder hierarchy is preserved, collapsed by default to keep your sidebar clean. |
| **Essential tabs** | Arc's top-toolbar Essentials become pinned tabs on the right space. |
| **Open tabs** | Optional. Live tabs become real Zen tabs. |
| **Bookmarks** | Pinned tabs are also mirrored to Firefox bookmarks as a backup. |
| **Favicons** | Arc's cached icons are inlined so tabs show their icons immediately, with no waiting for refetch. |
| **History** | Optional. Browsing history with original timestamps is copied over. |
| **Login state** | Optional, macOS only. Arc cookies are decrypted (one-time Keychain prompt) and re-encrypted into Zen so you stay logged in to Gmail, Twitter, and the rest. |

Every step writes a timestamped backup beside your Zen profile before
it changes anything, and Arc data is read-only. The Backups screen
inside the app lets you restore or delete those backups any time.

---

## Run from source

For Linux, Intel Mac, contributors, or anyone who'd rather skip the DMG:

```bash
git clone https://github.com/rafcabezas/arc2zen.git
cd arc2zen
pip install -r requirements.txt

# CLI
python3 migrate_arc_to_zen.py --dry-run    # preview only
python3 migrate_arc_to_zen.py              # actual migration

# GUI (macOS only)
pip install -r requirements-build.txt
python -m app
```

The CLI accepts `--zen-profile NAME`, `--arc-space NAME`,
`--folders-open`, `--skip-favicons`, `--open-tabs`, `--verbose`, and
`--dry-run`. Use `python3 migrate_arc_to_zen.py --help` for the full
list.

Individual importers can be run on their own. They are all idempotent
and produce timestamped backups:

```bash
python3 src/arc_history_importer.py --zen-profile "Default (release)"
python3 src/arc_cookies_importer.py --zen-profile "Default (release)"
python3 src/zen_favicon_importer.py --zen-profile "Default (release)"
```

---

## How it works

The migration runs as a fixed pipeline of independent importers, each
of which reads Arc data through a snapshot copy of the source SQLite
file (so Arc itself is never touched) and writes to its corresponding
Zen file with a backup taken first.

| Step | Reads (Arc) | Writes (Zen) |
| --- | --- | --- |
| Spaces & pinned tabs | `StorableSidebar.json` | `zen-sessions.jsonlz4`, `containers.json` |
| Bookmarks | `StorableSidebar.json` | `places.sqlite` |
| Favicons | `Default/Favicons` | `favicons.sqlite`, plus inline `image` data URIs in `zen-sessions.jsonlz4` |
| History | `Default/History` | `places.sqlite` |
| Cookies | `Default/Cookies` (AES-128-CBC, key from macOS Keychain) | `cookies.sqlite` (incl. all per-space containers) |

A few things that took some reverse-engineering and might be useful if
you're hacking on this:

- **Firefox `places::HashURL`** is the 48-bit hash function used in
  `moz_places.url_hash`, `moz_pages_w_icons.page_url_hash`, and
  `moz_icons.fixed_icon_url_hash`. Implementation in
  `src/zen_favicon_importer.py`.
- **Modern Zen renders pinned-tab favicons from each tab's inline
  `image` data URI in `zen-sessions.jsonlz4`**, not from
  `favicons.sqlite`. Writing only the SQLite store leaves the sidebar
  blank.
- **`moz_cookies.expiry` switched from seconds to milliseconds around
  Firefox 108.** Storing seconds makes Firefox treat every cookie as
  expired in 1970 and purge them all on next startup.
- **Cookies in container tabs are isolated.** Cookies imported with
  empty `originAttributes` are invisible to per-space containers, so
  each cookie is also written under `^userContextId=N` for every
  container the migration creates.

The GUI in `app/` wraps the same importer classes from `src/` without
modifying them. Architecture details are in [CLAUDE.md](./CLAUDE.md).

---

## Troubleshooting

- **"Zen profile not found"**: launch Zen once so it creates the
  profile, then click Recheck.
- **"No Arc data found"**: make sure Arc has been opened at least
  once and has at least one pinned tab.
- **Cookies didn't carry over**: close Zen completely before running
  (Firefox holds an exclusive lock on `cookies.sqlite` while open) and
  approve the Keychain prompt that appears on first run.
- **Something looks wrong after migration**: open the **Backups**
  screen in the app and restore the most recent backup of the relevant
  file. Or close Zen and copy any of the `*.backup.<timestamp>` files
  in your Zen profile directory back over the live file.

For anything else, [open an issue](./issues).

---

## Contributing

Pull requests welcome. The codebase is plain Python 3.7+ with a single
runtime dependency (`lz4`) plus `cryptography` for the cookies path.
The GUI uses PyWebView (Python) + vanilla HTML/CSS/JS with no build
step.

```bash
git clone https://github.com/rafcabezas/arc2zen.git
cd arc2zen
pip install -r requirements.txt -r requirements-build.txt
python3 migrate_arc_to_zen.py --dry-run --verbose   # CLI smoke test
python -m app --debug                               # GUI with WebKit DevTools
```

To produce a `.dmg` locally:

```bash
bash build/make_app.sh   # produces dist/Arc2Zen.app
bash build/make_dmg.sh   # produces dist/Arc2Zen-<version>-arm64.dmg
```

CI builds happen automatically on every `v*` git tag via
[`.github/workflows/release.yml`](./.github/workflows/release.yml).

---

## License

MIT, see [LICENSE](./LICENSE).

Always back up your data before running migrations. Use at your own
risk.

## Acknowledgements

- Arc Browser team for the original product.
- Zen Browser team for the privacy-focused alternative.
- The open source community for inspiration and tools.
