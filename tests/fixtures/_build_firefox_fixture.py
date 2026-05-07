"""Build a minimal synthetic Firefox profile for tests.

Run from repo root:
    python tests/fixtures/_build_firefox_fixture.py

Produces:
    tests/fixtures/firefox/profiles.ini
    tests/fixtures/firefox/Profiles/test.default-release/places.sqlite
    tests/fixtures/firefox/Profiles/test.default-release/cookies.sqlite

The places.sqlite has 2 bookmarks under bookmark_bar plus 1 history visit.
The cookies.sqlite has 1 unencrypted cookie.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE / "firefox"
PROFILE = ROOT / "Profiles" / "test.default-release"


def write_profiles_ini() -> None:
    out = ROOT / "profiles.ini"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "[Install4F96D1932A9F858E]\n"
        "Default=Profiles/test.default-release\n"
        "Locked=1\n"
        "\n"
        "[Profile0]\n"
        "Name=test\n"
        "IsRelative=1\n"
        "Path=Profiles/test.default-release\n"
        "Default=1\n"
        "\n"
        "[General]\n"
        "StartWithLastProfile=1\n"
        "Version=2\n"
    )


def write_places() -> None:
    db = PROFILE / "places.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
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
        )
        # Two places. last_visit_date is Unix microseconds.
        conn.execute(
            "INSERT INTO moz_places (id, url, title, rev_host, visit_count, last_visit_date, guid, url_hash) "
            "VALUES (1, 'https://example.com/', 'Example', 'moc.elpmaxe.', 1, 1700000000000000, 'place_001', 1)"
        )
        conn.execute(
            "INSERT INTO moz_places (id, url, title, rev_host, visit_count, last_visit_date, guid, url_hash) "
            "VALUES (2, 'https://mozilla.org/', 'Mozilla', 'gro.allizom.', 1, 1700000001000000, 'place_002', 2)"
        )
        # Firefox bookmark hierarchy: parent=1 holds the four root containers
        # (menu=2, toolbar=3, tags=4, unfiled=5, mobile=6).
        for (i, title, guid) in [
            (1, "", "root________"),
            (2, "menu", "menu________"),
            (3, "toolbar", "toolbar_____"),
            (4, "tags", "tags________"),
            (5, "unfiled", "unfiled_____"),
            (6, "mobile", "mobile______"),
        ]:
            parent = 0 if i == 1 else 1
            conn.execute(
                "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded, guid) "
                "VALUES (?, 2, NULL, ?, ?, ?, 1600000000000000, ?)",
                (i, parent, i - 1, title, guid),
            )
        # Two URL bookmarks under toolbar (id=3).
        conn.execute(
            "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded, guid) "
            "VALUES (10, 1, 1, 3, 0, 'Example', 1700000000000000, 'bm_example_')"
        )
        conn.execute(
            "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded, guid) "
            "VALUES (11, 1, 2, 3, 1, 'Mozilla', 1700000001000000, 'bm_mozilla_')"
        )
        # One history visit on place 1.
        conn.execute(
            "INSERT INTO moz_historyvisits (id, from_visit, place_id, visit_date, visit_type, session) "
            "VALUES (1, 0, 1, 1700000000000000, 1, 0)"
        )
        conn.commit()
    finally:
        conn.close()


def write_cookies() -> None:
    db = PROFILE / "cookies.sqlite"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
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
        )
        # One cookie on example.com.
        conn.execute(
            "INSERT INTO moz_cookies (id, name, value, host, path, expiry, "
            "lastAccessed, creationTime, isSecure, isHttpOnly, sameSite, schemeMap) "
            "VALUES (1, 'session', 'abc123', '.example.com', '/', "
            "9999999999000, 1700000000000000, 1700000000000000, 1, 0, 1, 1)"
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    write_profiles_ini()
    write_places()
    write_cookies()
    print(f"wrote firefox fixture under {ROOT}")


if __name__ == "__main__":
    main()
