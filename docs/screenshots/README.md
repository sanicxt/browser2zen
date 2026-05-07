# Screenshots

The PNGs in this folder are generated, not hand-captured. They feed the
README's screenshot grid and any future docs that show the GUI.

## How it works

The renderer is the *real* `app/frontend/index.html` + `styles.css` +
`app.js`. A Playwright test (`screens.spec.ts`) loads index.html into
headless Chromium with a mock PyWebView bridge injected via
`addInitScript`, walks each screen by calling internals exposed on
`window.__shoot`, and screenshots the viewport into this directory.

Because the pages use the same CSS bundle the desktop app does, the
captures look like macOS shots — minus the real window vibrancy, which
isn't a thing in Chromium. A faux-Mac chrome (titlebar + traffic
lights) is already drawn by the app's own CSS, so we get it for free.

## Run locally

```bash
cd docs/screenshots
npm install
npx playwright install chromium
npm run shoot
```

The PNGs land next to this README. Commit any deltas.

## CI

`.github/workflows/screenshots.yml` runs the same script on every PR
that touches `app/frontend/**` or `docs/screenshots/**`. If the PNGs
have changed, the workflow commits the new ones back to the PR branch
under the `browser2zen-shots[bot]` author. No human action needed.

## Adding a new screen

1. Add a `test("…")` block in `screens.spec.ts`. Use `window.__shoot`
   to reach app internals (state, screen-routing functions, etc.).
2. Pick a filename — the test name becomes `<name>.png`.
3. Run `npm run shoot` locally to verify it captures cleanly.
4. Reference the new file in the README if needed.

The hook on app.js (`if (window.__SHOOT__) { window.__shoot = …; }`)
is the only piece that needs to grow when you want to expose more
internals to tests; nothing else in the app is harness-aware.
