"""
JavaScript bridge for the GUI.

PyWebView passes the ``Bridge`` instance as ``js_api`` and every public method
becomes callable from JS as ``window.pywebview.api.<method>``. All methods
return JSON-serialisable values (or ``None``).

Migration runs on a worker thread so the JS side can poll
``drain_progress`` while the importers do their work. The orchestrator's
``ProgressBus`` is the queue between them.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional

from .browser_control import launch_zen, open_in_finder, quit_browser
from .env_check import env_report_to_dict
from .orchestrator import (
    GUI_STEPS,
    STEP_LABELS,
    MigrationOptions,
    MigrationOrchestrator,
    preview_to_dict,
)

logger = logging.getLogger(__name__)


def _safe(payload: Any) -> Any:
    """Best-effort JSON sanitiser for return values."""
    if isinstance(payload, Path):
        return str(payload)
    if is_dataclass(payload) and not isinstance(payload, type):
        return _safe(asdict(payload))
    if isinstance(payload, dict):
        return {str(k): _safe(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_safe(v) for v in payload]
    return payload


class Bridge:
    def __init__(self) -> None:
        self.orchestrator = MigrationOrchestrator()
        self._worker: Optional[threading.Thread] = None
        self._final_state: dict = {"status": "idle"}  # 'idle' | 'running' | 'done' | 'error'
        self._lock = threading.Lock()

    # ----------------------------- window helpers -----------------------------

    def quit_app(self) -> None:
        """Close the window. Deferred so the current JS-Python call can return
        before WKWebView is torn down (otherwise the JS promise never resolves
        and the window appears to crash/hang)."""
        win = getattr(self, "_window", None)
        if win is None:
            return
        threading.Timer(0.05, self._destroy_window_safely).start()

    def _destroy_window_safely(self) -> None:
        try:
            self._window.destroy()
        except Exception:
            pass

    # ---- backup management ------------------------------------------

    def list_backups(self, profile_path: Optional[str] = None) -> list:
        """List all *.backup.<unix_ts> files in a Zen profile, newest first."""
        from datetime import datetime
        profile = Path(profile_path) if profile_path else self._guess_zen_profile()
        if profile is None or not profile.is_dir():
            return []
        items: list[dict] = []
        for f in profile.glob("*.backup.*"):
            try:
                ts = int(f.name.rsplit(".backup.", 1)[1])
            except (ValueError, IndexError):
                continue
            original = f.name.split(".backup.")[0]
            try:
                size = f.stat().st_size
            except OSError:
                continue
            items.append({
                "path": str(f),
                "name": f.name,
                "original": original,
                "ts": ts,
                "size": size,
                "iso": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            })
        items.sort(key=lambda x: x["ts"], reverse=True)
        return items

    def restore_backup(self, backup_path: str) -> dict:
        src = Path(backup_path)
        if not src.is_file():
            return {"ok": False, "error": "backup not found"}
        try:
            original = src.name.split(".backup.")[0]
        except Exception:
            return {"ok": False, "error": "could not derive original filename"}
        target = src.parent / original
        try:
            import shutil as _shutil
            # Snapshot the current target before overwriting (so the user can
            # roll forward again if they restore the wrong backup).
            if target.is_file():
                ts = int(time.time())
                _shutil.copy2(target, target.with_name(f"{original}.backup.{ts}"))
            _shutil.copy2(src, target)
            # Force WAL/SHM stale files to be discarded so SQLite re-reads cleanly.
            for suffix in ("-wal", "-shm"):
                stale = target.with_name(target.name + suffix)
                if stale.is_file():
                    try: stale.unlink()
                    except Exception: pass
            return {"ok": True, "restored": str(target)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def delete_backup(self, backup_path: str) -> dict:
        src = Path(backup_path)
        if not src.is_file():
            return {"ok": False, "error": "backup not found"}
        try:
            src.unlink()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _guess_zen_profile(self) -> Optional[Path]:
        from .env_check import list_zen_profiles
        profs = list_zen_profiles()
        return profs[0].path if profs else None

    def open_path_in_finder(self, path: str) -> bool:
        return open_in_finder(path)

    def open_url(self, url: str) -> bool:
        """Open an http(s) URL in the user's default browser, cross-platform."""
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return False
        try:
            import webbrowser
            return webbrowser.open(url, new=2)
        except Exception:
            return False

    def platform(self) -> str:
        """Return ``"mac"``, ``"win"``, or ``"linux"`` for the JS side to gate UI."""
        if sys.platform == "darwin":
            return "mac"
        if os.name == "nt":
            return "win"
        return "linux"

    def copy_to_clipboard(self, text: str) -> bool:
        if not isinstance(text, str):
            return False
        try:
            import subprocess
            if sys.platform == "darwin":
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(input=text.encode("utf-8"), timeout=2)
                return p.returncode == 0
            if os.name == "nt":
                # clip.exe ships with every Windows install. It expects
                # UTF-16 LE on stdin; pass without a BOM.
                p = subprocess.Popen(["clip.exe"], stdin=subprocess.PIPE)
                p.communicate(input=text.encode("utf-16-le"), timeout=2)
                return p.returncode == 0
        except Exception:
            return False
        return False

    # ---------- environment / preview / migration --------------

    def check_env(self) -> dict:
        try:
            report = self.orchestrator.check_environment()
            return _safe(env_report_to_dict(report))
        except Exception as exc:
            logger.exception("check_env failed")
            return {"error": str(exc), "trace": traceback.format_exc()}

    def quit_browser(self, name: str) -> dict:
        if name not in ("arc", "zen"):
            return {"ok": False, "error": "unknown browser"}
        return _safe(quit_browser(name))  # type: ignore[arg-type]

    def launch_zen(self) -> bool:
        return launch_zen()

    def preview(self, opts_json: str) -> dict:
        try:
            opts = self._parse_options(opts_json)
            report = self.orchestrator.preview(opts)
            return _safe(preview_to_dict(report))
        except Exception as exc:
            logger.exception("preview failed")
            return {"error": str(exc), "trace": traceback.format_exc()}

    def start_migration(self, opts_json: str) -> dict:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return {"ok": False, "error": "migration already running"}

        try:
            opts = self._parse_options(opts_json)
        except Exception as exc:
            return {"ok": False, "error": f"bad options: {exc}"}

        with self._lock:
            self._final_state = {"status": "running"}

        def _run() -> None:
            try:
                # Drain the iterator; events flow into the queue via the bus.
                for _ in self.orchestrator.migrate(opts):
                    pass
                with self._lock:
                    self._final_state = {
                        "status": "done",
                        "backups": [str(p) for p in self.orchestrator.find_backups(opts.zen_profile_path)],
                        "zenProfilePath": str(opts.zen_profile_path),
                    }
            except Exception as exc:
                logger.exception("migration worker crashed")
                with self._lock:
                    self._final_state = {
                        "status": "error",
                        "error": str(exc),
                        "trace": traceback.format_exc(),
                    }

        self._worker = threading.Thread(target=_run, daemon=True, name="arc2zen-migrate")
        self._worker.start()
        return {"ok": True}

    def drain_progress(self) -> dict:
        events = self.orchestrator.bus.drain()
        with self._lock:
            state = dict(self._final_state)
        return {"events": _safe(events), "state": state, "steps": list(GUI_STEPS), "labels": STEP_LABELS}

    def get_step_metadata(self) -> dict:
        return {"steps": list(GUI_STEPS), "labels": STEP_LABELS}

    # ----------------------------- helpers -----------------------------------

    def set_window(self, window: Any) -> None:
        self._window = window

    @staticmethod
    def _parse_options(opts_json: str) -> MigrationOptions:
        data = json.loads(opts_json) if isinstance(opts_json, str) else dict(opts_json)
        zen_profile = Path(data["zenProfilePath"]).expanduser()
        return MigrationOptions(
            zen_profile_path=zen_profile,
            arc_space_filter=data.get("arcSpaceFilter") or None,
            folders_collapsed=bool(data.get("foldersCollapsed", True)),
            include_workspaces=bool(data.get("includeWorkspaces", True)),
            include_pinned_tabs=bool(data.get("includePinnedTabs", True)),
            include_bookmarks=bool(data.get("includeBookmarks", True)),
            include_favicons=bool(data.get("includeFavicons", True)),
            include_open_tabs=bool(data.get("includeOpenTabs", False)),
            include_history=bool(data.get("includeHistory", False)),
            include_cookies=bool(data.get("includeCookies", False)),
        )
