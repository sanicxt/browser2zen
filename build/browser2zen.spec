# PyInstaller spec for the browser2zen GUI app — macOS and Windows.
#
# Run from the repo root:
#   pyinstaller build/browser2zen.spec
#
# Produces dist/browser2zen.app (macOS arm64) or dist/browser2zen/ (Windows x64).
# The platform-specific build scripts (build/make_app.sh, build/make_exe.ps1)
# wrap this and produce the final distributable artifact.

# noqa: E402  (Analysis/PYZ/EXE/COLLECT/BUNDLE are injected by PyInstaller)

import sys
from pathlib import Path

REPO_ROOT = Path.cwd().resolve()
SRC_DIR = REPO_ROOT / "src"
APP_DIR = REPO_ROOT / "app"

# Single source of truth for the version string. Parse app/__version__.py
# so the bundled Info.plist (macOS) / file version block (Windows) and the
# runtime ``bridge.version()`` always agree.
import re as _re
_v_text = (APP_DIR / "__version__.py").read_text(encoding="utf-8")
_v_match = _re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', _v_text, _re.MULTILINE)
if _v_match is None:
    raise RuntimeError(f"VERSION not found in {APP_DIR / '__version__.py'}")
VERSION = _v_match.group(1)

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# ---- platform-specific Analysis inputs -----------------------------------

if IS_MAC:
    PLATFORM_HIDDEN_IMPORTS = [
        "webview.platforms.cocoa",
        "objc",
        "Foundation",
        "AppKit",
        "WebKit",
    ]
elif IS_WIN:
    PLATFORM_HIDDEN_IMPORTS = [
        # WebView2 backend on Windows. pywebview lazy-imports these via
        # ``webview/platforms/edgechromium.py``; PyInstaller's static
        # analysis doesn't see them without a hint.
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "pythonnet",
    ]
else:  # Linux
    PLATFORM_HIDDEN_IMPORTS = [
        # GTK + WebKit2 backend.
        "webview.platforms.gtk",
        "gi",
        "gi.repository.Gtk",
        "gi.repository.WebKit2",
        "gi.repository.GLib",
    ]

# Linux skips the bundled icon: PyInstaller's Linux EXE() doesn't accept
# .icns/.ico, and rendering a .png iconset on CI would mean adding
# rsvg-convert. PyInstaller-Linux doesn't need an icon at all.
if IS_MAC:
    PLATFORM_ICON = str(APP_DIR / "assets" / "icon.icns")
elif IS_WIN:
    PLATFORM_ICON = str(APP_DIR / "assets" / "icon.ico")
else:
    PLATFORM_ICON = None

# Don't pin ``target_arch`` on Windows or Linux: PyInstaller picks the
# host arch correctly. Pinning would break if/when GitHub moves to ARM64
# runners.
TARGET_ARCH = "arm64" if IS_MAC else None

block_cipher = None

a = Analysis(
    [str(REPO_ROOT / "build" / "run_app.py")],
    pathex=[str(REPO_ROOT), str(SRC_DIR)],
    binaries=[],
    datas=[
        (str(APP_DIR / "frontend"), "app/frontend"),
        (str(APP_DIR / "assets"),   "app/assets"),
    ],
    # Importers in src/ are loaded via sys.path manipulation in
    # app/orchestrator.py, so PyInstaller can't see them statically.
    hiddenimports=[
        "lz4", "lz4.block",
        "cryptography",
        "cryptography.hazmat.primitives.ciphers",
        "cryptography.hazmat.primitives.ciphers.aead",
        "cryptography.hazmat.primitives.kdf.pbkdf2",
        "webview",
        *PLATFORM_HIDDEN_IMPORTS,
        "arc_pinned_tab_extractor",
        "zen_space_importer",
        "zen_sessions_importer",
        "zen_bookmark_importer",
        "zen_favicon_importer",
        "zen_sessionstore_manager",
        "chromium_history_importer",
        "chromium_cookies_importer",
        "extractors",
        "extractors.arc",
        "extractors.base",
        "extractors.brave",
        "extractors.chrome",
        "extractors.chromium",
        "extractors.edge",
        "extractors.firefox",
        "extractors.safari",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

EXE_OBJ = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="browser2zen",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
    icon=PLATFORM_ICON,
)

coll = COLLECT(
    EXE_OBJ,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="browser2zen",
)

# BUNDLE() produces a .app on macOS only. On Windows the COLLECT() output
# directory is itself the ship artifact.
if IS_MAC:
    app = BUNDLE(
        coll,
        name="browser2zen.app",
        icon=PLATFORM_ICON,
        bundle_identifier="com.browser2zen.app",
        version=VERSION,
        info_plist={
            "CFBundleName": "browser2zen",
            "CFBundleDisplayName": "browser2zen",
            "CFBundleVersion": VERSION,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSApplicationCategoryType": "public.app-category.utilities",
            "LSMinimumSystemVersion": "12.0",
            "NSSupportsAutomaticTermination": True,
            "NSDesktopFolderUsageDescription":
                "browser2zen reads source-browser cookies from your library to migrate login state.",
        },
    )
