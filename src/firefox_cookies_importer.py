#!/usr/bin/env python3
"""
Firefox → Zen Cookies Importer

Source ``moz_cookies`` and target ``moz_cookies`` share the same
schema, so this is a direct row-level merge with no decryption. Two
caveats:

1. **Master password**: if the user has a Firefox master password set,
   stored credentials in ``logins.json`` are encrypted via NSS, but
   *cookies stay unencrypted*. We surface a clean error code anyway in
   case the user is mixing browsers and confused.
2. **Container cookies**: Firefox's ``originAttributes`` already carry
   ``^userContextId=N`` for container tabs. We preserve them verbatim,
   so per-space containers Just Work without us having to duplicate
   rows the way the Chromium path does.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class FirefoxCookiesImporter:
    """Merge Firefox cookies.sqlite into Zen cookies.sqlite (schema-identical)."""

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
        # ``container_ids`` is unused but accepted so the orchestrator can
        # pass the same kwargs as the Chromium importer without branching.
        self._container_ids = container_ids or []
        self._injected_dbs = [Path(p) for p in (cookie_dbs or [])]
        self._tempdir: Path | None = None

    def _snapshot(self, src: Path) -> Path:
        if self._tempdir is None:
            self._tempdir = Path(tempfile.mkdtemp(prefix="browser2zen_ff_cookies_"))
        dest = self._tempdir / f"{src.parent.name}_{src.name}.db"
        shutil.copy2(src, dest)
        for suffix in ("-wal", "-shm", "-journal"):
            sib = src.with_name(src.name + suffix)
            if sib.exists():
                shutil.copy2(sib, dest.with_name(dest.name + suffix))
        return dest

    def _cleanup(self) -> None:
        if self._tempdir and self._tempdir.exists():
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None

    def import_cookies(self) -> dict:
        result = {"read": 0, "imported": 0, "merged": 0, "skipped": 0}
        if not self.zen_cookies.exists():
            logger.error(f"Zen cookies.sqlite not found at {self.zen_cookies}")
            result["error"] = "cookies_db_missing"
            return result
        if not self._injected_dbs:
            logger.info("No Firefox cookies.sqlite paths supplied; nothing to import")
            return result

        # Master-password detection: if any source profile's key4.db
        # marks ``password-check`` we surface a clean error. v1 doesn't
        # implement the NSS dance to crack it.
        for src_db in self._injected_dbs:
            mp = _master_password_set(src_db.parent)
            if mp:
                result["error"] = "firefox_master_password_set"
                return result

        rows: list[tuple] = []
        try:
            for src_db in self._injected_dbs:
                if not src_db.is_file():
                    continue
                logger.info(f"📖 Reading Firefox cookies from {src_db.parent.name}")
                snap = self._snapshot(src_db)
                conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    for row in conn.execute(
                        """SELECT originAttributes, name, value, host, path,
                                  expiry, lastAccessed, creationTime,
                                  isSecure, isHttpOnly, sameSite, schemeMap
                           FROM moz_cookies"""
                    ):
                        result["read"] += 1
                        rows.append((
                            row["originAttributes"] or "",
                            row["name"] or "",
                            row["value"] or "",
                            row["host"] or "",
                            row["path"] or "/",
                            int(row["expiry"] or 0),
                            int(row["lastAccessed"] or 0),
                            int(row["creationTime"] or 0),
                            int(row["isSecure"] or 0),
                            int(row["isHttpOnly"] or 0),
                            int(row["sameSite"] or 0),
                            int(row["schemeMap"] or 0),
                        ))
                finally:
                    conn.close()
        finally:
            self._cleanup()

        logger.info(f"🔍 Read {result['read']} cookies from Firefox")
        if not rows:
            return result

        if self.dry_run:
            result["dry_run"] = True
            result["imported"] = len(rows)
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
            for r in rows:
                (origin, name, value, host, path, expiry, last_access,
                 creation, is_secure, is_http_only, same_site, scheme_map) = r
                # Dedup by (originAttributes, name, host, path).
                cur.execute(
                    """SELECT id FROM moz_cookies
                       WHERE originAttributes = ? AND name = ? AND host = ? AND path = ?""",
                    (origin, name, host, path),
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        """UPDATE moz_cookies SET value = ?, expiry = ?,
                                  lastAccessed = ?, isSecure = ?, isHttpOnly = ?,
                                  sameSite = ?, schemeMap = ?
                           WHERE id = ?""",
                        (value, expiry, last_access, is_secure, is_http_only,
                         same_site, scheme_map, existing[0]),
                    )
                    result["merged"] += 1
                    continue
                cur.execute(
                    """INSERT INTO moz_cookies
                       (originAttributes, name, value, host, path, expiry,
                        lastAccessed, creationTime, isSecure, isHttpOnly,
                        sameSite, schemeMap, inBrowserElement, rawSameSite)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                    (origin, name, value, host, path, expiry,
                     last_access or now_us, creation or now_us,
                     is_secure, is_http_only, same_site, scheme_map, same_site),
                )
                result["imported"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(
            f"✅ Cookies: +{result['imported']} new, ~{result['merged']} merged, "
            f"{result['skipped']} skipped"
        )
        return result


def _master_password_set(profile_dir: Path) -> bool:
    """Heuristic master-password detection.

    Firefox stores credentials master-password state in ``key4.db``,
    inside the ``metaData`` table under the ``password`` row. The row's
    payload starts with magic bytes that indicate whether a password
    has been set. Unset profiles still have the row but with the magic
    ``f8 7f 7c 5f 95 1b 6c 6d`` "no password" sentinel.

    Returns False when in doubt: a false negative just means we attempt
    the import (cookies aren't encrypted by master password anyway, so
    nothing breaks; we only surface this for clarity).
    """
    key_db = profile_dir / "key4.db"
    if not key_db.is_file():
        return False
    try:
        conn = sqlite3.connect(f"file:{key_db}?mode=ro", uri=True, timeout=2.0)
        try:
            row = conn.execute(
                "SELECT item1, item2 FROM metaData WHERE id = 'password'"
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return False
    if not row:
        return False
    item1 = row[0] or b""
    # The "no password" sentinel: 24 bytes starting with these magic bytes.
    no_password_prefix = bytes.fromhex("f87f7c5f951b6c6d")
    return not (isinstance(item1, (bytes, bytearray)) and bytes(item1).startswith(no_password_prefix))
