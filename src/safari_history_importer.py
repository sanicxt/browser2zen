#!/usr/bin/env python3
"""
Safari → Zen History Importer

Safari stores history in ``~/Library/Safari/History.db`` (SQLite) with
its own schema:

- ``history_items``: id, url, visit_count, ...
- ``history_visits``: id, history_item, visit_time, title

``visit_time`` is a Cocoa-epoch float64 (seconds since 2001-01-01 UTC).
We convert to Unix microseconds and merge into Zen's ``moz_places`` +
``moz_historyvisits`` using the same upsert pattern as the Chromium
importer.

Reading ``History.db`` requires Full Disk Access on Sequoia. We surface
``safari_needs_full_disk_access`` on PermissionError.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional

from zen_favicon_importer import hash_page_url

logger = logging.getLogger(__name__)


# Cocoa epoch starts 2001-01-01 UTC; Unix epoch is 1970-01-01 UTC.
_COCOA_TO_UNIX_S = 978_307_200


def cocoa_seconds_to_unix_us(cocoa_seconds: float) -> int:
    """Convert a Cocoa-epoch float to Unix microseconds.

    >>> cocoa_seconds_to_unix_us(0.0)  # 2001-01-01 UTC in unix us
    978307200000000
    """
    return int((cocoa_seconds + _COCOA_TO_UNIX_S) * 1_000_000)


class SafariHistoryImporter:
    """Merge Safari History.db into Zen places.sqlite."""

    def __init__(
        self,
        zen_profile: Path,
        history_dbs: list[Path] | None = None,
        dry_run: bool = False,
    ):
        self.zen_profile = Path(zen_profile)
        self.places_db = self.zen_profile / "places.sqlite"
        self.dry_run = dry_run
        self._injected_dbs = [Path(p) for p in (history_dbs or [])]
        self._tempdir: Path | None = None

    def _snapshot(self, src: Path) -> Path:
        if self._tempdir is None:
            self._tempdir = Path(tempfile.mkdtemp(prefix="browser2zen_safari_history_"))
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

    def import_history(self, since_days: int | None = None) -> dict:
        result = {"places_added": 0, "places_updated": 0, "visits_added": 0, "skipped": 0}
        if not self.places_db.exists():
            result["error"] = "places_missing"
            return result
        if not self._injected_dbs:
            return result

        cutoff_unix_us = 0
        if since_days:
            cutoff_unix_us = int((time.time() - since_days * 86400) * 1_000_000)

        urls_to_data: dict[str, dict] = {}
        try:
            for src_db in self._injected_dbs:
                if not src_db.is_file():
                    continue
                logger.info(f"📖 Reading Safari history from {src_db.parent.name}")
                try:
                    snap = self._snapshot(src_db)
                except PermissionError:
                    result["error"] = "safari_needs_full_disk_access"
                    return result
                conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        """
                        SELECT i.url        AS url,
                               i.visit_count AS visit_count,
                               v.visit_time AS visit_time,
                               v.title      AS title
                        FROM history_items i
                        LEFT JOIN history_visits v ON v.history_item = i.id
                        WHERE i.url LIKE 'http%' OR i.url LIKE 'ftp%'
                        """
                    )
                    for row in rows:
                        url = row["url"]
                        if not url:
                            continue
                        visit_unix_us = (
                            cocoa_seconds_to_unix_us(float(row["visit_time"] or 0.0))
                            if row["visit_time"] is not None
                            else 0
                        )
                        if cutoff_unix_us and visit_unix_us and visit_unix_us <= cutoff_unix_us:
                            continue
                        entry = urls_to_data.setdefault(
                            url,
                            {"title": "", "visit_count": 0, "last_visit": 0, "visits": []},
                        )
                        entry["visit_count"] = max(
                            entry["visit_count"], int(row["visit_count"] or 0)
                        )
                        entry["last_visit"] = max(entry["last_visit"], visit_unix_us)
                        title = row["title"] or ""
                        if title and len(title) > len(entry["title"]):
                            entry["title"] = title
                        if visit_unix_us:
                            # Safari's ``history_visits`` doesn't carry a
                            # transition code; default to "link" (1).
                            entry["visits"].append((visit_unix_us, 1))
                finally:
                    conn.close()
        finally:
            self._cleanup()

        logger.info(
            f"🔍 Aggregated {len(urls_to_data)} URLs from Safari history "
            f"({sum(len(d['visits']) for d in urls_to_data.values())} visits)"
        )
        if not urls_to_data:
            return result

        if self.dry_run:
            result["dry_run"] = True
            result["places_added"] = len(urls_to_data)
            result["visits_added"] = sum(len(d["visits"]) for d in urls_to_data.values())
            return result

        backup = self.places_db.with_name(f"{self.places_db.name}.backup.{int(time.time())}")
        shutil.copy2(self.places_db, backup)
        logger.info(f"💾 Backed up places.sqlite → {backup.name}")

        conn = sqlite3.connect(self.places_db, timeout=30.0)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("BEGIN")
            for url, data in urls_to_data.items():
                place_id, was_new = _upsert_place(cur, url, data)
                if was_new:
                    result["places_added"] += 1
                else:
                    result["places_updated"] += 1
                result["visits_added"] += _insert_visits(cur, place_id, data["visits"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(
            f"✅ History: +{result['places_added']} new places, "
            f"~{result['places_updated']} merged, +{result['visits_added']} visits"
        )
        return result


def _upsert_place(cur: sqlite3.Cursor, url: str, data: dict) -> tuple[int, bool]:
    url_hash = hash_page_url(url)
    cur.execute(
        "SELECT id, visit_count, last_visit_date, title FROM moz_places "
        "WHERE url_hash = ? AND url = ?",
        (url_hash, url),
    )
    row = cur.fetchone()
    if row:
        place_id, existing_visits, existing_last, existing_title = row
        cur.execute(
            "UPDATE moz_places SET visit_count = ?, last_visit_date = ?, title = ? WHERE id = ?",
            (
                max(int(existing_visits or 0), int(data["visit_count"] or 0)),
                max(int(existing_last or 0), int(data["last_visit"] or 0)),
                existing_title if existing_title else (data["title"] or None),
                place_id,
            ),
        )
        return place_id, False

    cur.execute(
        """INSERT INTO moz_places
           (url, title, rev_host, visit_count, hidden, typed, frecency,
            last_visit_date, guid, foreign_count, url_hash)
           VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, 0, ?)""",
        (
            url,
            data["title"] or None,
            _reverse_host(url),
            int(data["visit_count"] or 0),
            min(10000, 100 + int(data["visit_count"] or 0) * 50),
            int(data["last_visit"] or 0),
            _make_guid(),
            url_hash,
        ),
    )
    return cur.lastrowid, True


def _insert_visits(cur: sqlite3.Cursor, place_id: int, visits: list[tuple[int, int]]) -> int:
    existing = {
        row[0]
        for row in cur.execute(
            "SELECT visit_date FROM moz_historyvisits WHERE place_id = ?",
            (place_id,),
        )
    }
    added = 0
    for visit_us, visit_type in visits:
        if visit_us in existing:
            continue
        cur.execute(
            """INSERT INTO moz_historyvisits (from_visit, place_id, visit_date, visit_type, session)
               VALUES (0, ?, ?, ?, 0)""",
            (place_id, visit_us, visit_type),
        )
        added += 1
    return added


def _reverse_host(url: str) -> str:
    from urllib.parse import urlparse
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host[::-1] + "." if host else ""


def _make_guid() -> str:
    import base64
    import os
    return base64.urlsafe_b64encode(os.urandom(9)).decode("ascii")
