"""End-to-end tests for each source-browser extractor.

Each test:
1. Materialises the fixture profile under a fake $HOME.
2. Verifies ``is_installed()`` returns True against that fixture.
3. Verifies ``extract()`` produces the expected ExportData shape.
4. Verifies the legacy_dict round-trip preserves URLs and titles.
"""

from __future__ import annotations

import pytest

# ----- Arc ----------------------------------------------------------------

def test_arc_extractor_detects_fixture(arc_home):
    from extractors import ArcExtractor

    ext = ArcExtractor()
    assert ext.is_installed() is True


def test_arc_extractor_extract_shape(arc_home):
    from extractors import ArcExtractor

    data = ArcExtractor().extract()
    assert data.source == "arc"
    assert len(data.spaces) == 1
    space = data.spaces[0]
    assert space.space_name == "Test Space"
    urls = sorted(t.url for t in space.pinned_tabs)
    assert urls == ["https://example.com/", "https://mozilla.org/"]


def test_arc_extractor_legacy_dict(arc_home):
    from extractors import ArcExtractor

    legacy = ArcExtractor().extract().to_legacy_dict()
    assert legacy["source"] == "arc"
    assert legacy["total_spaces"] == 1
    space = legacy["spaces"][0]
    assert {"space_id", "space_name", "pinned_tabs", "open_tabs", "folders",
            "icon", "color"} <= set(space.keys())
    assert any(t["url"] == "https://example.com/" for t in space["pinned_tabs"])


# ----- Chrome -------------------------------------------------------------

def test_chrome_extractor_detects_fixture(chrome_home):
    from extractors import ChromeExtractor

    assert ChromeExtractor().is_installed() is True


def test_chrome_extractor_extract_shape(chrome_home):
    from extractors import ChromeExtractor

    data = ChromeExtractor().extract()
    assert data.source == "chrome"
    assert len(data.spaces) >= 1
    space = data.spaces[0]
    urls = sorted(t.url for t in space.pinned_tabs)
    assert "https://example.com/" in urls
    assert "https://mozilla.org/" in urls
    folder_titles = {f.title for f in space.folders}
    assert "Test Folder" in folder_titles


def test_chrome_history_db_paths(chrome_home):
    from extractors import ChromeExtractor

    paths = ChromeExtractor().history_db_paths()
    assert any(p.name == "History" for p in paths)


def test_chrome_cookie_db_paths(chrome_home):
    from extractors import ChromeExtractor

    paths = ChromeExtractor().cookie_db_paths()
    assert any(p.name == "Cookies" for p in paths)


# ----- Firefox ------------------------------------------------------------

def test_firefox_extractor_detects_fixture(firefox_home):
    from extractors import FirefoxExtractor

    assert FirefoxExtractor().is_installed() is True


def test_firefox_extractor_extract_shape(firefox_home):
    from extractors import FirefoxExtractor

    data = FirefoxExtractor().extract()
    assert data.source == "firefox"
    assert len(data.spaces) == 1
    space = data.spaces[0]
    urls = sorted(t.url for t in space.pinned_tabs)
    assert urls == ["https://example.com/", "https://mozilla.org/"]


def test_firefox_extractor_skips_disabled_root(firefox_home):
    """The fixture has bookmarks under toolbar (id=3); other / mobile /
    menu are empty. Extractor should only emit folders for non-empty
    roots."""
    from extractors import FirefoxExtractor

    data = FirefoxExtractor().extract()
    folder_titles = {f.title for f in data.spaces[0].folders}
    # The toolbar wrapper folder should be present.
    assert "Bookmarks Toolbar" in folder_titles
    # Empty roots should not produce wrappers.
    assert "Bookmarks Menu" not in folder_titles


# ----- Safari -------------------------------------------------------------

def test_safari_extractor_detects_fixture(safari_home):
    from extractors import SafariExtractor

    assert SafariExtractor().is_installed() is True


def test_safari_extractor_extract_shape(safari_home):
    from extractors import SafariExtractor

    data = SafariExtractor().extract()
    assert data.source == "safari"
    assert len(data.spaces) == 1
    space = data.spaces[0]
    urls = sorted(t.url for t in space.pinned_tabs)
    assert urls == ["https://example.com/", "https://mozilla.org/"]
    folder_titles = {f.title for f in space.folders}
    assert "Bookmarks Bar" in folder_titles
    assert "Bookmarks Menu" in folder_titles
    # Empty Reading List is filtered out by _root_has_content.
    assert "Reading List" not in folder_titles


# ----- Edge / Brave (no fixtures, just registry sanity) -------------------

def test_edge_extractor_registered():
    from extractors import EdgeExtractor, by_name
    assert by_name("edge") is EdgeExtractor


def test_brave_extractor_registered():
    from extractors import BraveExtractor, by_name
    assert by_name("brave") is BraveExtractor


def test_unknown_source_raises():
    from extractors import by_name
    with pytest.raises(KeyError):
        by_name("netscape")
