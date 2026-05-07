"""Build a minimal synthetic Safari Bookmarks.plist for tests.

Run from repo root:
    python tests/fixtures/_build_safari_fixture.py

Produces tests/fixtures/safari/Library/Safari/Bookmarks.plist with:
- BookmarksBar (1 leaf)
- BookmarksMenu (1 leaf)
- com.apple.ReadingList (empty)
"""

from __future__ import annotations

import plistlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "safari" / "Library" / "Safari" / "Bookmarks.plist"


def build() -> dict:
    return {
        "WebBookmarkUUID": "00000000-0000-0000-0000-000000000000",
        "WebBookmarkType": "WebBookmarkTypeList",
        "WebBookmarkFileVersion": 1,
        "Children": [
            {
                "WebBookmarkUUID": "11111111-1111-1111-1111-111111111111",
                "WebBookmarkType": "WebBookmarkTypeList",
                "Title": "BookmarksBar",
                "Children": [
                    {
                        "WebBookmarkUUID": "11111111-1111-1111-1111-111111111112",
                        "WebBookmarkType": "WebBookmarkTypeLeaf",
                        "URLString": "https://example.com/",
                        "URIDictionary": {"title": "Example"},
                    },
                ],
            },
            {
                "WebBookmarkUUID": "22222222-2222-2222-2222-222222222222",
                "WebBookmarkType": "WebBookmarkTypeList",
                "Title": "BookmarksMenu",
                "Children": [
                    {
                        "WebBookmarkUUID": "22222222-2222-2222-2222-222222222223",
                        "WebBookmarkType": "WebBookmarkTypeLeaf",
                        "URLString": "https://mozilla.org/",
                        "URIDictionary": {"title": "Mozilla"},
                    },
                ],
            },
            {
                "WebBookmarkUUID": "33333333-3333-3333-3333-333333333333",
                "WebBookmarkType": "WebBookmarkTypeList",
                "Title": "com.apple.ReadingList",
                "Children": [],
            },
        ],
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("wb") as fh:
        plistlib.dump(build(), fh, fmt=plistlib.FMT_BINARY)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
