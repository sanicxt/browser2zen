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
