# PyInstaller spec for the Arc2Zen GUI app.
#
# Run from the repo root:
#   pyinstaller build/arc2zen.spec
#
# Produces dist/Arc2Zen.app (arm64). The build script then ad-hoc signs it
# and packages it into a .dmg via create-dmg.

# noqa: E402  (Analysis/PYZ/EXE/COLLECT/BUNDLE are injected by PyInstaller)

import sys
from pathlib import Path

REPO_ROOT = Path.cwd().resolve()
SRC_DIR = REPO_ROOT / "src"
APP_DIR = REPO_ROOT / "app"

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
        "cryptography.hazmat.primitives.kdf.pbkdf2",
        "webview", "webview.platforms.cocoa",
        "objc", "Foundation", "AppKit", "WebKit",
        "arc_pinned_tab_extractor",
        "zen_space_importer",
        "zen_sessions_importer",
        "zen_bookmark_importer",
        "zen_favicon_importer",
        "zen_pinned_tab_importer",
        "zen_workspace_importer",
        "zen_sessionstore_manager",
        "arc_history_importer",
        "arc_cookies_importer",
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Arc2Zen",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
    icon=str(APP_DIR / "assets" / "icon.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Arc2Zen",
)

app = BUNDLE(
    coll,
    name="Arc2Zen.app",
    icon=str(APP_DIR / "assets" / "icon.icns"),
    bundle_identifier="com.tarikbc.arc2zen",
    version="1.0.0",
    info_plist={
        "CFBundleName": "Arc2Zen",
        "CFBundleDisplayName": "Arc2Zen",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "LSMinimumSystemVersion": "12.0",
        "NSSupportsAutomaticTermination": True,
        # Reading the macOS Keychain to decrypt Arc cookies needs an
        # explanation string the first time the user is prompted.
        "NSDesktopFolderUsageDescription":
            "Arc2Zen reads Arc cookies from your library to migrate login state.",
    },
)
