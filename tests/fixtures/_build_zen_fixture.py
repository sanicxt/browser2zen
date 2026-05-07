"""Build a minimal synthetic Zen profile for tests.

Run from repo root:
    python tests/fixtures/_build_zen_fixture.py

Produces tests/fixtures/zen/Profiles/test.default (release)/ with:
- places.sqlite (Firefox-shaped, empty bookmarks + places)
- cookies.sqlite (empty moz_cookies)
- favicons.sqlite (empty)
- containers.json (default Firefox containers)

The schema mirrors what Zen's writers expect to find on a freshly-created
Zen profile.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "zen" / "Profiles" / "test.default (release)"


PLACES_SCHEMA = """
CREATE TABLE moz_places (
    id INTEGER PRIMARY KEY,
    url LONGVARCHAR,
    title LONGVARCHAR,
    rev_host LONGVARCHAR,
    visit_count INTEGER DEFAULT 0,
    hidden INTEGER DEFAULT 0 NOT NULL,
    typed INTEGER DEFAULT 0 NOT NULL,
    frecency INTEGER DEFAULT -1 NOT NULL,
    last_visit_date INTEGER,
    guid TEXT,
    foreign_count INTEGER DEFAULT 0 NOT NULL,
    url_hash INTEGER DEFAULT 0 NOT NULL
);
CREATE TABLE moz_bookmarks (
    id INTEGER PRIMARY KEY,
    type INTEGER,
    fk INTEGER DEFAULT NULL,
    parent INTEGER,
    position INTEGER,
    title LONGVARCHAR,
    keyword_id INTEGER,
    folder_type TEXT,
    dateAdded INTEGER,
    lastModified INTEGER,
    guid TEXT,
    syncStatus INTEGER NOT NULL DEFAULT 0,
    syncChangeCounter INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE moz_historyvisits (
    id INTEGER PRIMARY KEY,
    from_visit INTEGER,
    place_id INTEGER,
    visit_date INTEGER,
    visit_type INTEGER,
    session INTEGER
);
"""

# Standard Firefox bookmark roots that Zen creates on first launch.
# Real profiles always have these six rows; ZenBookmarkImporter looks
# them up by GUID to find "unfiled" / "menu" / "toolbar".
PLACES_ROOTS = [
    (1, 0, "",        "root________"),
    (2, 1, "menu",    "menu________"),
    (3, 1, "toolbar", "toolbar_____"),
    (4, 1, "tags",    "tags________"),
    (5, 1, "unfiled", "unfiled_____"),
    (6, 1, "mobile",  "mobile______"),
]

COOKIES_SCHEMA = """
CREATE TABLE moz_cookies (
    id INTEGER PRIMARY KEY,
    originAttributes TEXT NOT NULL DEFAULT '',
    name TEXT,
    value TEXT,
    host TEXT,
    path TEXT,
    expiry INTEGER,
    lastAccessed INTEGER,
    creationTime INTEGER,
    isSecure INTEGER,
    isHttpOnly INTEGER,
    inBrowserElement INTEGER DEFAULT 0,
    sameSite INTEGER DEFAULT 0,
    rawSameSite INTEGER DEFAULT 0,
    schemeMap INTEGER DEFAULT 0,
    isPartitionedAttributeSet INTEGER DEFAULT 0
);
"""

FAVICONS_SCHEMA = """
CREATE TABLE moz_pages_w_icons (
    id INTEGER PRIMARY KEY,
    page_url LONGVARCHAR,
    page_url_hash INTEGER NOT NULL
);
CREATE TABLE moz_icons (
    id INTEGER PRIMARY KEY,
    icon_url LONGVARCHAR,
    fixed_icon_url_hash INTEGER NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    root INTEGER NOT NULL DEFAULT 0,
    expire_ms INTEGER NOT NULL DEFAULT 0,
    data BLOB,
    flags INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE moz_icons_to_pages (
    page_id INTEGER NOT NULL,
    icon_id INTEGER NOT NULL,
    expire_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (page_id, icon_id)
);
"""

CONTAINERS_DEFAULT = {
    "version": 5,
    "lastUserContextId": 4,
    "identities": [
        {
            "userContextId": 1,
            "public": True,
            "icon": "fingerprint",
            "color": "blue",
            "l10nID": "user-context-personal",
        },
        {
            "userContextId": 2,
            "public": True,
            "icon": "briefcase",
            "color": "orange",
            "l10nID": "user-context-work",
        },
        {
            "userContextId": 3,
            "public": True,
            "icon": "dollar",
            "color": "green",
            "l10nID": "user-context-banking",
        },
        {
            "userContextId": 4,
            "public": True,
            "icon": "cart",
            "color": "pink",
            "l10nID": "user-context-shopping",
        },
        {
            "userContextId": -1,
            "public": False,
            "icon": "",
            "color": "",
            "l10nID": "userContextIdInternal.thumbnail",
        },
    ],
}


def write_sqlite(path: Path, schema: str) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()


def write_places_with_roots(path: Path) -> None:
    write_sqlite(path, PLACES_SCHEMA)
    conn = sqlite3.connect(path)
    try:
        for (id_, parent, title, guid) in PLACES_ROOTS:
            conn.execute(
                "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, "
                "dateAdded, lastModified, guid) VALUES (?, 2, NULL, ?, ?, ?, "
                "1600000000000000, 1600000000000000, ?)",
                (id_, parent, id_ - 1, title, guid),
            )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    write_places_with_roots(PROFILE / "places.sqlite")
    write_sqlite(PROFILE / "cookies.sqlite", COOKIES_SCHEMA)
    write_sqlite(PROFILE / "favicons.sqlite", FAVICONS_SCHEMA)
    (PROFILE / "containers.json").write_text(json.dumps(CONTAINERS_DEFAULT, indent=2))
    print(f"wrote zen fixture under {PROFILE}")


if __name__ == "__main__":
    main()
