#!/usr/bin/env python3
"""
Chromium → Zen History Importer

Copies any Chromium-format ``History`` SQLite (Arc / Chrome / Edge /
Brave) into Zen's Firefox-format ``places.sqlite``. Handles
WebKit→Unix time conversion and Chromium→Firefox transition mapping.
Idempotent: re-running merges new visits without duplicating existing
places/visits.

The orchestrator hands in a list of source ``History`` paths via
``history_dbs=`` so the same code serves every Chromium browser.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# Reuse Firefox URL hash from the favicon importer so moz_places.url_hash matches.
from zen_favicon_importer import hash_page_url

# Chrome PageTransition enum (chrome/common/page_transition_types.h) → Firefox visit_type.
# Firefox values: 1=link, 2=typed, 3=bookmark, 4=embed, 5=redirect_perm,
#                 6=redirect_temp, 7=download, 8=framed_link, 9=reload.
_CHROMIUM_CORE_TRANSITION = {
    0: 1,   # LINK -> link
    1: 2,   # TYPED -> typed
    2: 3,   # AUTO_BOOKMARK -> bookmark
    3: 8,   # AUTO_SUBFRAME -> framed_link
    4: 4,   # MANUAL_SUBFRAME -> embed
    5: 1,   # GENERATED -> link
    6: 1,   # AUTO_TOPLEVEL -> link
    7: 1,   # FORM_SUBMIT -> link
    8: 9,   # RELOAD -> reload
    9: 1,   # KEYWORD -> link
    10: 1,  # KEYWORD_GENERATED -> link
}
_CHROMIUM_REDIRECT_PERMANENT = 0x08000000
_CHROMIUM_REDIRECT_TEMPORARY = 0x04000000

# Webkit/Chrome epoch is 1601-01-01; Unix epoch is 1970-01-01.
_WEBKIT_EPOCH_OFFSET_US = 11_644_473_600_000_000


def _chrome_to_unix_us(chrome_us: int | None) -> int:
    if not chrome_us:
        return 0
    val = chrome_us - _WEBKIT_EPOCH_OFFSET_US
    return val if val > 0 else 0


def _map_transition(chrome_transition: int) -> int:
    if chrome_transition & _CHROMIUM_REDIRECT_PERMANENT:
        return 5
    if chrome_transition & _CHROMIUM_REDIRECT_TEMPORARY:
        return 6
    core = chrome_transition & 0xFF
    return _CHROMIUM_CORE_TRANSITION.get(core, 1)


def _reverse_host(url: str) -> str:
    """Firefox stores hosts reversed with trailing dot (`moc.elgoog.www.`)."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host[::-1] + "." if host else ""


