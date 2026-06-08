#!/usr/bin/env python3
"""
Chromium → Zen Favicon Importer

Reads favicons from any Chromium-format ``Favicons`` SQLite database
(Arc / Chrome / Edge / Brave) and imports them into Zen's Firefox-format
``favicons.sqlite``, linking each favicon to the migrated page URLs.

Also injects each tab's icon as an inline ``image`` data URI inside
``zen-sessions.jsonlz4`` — modern Zen renders pinned-tab favicons from
that field, not from the SQLite store.

Hash algorithms mirror Firefox's ``mozilla::HashString`` and
``places::HashURL`` so Zen finds the inserted entries via its
hash-indexed lookups.

The orchestrator hands in a list of source ``Favicons`` paths via
``favicon_dbs=`` so the same code serves every Chromium browser.
"""

from __future__ import annotations

import base64
import logging
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Firefox MFBT golden ratio prime used by mozilla::AddToHash.
_GOLDEN_RATIO_U32 = 0x9E3779B9


def _add_to_hash(h: int, b: int) -> int:
    rotated = ((h << 5) | (h >> 27)) & 0xFFFFFFFF
    return (_GOLDEN_RATIO_U32 * (rotated ^ b)) & 0xFFFFFFFF


def _hash_string(s: str) -> int:
    h = 0
    for byte in s.encode("utf-8"):
        h = _add_to_hash(h, byte)
    return h


def hash_page_url(url: str) -> int:
    """places::HashURL. 48-bit hash with prefix in upper 16 bits."""
    full = _hash_string(url)
    idx = url.find(":")
    prefix = _hash_string(url[:idx]) if idx > 0 else 0
    return (((prefix & 0xFFFF) << 32) | full) & 0xFFFFFFFFFFFF


