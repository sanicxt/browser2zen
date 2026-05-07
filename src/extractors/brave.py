"""Brave browser extractor."""

from __future__ import annotations

from pathlib import Path

from .chromium import ChromiumExtractor


class BraveExtractor(ChromiumExtractor):
    name = "brave"
    display_name = "Brave"

    user_data_dirs_macos = (
        Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser",
    )
    user_data_dirs_windows = (
        Path.home() / "AppData/Local/BraveSoftware/Brave-Browser/User Data",
    )
    user_data_dirs_linux = (
        Path.home() / ".config/BraveSoftware/Brave-Browser",
    )

    keychain_service = "Brave Safe Storage"
    keychain_account = "Brave"

    macos_app_name = "Brave Browser"
    macos_process_paths = ("Brave Browser.app/Contents/MacOS/Brave Browser",)
    windows_process_names = ("brave.exe",)
