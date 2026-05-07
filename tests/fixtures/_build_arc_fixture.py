"""Build a minimal synthetic Arc StorableSidebar.json for tests.

Arc's format is unusual: ``space_models``, ``sidebar.containers[1].items``,
and ``sidebar.containers[1].spaces`` are flat arrays with interleaved
``[id, object, id, object, ...]`` pairs. Folders carry ``data.list`` (an
empty dict is enough). Tabs carry ``data.tab.savedURL``.

Run from repo root:
    python tests/fixtures/_build_arc_fixture.py

Produces tests/fixtures/arc/StorableSidebar.json with:
- 1 space named "Test Space"
- 2 pinned tabs: example.com directly, mozilla.org inside a folder
- 1 folder containing the second tab
- No essentials, no open tabs
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "arc" / "StorableSidebar.json"

SPACE_ID = "11111111-1111-1111-1111-111111111111"
PINNED_CONTAINER = "22222222-2222-2222-2222-222222222222"
UNPINNED_CONTAINER = "33333333-3333-3333-3333-333333333333"
TAB1_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"
TAB2_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"
FOLDER_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def build() -> dict:
    return {
        "version": 4,
        "sidebar": {
            "containers": [
                {},
                {
                    "spaces": [
                        SPACE_ID,
                        {
                            "id": SPACE_ID,
                            "title": "Test Space",
                            "containerIDs": [
                                "pinned", PINNED_CONTAINER,
                                "unpinned", UNPINNED_CONTAINER,
                            ],
                            "customInfo": {"iconType": {"emoji_v2": "✨"}},
                        },
                    ],
                    "items": [
                        # Pinned container record
                        PINNED_CONTAINER,
                        {
                            "id": PINNED_CONTAINER,
                            "data": {"itemContainer": {
                                "containerType": {"spaceItems": {"_0": SPACE_ID}}
                            }},
                            "childrenIds": [TAB1_ID, FOLDER_ID],
                        },
                        # Unpinned container record (empty)
                        UNPINNED_CONTAINER,
                        {
                            "id": UNPINNED_CONTAINER,
                            "data": {"itemContainer": {
                                "containerType": {"spaceItems": {"_0": SPACE_ID}}
                            }},
                            "childrenIds": [],
                        },
                        # Tab 1: top-level pinned tab
                        TAB1_ID,
                        {
                            "id": TAB1_ID,
                            "title": "Example",
                            "data": {"tab": {"savedURL": "https://example.com/"}},
                            "parentID": PINNED_CONTAINER,
                            "createdAt": 700_000_000,
                        },
                        # Folder
                        FOLDER_ID,
                        {
                            "id": FOLDER_ID,
                            "title": "Test Folder",
                            "data": {"list": {}},
                            "parentID": PINNED_CONTAINER,
                            "childrenIds": [TAB2_ID],
                            "createdAt": 699_000_000,
                        },
                        # Tab 2: nested inside the folder
                        TAB2_ID,
                        {
                            "id": TAB2_ID,
                            "title": "Mozilla",
                            "data": {"tab": {"savedURL": "https://mozilla.org/"}},
                            "parentID": FOLDER_ID,
                            "createdAt": 700_000_001,
                        },
                    ],
                },
            ],
        },
        "firebaseSyncState": {
            "syncData": {
                "spaceModels": [
                    SPACE_ID,
                    {
                        "value": {
                            "title": "Test Space",
                            "customInfo": {
                                "iconType": {"emoji_v2": "✨"},
                                "windowTheme": {
                                    "primaryColorPalette": {
                                        "midTone": {
                                            "red": 0.5,
                                            "green": 0.4,
                                            "blue": 0.9,
                                        },
                                    },
                                },
                            },
                        },
                    },
                ],
            },
        },
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
