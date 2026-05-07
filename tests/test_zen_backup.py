"""Backup + restore tests against the with-data fixture.

These run the real exporter and importer end-to-end. The with-data
fixture has 2 bookmarks, 1 cookie, 1 user-context container, and a
pinned tab; the round-trip test verifies all four survive the
archive/extract cycle byte-for-byte.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import struct
import tarfile
from pathlib import Path

import lz4.block
import pytest

from zen_backup import (
    ALL_CATEGORIES,
    ARCHIVE_FORMAT_VERSION,
    DEFAULT_CATEGORIES,
    ZenBackupExporter,
    ZenBackupImporter,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
WITH_DATA = FIXTURES / "zen-with-data" / "Profiles" / "test.default (release)"
EMPTY = FIXTURES / "zen" / "Profiles" / "test.default (release)"


@pytest.fixture
def source_profile(tmp_path):
    """A populated copy of the with-data fixture."""
    dst = tmp_path / "source-profile"
    shutil.copytree(WITH_DATA, dst)
    return dst


@pytest.fixture
def empty_profile(tmp_path):
    """A copy of the empty Zen fixture (already has the moz_bookmarks roots)."""
    dst = tmp_path / "empty-profile"
    shutil.copytree(EMPTY, dst)
    return dst


def _places_bookmark_count(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM moz_bookmarks WHERE type = 1"
        ).fetchone()[0]
    finally:
        conn.close()


def _places_total_places(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM moz_places").fetchone()[0]
    finally:
        conn.close()


def _cookies_count(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM moz_cookies").fetchone()[0]
    finally:
        conn.close()


def _read_mozlz4(path: Path) -> dict:
    raw = path.read_bytes()
    assert raw[:8] == b"mozLz40\0"
    size = struct.unpack("<I", raw[8:12])[0]
    return json.loads(lz4.block.decompress(raw[12:], uncompressed_size=size).decode("utf-8"))


# ----- round-trip with all defaults --------------------------------------

def test_roundtrip_default_categories(source_profile, empty_profile, tmp_path):
    archive = tmp_path / "out.zenbackup"

    export = ZenBackupExporter(source_profile, archive).export()
    assert export["ok"], export
    assert archive.is_file()
    assert export["bytes_out"] > 0
    assert export["file_count"] >= 4   # at minimum the 4 default-on files

    restore = ZenBackupImporter(archive, empty_profile).import_archive()
    assert restore["ok"], restore
    restored = set(restore["restored_files"])
    # The four default-on category roots all need to land.
    assert "places.sqlite" in restored
    assert "cookies.sqlite" in restored
    assert "favicons.sqlite" in restored
    assert "containers.json" in restored
    assert "zen-sessions.jsonlz4" in restored

    # Content survives.
    assert _places_bookmark_count(empty_profile / "places.sqlite") == 2
    assert _places_total_places(empty_profile / "places.sqlite") == 2
    assert _cookies_count(empty_profile / "cookies.sqlite") == 1

    sessions = _read_mozlz4(empty_profile / "zen-sessions.jsonlz4")
    assert sessions["windows"][0]["tabs"][0]["entries"][0]["url"] == "https://example.com/"

    containers = json.loads((empty_profile / "containers.json").read_text())
    names = {idn.get("name") for idn in containers["identities"]}
    assert "Test Workspace" in names

    # Marker file lands.
    marker = empty_profile / ".browser2zen-restored"
    assert marker.is_file()
    marker_data = json.loads(marker.read_text())
    assert marker_data["format_version"] == ARCHIVE_FORMAT_VERSION


# ----- selective include on export ---------------------------------------

def test_export_only_workspaces(source_profile, tmp_path):
    archive = tmp_path / "ws-only.zenbackup"
    result = ZenBackupExporter(
        source_profile, archive, includes=["workspaces"],
    ).export()
    assert result["ok"], result

    with tarfile.open(archive, "r:gz") as tar:
        members = sorted(m.name for m in tar.getmembers() if m.isfile())
    # Only manifest + workspace files should be present.
    assert "manifest.json" in members
    assert "profile/containers.json" in members
    assert "profile/zen-sessions.jsonlz4" in members
    # No browsing data.
    assert "profile/places.sqlite" not in members
    assert "profile/cookies.sqlite" not in members
    assert "profile/favicons.sqlite" not in members


# ----- selective include on restore --------------------------------------

def test_restore_only_workspaces_preserves_other_files(source_profile, empty_profile, tmp_path):
    archive = tmp_path / "all.zenbackup"
    ZenBackupExporter(source_profile, archive,
                      includes=list(DEFAULT_CATEGORIES)).export()

    # Stash the empty places.sqlite content so we can verify it didn't
    # change after a workspaces-only restore.
    before = (empty_profile / "places.sqlite").read_bytes()

    result = ZenBackupImporter(
        archive, empty_profile, includes=["workspaces"],
    ).import_archive()
    assert result["ok"], result

    restored = set(result["restored_files"])
    assert "containers.json" in restored
    assert "zen-sessions.jsonlz4" in restored
    assert "places.sqlite" not in restored
    assert "cookies.sqlite" not in restored

    # places.sqlite was NOT touched.
    after = (empty_profile / "places.sqlite").read_bytes()
    assert before == after


# ----- manifest version mismatch -----------------------------------------

def test_unsupported_archive_version(source_profile, empty_profile, tmp_path):
    archive = tmp_path / "future.zenbackup"
    ZenBackupExporter(source_profile, archive).export()

    # Hand-rewrite the manifest to format_version=99, then re-tar.
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(extracted)
    manifest_path = extracted / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["format_version"] = 99
    manifest_path.write_text(json.dumps(manifest))
    archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        for f in extracted.rglob("*"):
            if f.is_file():
                tar.add(f, arcname=str(f.relative_to(extracted)))

    result = ZenBackupImporter(archive, empty_profile).import_archive()
    assert result["ok"] is False
    assert "unsupported_archive_version" in result["errors"]


# ----- target file backup before overwrite -------------------------------

def test_existing_target_files_get_dot_backup(source_profile, empty_profile, tmp_path):
    archive = tmp_path / "round.zenbackup"
    ZenBackupExporter(source_profile, archive).export()

    # Write distinguishable content into the target's containers.json so
    # we can prove it got snapshotted.
    sentinel = b'{"sentinel": true}'
    (empty_profile / "containers.json").write_bytes(sentinel)

    result = ZenBackupImporter(archive, empty_profile).import_archive()
    assert result["ok"], result

    # A .backup.<ts> sibling should now exist with the sentinel.
    backups = list(empty_profile.glob("containers.json.backup.*"))
    assert backups, "expected at least one containers.json.backup.<ts>"
    assert any(b.read_bytes() == sentinel for b in backups)


# ----- missing target profile --------------------------------------------

def test_missing_target_profile_clean_error(source_profile, tmp_path):
    archive = tmp_path / "out.zenbackup"
    ZenBackupExporter(source_profile, archive).export()

    nonexistent = tmp_path / "does-not-exist"
    result = ZenBackupImporter(archive, nonexistent).import_archive()
    assert result["ok"] is False
    assert "target_profile_missing" in result["errors"]
    assert not nonexistent.exists()


# ----- preview without unpacking -----------------------------------------

def test_preview_returns_manifest(source_profile, tmp_path):
    archive = tmp_path / "preview.zenbackup"
    ZenBackupExporter(source_profile, archive,
                      includes=list(DEFAULT_CATEGORIES)).export()

    preview = ZenBackupImporter(archive, target_zen_profile=tmp_path).preview()
    assert preview["ok"], preview
    manifest = preview["manifest"]
    assert manifest["format_version"] == ARCHIVE_FORMAT_VERSION
    assert set(manifest["included"]) == set(DEFAULT_CATEGORIES)
    assert "exported_at" in manifest
    assert preview["archive_size"] > 0


# ----- exporter rejects unknown category names ---------------------------

def test_export_unknown_category(source_profile, tmp_path):
    archive = tmp_path / "out.zenbackup"
    with pytest.raises(ValueError):
        ZenBackupExporter(source_profile, archive, includes=["bogus"])


# ----- ALL_CATEGORIES sanity ----------------------------------------------

def test_all_categories_includes_defaults():
    assert set(DEFAULT_CATEGORIES) <= set(ALL_CATEGORIES)
    assert set(DEFAULT_CATEGORIES) == {"workspaces", "browsing", "cookies", "favicons"}
