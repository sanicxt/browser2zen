import sys
from pathlib import Path
from unittest.mock import MagicMock

from app.bridge import Bridge
from app.theme import detect_system_theme, get_platform_info, setup_linux_theme_listener


def test_get_platform_info_structure():
    info = get_platform_info()
    assert isinstance(info, dict)
    assert "platform" in info
    assert "platformName" in info
    assert "arch" in info
    assert "theme" in info
    assert info["theme"] in ("dark", "light")
    if sys.platform.startswith("linux"):
        assert info["platform"] == "linux"
        assert info["platformName"] == "Linux"


def test_bridge_system_info():
    b = Bridge()
    info = b.system_info()
    assert isinstance(info, dict)
    assert info["theme"] in ("dark", "light")
    assert b.get_theme() in ("dark", "light")


def test_detect_system_theme_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    class MockRun:
        returncode = 0
        stdout = "Dark\n"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: MockRun())
    assert detect_system_theme() == "dark"


def test_detect_system_theme_gsettings_light(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def mock_run(cmd, *a, **kw):
        if "color-scheme" in cmd:
            return MagicMock(returncode=0, stdout="'prefer-light'\n")
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr("subprocess.run", mock_run)
    # Also ensure Gio portal lookup raises or fails so it falls back to gsettings
    if "gi" in sys.modules:
        monkeypatch.setattr("gi.repository.Gio.bus_get_sync", MagicMock(side_effect=Exception("No DBus")))
    assert detect_system_theme() == "light"


def test_index_html_does_not_contain_hardcoded_macos():
    index_path = Path(__file__).resolve().parent.parent / "app" / "frontend" / "index.html"
    content = index_path.read_text(encoding="utf-8")
    assert "<span>macOS · arm64</span>" not in content
    assert 'id="footer-platform"' in content


def test_tokens_css_has_data_theme():
    tokens_path = Path(__file__).resolve().parent.parent / "app" / "frontend" / "tokens.css"
    content = tokens_path.read_text(encoding="utf-8")
    assert '[data-theme="dark"]' in content
    assert '[data-theme="light"]' in content


def test_setup_linux_theme_listener(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mock_window = MagicMock()
    # Should run without error
    setup_linux_theme_listener(mock_window)
