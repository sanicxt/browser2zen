"""Firefox-specific history + cookies importer tests.

The Firefox importers do a direct moz_* merge into Zen's profile,
bypassing the Chromium transformation path. These tests run them
against the fixture profile and verify the resulting Zen rows.
"""

from __future__ import annotations

import sqlite3


def test_firefox_history_imports_into_zen(firefox_home, zen_profile):
    from extractors import FirefoxExtractor
    from firefox_history_importer import FirefoxHistoryImporter

    paths = FirefoxExtractor().history_db_paths()
    assert paths, "Firefox extractor returned no places.sqlite paths"

    importer = FirefoxHistoryImporter(zen_profile, history_dbs=paths)
    summary = importer.import_history()
    assert summary.get("error") is None, summary
    assert summary["places_added"] >= 1

    conn = sqlite3.connect(zen_profile / "places.sqlite")
    try:
        urls = {row[0] for row in conn.execute("SELECT url FROM moz_places")}
        visits = conn.execute("SELECT COUNT(*) FROM moz_historyvisits").fetchone()[0]
    finally:
        conn.close()
    assert "https://example.com/" in urls
    assert "https://mozilla.org/" in urls
    assert visits >= 1


def test_firefox_history_dedupes(firefox_home, zen_profile):
    """Running the same import twice should update existing rows
    rather than duplicate them."""
    from extractors import FirefoxExtractor
    from firefox_history_importer import FirefoxHistoryImporter

    paths = FirefoxExtractor().history_db_paths()
    FirefoxHistoryImporter(zen_profile, history_dbs=paths).import_history()
    second = FirefoxHistoryImporter(zen_profile, history_dbs=paths).import_history()
    assert second["places_updated"] >= 1
    assert second["places_added"] == 0


def test_firefox_cookies_imports_into_zen(firefox_home, zen_profile):
    from extractors import FirefoxExtractor
    from firefox_cookies_importer import FirefoxCookiesImporter

    paths = FirefoxExtractor().cookie_db_paths()
    assert paths

    importer = FirefoxCookiesImporter(zen_profile, cookie_dbs=paths)
    summary = importer.import_cookies()
    assert summary.get("error") is None, summary
    assert summary["read"] >= 1
    assert summary["imported"] >= 1

    conn = sqlite3.connect(zen_profile / "cookies.sqlite")
    try:
        rows = list(conn.execute(
            "SELECT host, name, value FROM moz_cookies WHERE host LIKE '%example%'"
        ))
    finally:
        conn.close()
    assert rows, "Expected example.com cookie to land in Zen cookies.sqlite"
    assert any(r[1] == "session" and r[2] == "abc123" for r in rows)


def test_firefox_extractor_exposes_paths(firefox_home):
    """The extractor should now return non-empty history + cookie paths."""
    from extractors import FirefoxExtractor

    e = FirefoxExtractor()
    history = e.history_db_paths()
    cookies = e.cookie_db_paths()
    assert any(p.name == "places.sqlite" for p in history)
    assert any(p.name == "cookies.sqlite" for p in cookies)
