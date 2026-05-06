"""
Environment detection for the Detect screen.

We deliberately do NOT import ``src.arc_profile_discovery`` because that file
contains a syntax error (mixed tabs/spaces) in the current upstream tree.
Instead we glob the well-known Arc and Zen paths directly. The logic mirrors
the working subset that the CLI tool already relies on via
``arc_pinned_tab_extractor`` (which reads ``StorableSidebar.json`` directly).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ZenProfile:
    name: str
    path: Path
    is_release: bool          # cheap heuristic: name contains "release"
    has_zen_sessions: bool    # zen-sessions.jsonlz4 exists (modern format)


@dataclass(frozen=True)
class EnvReport:
    arc_installed: bool
    arc_data_path: Optional[Path]
    arc_storable_sidebar: Optional[Path]
    arc_profiles: list[str]            # subdirectories under "User Data"
    arc_running: bool
    zen_installed: bool
    zen_profiles: list[ZenProfile]
    zen_running: bool
    has_lz4: bool
    has_cryptography: bool
    previous_migration_detected: bool
    errors: list[str] = field(default_factory=list)


# ---------- Arc ----------


def _arc_user_data_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/Arc/User Data"
    if os.name == "nt":
        return (
            home
            / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4"
            / "LocalCache/Local/Arc/User Data"
        )
    return home / ".config/Arc/User Data"


def _arc_storable_sidebar() -> Optional[Path]:
    """Path to Arc's StorableSidebar.json: present whenever Arc has data."""
    home = Path.home()
    candidates = [
        home / "Library/Application Support/Arc/StorableSidebar.json",
        home / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4"
             / "LocalCache/Local/Arc/StorableSidebar.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _arc_profiles(user_data: Path) -> list[str]:
    if not user_data.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(user_data.iterdir()):
        if not entry.is_dir():
            continue
        # Arc's per-profile dirs always contain a History SQLite file
        if (entry / "History").is_file():
            out.append(entry.name)
    return out


# ---------- Zen ----------


def _zen_profiles_root() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/zen/Profiles"
    if os.name == "nt":
        return home / "AppData/Roaming/zen/Profiles"
    return home / ".zen"


def list_zen_profiles() -> list[ZenProfile]:
    root = _zen_profiles_root()
    if not root.is_dir():
        return []
    result: list[ZenProfile] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        # Heuristic: a real profile has at least places.sqlite
        if not (entry / "places.sqlite").is_file():
            continue
        name = entry.name.split(".", 1)[1] if "." in entry.name else entry.name
        result.append(
            ZenProfile(
                name=name,
                path=entry,
                is_release="release" in entry.name.lower(),
                has_zen_sessions=(entry / "zen-sessions.jsonlz4").is_file(),
            )
        )
    # Prefer release-labelled profiles first
    result.sort(key=lambda p: (not p.is_release, p.name.lower()))
    return result


# ---------- browsers running ----------


_ARC_PROCESS_PATHS = ("/Applications/Arc.app",)
_ZEN_PROCESS_PATHS = (
    "/Applications/Zen.app/Contents/MacOS/zen",
    "/Applications/Zen Browser.app/Contents/MacOS/zen",
    "/Applications/zen.app/Contents/MacOS/zen",
)


def _pgrep_any(paths: tuple[str, ...]) -> bool:
    for p in paths:
        try:
            r = subprocess.run(["pgrep", "-f", p], capture_output=True, text=True, timeout=2)
        except Exception:
            continue
        if r.returncode == 0 and r.stdout.strip():
            return True
    return False


def _powershell_running(name: str) -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f'(Get-Process -Name "{name}" -ErrorAction SilentlyContinue).Count'],
            capture_output=True, text=True, timeout=4,
        )
    except Exception:
        return False
    return r.returncode == 0 and (r.stdout or "0").strip() != "0"


def is_arc_running() -> bool:
    if sys.platform == "darwin" or sys.platform == "linux":
        return _pgrep_any(_ARC_PROCESS_PATHS)
    if os.name == "nt":
        return _powershell_running("Arc")
    return False


def is_zen_running() -> bool:
    if sys.platform == "darwin" or sys.platform == "linux":
        return _pgrep_any(_ZEN_PROCESS_PATHS)
    if os.name == "nt":
        return _powershell_running("zen") or _powershell_running("Zen")
    return False


# ---------- previous migration marker ----------


def _previous_migration_detected(zen_profile_path: Optional[Path]) -> bool:
    if zen_profile_path is None:
        return False
    return (zen_profile_path / ".arc2zen-migrated").is_file()


# ---------- module checks ----------


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


# ---------- public ----------


def check_environment() -> EnvReport:
    errors: list[str] = []

    arc_user_data = _arc_user_data_dir()
    arc_sidebar = _arc_storable_sidebar()
    arc_installed = arc_sidebar is not None
    arc_profile_names = _arc_profiles(arc_user_data) if arc_installed else []

    zen_profiles = list_zen_profiles()
    zen_installed = bool(zen_profiles)

    return EnvReport(
        arc_installed=arc_installed,
        arc_data_path=arc_user_data if arc_installed else None,
        arc_storable_sidebar=arc_sidebar,
        arc_profiles=arc_profile_names,
        arc_running=is_arc_running(),
        zen_installed=zen_installed,
        zen_profiles=zen_profiles,
        zen_running=is_zen_running(),
        has_lz4=_has_module("lz4"),
        has_cryptography=_has_module("cryptography"),
        previous_migration_detected=_previous_migration_detected(
            zen_profiles[0].path if zen_profiles else None
        ),
        errors=errors,
    )


def env_report_to_dict(report: EnvReport) -> dict:
    """JSON-serializable shape for the JS bridge."""
    return {
        "arcInstalled": report.arc_installed,
        "arcDataPath": str(report.arc_data_path) if report.arc_data_path else None,
        "arcStorableSidebar": str(report.arc_storable_sidebar) if report.arc_storable_sidebar else None,
        "arcProfiles": list(report.arc_profiles),
        "arcRunning": report.arc_running,
        "zenInstalled": report.zen_installed,
        "zenProfiles": [
            {
                "name": p.name,
                "path": str(p.path),
                "isRelease": p.is_release,
                "hasZenSessions": p.has_zen_sessions,
            }
            for p in report.zen_profiles
        ],
        "zenRunning": report.zen_running,
        "hasLz4": report.has_lz4,
        "hasCryptography": report.has_cryptography,
        "previousMigrationDetected": report.previous_migration_detected,
        "errors": list(report.errors),
    }
