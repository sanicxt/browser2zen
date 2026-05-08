"""Build a Zen profile with realistic content for backup-flow tests.

The empty Zen fixture (built by ``_build_zen_fixture.py``) is good for
"target" tests where we write into an empty profile. The backup flow
needs the inverse: a "source" profile with actual bookmarks, cookies,
a container, and a pinned tab in the session, so the round-trip test
can verify content survives export → import.

Run from repo root:
    python tests/fixtures/_build_zen_with_data_fixture.py

Produces tests/fixtures/zen-with-data/Profiles/test.default (release)/
with the same shape as a real Zen profile.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import sys
from pathlib import Path

import lz4.block

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "zen-with-data" / "Profiles" / "test.default (release)"


# Reuse the empty-fixture schema definitions so the two stay in lock-step.
sys.path.insert(0, str(HERE))
from _build_zen_fixture import (  # noqa: E402
    PLACES_SCHEMA,
    PLACES_ROOTS,
    COOKIES_SCHEMA,
    FAVICONS_SCHEMA,
    CONTAINERS_DEFAULT,
)


# mozLz4 helpers (mirror src/zen_sessions_importer.py's read_mozlz4 /
# write_mozlz4) so the fixture is self-contained.
_MOZLZ4_MAGIC = b"mozLz40\0"


def _write_mozlz4(path: Path, data: dict) -> None:
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    compressed = lz4.block.compress(payload, store_size=False)
    path.write_bytes(_MOZLZ4_MAGIC + struct.pack("<I", len(payload)) + compressed)


def _populate_places(db: Path) -> None:
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(PLACES_SCHEMA)
        for (id_, parent, title, guid) in PLACES_ROOTS:
            conn.execute(
                "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, "
                "dateAdded, lastModified, guid) VALUES (?, 2, NULL, ?, ?, ?, "
                "1700000000000000, 1700000000000000, ?)",
                (id_, parent, id_ - 1, title, guid),
            )

        # Two real bookmarks under the toolbar (id=3).
        conn.execute(
            "INSERT INTO moz_places (id, url, title, rev_host, visit_count, "
            "last_visit_date, guid, url_hash) VALUES "
            "(100, 'https://example.com/', 'Example', 'moc.elpmaxe.', 1, "
            "1700000000000000, 'place_ex_001', 1)"
        )
        conn.execute(
            "INSERT INTO moz_places (id, url, title, rev_host, visit_count, "
            "last_visit_date, guid, url_hash) VALUES "
            "(101, 'https://mozilla.org/', 'Mozilla', 'gro.allizom.', 1, "
            "1700000001000000, 'place_mz_002', 2)"
        )
        conn.execute(
            "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, "
            "dateAdded, lastModified, guid) VALUES (200, 1, 100, 3, 0, 'Example', "
            "1700000000000000, 1700000000000000, 'bm_example_')"
        )
        conn.execute(
            "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, "
            "dateAdded, lastModified, guid) VALUES (201, 1, 101, 3, 1, 'Mozilla', "
            "1700000001000000, 1700000001000000, 'bm_mozilla_')"
        )
        # One history visit on place 100.
        conn.execute(
            "INSERT INTO moz_historyvisits (id, from_visit, place_id, visit_date, "
            "visit_type, session) VALUES (1, 0, 100, 1700000000000000, 1, 0)"
        )
        conn.commit()
    finally:
        conn.close()


def _populate_cookies(db: Path) -> None:
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(COOKIES_SCHEMA)
        conn.execute(
            "INSERT INTO moz_cookies (id, originAttributes, name, value, host, path, "
            "expiry, lastAccessed, creationTime, isSecure, isHttpOnly, sameSite, "
            "schemeMap) VALUES (1, '', 'session', 'abc123', '.example.com', '/', "
            "9999999999000, 1700000000000000, 1700000000000000, 1, 0, 1, 1)"
        )
        conn.commit()
    finally:
        conn.close()


def _populate_favicons(db: Path) -> None:
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(FAVICONS_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _write_containers_with_workspace(out: Path) -> None:
    """Add one user workspace alongside the default identities."""
    payload = json.loads(json.dumps(CONTAINERS_DEFAULT))   # deep copy
    payload["lastUserContextId"] = 5
    payload["identities"].append({
        "userContextId": 5,
        "public": True,
        "icon": "fingerprint",
        "color": "purple",
        "name": "Test Workspace",
    })
    out.write_text(json.dumps(payload, indent=2))


def _write_zen_sessions(out: Path) -> None:
    """Minimal zen-sessions.jsonlz4 with one window holding one pinned tab."""
    sessions = {
        "version": ["sessionrestore", 1],
        "windows": [
            {
                "tabs": [
                    {
                        "entries": [
                            {"url": "https://example.com/", "title": "Example"},
                        ],
                        "index": 1,
                        "pinned": True,
                        "image": None,
                    },
                ],
                "selected": 1,
                "_closedTabs": [],
                "workspaces": [
                    {"id": "test-workspace", "name": "Test Workspace"},
                ],
            },
        ],
        "selectedWindow": 0,
        "_closedWindows": [],
    }
    _write_mozlz4(out, sessions)


_USER_CHROME_CSS = """\
/* Test fixture Zen Mod — recolour the toolbar so the round-trip test
   has something deterministic to check.  */
:root {
  --toolbar-bg: #1a1a1a;
}
"""


def _write_zen_mods(profile: Path) -> None:
    """Drop a chrome/userChrome.css so the mods category has content."""
    chrome = profile / "chrome"
    chrome.mkdir(exist_ok=True)
    (chrome / "userChrome.css").write_text(_USER_CHROME_CSS)


def main() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    _populate_places(PROFILE / "places.sqlite")
    _populate_cookies(PROFILE / "cookies.sqlite")
    _populate_favicons(PROFILE / "favicons.sqlite")
    _write_containers_with_workspace(PROFILE / "containers.json")
    _write_zen_sessions(PROFILE / "zen-sessions.jsonlz4")
    # Some Zen profiles also have a sessionstore.jsonlz4; mirror it from
    # zen-sessions so the backup flow has a second file to handle.
    _write_zen_sessions(PROFILE / "sessionstore.jsonlz4")
    _write_zen_mods(PROFILE)
    print(f"wrote zen-with-data fixture under {PROFILE}")


if __name__ == "__main__":
    main()
