"""ExportData.to_legacy_dict produces the dict shape the Zen-side writers
consume. If a writer adds a new required key, it should fail loudly here
rather than partway through a real migration.
"""

from __future__ import annotations

import pytest

# Keys every space dict must carry.
_REQUIRED_SPACE_KEYS = {
    "space_id", "space_name", "icon", "color",
    "total_pinned_tabs", "total_open_tabs", "total_folders",
    "pinned_tabs", "open_tabs", "folders",
}
_REQUIRED_TAB_KEYS = {
    "url", "title", "space_id", "space_name", "folder_path",
    "tab_id", "parent_id", "index", "is_essential",
}
_REQUIRED_FOLDER_KEYS = {
    "folder_id", "title", "parent_id", "space_id",
    "children_ids", "index",
}


@pytest.mark.parametrize(
    "extractor_name,home_fixture",
    [
        ("ArcExtractor", "arc_home"),
        ("ChromeExtractor", "chrome_home"),
        ("FirefoxExtractor", "firefox_home"),
        ("SafariExtractor", "safari_home"),
    ],
)
def test_legacy_dict_shape(request, extractor_name, home_fixture):
    request.getfixturevalue(home_fixture)

    import extractors
    ext = getattr(extractors, extractor_name)()
    legacy = ext.extract().to_legacy_dict()

    assert legacy["source"] == ext.name
    assert "total_spaces" in legacy
    assert legacy["total_spaces"] == len(legacy["spaces"])
    assert legacy["spaces"], f"{extractor_name} produced no spaces"

    for space in legacy["spaces"]:
        missing = _REQUIRED_SPACE_KEYS - set(space.keys())
        assert not missing, f"{extractor_name} space missing keys: {missing}"
        for tab in space["pinned_tabs"]:
            missing_t = _REQUIRED_TAB_KEYS - set(tab.keys())
            assert not missing_t, f"{extractor_name} tab missing keys: {missing_t}"
            assert tab["url"].startswith(("http://", "https://", "ftp://"))
        for folder in space["folders"]:
            missing_f = _REQUIRED_FOLDER_KEYS - set(folder.keys())
            assert not missing_f, f"{extractor_name} folder missing keys: {missing_f}"
            assert isinstance(folder["children_ids"], list)


# --- bookmarks channel ----------------------------------------------------
# Bookmarks travel in their own ``bookmarks`` / ``bookmark_folders`` keys so
# that a source's real pinned tabs (pinned_tabs) and its bookmarks no longer
# share a channel. When an extractor doesn't set them, they fall back to
# pinned_tabs / folders so bookmark-only sources keep working unchanged.

def _make_space(**kw):
    from extractors.base import SpaceRecord
    return SpaceRecord(space_id="s1", space_name="S1", **kw)


def test_bookmarks_default_to_pinned_tabs_when_unset():
    from extractors.base import ExportData, FolderRecord, TabRecord

    space = _make_space(
        pinned_tabs=[TabRecord(url="https://pin.example/", title="P")],
        folders=[FolderRecord(folder_id="f1", title="F", space_id="s1")],
    )
    legacy = ExportData(source="x", spaces=[space]).to_legacy_dict()["spaces"][0]

    assert [t["url"] for t in legacy["bookmarks"]] == ["https://pin.example/"]
    assert [f["title"] for f in legacy["bookmark_folders"]] == ["F"]


def test_explicit_bookmarks_stay_separate_from_pinned_tabs():
    from extractors.base import ExportData, FolderRecord, TabRecord

    space = _make_space(
        pinned_tabs=[TabRecord(url="https://pin.example/", title="P")],
        folders=[],
        bookmarks=[TabRecord(url="https://bm.example/", title="B")],
        bookmark_folders=[FolderRecord(folder_id="bf", title="BF", space_id="s1")],
    )
    legacy = ExportData(source="x", spaces=[space]).to_legacy_dict()["spaces"][0]

    assert [t["url"] for t in legacy["pinned_tabs"]] == ["https://pin.example/"]
    assert [t["url"] for t in legacy["bookmarks"]] == ["https://bm.example/"]
    assert [f["title"] for f in legacy["bookmark_folders"]] == ["BF"]
