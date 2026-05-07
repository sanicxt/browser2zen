"""Safari-specific history + cookies importer tests."""

from __future__ import annotations

import sqlite3

# ----- history -----------------------------------------------------------

def test_safari_history_imports_into_zen(safari_home, zen_profile):
    from extractors import SafariExtractor
    from safari_history_importer import SafariHistoryImporter

    paths = SafariExtractor().history_db_paths()
    assert paths, "Safari extractor returned no History.db paths"

    importer = SafariHistoryImporter(zen_profile, history_dbs=paths)
    summary = importer.import_history()
    assert summary.get("error") is None, summary
    assert summary["places_added"] >= 2
    assert summary["visits_added"] >= 2

    conn = sqlite3.connect(zen_profile / "places.sqlite")
    try:
        urls = {row[0] for row in conn.execute("SELECT url FROM moz_places")}
        last = conn.execute(
            "SELECT last_visit_date FROM moz_places WHERE url = 'https://example.com/'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "https://example.com/" in urls
    assert "https://mozilla.org/" in urls
    # Cocoa 700_000_000 + Cocoa-Unix offset (978307200) = 1_678_307_200
    # in Unix seconds. Convert to microseconds for moz_places.
    assert last == int((700_000_000 + 978_307_200) * 1_000_000)


def test_safari_history_extractor_paths(safari_home):
    from extractors import SafariExtractor

    paths = SafariExtractor().history_db_paths()
    assert any(p.name == "History.db" for p in paths)


# ----- cookies -----------------------------------------------------------

def test_binarycookies_parser_roundtrip():
    """Parser should read what our fixture-builder writes."""
    from safari_cookies_importer import parse_binarycookies

    fixtures_root = __import__("pathlib").Path(__file__).resolve().parent / "fixtures"
    blob = (
        fixtures_root
        / "safari/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies"
    ).read_bytes()
    cookies = parse_binarycookies(blob)
    assert len(cookies) == 1
    c = cookies[0]
    assert c["url"] == ".example.com"
    assert c["name"] == "session"
    assert c["value"] == "abc123"
    assert c["path"] == "/"
    assert c["is_secure"] == 1
    assert c["is_http_only"] == 0


def test_safari_cookies_imports_into_zen(safari_home, zen_profile):
    from extractors import SafariExtractor
    from safari_cookies_importer import SafariCookiesImporter

    paths = SafariExtractor().cookie_db_paths()
    assert paths, "Safari extractor returned no Cookies.binarycookies paths"

    importer = SafariCookiesImporter(zen_profile, cookie_dbs=paths)
    summary = importer.import_cookies()
    assert summary.get("error") is None, summary
    assert summary["read"] == 1
    assert summary["imported"] == 1

    conn = sqlite3.connect(zen_profile / "cookies.sqlite")
    try:
        rows = list(conn.execute(
            "SELECT host, name, value, isSecure FROM moz_cookies"
        ))
    finally:
        conn.close()
    assert len(rows) == 1
    host, name, value, is_secure = rows[0]
    assert host == ".example.com"
    assert name == "session"
    assert value == "abc123"
    assert is_secure == 1


def test_safari_extractor_exposes_paths(safari_home):
    from extractors import SafariExtractor

    e = SafariExtractor()
    history = e.history_db_paths()
    cookies = e.cookie_db_paths()
    assert any(p.name == "History.db" for p in history)
    assert any(p.name == "Cookies.binarycookies" for p in cookies)
