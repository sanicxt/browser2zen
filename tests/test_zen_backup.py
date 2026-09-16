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
    _merge_prefs_text,
    _scrub_extensions_json,
    _scrub_logins_json,
    _scrub_prefs_text,
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
    assert set(DEFAULT_CATEGORIES) == {
        "workspaces", "browsing", "cookies", "favicons", "mods",
    }


# ----- Zen Mods round-trip ------------------------------------------------

def test_mods_roundtrip(source_profile, empty_profile, tmp_path):
    archive = tmp_path / "mods.zenbackup"
    result = ZenBackupExporter(
        source_profile, archive, includes=["mods"],
    ).export()
    assert result["ok"], result

    # The fixture's chrome/userChrome.css should be in the archive.
    with tarfile.open(archive, "r:gz") as tar:
        members = sorted(m.name for m in tar.getmembers() if m.isfile())
    assert "profile/chrome/userChrome.css" in members
    # And nothing from the unrelated categories.
    assert "profile/places.sqlite" not in members

    restore = ZenBackupImporter(
        archive, empty_profile, includes=["mods"],
    ).import_archive()
    assert restore["ok"], restore
    landed = empty_profile / "chrome" / "userChrome.css"
    assert landed.is_file()
    expected = (source_profile / "chrome" / "userChrome.css").read_bytes()
    assert landed.read_bytes() == expected


# ----- Restore preserves untouched mod siblings on the target ------------

def test_mods_restore_is_additive(source_profile, empty_profile, tmp_path):
    archive = tmp_path / "mods.zenbackup"
    ZenBackupExporter(source_profile, archive, includes=["mods"]).export()

    # Pre-seed a mod the archive doesn't carry.
    target_chrome = empty_profile / "chrome"
    target_chrome.mkdir(exist_ok=True)
    sentinel = target_chrome / "other-mod.css"
    sentinel.write_text("/* user's local mod */")

    result = ZenBackupImporter(
        archive, empty_profile, includes=["mods"],
    ).import_archive()
    assert result["ok"], result

    # The archive's userChrome.css landed.
    assert (target_chrome / "userChrome.css").is_file()
    # The user's untouched local mod is still there (additive merge —
    # same semantics extensions/ already use).
    assert sentinel.is_file()
    assert sentinel.read_text() == "/* user's local mod */"


# ----- Forward-compat: importer skips unknown categories cleanly ---------

def test_unknown_category_is_skipped_not_fatal(source_profile, empty_profile, tmp_path):
    archive = tmp_path / "future.zenbackup"
    ZenBackupExporter(source_profile, archive,
                      includes=list(DEFAULT_CATEGORIES)).export()

    # Hand-rewrite the manifest so it advertises a category this version
    # of browser2zen doesn't know about.
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(extracted)
    manifest_path = extracted / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["included"].append("future-cat")
    manifest_path.write_text(json.dumps(manifest))
    archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        for f in extracted.rglob("*"):
            if f.is_file():
                tar.add(f, arcname=str(f.relative_to(extracted)))

    # Restore everything in the manifest. The unknown category should
    # show up in skipped, not blow up the whole restore.
    result = ZenBackupImporter(archive, empty_profile).import_archive()
    assert result["ok"] is True, result
    skipped_cats = [s.get("category") for s in result["skipped"]
                    if "category" in s]
    assert "future-cat" in skipped_cats
    # The known categories still land.
    assert (empty_profile / "places.sqlite").is_file()


# ----- source-profile identity is never propagated on restore -------------

_PREFS = (
    'user_pref("browser.startup.homepage", "https://example.com");\n'
    'user_pref("services.sync.username", "someone@example.com");\n'
    'user_pref("services.sync.engine.spaces", true);\n'
    'user_pref("identity.fxaccounts.account.device.name", "source laptop");\n'
    'user_pref("toolkit.profiles.storeID", "355e414e");\n'
    'user_pref("browser.profiles.enabled", true);\n'
    'user_pref("datareporting.dau.cachedUsageProfileID", "abc");\n'
    'user_pref("nimbus.profileId", "def");\n'
    'user_pref("extensions.webextensions.uuids", "{\\"x\\":\\"y\\"}");\n'
)


def test_scrub_prefs_keeps_user_prefs_drops_identity():
    out = _scrub_prefs_text(_PREFS)
    assert 'browser.startup.homepage' in out
    for leaked in (
        "services.sync.", "identity.fxaccounts.", "toolkit.profiles.storeID",
        "browser.profiles.enabled", "datareporting.dau.", "nimbus.profileId",
        "extensions.webextensions.uuids",
    ):
        assert leaked not in out


def test_merge_prefs_preserves_target_identity():
    target = (
        'user_pref("toolkit.profiles.storeID", "targetstore");\n'
        'user_pref("services.sync.username", "target@example.com");\n'
    )
    out = _merge_prefs_text(_PREFS, target)
    # Source identity gone, target identity preserved verbatim.
    assert "someone@example.com" not in out
    assert 'toolkit.profiles.storeID", "targetstore"' in out
    assert "target@example.com" in out
    # User prefs survive.
    assert "browser.startup.homepage" in out


def test_scrub_logins_drops_firefox_account_credential():
    logins = json.dumps({
        "logins": [
            {"hostname": "chrome://FirefoxAccounts", "encryptedPassword": "x"},
            {"hostname": "https://example.com", "encryptedPassword": "keep"},
        ],
        "potentiallyVulnerablePasswords": [],
    }).encode("utf-8")
    out = json.loads(_scrub_logins_json(logins).decode("utf-8"))
    hosts = [entry["hostname"] for entry in out["logins"]]
    assert hosts == ["https://example.com"]


