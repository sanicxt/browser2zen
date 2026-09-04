"""
OS theme and platform detection for browser2zen.

Provides system dark/light theme detection across Linux (GNOME, KDE Plasma,
Freedesktop portal), macOS, and Windows, plus dynamic theme change listening
on Linux via D-Bus / XDG portal.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)


def detect_system_theme() -> str:
    """Return 'dark' or 'light' matching the current OS desktop setting."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            return "dark" if r.returncode == 0 and "dark" in r.stdout.lower() else "light"
        except Exception:
            return "dark"

    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if val == 1 else "dark"
        except Exception:
            return "dark"

    # --- Linux ---
    # 1. XDG Desktop Portal via Gio (GNOME, KDE Plasma 5.27+, Plasma 6, wlroots)
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            "Read",
            GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE,
            800,
            None,
        )
        val = res.unpack()[0]
        # 1 = prefer-dark, 2 = prefer-light, 0 = no preference
        if val == 1:
            return "dark"
        if val == 2:
            return "light"
    except Exception:
        pass

    # 2. XDG Desktop Portal via gdbus CLI fallback
    try:
        r = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.portal.Desktop",
                "--object-path",
                "/org/freedesktop/portal/desktop",
                "--method",
                "org.freedesktop.portal.Settings.Read",
                "org.freedesktop.appearance",
                "color-scheme",
            ],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if r.returncode == 0:
            if "uint32 1" in r.stdout:
                return "dark"
            if "uint32 2" in r.stdout:
                return "light"
    except Exception:
        pass

    # 3. GNOME gsettings
    try:
        r = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if r.returncode == 0:
            cs = r.stdout.strip().strip("'\"")
            if "dark" in cs:
                return "dark"
            if cs in ("default", "prefer-light"):
                return "light"
    except Exception:
        pass

    # 4. KDE Plasma config via kreadconfig
    for cmd in (
        ["kreadconfig6", "--group", "General", "--key", "ColorScheme"],
        ["kreadconfig5", "--group", "General", "--key", "ColorScheme"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            if r.returncode == 0 and r.stdout.strip():
                return "dark" if "dark" in r.stdout.lower() else "light"
        except Exception:
            pass

    # 5. Fallback check for GTK theme name
    try:
        r = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if r.returncode == 0 and "dark" in r.stdout.lower():
            return "dark"
    except Exception:
        pass

    return "dark"


def get_platform_info() -> dict[str, str]:
    """Return friendly platform, key for CSS dataset, and architecture."""
    sys_name = platform.system()
    if sys_name == "Darwin":
        plat_name = "macOS"
        plat_key = "mac"
    elif sys_name == "Linux":
        plat_name = "Linux"
        plat_key = "linux"
    elif sys_name == "Windows":
        plat_name = "Windows"
        plat_key = "win"
    else:
        plat_name = sys_name
        plat_key = sys.platform

    raw_arch = platform.machine().lower()
    if raw_arch in ("x86_64", "amd64"):
        arch_name = "x64"
    elif raw_arch in ("aarch64", "arm64"):
        arch_name = "arm64"
    else:
        arch_name = platform.machine()

    theme = detect_system_theme()

    return {
        "platform": plat_key,
        "platformName": plat_name,
        "arch": arch_name,
        "theme": theme,
    }


def setup_linux_theme_listener(window: Any) -> None:
    """On Linux, subscribe to XDG desktop portal appearance changes and notify webview."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gio, Gtk

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def _on_setting_changed(
            conn: Any,
            sender: Any,
            path: Any,
            iface: Any,
            signal: Any,
            params: Any,
            user_data: Any,
        ) -> None:
            try:
                ns, key, val = params.unpack()
                if ns == "org.freedesktop.appearance" and key == "color-scheme":
                    new_theme = "dark" if val == 1 else "light"
                    settings = Gtk.Settings.get_default()
                    if settings:
                        settings.set_property(
                            "gtk-application-prefer-dark-theme", new_theme == "dark"
                        )
                    window.evaluate_js(
                        f"if (window.__setTheme) window.__setTheme({new_theme!r});"
                        f"else document.documentElement.dataset.theme = {new_theme!r};"
                    )
            except Exception as e:
                logger.debug(f"Error handling portal setting change: {e}")

        bus.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Settings",
            "SettingChanged",
            "/org/freedesktop/portal/desktop",
            None,
            Gio.DBusSignalFlags.NONE,
            _on_setting_changed,
            None,
        )
    except Exception as exc:
        logger.debug(f"Could not setup Linux theme listener: {exc}")
