"""Zen-side writer tests.

Run each writer against the fresh empty Zen fixture, then assert the
expected records appear in containers.json / places.sqlite. Validates
the ``to_legacy_dict()`` → writer contract end-to-end.
"""

from __future__ import annotations

import json
import sqlite3

from extractors import ArcExtractor


def _legacy_dict_from_arc():
    return ArcExtractor().extract().to_legacy_dict()


def test_zen_space_importer_creates_container(arc_home, zen_profile):
    from zen_space_importer import ZenProfile, ZenSpaceImporter

    legacy = _legacy_dict_from_arc()
    zp = ZenProfile(name=zen_profile.name, path=zen_profile)
    mappings = ZenSpaceImporter(zp).import_spaces_as_containers(legacy, dry_run=False)

    # Mappings are space_name -> container userContextId.
    assert "Test Space" in mappings

    containers = json.loads((zen_profile / "containers.json").read_text())
    names = {idn["name"] for idn in containers["identities"] if "name" in idn}
    assert "Test Space" in names


def test_zen_bookmark_importer_writes_bookmarks(arc_home, zen_profile):
    from zen_bookmark_importer import ZenBookmarkImporter

    legacy = _legacy_dict_from_arc()
    importer = ZenBookmarkImporter(zen_profile)
    ok = importer.import_bookmarks(legacy, dry_run=False)
    assert ok is True

    conn = sqlite3.connect(zen_profile / "places.sqlite")
    try:
        urls = {row[0] for row in conn.execute("SELECT url FROM moz_places")}
    finally:
        conn.close()
    assert "https://example.com/" in urls
    assert "https://mozilla.org/" in urls


def test_zen_sessions_importer_writes_session(arc_home, zen_profile):
    """Verify the sessions importer produces a zen-sessions.jsonlz4 file
    (binary, mozLz4-compressed) without raising."""
    from zen_sessions_importer import ZenSessionsImporter

    legacy = _legacy_dict_from_arc()
    importer = ZenSessionsImporter(zen_profile, folders_collapsed=True)
    importer.import_data(legacy, container_mappings={}, dry_run=False)

    out = zen_profile / "zen-sessions.jsonlz4"
    assert out.exists()
    blob = out.read_bytes()
    # mozLz4 magic header
    assert blob.startswith(b"mozLz40\0")