def test_scrub_extensions_repoints_source_paths():
    exts = json.dumps({
        "addons": [{
            "id": "uBlock0@raymondhill.net",
            "path": "/home/source/.zen/tsibjxyu.Default (release)/extensions/uBlock0@raymondhill.net.xpi",
            "rootURI": "jar:file:///home/source/.zen/tsibjxyu.Default%20(release)/extensions/uBlock0@raymondhill.net.xpi!/",
        }],
    }).encode("utf-8")
    target = Path("/tmp/target-profile")
    out = json.loads(_scrub_extensions_json(exts, target).decode("utf-8"))
    addon = out["addons"][0]
    assert addon["path"] == "/tmp/target-profile/extensions/uBlock0@raymondhill.net.xpi"
    assert addon["rootURI"] == (
        "jar:file:///tmp/target-profile/extensions/uBlock0@raymondhill.net.xpi!/"
    )
    assert "source" not in addon["path"]


def test_restore_scrubs_identity_end_to_end(source_profile, empty_profile, tmp_path):
    # Seed the source profile with a signed-in prefs.js + FxA login, and a
    # target whose own identity must survive.
    (source_profile / "prefs.js").write_text(_PREFS)
    (source_profile / "logins.json").write_text(json.dumps({
        "logins": [{"hostname": "chrome://FirefoxAccounts", "encryptedPassword": "x"}],
    }))
    (empty_profile / "prefs.js").write_text(
        'user_pref("toolkit.profiles.storeID", "targetstore");\n'
    )

    archive = tmp_path / "identity.zenbackup"
    ZenBackupExporter(source_profile, archive,
                      includes=["prefs", "passwords"]).export()
    result = ZenBackupImporter(archive, empty_profile,
                               includes=["prefs", "passwords"]).import_archive()
    assert result["ok"], result

    restored_prefs = (empty_profile / "prefs.js").read_text()
    assert "services.sync.username" not in restored_prefs
    assert "identity.fxaccounts." not in restored_prefs
    assert 'toolkit.profiles.storeID", "targetstore"' in restored_prefs

    restored_logins = json.loads((empty_profile / "logins.json").read_text())
    assert all(entry["hostname"] != "chrome://FirefoxAccounts"
               for entry in restored_logins["logins"])


def test_restore_preserves_flatpak_profile_storeid(source_profile, empty_profile, tmp_path):
    """A restore must not replace the target install's unified-profile storeID.

    Flatpak Zen keeps its profiles directly under ``.zen`` and registers
    them in ``Profile Groups/<storeID>.sqlite``. The profile's ``prefs.js``
    carries the matching ``toolkit.profiles.storeID``. Overwriting it with
    the source's storeID points Zen at a store that doesn't exist and the
    profile list goes blank after restart (the reported bug).
    """
    (source_profile / "prefs.js").write_text(
        'user_pref("services.sync.username", "signedin@example.com");\n'
        'user_pref("toolkit.profiles.storeID", "355e414e");\n'
        'user_pref("browser.startup.homepage", "https://example.com");\n'
    )
    (empty_profile / "prefs.js").write_text(
        'user_pref("toolkit.profiles.storeID", "da222999");\n'
    )

    archive = tmp_path / "flatpak.zenbackup"
    ZenBackupExporter(source_profile, archive, includes=["prefs"]).export()
    result = ZenBackupImporter(archive, empty_profile,
                               includes=["prefs"]).import_archive()
    assert result["ok"], result

    restored = (empty_profile / "prefs.js").read_text()
    assert '"355e414e"' not in restored          # source storeID dropped
    assert '"da222999"' in restored              # target storeID survives
    assert "signedin@example.com" not in restored
    assert "browser.startup.homepage" in restored


def test_restore_injects_install_storeid_when_target_has_none(
        source_profile, empty_profile, tmp_path):
    """A fresh target without its own storeID still gets registered.

    Derive the install storeID from ``Profile Groups/<id>.sqlite`` so the
    restored profile isn't left pointing at the source's (foreign) store.
    """
    import sqlite3

    (source_profile / "prefs.js").write_text(
        'user_pref("toolkit.profiles.storeID", "355e414e");\n'
        'user_pref("browser.startup.homepage", "https://example.com");\n'
    )
    # No target prefs.js at all, but this install has a store that lists
    # the target profile by name.
    groups = empty_profile.parent / "Profile Groups"
    groups.mkdir(exist_ok=True)
    conn = sqlite3.connect(groups / "deadbeef.sqlite")
    conn.execute(
        'CREATE TABLE "Profiles" (id INTEGER, path TEXT, name TEXT, avatar TEXT, '
        "themeId TEXT, themeFg TEXT, themeBg TEXT)"
    )
    conn.execute("INSERT INTO Profiles VALUES (1, ?, 'Main', '', '', '', '')",
                 (empty_profile.name,))
    conn.commit()
    conn.close()

    archive = tmp_path / "storeid.zenbackup"
    ZenBackupExporter(source_profile, archive, includes=["prefs"]).export()
    result = ZenBackupImporter(archive, empty_profile,
                               includes=["prefs"]).import_archive()
    assert result["ok"], result

    restored = (empty_profile / "prefs.js").read_text()
    assert '"355e414e"' not in restored
    assert 'toolkit.profiles.storeID", "deadbeef"' in restored
    assert "browser.startup.homepage" in restored
