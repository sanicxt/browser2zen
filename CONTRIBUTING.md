# Contributing

Thanks for poking at browser2zen. The codebase is small and the
contribution loop is short.

## Quick start

```bash
git clone https://github.com/tarikbc/browser2zen.git
cd browser2zen
pip install -r requirements.txt -r requirements-build.txt
python -m app --debug   # GUI with WebKit DevTools
```

The app launches as a single PyWebView window. `--debug` opens DevTools
and prints log output to your terminal.

## Project shape

- `app/` is the GUI. `app.orchestrator.MigrationOrchestrator` is the
  single migration entry point; `app.bridge.Bridge` exposes it to
  JavaScript via `window.pywebview.api.<method>`.
- `src/extractors/` is one module per source browser (Arc, Chrome, Edge,
  Brave, Firefox, Safari) implementing a small `BrowserExtractor` ABC.
- `src/zen_*.py` are the source-agnostic Zen-side writers.
- `src/chromium_*.py` are the generic Chromium-format readers (history,
  cookies). They take per-browser paths via `__init__` kwargs.
- `app/frontend/` is vanilla HTML + CSS + JS, no build step.

`CLAUDE.md` has the full architecture overview, including the
reverse-engineered Firefox internals.

## Adding a new source browser

1. Create `src/extractors/<name>.py`. Subclass `BrowserExtractor`
   directly, or `ChromiumExtractor` if it's Chromium-family
   (Bookmarks JSON tree + standard SQLite).
2. Implement `extract()` to produce an `ExportData` (one or more
   `SpaceRecord`s). Sources without a workspaces concept emit a single
   default space.
3. Register the class in `src/extractors/__init__.py`'s `EXTRACTORS`
   tuple (order is the order shown in the source-picker).
4. Drop the brand SVG into `app/frontend/assets/sources/<name>.svg`
   and add the slug to `SOURCE_NAMES` in `app/frontend/app.js`.
5. Add a brand-tinted background rule in `app/frontend/styles.css`
   (`.brand-mark.<name>`).
6. Add a fixture under `tests/fixtures/<name>/` and a test in
   `tests/test_extractors.py`.

The picker, orchestrator, and Zen writers all pick it up without
further changes.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
ruff check
```

The CI workflow at `.github/workflows/ci.yml` runs the same two
commands on every pull request.

### Regenerating fixtures

Synthetic fixture profiles live under `tests/fixtures/`. They're
checked in. If a schema changes, regenerate via the small builders:

```bash
python tests/fixtures/_build_firefox_fixture.py
python tests/fixtures/_build_safari_fixture.py
python tests/fixtures/_build_chrome_fixture.py
python tests/fixtures/_build_zen_fixture.py
```

Never check in real user data. The builders produce deterministic
synthetic profiles with two bookmarks, one history visit, no cookies.

## Conventions

- **No em dashes** anywhere in code or docs. Use periods, commas, or
  parens instead. (Personal preference, but consistent everywhere.)
- **No `innerHTML` with non-literal data.** Use `createElement` +
  `textContent`. The single existing `innerHTML = ""` clear is the
  exception.
- **Source-agnostic identifiers in shared code** (`source`,
  `export_data`, `db`). Arc-specific names only inside Arc-only files.
- **Trailing whitespace** is fine, but a clean diff is appreciated.

## Build

```bash
bash build/make_iconset.sh && bash build/make_app.sh && bash build/make_dmg.sh   # macOS
pwsh build/make_iconset.ps1; pwsh build/make_exe.ps1; pwsh build/make_zip.ps1    # Windows
```

CI builds happen automatically on every `v*` git tag via
`.github/workflows/release.yml`. Tags are how releases get cut. To ship
a new version, bump `app/__version__.py`, commit, then `git tag -a vX.Y.Z`
and `git push origin vX.Y.Z`.

## Reporting bugs

Use the bug-report template in
[issues/new](https://github.com/tarikbc/browser2zen/issues/new/choose).
The structured form gets us straight to the cause.

## Disclosure of security issues

See [SECURITY.md](./SECURITY.md). Please don't open public issues for
security findings.
