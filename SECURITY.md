# Security policy

browser2zen reads bookmarks, history, and cookies from a user's source
browser (Arc, Chrome, Edge, Brave, Firefox, or Safari) and writes them
into a Zen Browser profile on the same machine. The tool is local-only:
no network calls, no telemetry, no remote endpoints. Cookie decryption
on Chromium-family browsers happens via the OS keystore (macOS Keychain
or Windows DPAPI).

## Supported versions

Active security support is limited to the latest released version on the
[releases page](https://github.com/tarikbc/browser2zen/releases). Earlier
versions are unsupported.

## Reporting a vulnerability

Please report security issues privately, not in public issues.

- **Email**: open a [GitHub Security Advisory](https://github.com/tarikbc/browser2zen/security/advisories/new)
  on this repo. That route gives us a private channel and a CVE process
  if needed.

We aim to acknowledge new reports within 7 days. Fix timelines depend on
severity. We will credit reporters in the release notes unless you ask
us not to.

## In scope

- Bugs that let browser2zen exfiltrate, leak, or expose user data
  beyond the user's own machine.
- Bugs that let browser2zen write to or corrupt files outside the
  documented Zen profile and its `.backup.<ts>` siblings.
- Bugs that let a malicious source-browser profile or `Bookmarks.plist`
  / `StorableSidebar.json` cause arbitrary code execution.
- Bugs in the cookie-decryption path that mishandle keys, leak
  plaintext to disk, or persist them anywhere they shouldn't be.
- Bundle-tampering risks in the macOS `.dmg` or Windows `.zip` we
  publish via the release workflow.

## Out of scope

- Vulnerabilities in third-party browsers (Arc, Chrome, Edge, Brave,
  Firefox, Safari) themselves. Report those upstream.
- Vulnerabilities in Zen Browser itself. Report those at the
  [Zen Browser issue tracker](https://github.com/zen-browser/desktop/issues).
- Issues that require physical access to an unlocked machine. The
  tool's threat model assumes a trusted local user.
- Macros, AppleScript, or Powershell that run with the user's full
  privileges. The user is the trust boundary.

## What the tool reads and writes

- **Reads** (read-only, from snapshot copies, never the live files):
  - Source-browser bookmarks (`StorableSidebar.json`, Chromium
    `Bookmarks` JSON, `places.sqlite`, `Bookmarks.plist`).
  - Source-browser favicons (Chromium `Favicons` SQLite).
  - Source-browser browsing history (Chromium `History`,
    `places.sqlite`, Safari `History.db`).
  - Source-browser cookies (Chromium `Cookies` SQLite, Firefox
    `cookies.sqlite`, Safari `Cookies.binarycookies`).
  - The OS keystore entry that holds the source-browser cookie key
    (macOS Keychain / Windows DPAPI). Only the entry for the chosen
    source browser is queried.

- **Writes** (always preceded by a timestamped `.backup.<ts>` sibling):
  - The selected Zen profile's `places.sqlite`, `cookies.sqlite`,
    `favicons.sqlite`, `containers.json`, and `zen-sessions.jsonlz4`.
  - A `.browser2zen-migrated` marker file in the Zen profile root.

Nothing is sent over the network. No analytics, no crash reporting, no
update checks.