class HistoryImporter:
    def __init__(
        self,
        zen_profile: Path,
        dry_run: bool = False,
        history_dbs: list[Path] | None = None,
    ):
        # ``history_dbs`` lets the multi-source orchestrator inject paths
        # from any Chromium-format browser (Chrome/Edge/Brave/Arc). When
        # left as None we fall back to the original Arc-only lookup so the
        # standalone CLI keeps working unchanged.
        self.zen_profile = Path(zen_profile)
        self.places_db = self.zen_profile / "places.sqlite"
        self.dry_run = dry_run
        self._injected_dbs: list[Path] | None = (
            [Path(p) for p in history_dbs] if history_dbs is not None else None
        )
        self._tempdir: Path | None = None

    def _history_dbs(self) -> list[Path]:
        if self._injected_dbs is not None:
            return [p for p in self._injected_dbs if p.is_file()]
        # Standalone-CLI fallback: glob Arc's known data dirs. The
        # orchestrator always passes ``history_dbs=`` so this path is
        # only used when the legacy ``migrate.py`` runs without a
        # configured source.
        roots: list[Path] = []
        home = Path.home()
        macos = home / "Library/Application Support/Arc/User Data"
        windows = (
            home
            / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4"
            / "LocalCache/Local/Arc/User Data"
        )
        for root in (macos, windows):
            if root.exists():
                roots.extend(p for p in root.glob("*/History") if p.is_file())
        return sorted(set(roots))

    def _snapshot(self, src: Path) -> Path:
        if self._tempdir is None:
            self._tempdir = Path(tempfile.mkdtemp(prefix="browser2zen_history_"))
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
        """Import Chromium-format history into places.sqlite.

        since_days: only import visits newer than this many days. None = all.
        """
        result = {"places_added": 0, "places_updated": 0, "visits_added": 0, "skipped": 0}
        if not self.places_db.exists():
            logger.error(f"Zen places.sqlite not found at {self.places_db}")
            result["error"] = "places_missing"
            return result

        cutoff_unix_us = 0
        if since_days:
            cutoff_unix_us = int((time.time() - since_days * 86400) * 1_000_000)

        # Aggregate source-browser data across profiles in memory.
        # Map url -> (title, total_visits, last_visit_unix_us, [(visit_unix_us, transition)])
        urls_to_data: dict[str, dict] = {}
        try:
            for db in self._history_dbs():
                logger.info(f"📖 Reading history from {db.parent.name}")
                snap = self._snapshot(db)
                conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        SELECT u.url AS url,
                               u.title AS title,
                               v.visit_time AS visit_time,
                               v.transition AS transition
                        FROM visits v JOIN urls u ON u.id = v.url
                        WHERE u.url LIKE 'http%' OR u.url LIKE 'ftp%'
                        """
                    )
                    for row in cur:
                        unix_us = _chrome_to_unix_us(row["visit_time"])
                        if unix_us <= cutoff_unix_us:
                            continue
                        url = row["url"]
                        if not url:
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
                        entry["visit_count"] += 1
                        entry["last_visit"] = max(entry["last_visit"], unix_us)
                        if row["title"] and len(row["title"]) > len(entry["title"]):
                            entry["title"] = row["title"]
                        entry["visits"].append((unix_us, _map_transition(row["transition"])))
                finally:
                    conn.close()
        finally:
            self._cleanup()

        logger.info(
            f"🔍 Aggregated {len(urls_to_data)} URLs from history "
            f"({sum(d['visit_count'] for d in urls_to_data.values())} visits)"
        )
        if not urls_to_data:
            return result

        if self.dry_run:
            result["dry_run"] = True
            result["places_added"] = len(urls_to_data)
            result["visits_added"] = sum(d["visit_count"] for d in urls_to_data.values())
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
                place_id, was_new = self._upsert_place(cur, url, data)
                if was_new:
                    result["places_added"] += 1
                else:
                    result["places_updated"] += 1
                visits_added = self._insert_visits(cur, place_id, data["visits"])
                result["visits_added"] += visits_added
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

    @staticmethod
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
            new_visit_count = (existing_visits or 0) + data["visit_count"]
            new_last = max(existing_last or 0, data["last_visit"])
            new_title = existing_title if existing_title else data["title"] or None
            cur.execute(
                "UPDATE moz_places SET visit_count = ?, last_visit_date = ?, title = ? "
                "WHERE id = ?",
                (new_visit_count, new_last, new_title, place_id),
            )
            return place_id, False

        rev_host = _reverse_host(url)
        guid = _make_guid()
        cur.execute(
            """INSERT INTO moz_places
               (url, title, rev_host, visit_count, hidden, typed, frecency,
                last_visit_date, guid, foreign_count, url_hash)
               VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, 0, ?)""",
            (
                url,
                data["title"] or None,
                rev_host,
                data["visit_count"],
                _frecency(data["visit_count"]),
                data["last_visit"],
                guid,
                url_hash,
            ),
        )
        return cur.lastrowid, True

    @staticmethod
    def _insert_visits(cur: sqlite3.Cursor, place_id: int, visits: list[tuple[int, int]]) -> int:
        # Skip visits already present (by exact place_id+visit_date).
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
                """INSERT INTO moz_historyvisits
                   (from_visit, place_id, visit_date, visit_type, session)
                   VALUES (0, ?, ?, ?, 0)""",
                (place_id, visit_us, visit_type),
            )
            added += 1
        return added


def _frecency(visit_count: int) -> int:
    """Cheap frecency estimate. Firefox recomputes on its own; this is a starting score."""
    return min(10000, 100 + visit_count * 50)


def _make_guid() -> str:
    """12-char base64url GUID, matching moz_places.guid format."""
    import base64
    import os
    return base64.urlsafe_b64encode(os.urandom(9)).decode("ascii")


def main() -> int:
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(
        description="Import Chromium-format browsing history into Zen"
    )
    parser.add_argument("--zen-profile", help="Zen profile name (partial match)")
    parser.add_argument("--since-days", type=int, help="Only import visits newer than N days")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    profiles_root = Path.home() / "Library/Application Support/zen/Profiles"
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

    importer = HistoryImporter(zen_profile, dry_run=args.dry_run)
    summary = importer.import_history(since_days=args.since_days)
    logger.info(f"Summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
