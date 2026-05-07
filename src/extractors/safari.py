"""
Safari extractor.

Safari is macOS-only and stores bookmarks in a binary plist at
``~/Library/Safari/Bookmarks.plist``. The plist is a tree of nodes
typed by ``WebBookmarkType``:

- ``WebBookmarkTypeList``  — folder. ``Title`` (or, for top-level
  containers, the magic strings ``BookmarksBar``, ``BookmarksMenu``,
  ``com.apple.ReadingList``). ``Children`` is the list of child nodes.
- ``WebBookmarkTypeLeaf``  — bookmark. ``URLString`` is the URL,
  ``URIDictionary.title`` is the user-visible title.
- ``WebBookmarkTypeProxy`` — virtual reference (e.g. ``History``);
  skipped.

Per the plan, v1 ships **bookmarks only**. History (``History.db``)
and cookies (``Cookies.binarycookies``) require dedicated readers and
are not in scope here.

On macOS Sequoia, ``~/Library/Safari`` requires Full Disk Access for
non-Safari processes to read; we surface a clean error code so the GUI
can point the user at System Settings.
"""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .base import (
    BrowserExtractor,
    BrowserExtractorError,
    ExportData,
    FolderRecord,
    SpaceRecord,
    TabRecord,
)

logger = logging.getLogger(__name__)


_NS_FOLDER = uuid.UUID("4e7b8d6a-9c33-4e2b-9fa3-1bd1d1ea2c41")
_NS_SPACE = uuid.UUID("0ad3a9d9-95c0-4de1-87c1-2c41a9a3b1aa")

_TOP_LEVEL_LABELS = {
    "BookmarksBar":          "Bookmarks Bar",
    "BookmarksMenu":         "Bookmarks Menu",
    "com.apple.ReadingList": "Reading List",
}


def _bookmarks_plist() -> Path:
    return Path.home() / "Library/Safari/Bookmarks.plist"


def _list_has_content(node: dict) -> bool:
    """True if the list has at least one URL leaf anywhere under it."""
    stack = list(node.get("Children") or [])
    while stack:
        child = stack.pop()
        if not isinstance(child, dict):
            continue
        wtype = child.get("WebBookmarkType")
        if wtype == "WebBookmarkTypeLeaf" and (child.get("URLString") or "").strip():
            return True
        if wtype == "WebBookmarkTypeList":
            stack.extend(child.get("Children") or [])
    return False


