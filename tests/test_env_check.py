"""Zen profile detection: display names must come from profiles.ini and
Zen's unified-profiles DB (Profile Groups/*.sqlite), not from directory
names. Zen 1.18+ registers menubar profiles there; they never appear in
about:profiles, so deriving names from folder names shows the wrong thing.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.env_check import list_zen_profiles


@pytest.fixture
def zen_home(fake_home):
    """A zen data root with three profiles: two real, one empty stub."""
    zen = fake_home / "Library/Application Support/zen"
    profiles = zen / "Profiles"
    for name in (
        "6ghmiudn.Default (release)",
        "jLOkT6K1.Profile 1",
        "vjr38owr.Empty Stub",
    ):
        (profiles / name).mkdir(parents=True)
    for name in ("6ghmiudn.Default (release)", "jLOkT6K1.Profile 1"):
        (profiles / name / "places.sqlite").write_bytes(b"")
    return zen


def _write_profiles_ini(zen, mapping):
    lines = ["[General]", "StartWithLastProfile=1", "Version=2"]
    for i, (path, name) in enumerate(mapping.items()):
        lines += [f"[Profile{i}]", f"Name={name}", "IsRelative=1", f"Path={path}"]
    (zen / "profiles.ini").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_profile_groups_db(zen, rows, store_id="3dfede00"):
    groups = zen / "Profile Groups"
    groups.mkdir(exist_ok=True)
    conn = sqlite3.connect(groups / f"{store_id}.sqlite")
    conn.execute(
        'CREATE TABLE "Profiles" ('
        "id INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL, "
        "avatar TEXT NOT NULL, themeId TEXT NOT NULL, themeFg TEXT NOT NULL, "
        "themeBg TEXT NOT NULL, PRIMARY KEY(id))"
    )
    conn.executemany("INSERT INTO Profiles VALUES (?, ?, ?, '', '', '', '')", rows)
    conn.commit()
    conn.close()


def test_names_come_from_ini_when_present(zen_home):
    _write_profiles_ini(
        zen_home,
        {
            "Profiles/6ghmiudn.Default (release)": "Personal",
            "Profiles/jLOkT6K1.Profile 1": "Work",
        },
    )
    names = {p.path.name: p.name for p in list_zen_profiles()}
    assert names == {
        "6ghmiudn.Default (release)": "Personal",
        "jLOkT6K1.Profile 1": "Work",
    }


def test_names_come_from_profile_groups_when_ini_lacks_them(zen_home):
    # Unified-profiles Zen: profiles registered only in the menubar DB.
    _write_profile_groups_db(
        zen_home,
        [
            (1, "Profiles/6ghmiudn.Default (release)", "Main"),
            (2, "Profiles/jLOkT6K1.Profile 1", "Work"),
        ],
    )
    names = {p.path.name: p.name for p in list_zen_profiles()}
    assert names["6ghmiudn.Default (release)"] == "Main"
    assert names["jLOkT6K1.Profile 1"] == "Work"


def test_profile_groups_win_over_stale_ini(zen_home):
    # Modern Zen keeps the live menubar names in Profile Groups; the
    # profiles.ini entry may be a stale legacy name from before the
    # unified-profiles migration.
    _write_profiles_ini(zen_home, {"Profiles/jLOkT6K1.Profile 1": "Stale Legacy"})
    _write_profile_groups_db(
        zen_home, [(2, "Profiles/jLOkT6K1.Profile 1", "Live Name")]
    )
    names = {p.path.name: p.name for p in list_zen_profiles()}
    assert names["jLOkT6K1.Profile 1"] == "Live Name"


def test_falls_back_to_directory_name(zen_home):
    names = {p.path.name: p.name for p in list_zen_profiles()}
    assert names["6ghmiudn.Default (release)"] == "Default (release)"


def test_profile_without_places_sqlite_is_skipped(zen_home):
    assert all(p.path.name != "vjr38owr.Empty Stub" for p in list_zen_profiles())
