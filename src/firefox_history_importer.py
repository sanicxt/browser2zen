#!/usr/bin/env python3
"""
Firefox → Zen History Importer

Source and target share the same schema (both are Firefox descendants),
so this is a direct ``moz_places`` + ``moz_historyvisits`` merge with no
time conversion or transition-code mapping.

The orchestrator dispatches to this importer when ``source.name ==
"firefox"`` and falls back to the Chromium one for everyone else.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional

# Reuse the URL hash so moz_places.url_hash matches what Zen would
# compute itself when it re-validates the row.
from zen_favicon_importer import hash_page_url

logger = logging.getLogger(__name__)


class FirefoxHistoryImporter:
    """Merge Firefox places.sqlite into Zen places.sqlite (schema-identical)."""

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
        """Copy a source places.sqlite (+ wal/shm) into a temp dir so we
        can read it without fighting Firefox's own lock."""
        if self._tempdir is None:
            self._tempdir = Path(tempfile.mkdtemp(prefix="browser2zen_ff_history_"))
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
        """Merge every source ``places.sqlite`` into the Zen target.

        ``since_days`` is honoured if Firefox stores ``last_visit_date``
        in the source row. Visits older than the cutoff are skipped.
        """
        result = {"places_added": 0, "places_updated": 0, "visits_added": 0, "skipped": 0}
        if not self.places_db.exists():
            logger.error(f"Zen places.sqlite not found at {self.places_db}")
            result["error"] = "places_missing"
            return result
        if not self._injected_dbs:
            logger.info("No Firefox places.sqlite paths supplied; nothing to import")
            return result

        cutoff_unix_us = 0
        if since_days:
            cutoff_unix_us = int((time.time() - since_days * 86400) * 1_000_000)

        # Aggregate all source places + visits into memory keyed by URL,
        # mirroring the Chromium importer's shape so the upsert helpers
        # can stay symmetric.
        urls_to_data: dict[str, dict] = {}
        try:
            for src_db in self._injected_dbs:
                if not src_db.is_file():
                    continue
                logger.info(f"📖 Reading Firefox history from {src_db.parent.name}")
                snap = self._snapshot(src_db)
                conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        """
                        SELECT p.url        AS url,
                               p.title      AS title,
                               p.visit_count AS visit_count,
                               p.last_visit_date AS last_visit_date,
                               v.visit_date AS visit_date,
                               v.visit_type AS visit_type
                        FROM moz_places p
                        LEFT JOIN moz_historyvisits v ON v.place_id = p.id
                        WHERE p.url LIKE 'http%' OR p.url LIKE 'ftp%'
                        """
                    )
                    for row in rows:
                        url = row["url"]
                        if not url:
                            continue
                        last_visit = int(row["last_visit_date"] or 0)
                        if cutoff_unix_us and last_visit and last_visit <= cutoff_unix_us:
                            continue
                        entry = urls_to_data.setdefault(
                            url,
                            {
                                "title": row["title"] or "",
                                "visit_count": 0,
                                "last_visit": 0,
                                "visits": [],
                            },
                        )
                        # ``visit_count`` on moz_places is the canonical
                        # source-side count. Don't double-add per-visit
                        # rows, just take the larger of the two.
                        entry["visit_count"] = max(entry["visit_count"], int(row["visit_count"] or 0))
                        entry["last_visit"] = max(entry["last_visit"], last_visit)
                        if row["title"] and len(row["title"]) > len(entry["title"]):
                            entry["title"] = row["title"]
                        if row["visit_date"] is not None:
                            entry["visits"].append(
                                (int(row["visit_date"]), int(row["visit_type"] or 1))
                            )
                finally:
                    conn.close()
        finally:
            self._cleanup()

        logger.info(
            f"🔍 Aggregated {len(urls_to_data)} URLs from Firefox history "
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
    """Insert or update a moz_places row. Returns (place_id, was_new).

    Mirrors :func:`chromium_history_importer.HistoryImporter._upsert_place`.
    """
    url_hash = hash_page_url(url)
    cur.execute(
        "SELECT id, visit_count, last_visit_date, title FROM moz_places "
        "WHERE url_hash = ? AND url = ?",
        (url_hash, url),
    )
    row = cur.fetchone()
    if row:
        place_id, existing_visits, existing_last, existing_title = row
        new_visit_count = max(int(existing_visits or 0), int(data["visit_count"] or 0))
        new_last = max(int(existing_last or 0), int(data["last_visit"] or 0))
        new_title = existing_title if existing_title else data["title"] or None
        cur.execute(
            "UPDATE moz_places SET visit_count = ?, last_visit_date = ?, title = ? WHERE id = ?",
            (new_visit_count, new_last, new_title, place_id),
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
            _frecency(int(data["visit_count"] or 0)),
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
    """Firefox stores hosts reversed with trailing dot (``moc.elgoog.www.``)."""
    from urllib.parse import urlparse
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host[::-1] + "." if host else ""


def _frecency(visit_count: int) -> int:
    return min(10000, 100 + visit_count * 50)


def _make_guid() -> str:
    import base64
    import os
    return base64.urlsafe_b64encode(os.urandom(9)).decode("ascii")