class SafariExtractor(BrowserExtractor):
    name = "safari"
    display_name = "Safari"

    # ---------- detection ----------

    def is_installed(self) -> bool:
        if sys.platform != "darwin":
            return False
        return _bookmarks_plist().is_file()

    def profile_paths(self) -> list[Path]:
        # Safari has no concept of separate profiles; return the
        # ``~/Library/Safari`` dir as the single "profile" so the
        # detect screen has something concrete to display.
        if not self.is_installed():
            return []
        return [_bookmarks_plist().parent]

    def is_running(self) -> bool:
        if sys.platform != "darwin":
            return False
        try:
            r = subprocess.run(
                ["pgrep", "-f", "Safari.app/Contents/MacOS/Safari"],
                capture_output=True, text=True, timeout=2,
            )
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception:
            return False

    def quit(self) -> dict:
        if sys.platform != "darwin":
            return {"ok": False, "running": self.is_running(), "elapsed": 0.0,
                    "error": "Safari is macOS only"}
        started = time.time()
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Safari" to quit'],
                capture_output=True, timeout=3,
            )
        except Exception as exc:
            return {"ok": False, "running": True,
                    "elapsed": time.time() - started, "error": str(exc)}
        deadline = started + 6.0
        while time.time() < deadline:
            if not self.is_running():
                return {"ok": True, "running": False,
                        "elapsed": time.time() - started}
            time.sleep(0.25)
        return {"ok": False, "running": True,
                "elapsed": time.time() - started,
                "error": "Safari did not quit within timeout"}

    # ---------- extraction ----------

    def extract(self) -> ExportData:
        plist_path = _bookmarks_plist()
        if not plist_path.is_file():
            raise BrowserExtractorError(
                "no_safari_data",
                "Safari is not installed or has no bookmarks.",
            )
        try:
            raw = plist_path.read_bytes()
        except PermissionError as exc:
            # Sequoia + sandboxed Safari: this is the canonical FDA
            # symptom. Anything else surfaces as a generic error.
            raise BrowserExtractorError(
                "safari_needs_full_disk_access",
                "Reading Safari bookmarks needs Full Disk Access. "
                "Open System Settings → Privacy & Security → Full Disk Access "
                "and enable it for this app.",
            ) from exc

        try:
            tree = plistlib.loads(raw)
        except Exception as exc:
            raise BrowserExtractorError(
                "safari_bookmarks_unreadable",
                f"Could not parse Safari bookmarks: {exc}",
            ) from exc

        space_id = str(uuid.uuid5(_NS_SPACE, f"{self.name}:default"))
        tabs: list[TabRecord] = []
        folders: list[FolderRecord] = []

        # Top-level wrappers come from the children of the root list.
        # Each Safari "section" (Bookmarks Bar, Bookmarks Menu, Reading
        # List) becomes a wrapper folder so the user can tell them apart
        # in Zen.
        for child in (tree.get("Children") or []):
            if not isinstance(child, dict):
                continue
            wtype = child.get("WebBookmarkType")
            if wtype != "WebBookmarkTypeList":
                continue
            # Skip empty wrappers so the user doesn't see vestigial
            # "Reading List" or "Bookmarks Menu" folders in Zen.
            if not _list_has_content(child):
                continue
            title = child.get("Title", "") or ""
            label = _TOP_LEVEL_LABELS.get(title, title or "Bookmarks")
            wrapper_id = self._stable_folder_id(space_id, [label])
            folders.append(FolderRecord(
                folder_id=wrapper_id,
                title=label,
                parent_id=None,
                space_id=space_id,
                children_ids=[],
                index=len(folders),
            ))
            self._walk_node(
                node=child,
                folder_path=[label],
                parent_record_id=wrapper_id,
                space_id=space_id,
                tabs_out=tabs,
                folders_out=folders,
            )

        # Backfill children_ids on parent folders.
        by_id = {f.folder_id: f for f in folders}
        for f in folders:
            if f.parent_id and f.parent_id in by_id:
                parent = by_id[f.parent_id]
                if f.folder_id not in parent.children_ids:
                    parent.children_ids.append(f.folder_id)

        if not tabs and not folders:
            raise BrowserExtractorError(
                "no_safari_bookmarks",
                "Safari has no bookmarks to migrate.",
            )

        return ExportData(source=self.name, spaces=[
            SpaceRecord(
                space_id=space_id,
                space_name="Safari",
                pinned_tabs=tabs,
                open_tabs=[],
                folders=folders,
            ),
        ])

    def _walk_node(
        self,
        node: dict[str, Any],
        folder_path: list[str],
        parent_record_id: str | None,
        space_id: str,
        tabs_out: list[TabRecord],
        folders_out: list[FolderRecord],
    ) -> None:
        for child in (node.get("Children") or []):
            if not isinstance(child, dict):
                continue
            wtype = child.get("WebBookmarkType")
            if wtype == "WebBookmarkTypeLeaf":
                url = (child.get("URLString") or "").strip()
                if not url or not url.lower().startswith(("http://", "https://", "ftp://")):
                    continue
                title = ""
                uri_dict = child.get("URIDictionary") or {}
                if isinstance(uri_dict, dict):
                    title = uri_dict.get("title") or ""
                tabs_out.append(TabRecord(
                    url=url,
                    title=title,
                    folder_path=list(folder_path),
                    folder_id=parent_record_id,
                    is_essential=False,
                ))
            elif wtype == "WebBookmarkTypeList":
                title = child.get("Title", "") or "Untitled"
                child_path = folder_path + [title]
                fid = self._stable_folder_id(space_id, child_path)
                folders_out.append(FolderRecord(
                    folder_id=fid,
                    title=title,
                    parent_id=parent_record_id,
                    space_id=space_id,
                    children_ids=[],
                    index=len(folders_out),
                ))
                self._walk_node(
                    node=child,
                    folder_path=child_path,
                    parent_record_id=fid,
                    space_id=space_id,
                    tabs_out=tabs_out,
                    folders_out=folders_out,
                )
            # ProxyTypes are intentionally skipped.

    @staticmethod
    def _stable_folder_id(space_id: str, folder_path: list[str]) -> str:
        return str(uuid.uuid5(_NS_FOLDER, space_id + "/" + "/".join(folder_path)))

    # ---------- history / cookies ----------
    #
    # Safari has its own importers (SQLite History.db reader and a
    # binarycookies parser). The orchestrator dispatches by source.name
    # so the Chromium importers don't get called with these paths.

    def history_db_paths(self) -> list[Path]:
        history = Path.home() / "Library/Safari/History.db"
        return [history] if history.is_file() else []

    def cookie_db_paths(self) -> list[Path]:
        # Sandboxed Safari (modern macOS) keeps cookies inside its
        # container. We try both locations and return the first that
        # exists.
        candidates = (
            Path.home() / "Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies",
            Path.home() / "Library/Cookies/Cookies.binarycookies",
        )
        return [p for p in candidates if p.is_file()]

    def cookie_master_key(self) -> bytes:
        # Safari cookies are unencrypted; the orchestrator never hits
        # this path for Safari. Defensive raise.
        raise BrowserExtractorError(
            "safari_cookies_use_direct_path",
            "Safari cookies should be imported via SafariCookiesImporter, "
            "not via the Chromium key-unwrap path.",
        )
