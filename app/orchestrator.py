"""
MigrationOrchestrator: the GUI's facade over the existing importer modules.

We import the existing classes from ``src/`` unchanged. The only thing the
GUI adds on top is structured progress events, an isolation-safe install of
the ProgressBus around each step, and a couple of cross-cutting concerns
(detecting previous migrations, computing per-space counts for Preview).

The CLI tool (``migrate_arc_to_zen.py``) keeps working: it does not import
this module. There are intentionally no changes to ``src/`` here.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple

# Make ``src/`` importable when running from the repo root or as a packaged app.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Existing importer modules (unchanged).
from arc_pinned_tab_extractor import ArcPinnedTabExtractor               # noqa: E402
from zen_space_importer import ZenSpaceImporter, ZenProfile as _SrcZenProfile  # noqa: E402
from zen_sessions_importer import ZenSessionsImporter                    # noqa: E402
from zen_bookmark_importer import ZenBookmarkImporter                    # noqa: E402
from zen_favicon_importer import FaviconImporter, _iter_pinned_urls      # noqa: E402
from arc_history_importer import HistoryImporter                          # noqa: E402
from arc_cookies_importer import CookiesImporter, _discover_user_containers  # noqa: E402

from .env_check import EnvReport, ZenProfile, check_environment
from .progress_bus import ProgressBus, ProgressEvent

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ models

@dataclass(frozen=True)
class SpaceSummary:
    name: str
    icon: Optional[str]
    pinned_count: int
    open_count: int
    folder_count: int
    essential_count: int = 0
    # Arc midTone colour as integer RGB (0-255) so the frontend can tint the
    # space card background to match the Arc workspace. ``None`` when the
    # space has no theme set.
    color: Optional[Tuple[int, int, int]] = None


@dataclass(frozen=True)
class PreviewReport:
    spaces: list[SpaceSummary]
    pinned_total: int
    open_total: int
    folder_total: int
    bookmark_total: int            # pinned + folder tabs (what gets bookmarked)
    favicon_match_estimate: int    # how many URLs Arc has cached icons for
    history_rows_estimate: int
    cookies_estimate: int


@dataclass
class MigrationOptions:
    zen_profile_path: Path
    arc_space_filter: Optional[str] = None
    folders_collapsed: bool = True
    include_workspaces: bool = True
    include_pinned_tabs: bool = True
    include_bookmarks: bool = True
    include_favicons: bool = True
    include_open_tabs: bool = True
    include_history: bool = False
    include_cookies: bool = False


# ----------- step ordering used in the GUI's progress list (left to right) ---

GUI_STEPS = (
    "extract",
    "containers",
    "sessions",
    "bookmarks",
    "favicons",
    "history",
    "cookies",
    "finalize",
)

STEP_LABELS = {
    "extract":    "Reading Arc data",
    "containers": "Creating containers",
    "sessions":   "Importing spaces, pinned tabs, open tabs and folders",
    "bookmarks":  "Backing up as bookmarks",
    "favicons":   "Importing favicons",
    "history":    "Importing browsing history",
    "cookies":    "Importing cookies",
    "finalize":   "Finalizing",
}


# ------------------------------------------------------------------ orchestrator

class MigrationOrchestrator:
    def __init__(self) -> None:
        self.bus = ProgressBus()

    # ---- env / preview --------------------------------------------------

    def check_environment(self) -> EnvReport:
        return check_environment()

    def preview(self, opts: MigrationOptions) -> PreviewReport:
        # Read-only: no bus, no temp files left behind.
        extractor = ArcPinnedTabExtractor()
        spaces = extractor.extract_pinned_tabs() or []

        if opts.arc_space_filter:
            needle = opts.arc_space_filter.lower()
            spaces = [s for s in spaces if needle in s.space_name.lower()]

        # ``ArcSpace.pinned_tabs`` is the flat list (folder membership is
        # tracked via ``ArcFolder.children_ids``, not nested children).
        space_summaries: list[SpaceSummary] = []
        pinned_total = open_total = folder_total = bookmark_total = 0
        all_urls: list[str] = []

        for s in spaces:
            pinned_count = len(s.pinned_tabs or [])
            open_count = len(s.open_tabs or [])
            essential = sum(1 for t in (s.pinned_tabs or []) if getattr(t, "is_essential", False))
            folder_count = len(s.folders or [])
            color_rgb: Optional[Tuple[int, int, int]] = None
            if s.color and all(k in s.color for k in ("r", "g", "b")):
                color_rgb = (
                    int(round(s.color["r"] * 255)),
                    int(round(s.color["g"] * 255)),
                    int(round(s.color["b"] * 255)),
                )
            space_summaries.append(SpaceSummary(
                name=s.space_name,
                icon=s.icon,
                pinned_count=pinned_count,
                open_count=open_count,
                folder_count=folder_count,
                essential_count=essential,
                color=color_rgb,
            ))
            pinned_total += pinned_count
            open_total += open_count
            folder_total += folder_count
            bookmark_total += pinned_count
            for t in (s.pinned_tabs or []):
                if t.url:
                    all_urls.append(t.url)
            for t in (s.open_tabs or []):
                if t.url:
                    all_urls.append(t.url)

        # Cheap estimates for the heavy steps. Real counts emerge during the
        # run; the Preview screen just uses these for UX.
        favicon_match_estimate = self._estimate_favicons(all_urls)
        history_rows_estimate = self._estimate_history_rows()
        cookies_estimate = self._estimate_cookies()

        return PreviewReport(
            spaces=space_summaries,
            pinned_total=pinned_total,
            open_total=open_total,
            folder_total=folder_total,
            bookmark_total=bookmark_total,
            favicon_match_estimate=favicon_match_estimate,
            history_rows_estimate=history_rows_estimate,
            cookies_estimate=cookies_estimate,
        )

    @staticmethod
    def _estimate_favicons(urls: list[str]) -> int:
        # Cheapest accurate estimate: count Arc URLs that have a cached icon.
        try:
            home = Path.home()
            arc_root = home / "Library/Application Support/Arc/User Data"
            if not arc_root.is_dir():
                return 0
            import sqlite3
            unique = set()
            for db in arc_root.glob("*/Favicons"):
                try:
                    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
                    cur = conn.execute("SELECT DISTINCT page_url FROM icon_mapping")
                    for (page_url,) in cur:
                        unique.add(page_url)
                    conn.close()
                except Exception:
                    continue
            return len(unique & set(urls))
        except Exception:
            return 0

    @staticmethod
    def _estimate_history_rows() -> int:
        try:
            import sqlite3
            home = Path.home()
            root = home / "Library/Application Support/Arc/User Data"
            total = 0
            for db in root.glob("*/History"):
                try:
                    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
                    n = conn.execute("SELECT COUNT(*) FROM urls WHERE url LIKE 'http%' OR url LIKE 'ftp%'").fetchone()[0]
                    conn.close()
                    total += int(n or 0)
                except Exception:
                    continue
            return total
        except Exception:
            return 0

    @staticmethod
    def _estimate_cookies() -> int:
        try:
            import sqlite3
            home = Path.home()
            root = home / "Library/Application Support/Arc/User Data"
            total = 0
            for db in root.glob("*/Cookies"):
                try:
                    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
                    n = conn.execute("SELECT COUNT(*) FROM cookies").fetchone()[0]
                    conn.close()
                    total += int(n or 0)
                except Exception:
                    continue
            return total
        except Exception:
            return 0

    # ---- migration ------------------------------------------------------

    def migrate(self, opts: MigrationOptions) -> Iterator[ProgressEvent]:
        """Run the full migration. Yields events through the bus.

        Designed to be called on a worker thread; the JS bridge polls
        ``self.bus.drain()`` from its own thread.
        """
        self.bus.install()
        try:
            yield from self._run(opts)
        finally:
            self.bus.uninstall()

    def _emit(self, event: ProgressEvent) -> None:
        self.bus.push(event)

    def _start_step(self, step: str) -> None:
        self.bus.set_step(step)
        self._emit({"kind": "step_start", "step": step,
                    "message": STEP_LABELS.get(step, step)})

    def _done_step(self, step: str, summary: Optional[dict] = None,
                   message: Optional[str] = None) -> None:
        ev: ProgressEvent = {"kind": "step_done", "step": step,
                             "message": message or f"{STEP_LABELS.get(step, step)} done"}
        if summary is not None:
            ev["summary"] = summary
        self._emit(ev)

    def _error_step(self, step: str, exc: BaseException) -> None:
        self._emit({
            "kind": "step_error",
            "step": step,
            "message": f"{STEP_LABELS.get(step, step)} failed",
            "detail": "".join(traceback.format_exception_only(type(exc), exc)).strip(),
        })

    def _run(self, opts: MigrationOptions) -> Iterator[ProgressEvent]:
        zen_profile = opts.zen_profile_path

        # 1: extract -----------------------------------------------------
        self._start_step("extract")
        extractor = ArcPinnedTabExtractor()
        spaces = extractor.extract_pinned_tabs()
        if not spaces:
            self._error_step("extract", RuntimeError("No Arc data found."))
            yield from self._drain_yield()
            return
        if opts.arc_space_filter:
            needle = opts.arc_space_filter.lower()
            spaces = [s for s in spaces if needle in s.space_name.lower()]
            if not spaces:
                self._error_step("extract", RuntimeError(f"No Arc space matches '{opts.arc_space_filter}'."))
                yield from self._drain_yield()
                return

        # Materialize the JSON shape the existing importers expect.
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
            export_path = Path(tf.name)
        try:
            extractor.export_to_json(spaces, export_path)
            with export_path.open(encoding="utf-8") as fh:
                arc_export_data = json.load(fh)
        finally:
            try:
                export_path.unlink()
            except OSError:
                pass

        space_count = len(arc_export_data.get("spaces", []))
        pinned_count = sum(len(sp.get("pinned_tabs") or []) for sp in arc_export_data.get("spaces", []))
        self._done_step("extract", summary={
            "spaces": space_count,
            "pinned": pinned_count,
        }, message=f"Read {space_count} Arc spaces")
        yield from self._drain_yield()

        # 2: containers --------------------------------------------------
        container_mappings: dict = {}
        if opts.include_workspaces:
            self._start_step("containers")
            try:
                src_zen = _SrcZenProfile(name=zen_profile.name, path=zen_profile)
                space_importer = ZenSpaceImporter(src_zen)
                container_mappings = space_importer.import_arc_spaces_as_containers(
                    arc_export_data, dry_run=False
                ) or {}
                self._done_step("containers", summary={"created_or_reused": len(container_mappings)})
            except Exception as exc:
                self._error_step("containers", exc)
            yield from self._drain_yield()

        # 3: sessions / pinned tabs / folders ----------------------------
        if opts.include_pinned_tabs or opts.include_workspaces:
            self._start_step("sessions")
            try:
                # ``include_open_tabs`` controls whether Arc's currently-open
                # (non-pinned) tabs come along too. They go into the same
                # zen-sessions.jsonlz4 the pinned tabs do — modern Zen reads
                # both from there, regardless of pinned/unpinned status.
                # Filter them out of the data fed to the importer when the
                # toggle is off so we don't have to plumb a flag through.
                payload = arc_export_data
                if not opts.include_open_tabs:
                    payload = dict(arc_export_data)
                    payload["spaces"] = [
                        {**sp, "open_tabs": []}
                        for sp in arc_export_data.get("spaces", [])
                    ]

                sess = ZenSessionsImporter(zen_profile, folders_collapsed=opts.folders_collapsed)
                ok = sess.import_arc_data(payload, container_mappings, dry_run=False)
                pinned_total = sum(len(sp.get("pinned_tabs") or [])
                                   for sp in payload.get("spaces", []))
                open_total = sum(len(sp.get("open_tabs") or [])
                                 for sp in payload.get("spaces", []))
                self._done_step("sessions", summary={
                    "ok": bool(ok), "pinned": pinned_total, "open": open_total,
                })
            except Exception as exc:
                self._error_step("sessions", exc)
            yield from self._drain_yield()

        # 4: bookmarks ---------------------------------------------------
        if opts.include_bookmarks:
            self._start_step("bookmarks")
            try:
                bm = ZenBookmarkImporter(zen_profile)
                ok = bm.import_arc_bookmarks(arc_export_data, dry_run=False)
                self._done_step("bookmarks", summary={"ok": bool(ok)})
            except Exception as exc:
                self._error_step("bookmarks", exc)
            yield from self._drain_yield()

        # 5: favicons (DB + inline session image) -----------------------
        if opts.include_favicons:
            self._start_step("favicons")
            try:
                fav = FaviconImporter(zen_profile, dry_run=False)
                urls = list(dict.fromkeys(_iter_pinned_urls(arc_export_data)))
                db_summary = fav.import_favicons(urls)
                session_summary = fav.inject_session_images(urls)
                self._done_step("favicons", summary={
                    "db": db_summary, "session": session_summary,
                })
            except Exception as exc:
                self._error_step("favicons", exc)
            yield from self._drain_yield()

        # Open tabs are now part of the "sessions" step above (they go
        # into zen-sessions.jsonlz4 alongside pinned tabs, which is where
        # modern Zen actually reads them from). The legacy
        # ZenSessionstoreManager that used to write to sessionstore.jsonlz4
        # was a no-op because Zen's #restoreWindowData() overwrites the
        # sessionstore from zen-sessions on every launch.

        # 7: history -----------------------------------------------------
        if opts.include_history:
            self._start_step("history")
            try:
                h = HistoryImporter(zen_profile, dry_run=False)
                summary = h.import_history()
                self._done_step("history", summary=summary)
            except Exception as exc:
                self._error_step("history", exc)
            yield from self._drain_yield()

        # 8: cookies -----------------------------------------------------
        if opts.include_cookies:
            self._start_step("cookies")
            try:
                container_ids = _discover_user_containers(zen_profile)
                c = CookiesImporter(zen_profile, dry_run=False, container_ids=container_ids)
                summary = c.import_cookies()
                if summary.get("error"):
                    err = summary["error"]
                    msg = {
                        # macOS
                        "keychain_denied":           "macOS Keychain access was denied; cookies skipped.",
                        # Windows
                        "arc_local_state_missing":   "Arc has not been launched on this Windows account yet; cookies skipped.",
                        "arc_no_encrypted_key":      "Arc has no cookie encryption key on this account; cookies skipped.",
                        "arc_appbound_encryption":   "Arc cookies use newer (v20) app-bound encryption; cookies skipped. Sign in fresh on imported sites.",
                        "arc_unknown_key_prefix":    "Arc Local State key has an unrecognised prefix; cookies skipped.",
                        "arc_unexpected_key_length": "Arc DPAPI key has unexpected length; cookies skipped.",
                        "dpapi_wrong_user":          "Cookies were encrypted on a different Windows account and can't be migrated. Sign in fresh on imported sites.",
                        "dpapi_failed":              "Windows DPAPI rejected the cookie key; cookies skipped.",
                        # Both
                        "unsupported_platform":      "Cookie import only supports macOS and Windows.",
                        "cookies_db_missing":        "Zen cookies.sqlite was not found.",
                    }.get(err, f"Cookie import failed: {err}")
                    self._error_step("cookies", RuntimeError(msg))
                else:
                    self._done_step("cookies", summary=summary)
            except Exception as exc:
                self._error_step("cookies", exc)
            yield from self._drain_yield()

        # 9: finalize ----------------------------------------------------
        self._start_step("finalize")
        try:
            (zen_profile / ".arc2zen-migrated").write_text(
                json.dumps({"ts": time.time(), "version": 1}), encoding="utf-8"
            )
        except Exception:
            pass
        self._done_step("finalize", message="Migration complete")
        yield from self._drain_yield()

    def _drain_yield(self) -> Iterator[ProgressEvent]:
        for ev in self.bus.drain():
            yield ev

    # ---- backups + utility for the Done screen --------------------------

    @staticmethod
    def find_backups(zen_profile: Path) -> list[Path]:
        """Backup files we (and the existing importers) leave behind."""
        if not zen_profile.is_dir():
            return []
        out: list[Path] = []
        out.extend(sorted(zen_profile.glob("*.backup.*")))
        return out


# ----- JSON helpers for the bridge ----------------------------------------


def preview_to_dict(report: PreviewReport) -> dict:
    return {
        "spaces": [
            {
                "name": s.name,
                "icon": s.icon,
                "pinnedCount": s.pinned_count,
                "openCount": s.open_count,
                "folderCount": s.folder_count,
                "essentialCount": s.essential_count,
                "color": list(s.color) if s.color else None,
            }
            for s in report.spaces
        ],
        "pinnedTotal": report.pinned_total,
        "openTotal": report.open_total,
        "folderTotal": report.folder_total,
        "bookmarkTotal": report.bookmark_total,
        "faviconMatchEstimate": report.favicon_match_estimate,
        "historyRowsEstimate": report.history_rows_estimate,
        "cookiesEstimate": report.cookies_estimate,
    }
