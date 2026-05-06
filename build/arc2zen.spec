# PyInstaller spec for the Arc2Zen GUI app — macOS and Windows.
#
# Run from the repo root:
#   pyinstaller build/arc2zen.spec
#
# Produces dist/Arc2Zen.app (macOS arm64) or dist/Arc2Zen/ (Windows x64).
# The platform-specific build scripts (build/make_app.sh, build/make_exe.ps1)
# wrap this and produce the final distributable artifact.

# noqa: E402  (Analysis/PYZ/EXE/COLLECT/BUNDLE are injected by PyInstaller)

import sys
from pathlib import Path

REPO_ROOT = Path.cwd().resolve()
SRC_DIR = REPO_ROOT / "src"
APP_DIR = REPO_ROOT / "app"

# Single source of truth for the version string. Read app/__version__.py
# at spec evaluation time so the bundled Info.plist (macOS) / file version
# block (Windows) and the runtime ``bridge.version()`` always agree.
_version_globals: dict = {}
exec(compile((APP_DIR / "__version__.py").read_text(encoding="utf-8"),
             str(APP_DIR / "__version__.py"), "exec"),
     _version_globals)
VERSION = _version_globals["VERSION"]

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# ---- platform-specific Analysis inputs -----------------------------------

PLATFORM_HIDDEN_IMPORTS = (
    [
        "webview.platforms.cocoa",
        "objc",
        "Foundation",
        "AppKit",
        "WebKit",
    ]
    if IS_MAC
    else [
        # WebView2 backend on Windows. pywebview lazy-imports these via
        # ``webview/platforms/edgechromium.py``; PyInstaller's static
        # analysis doesn't see them without a hint.
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "pythonnet",
    ]
)

ICON_NAME = "icon.icns" if IS_MAC else "icon.ico"
PLATFORM_ICON = str(APP_DIR / "assets" / ICON_NAME)

# Don't pin ``target_arch`` on Windows: PyInstaller picks the host arch
# correctly. Pinning would break if/when GitHub moves to ARM64 runners.
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
    target_arch=TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
    icon=PLATFORM_ICON,
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

# BUNDLE() produces a .app on macOS only. On Windows the COLLECT() output
# directory is itself the ship artifact.
if IS_MAC:
    app = BUNDLE(
        coll,
        name="Arc2Zen.app",
        icon=PLATFORM_ICON,
        bundle_identifier="com.arc2zen.app",
        version=VERSION,
        info_plist={
            "CFBundleName": "Arc2Zen",
            "CFBundleDisplayName": "Arc2Zen",
            "CFBundleVersion": VERSION,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSApplicationCategoryType": "public.app-category.utilities",
            "LSMinimumSystemVersion": "12.0",
            "NSSupportsAutomaticTermination": True,
            "NSDesktopFolderUsageDescription":
                "Arc2Zen reads Arc cookies from your library to migrate login state.",
        },
    )
