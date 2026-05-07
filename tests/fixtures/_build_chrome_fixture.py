"""Build a minimal synthetic Chrome profile for tests.

Run from repo root:
    python tests/fixtures/_build_chrome_fixture.py

Produces:
    tests/fixtures/chrome/User Data/Default/Bookmarks
    tests/fixtures/chrome/User Data/Default/History
    tests/fixtures/chrome/User Data/Default/Favicons
    tests/fixtures/chrome/User Data/Default/Cookies
    tests/fixtures/chrome/User Data/Local State

The Bookmarks file mimics Chrome's JSON tree shape. The SQLite files
have the minimum schema the readers expect.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE / "chrome" / "User Data"
PROFILE = ROOT / "Default"


BOOKMARKS = {
    "checksum": "0",
    "roots": {
        "bookmark_bar": {
            "children": [
                {
                    "type": "url",
                    "name": "Example",
                    "url": "https://example.com/",
                    "guid": "11111111-aaaa-bbbb-cccc-111111111111",
                    "id": "1",
                    "date_added": "13000000000000000",
                },
                {
                    "type": "folder",
                    "name": "Test Folder",
                    "guid": "11111111-aaaa-bbbb-cccc-222222222222",
                    "id": "2",
                    "children": [
                        {
                            "type": "url",
                            "name": "Mozilla",
                            "url": "https://mozilla.org/",
                            "guid": "11111111-aaaa-bbbb-cccc-333333333333",
                            "id": "3",
                            "date_added": "13000000000000001",
                        },
                    ],
                },
            ],
        },
        "other": {"children": []},
        "synced": {"children": []},
    },
    "version": 1,
}


def write_bookmarks() -> None:
    out = PROFILE / "Bookmarks"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(BOOKMARKS, indent=2))


def write_history() -> None:
    db = PROFILE / "History"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT,
                visit_count INTEGER, typed_count INTEGER, last_visit_time INTEGER,
                hidden INTEGER);
            CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER,
                from_visit INTEGER, transition INTEGER, segment_id INTEGER,
                visit_duration INTEGER);
            """
        )
        # Two URLs, one visit each. visit_time uses Chromium's WebKit epoch
        # microseconds (since 1601-01-01). 13_300_000_000_000_000 ~= 2022.
        conn.execute(
            "INSERT INTO urls (id, url, title, visit_count, typed_count, last_visit_time, hidden) "
            "VALUES (1, 'https://example.com/', 'Example', 1, 0, 13300000000000000, 0)"
        )
        conn.execute(
            "INSERT INTO urls (id, url, title, visit_count, typed_count, last_visit_time, hidden) "
            "VALUES (2, 'https://mozilla.org/', 'Mozilla', 1, 0, 13300000001000000, 0)"
        )
        conn.execute(
            "INSERT INTO visits (id, url, visit_time, from_visit, transition, segment_id, visit_duration) "
            "VALUES (1, 1, 13300000000000000, 0, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO visits (id, url, visit_time, from_visit, transition, segment_id, visit_duration) "
            "VALUES (2, 2, 13300000001000000, 0, 0, 0, 0)"
        )
        conn.commit()
    finally:
        conn.close()


def write_favicons() -> None:
    db = PROFILE / "Favicons"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE favicons (id INTEGER PRIMARY KEY, url TEXT,
                icon_type INTEGER);
            CREATE TABLE icon_mapping (id INTEGER PRIMARY KEY,
                page_url TEXT, icon_id INTEGER);
            CREATE TABLE favicon_bitmaps (id INTEGER PRIMARY KEY, icon_id INTEGER,
                last_updated INTEGER, image_data BLOB,
                width INTEGER, height INTEGER);
            """
        )
        # One favicon (a 1x1 transparent PNG) mapped to example.com.
        png_1x1 = bytes.fromhex(
            "89504e470d0a1a0a"  # PNG magic
            "0000000d49484452"
            "0000000100000001"
            "0806000000"
            "1f15c489"
            "0000000d49444154"
            "789c63f8cffcffff3f0005fe02fe1c8b34780000000049454e44ae426082"
        )
        conn.execute(
            "INSERT INTO favicons (id, url, icon_type) VALUES (1, 'https://example.com/favicon.ico', 1)"
        )
        conn.execute(
            "INSERT INTO icon_mapping (id, page_url, icon_id) VALUES (1, 'https://example.com/', 1)"
        )
        conn.execute(
            "INSERT INTO favicon_bitmaps (id, icon_id, last_updated, image_data, width, height) "
            "VALUES (1, 1, 13300000000000000, ?, 16, 16)",
            (png_1x1,),
        )
        conn.commit()
    finally:
        conn.close()


def write_cookies() -> None:
    db = PROFILE / "Cookies"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        # Real Chromium has more columns; we only populate what
        # chromium_cookies_importer reads.
        conn.executescript(
            """
            CREATE TABLE cookies (
                creation_utc INTEGER NOT NULL,
                host_key TEXT NOT NULL,
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                path TEXT NOT NULL,
                expires_utc INTEGER NOT NULL,
                is_secure INTEGER NOT NULL,
                is_httponly INTEGER NOT NULL,
                last_access_utc INTEGER NOT NULL,
                has_expires INTEGER NOT NULL,
                is_persistent INTEGER NOT NULL,
                priority INTEGER NOT NULL,
                encrypted_value BLOB DEFAULT '',
                samesite INTEGER NOT NULL,
                source_scheme INTEGER NOT NULL
            );
            """
        )
        # No cookies in the fixture (chromium cookies need a Keychain
        # key to decrypt; we don't want to require Keychain access for
        # tests). The reader gracefully handles an empty cookies table.
        conn.commit()
    finally:
        conn.close()


def write_local_state() -> None:
    out = ROOT / "Local State"
    out.write_text(json.dumps({
        "profile": {
            "info_cache": {
                "Default": {"name": "Test User", "user_name": "test@example.com"},
            },
        },
        "os_crypt": {"encrypted_key": ""},
    }))


def main() -> None:
    write_bookmarks()
    write_history()
    write_favicons()
    write_cookies()
    write_local_state()
    print(f"wrote chrome fixture under {ROOT}")


if __name__ == "__main__":
    main()
