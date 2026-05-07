"""Build a minimal synthetic Safari History.db for tests.

Run from repo root:
    python tests/fixtures/_build_safari_history_fixture.py

Produces tests/fixtures/safari/Library/Safari/History.db with two
history items + one visit each. visit_time uses Cocoa epoch (seconds
since 2001-01-01 UTC) — so 700_000_000.0 is roughly 2023.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "safari" / "Library" / "Safari" / "History.db"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(OUT)
    try:
        conn.executescript(
            """
            CREATE TABLE history_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                domain_expansion TEXT NULL,
                visit_count INTEGER NOT NULL,
                daily_visit_counts BLOB NOT NULL DEFAULT '',
                weekly_visit_counts BLOB NULL,
                autocomplete_triggers BLOB NULL,
                should_recompute_derived_visit_counts INTEGER NOT NULL
            );
            CREATE TABLE history_visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                history_item INTEGER NOT NULL REFERENCES history_items(id) ON DELETE CASCADE,
                visit_time REAL NOT NULL,
                title TEXT NULL,
                load_successful BOOLEAN NOT NULL DEFAULT 1,
                http_non_get BOOLEAN NOT NULL DEFAULT 0,
                synthesized BOOLEAN NOT NULL DEFAULT 0,
                redirect_source INTEGER,
                redirect_destination INTEGER,
                origin INTEGER NOT NULL DEFAULT 0,
                generation INTEGER NOT NULL DEFAULT 0,
                attributes INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO history_items (id, url, visit_count, should_recompute_derived_visit_counts) "
            "VALUES (1, 'https://example.com/', 1, 0)"
        )
        conn.execute(
            "INSERT INTO history_items (id, url, visit_count, should_recompute_derived_visit_counts) "
            "VALUES (2, 'https://mozilla.org/', 1, 0)"
        )
        conn.execute(
            "INSERT INTO history_visits (id, history_item, visit_time, title) "
            "VALUES (1, 1, 700000000.0, 'Example')"
        )
        conn.execute(
            "INSERT INTO history_visits (id, history_item, visit_time, title) "
            "VALUES (2, 2, 700000001.0, 'Mozilla')"
        )
        conn.commit()
    finally:
        conn.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
