#!/usr/bin/env python3
"""
Safari → Zen Cookies Importer

Safari stores cookies in
``~/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies``,
Apple's custom binary format. We parse it locally and merge into Zen's
``moz_cookies``.

The format is well-documented; this is a faithful implementation of:

    "cook" magic
    big-endian uint32: number of pages
    N × big-endian uint32: page sizes
    each page (concatenated):
        page header 0x00000100 (LE)
        LE uint32: cookies-in-page
        N × LE uint32: cookie-offsets within page
        per cookie at offset:
            LE uint32: cookie size
            4 bytes pad
            LE uint32: flags (1=secure, 4=httpOnly, 5=both)
            4 bytes pad
            LE uint32: URL offset
            LE uint32: name offset
            LE uint32: path offset
            LE uint32: value offset
            8 bytes "endofcookie" pad
            LE float64: expiry  (Cocoa epoch)
            LE float64: creation (Cocoa epoch)
            null-terminated UTF-8 strings at the offsets

Reading the file requires Full Disk Access on Sequoia. We surface
``safari_needs_full_disk_access`` on PermissionError.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import struct
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Cocoa epoch starts 2001-01-01 UTC.
_COCOA_TO_UNIX_S = 978_307_200


def _cocoa_to_unix_seconds(cocoa: float) -> int:
    return int(cocoa + _COCOA_TO_UNIX_S) if cocoa else 0


def parse_binarycookies(blob: bytes) -> list[dict]:
    """Parse a ``Cookies.binarycookies`` file into a list of cookie dicts.

    Each dict has: ``name``, ``value``, ``url`` (host), ``path``,
    ``expiry`` (Unix seconds), ``creation`` (Unix seconds),
    ``is_secure`` (0/1), ``is_http_only`` (0/1).

    Raises ``ValueError`` if the magic byte or page header doesn't match.
    """
    if len(blob) < 8 or blob[:4] != b"cook":
        raise ValueError("Not a Cookies.binarycookies file (bad magic)")
    num_pages = struct.unpack(">I", blob[4:8])[0]
    page_sizes = list(struct.unpack(f">{num_pages}I", blob[8 : 8 + 4 * num_pages]))

    cookies: list[dict] = []
    cursor = 8 + 4 * num_pages
    for size in page_sizes:
        page = blob[cursor : cursor + size]
        cursor += size
        cookies.extend(_parse_page(page))
    return cookies


def _parse_page(page: bytes) -> list[dict]:
    if len(page) < 8 or page[:4] != b"\x00\x00\x01\x00":
        raise ValueError("Bad page header")
    num_cookies = struct.unpack("<I", page[4:8])[0]
    cookie_offsets = list(
        struct.unpack(f"<{num_cookies}I", page[8 : 8 + 4 * num_cookies])
    )
    out: list[dict] = []
    for off in cookie_offsets:
        out.append(_parse_cookie(page, off))
    return out


def _parse_cookie(page: bytes, offset: int) -> dict:
    # Cookie record layout, all little-endian.
    # 0:4   size
    # 4:8   pad
    # 8:12  flags
    # 12:16 pad
    # 16:20 url offset
    # 20:24 name offset
    # 24:28 path offset
    # 28:32 value offset
    # 32:40 end-of-cookie marker (8 bytes)
    # 40:48 expiry (float64)
    # 48:56 creation (float64)
    head = page[offset : offset + 56]
    if len(head) < 56:
        raise ValueError("Truncated cookie record")
    flags = struct.unpack("<I", head[8:12])[0]
    url_off = struct.unpack("<I", head[16:20])[0]
    name_off = struct.unpack("<I", head[20:24])[0]
    path_off = struct.unpack("<I", head[24:28])[0]
    value_off = struct.unpack("<I", head[28:32])[0]
    expiry = struct.unpack("<d", head[40:48])[0]
    creation = struct.unpack("<d", head[48:56])[0]

    return {
        "url": _read_cstring(page, offset + url_off),
        "name": _read_cstring(page, offset + name_off),
        "path": _read_cstring(page, offset + path_off),
        "value": _read_cstring(page, offset + value_off),
        "expiry": _cocoa_to_unix_seconds(expiry),
        "creation": _cocoa_to_unix_seconds(creation),
        "is_secure": 1 if flags & 0x1 else 0,
        "is_http_only": 1 if flags & 0x4 else 0,
    }


def _read_cstring(buf: bytes, start: int) -> str:
    end = buf.find(b"\x00", start)
    if end < 0:
        end = len(buf)
    return buf[start:end].decode("utf-8", errors="replace")


# --------------------------------------------------------------------- importer


class SafariCookiesImporter:
    """Read Safari Cookies.binarycookies and merge into Zen cookies.sqlite."""

    def __init__(
        self,
        zen_profile: Path,
        cookie_dbs: list[Path] | None = None,
        container_ids: list[int] | None = None,
        dry_run: bool = False,
    ):
        self.zen_profile = Path(zen_profile)
        self.zen_cookies = self.zen_profile / "cookies.sqlite"
        self.dry_run = dry_run
        # Per-container duplication is unused for Safari (no containers).
        self._container_ids = container_ids or []
        self._sources = [Path(p) for p in (cookie_dbs or [])]

    def import_cookies(self) -> dict:
        result = {"read": 0, "imported": 0, "merged": 0, "skipped": 0}
        if not self.zen_cookies.exists():
            result["error"] = "cookies_db_missing"
            return result
        if not self._sources:
            return result

        cookies: list[dict] = []
        for src in self._sources:
            if not src.is_file():
                continue
            try:
                blob = src.read_bytes()
            except PermissionError:
                result["error"] = "safari_needs_full_disk_access"
                return result
            try:
                page_cookies = parse_binarycookies(blob)
            except ValueError as exc:
                logger.warning(f"Could not parse {src}: {exc}")
                continue
            logger.info(f"📖 Read {len(page_cookies)} cookies from {src.name}")
            cookies.extend(page_cookies)
        result["read"] = len(cookies)
        if not cookies:
            return result

        if self.dry_run:
            result["dry_run"] = True
            result["imported"] = len(cookies)
            return result

        backup = self.zen_cookies.with_name(f"{self.zen_cookies.name}.backup.{int(time.time())}")
        shutil.copy2(self.zen_cookies, backup)
        logger.info(f"💾 Backed up cookies.sqlite → {backup.name}")

        conn = sqlite3.connect(self.zen_cookies, timeout=30.0)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("BEGIN")
            now_us = int(time.time() * 1000)
            for c in cookies:
                host = c["url"]  # Safari stores host in the URL slot
                # Dedup by (originAttributes, name, host, path).
                cur.execute(
                    """SELECT id FROM moz_cookies
                       WHERE originAttributes = '' AND name = ? AND host = ? AND path = ?""",
                    (c["name"], host, c["path"]),
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        """UPDATE moz_cookies SET value = ?, expiry = ?, lastAccessed = ?,
                                  isSecure = ?, isHttpOnly = ? WHERE id = ?""",
                        (c["value"], c["expiry"] * 1000, now_us,
                         c["is_secure"], c["is_http_only"], existing[0]),
                    )
                    result["merged"] += 1
                    continue
                cur.execute(
                    """INSERT INTO moz_cookies
                       (originAttributes, name, value, host, path, expiry,
                        lastAccessed, creationTime, isSecure, isHttpOnly,
                        sameSite, schemeMap, inBrowserElement, rawSameSite)
                       VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0)""",
                    (
                        c["name"], c["value"], host, c["path"],
                        # Firefox cookie expiry is milliseconds.
                        c["expiry"] * 1000,
                        now_us,
                        (c["creation"] * 1_000_000) or now_us * 1000,
                        c["is_secure"], c["is_http_only"],
                    ),
                )
                result["imported"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(
            f"✅ Cookies: +{result['imported']} new, ~{result['merged']} merged"
        )
        return result