def fixed_icon_url(icon_url: str) -> str:
    """Firefox normalizes icon URLs by stripping the scheme and leading 'www.'."""
    s = icon_url
    for scheme in ("https://", "http://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    if s.startswith("www."):
        s = s[4:]
    return s


def hash_icon_url(icon_url: str) -> int:
    return _hash_string(fixed_icon_url(icon_url))


# Chromium icon_type values (chrome/browser/favicon/favicon_types.h).
# 1 = FAVICON (preferred), 2/3 = TOUCH_ICON, 4 = WEB_MANIFEST_ICON.
_ICON_TYPE_PRIORITY = {1: 4, 2: 3, 3: 2, 4: 1}


def _detect_image_mime(blob: bytes) -> str:
    """Sniff image format from the leading bytes."""
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if blob[:3] == b"GIF":
        return "image/gif"
    if blob[:2] == b"\xff\xd8":
        return "image/jpeg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    if blob.lstrip()[:5] in (b"<?xml", b"<svg "):
        return "image/svg+xml"
    return "image/png"  # safest default. Firefox decoders accept misnamed PNGs


class FaviconImporter:
    """Imports favicons from any Chromium-format Favicons DB into Zen's favicons.sqlite."""

    DEFAULT_EXPIRE_DAYS = 28

    def __init__(
        self,
        zen_profile_path: Path,
        dry_run: bool = False,
        favicon_dbs: list[Path] | None = None,
    ):
        # ``favicon_dbs`` lets the multi-source orchestrator inject paths
        # from any Chromium-format browser. None = original Arc-only lookup.
        self.zen_profile = Path(zen_profile_path)
        self.zen_favicons_db = self.zen_profile / "favicons.sqlite"
        self.dry_run = dry_run
        self._injected_dbs: list[Path] | None = (
            [Path(p) for p in favicon_dbs] if favicon_dbs is not None else None
        )
        self._tempdir: Path | None = None

    def _favicon_dbs(self) -> list[Path]:
        """Locate every source-browser profile's Favicons SQLite file."""
        if self._injected_dbs is not None:
            return [p for p in self._injected_dbs if p.is_file()]
        candidates: list[Path] = []
        home = Path.home()
        macos = home / "Library" / "Application Support" / "Arc" / "User Data"
        windows = (
            home
            / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4"
            / "LocalCache/Local/Arc/User Data"
        )
        for root in (macos, windows):
            if root.exists():
                candidates.extend(p for p in root.glob("*/Favicons") if p.is_file())
        return sorted(set(candidates))

    def _snapshot_db(self, src: Path) -> Path:
        """Copy a SQLite db to a temp dir so we can read it without lock contention."""
        if self._tempdir is None:
            self._tempdir = Path(tempfile.mkdtemp(prefix="browser2zen_favicons_"))
        dest = self._tempdir / f"{src.parent.name}_{src.name}.db"
        shutil.copy2(src, dest)
        for suffix in ("-wal", "-shm", "-journal"):
            sibling = src.with_name(src.name + suffix)
            if sibling.exists():
                shutil.copy2(sibling, dest.with_name(dest.name + suffix))
        return dest

    def _cleanup_temp(self) -> None:
        if self._tempdir and self._tempdir.exists():
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None

    @staticmethod
    def _normalize(url: str) -> str:
        """Normalize a URL for fuzzy matching (strip fragment, trailing slash)."""
        if "#" in url:
            url = url.split("#", 1)[0]
        if url.endswith("/"):
            url = url[:-1]
        return url

    @staticmethod
    def _origin(url: str) -> str | None:
        try:
            p = urlparse(url)
        except ValueError:
            return None
        if not p.scheme or not p.netloc:
            return None
        return f"{p.scheme}://{p.netloc}"

    def _collect_favicons(
        self, page_urls: set[str]
    ) -> dict[str, tuple[str, bytes, int]]:
        """For each requested page URL, return best (icon_url, image_data, width)."""

        # Build lookup keys: exact, normalized (no fragment / trailing slash), origin.
        normalized_to_original: dict[str, str] = {}
        origin_to_original: dict[str, str] = {}
        for url in page_urls:
            normalized_to_original.setdefault(self._normalize(url), url)
            origin = self._origin(url)
            if origin:
                origin_to_original.setdefault(origin, url)

        # page_url -> (icon_url, image_bytes, width, score). score is the tie-breaker.
        best: dict[str, tuple[str, bytes, int, tuple]] = {}

        for db in self._favicon_dbs():
            logger.info(f"📖 Reading favicons from {db.parent.name}")
            try:
                snap = self._snapshot_db(db)
            except Exception as exc:
                logger.warning(f"Could not snapshot {db}: {exc}")
                continue

            conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT
                        im.page_url AS page_url,
                        f.url       AS icon_url,
                        fb.image_data AS image_data,
                        fb.width    AS width,
                        fb.height   AS height,
                        f.icon_type AS icon_type
                    FROM icon_mapping im
                    JOIN favicons f          ON f.id  = im.icon_id
                    JOIN favicon_bitmaps fb  ON fb.icon_id = f.id
                    WHERE fb.image_data IS NOT NULL AND length(fb.image_data) > 0
                    """
                )
                for row in cur:
                    arc_page = row["page_url"]
                    matched_url = self._match_url(
                        arc_page, page_urls, normalized_to_original, origin_to_original
                    )
                    if matched_url is None:
                        continue
                    score = self._score(row, exact=arc_page in page_urls)
                    existing = best.get(matched_url)
                    if existing is None or score > existing[3]:
                        best[matched_url] = (
                            row["icon_url"],
                            bytes(row["image_data"]),
                            int(row["width"] or 0),
                            score,
                        )
            finally:
                conn.close()

        return {url: (icon_url, data, width) for url, (icon_url, data, width, _) in best.items()}

    def _match_url(
        self,
        arc_page: str,
        page_urls: set[str],
        normalized_to_original: dict[str, str],
        origin_to_original: dict[str, str],
    ) -> str | None:
        if arc_page in page_urls:
            return arc_page
        norm = self._normalize(arc_page)
        if norm in normalized_to_original:
            return normalized_to_original[norm]
        origin = self._origin(arc_page)
        if origin and origin in origin_to_original:
            return origin_to_original[origin]
        return None

    @staticmethod
    def _score(row: sqlite3.Row, exact: bool) -> tuple:
        """Higher tuple wins. Prefer exact URL, then larger size, then favicon type."""
        size = max(int(row["width"] or 0), int(row["height"] or 0))
        type_pri = _ICON_TYPE_PRIORITY.get(row["icon_type"], 0)
        return (
            1 if exact else 0,
            size,
            type_pri,
            len(row["image_data"] or b""),
        )

    def inject_session_images(self, page_urls: Iterable[str]) -> dict:
        """Embed favicon data URIs in each tab's `image` field in zen-sessions.jsonlz4.

        Pinned tab favicons in modern Zen are rendered from the tab's inline `image`
        field (a `data:image/...;base64,...` URI), not from favicons.sqlite. This
        looks up cached favicons for each tab URL and writes them inline so icons
        appear immediately when Zen starts.
        """
        from zen_sessions_importer import read_mozlz4, write_mozlz4

        urls = {u for u in page_urls if u}
        result = {"requested": len(urls), "matched": 0, "updated": 0, "skipped": 0}
        sessions_file = self.zen_profile / "zen-sessions.jsonlz4"
        if not sessions_file.exists():
            logger.warning(f"zen-sessions.jsonlz4 not found at {sessions_file}. skipping inline injection")
            result["error"] = "sessions_missing"
            return result

        try:
            cached_favicons = self._collect_favicons(urls)
        finally:
            self._cleanup_temp()
        result["matched"] = len(cached_favicons)
        logger.info(f"🔍 Matched favicons for {len(cached_favicons)} of {len(urls)} URLs")

        if not cached_favicons:
            return result

        # Encode each match as a data URI once.
        url_to_data_uri: dict[str, str] = {}
        for page_url, (_icon_url, image_data, _width) in cached_favicons.items():
            if not image_data:
                continue
            mime = _detect_image_mime(image_data)
            b64 = base64.b64encode(image_data).decode("ascii")
            url_to_data_uri[page_url] = f"data:{mime};base64,{b64}"

        if self.dry_run:
            logger.info(f"🧪 DRY RUN: would inline {len(url_to_data_uri)} favicons into zen-sessions.jsonlz4")
            result["dry_run"] = True
            return result

        # Read, mutate, write (with a backup first).
        backup = sessions_file.with_name(f"{sessions_file.name}.backup.{int(time.time())}")
        shutil.copy2(sessions_file, backup)
        logger.info(f"💾 Backed up zen-sessions.jsonlz4 → {backup.name}")

        session = read_mozlz4(sessions_file)
        for tab in session.get("tabs", []):
            if tab.get("image"):
                continue  # already has an icon (likely native Zen pinned tab)
            url = self._extract_tab_url(tab)
            if not url:
                result["skipped"] += 1
                continue
            data_uri = url_to_data_uri.get(url)
            if not data_uri:
                # Fall back to normalized / origin lookup.
                normalized_to_uri = {self._normalize(u): du for u, du in url_to_data_uri.items()}
                origin_to_uri = {}
                for u, du in url_to_data_uri.items():
                    o = self._origin(u)
                    if o:
                        origin_to_uri.setdefault(o, du)
                data_uri = normalized_to_uri.get(self._normalize(url))
                if not data_uri:
                    origin = self._origin(url)
                    data_uri = origin_to_uri.get(origin) if origin else None
            if not data_uri:
                result["skipped"] += 1
                continue
            tab["image"] = data_uri
            result["updated"] += 1

        write_mozlz4(sessions_file, session)
        logger.info(f"✅ Injected favicons into {result['updated']} tabs (skipped {result['skipped']})")
        return result

    @staticmethod
    def _extract_tab_url(tab: dict) -> str | None:
        """Pull the active URL out of a session tab entry."""
        entries = tab.get("entries") or []
        if entries:
            idx = tab.get("index", len(entries)) - 1
            if 0 <= idx < len(entries):
                url = entries[idx].get("url")
                if url:
                    return url
            url = entries[-1].get("url")
            if url:
                return url
        return tab.get("userTypedValue") or None

    def import_favicons(self, page_urls: Iterable[str]) -> dict:
        urls = {u for u in page_urls if u}
        result = {"requested": len(urls), "matched": 0, "imported": 0, "skipped": 0}
        if not urls:
            return result
        if not self.zen_favicons_db.exists():
            logger.error(f"Zen favicons.sqlite not found at {self.zen_favicons_db}")
            result["error"] = "favicons_db_missing"
            return result

        try:
            cached_favicons = self._collect_favicons(urls)
        finally:
            self._cleanup_temp()

        result["matched"] = len(cached_favicons)
        logger.info(
            f"🔍 Matched favicons for {len(cached_favicons)} of {len(urls)} requested URLs"
        )

        if not cached_favicons:
            return result

        if self.dry_run:
            logger.info(f"🧪 DRY RUN: would import {len(cached_favicons)} favicons")
            result["dry_run"] = True
            return result

        backup = self.zen_favicons_db.with_name(
            f"{self.zen_favicons_db.name}.backup.{int(time.time())}"
        )
        shutil.copy2(self.zen_favicons_db, backup)
        logger.info(f"💾 Backed up favicons.sqlite → {backup.name}")

        now_ms = int(time.time() * 1000)
        expire_ms = now_ms + self.DEFAULT_EXPIRE_DAYS * 24 * 60 * 60 * 1000

        conn = sqlite3.connect(self.zen_favicons_db, timeout=10.0)
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            for page_url, (icon_url, image_data, width) in cached_favicons.items():
                if not image_data:
                    result["skipped"] += 1
                    continue
                icon_id = self._upsert_icon(cur, icon_url, image_data, width, expire_ms)
                page_id = self._upsert_page(cur, page_url)
                cur.execute(
                    """INSERT OR REPLACE INTO moz_icons_to_pages
                          (page_id, icon_id, expire_ms) VALUES (?, ?, ?)""",
                    (page_id, icon_id, expire_ms),
                )
                result["imported"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(
            f"✅ Imported {result['imported']} favicons "
            f"(skipped {result['skipped']})"
        )
        return result

    @staticmethod
    def _upsert_icon(
        cur: sqlite3.Cursor,
        icon_url: str,
        image_data: bytes,
        width: int,
        expire_ms: int,
    ) -> int:
        url_hash = hash_icon_url(icon_url)
        cur.execute(
            "SELECT id FROM moz_icons WHERE fixed_icon_url_hash=? AND icon_url=?",
            (url_hash, icon_url),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE moz_icons SET data=?, width=?, expire_ms=? WHERE id=?",
                (sqlite3.Binary(image_data), width or 16, expire_ms, row[0]),
            )
            return row[0]
        cur.execute(
            """INSERT INTO moz_icons
                  (icon_url, fixed_icon_url_hash, width, root, color,
                   expire_ms, flags, data)
                  VALUES (?, ?, ?, 0, NULL, ?, 0, ?)""",
            (icon_url, url_hash, width or 16, expire_ms, sqlite3.Binary(image_data)),
        )
        return cur.lastrowid

    @staticmethod
    def _upsert_page(cur: sqlite3.Cursor, page_url: str) -> int:
        url_hash = hash_page_url(page_url)
        cur.execute(
            "SELECT id FROM moz_pages_w_icons WHERE page_url_hash=? AND page_url=?",
            (url_hash, page_url),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO moz_pages_w_icons (page_url, page_url_hash) VALUES (?, ?)",
            (page_url, url_hash),
        )
        return cur.lastrowid


def _iter_pinned_urls(extracted: dict) -> Iterable[str]:
    """Walk the legacy export-dict shape.."""
    for space in extracted.get("spaces", []):
        for tab in space.get("pinned_tabs", []) or []:
            url = tab.get("url")
            if url:
                yield url
        for tab in space.get("essential_tabs", []) or []:
            url = tab.get("url")
            if url:
                yield url
        for tab in space.get("open_tabs", []) or []:
            url = tab.get("url")
            if url:
                yield url
        # Bookmarks ride their own channel on sources that separate them
        # (Chromium); seed their favicons too. Falls back gracefully to
        # nothing on sources that don't set it.
        for tab in space.get("bookmarks", []) or []:
            url = tab.get("url")
            if url:
                yield url
        for folder in space.get("folders", []) or []:
            for tab in folder.get("tabs", []) or []:
                url = tab.get("url")
                if url:
                    yield url


def main() -> int:
    """CLI: import favicons for all pinned URLs in arc_pinned_tabs_export.json."""
    import argparse
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Import favicons into Zen")
    parser.add_argument("--zen-profile", help="Zen profile name (partial match)")
    parser.add_argument("--export-file", default="arc_pinned_tabs_export.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    home = Path.home()
    profiles_root = home / "Library/Application Support/zen/Profiles"
    if not profiles_root.exists():
        logger.error(f"No Zen profile dir at {profiles_root}")
        return 1
    profiles = [p for p in profiles_root.iterdir() if p.is_dir()]
    if args.zen_profile:
        profiles = [p for p in profiles if args.zen_profile.lower() in p.name.lower()]
    if not profiles:
        logger.error("No matching Zen profile found")
        return 1
    zen_profile = profiles[0]
    logger.info(f"Using Zen profile: {zen_profile.name}")

    export_path = Path(args.export_file)
    if export_path.exists():
        with export_path.open() as fh:
            data = json.load(fh)
        urls = list(dict.fromkeys(_iter_pinned_urls(data)))
        logger.info(f"Found {len(urls)} unique URLs in {export_path.name}")
    else:
        logger.info(f"No {export_path.name} found: extracting URLs from Arc directly")
        from arc_pinned_tab_extractor import ArcPinnedTabExtractor
        extractor = ArcPinnedTabExtractor()
        spaces = extractor.extract_pinned_tabs()
        if not spaces:
            logger.error("No Arc data found. Is Arc installed?")
            return 1
        # Reuse the extractor's JSON exporter to get a stable dict shape.
        tmp = export_path.parent / f".browser2zen_favicon_tmp_{int(time.time())}.json"
        try:
            extractor.export_to_json(spaces, tmp)
            with tmp.open() as fh:
                export_data = json.load(fh)
        finally:
            if tmp.exists():
                tmp.unlink()
        urls = list(dict.fromkeys(_iter_pinned_urls(export_data)))
        logger.info(f"Extracted {len(urls)} unique URLs from Arc")

    importer = FaviconImporter(zen_profile, dry_run=args.dry_run)
    db_summary = importer.import_favicons(urls)
    logger.info(f"favicons.sqlite: {db_summary}")
    session_summary = importer.inject_session_images(urls)
    logger.info(f"zen-sessions.jsonlz4: {session_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
