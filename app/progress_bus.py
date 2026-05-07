"""
Structured progress bus for the GUI.

A `logging.Handler` subclass collects log records emitted by the existing
importer modules (`logger.info(...)` calls) and converts them into
structured `ProgressEvent` dicts the JavaScript frontend can render.

The orchestrator also `push()`es its own high-level events
(step_start / step_done / step_error) directly, so the frontend has both:
- A canonical step list driven by orchestrator events.
- A free-form "View details" stream of raw log messages.

The frontend drains the queue every 100ms via the JS bridge.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Literal, Optional, TypedDict


class ProgressEvent(TypedDict, total=False):
    kind: Literal["step_start", "step_progress", "step_done", "step_error", "log", "warn", "info"]
    step: str
    percent: float | None   # 0..1, None when indeterminate
    message: str
    detail: str | None
    ts: float
    summary: dict | None    # populated on step_done with the importer's return dict


class ProgressBus(logging.Handler):
    """Logging handler that records the active step and pushes events to a queue.

    Thread-safe: events from any thread can be pushed; only the JS bridge thread
    drains. Uses an unbounded queue (~kB per event, finite migration time, fine).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._queue: queue.Queue[ProgressEvent] = queue.Queue()
        self._step: str | None = None
        self._lock = threading.Lock()

    # ---- step context ----

    def set_step(self, step: str) -> None:
        with self._lock:
            self._step = step

    def current_step(self) -> str | None:
        with self._lock:
            return self._step

    # ---- direct push (orchestrator-level events) ----

    def push(self, event: ProgressEvent) -> None:
        event.setdefault("ts", time.time())
        if "step" not in event and self._step is not None:
            event["step"] = self._step
        self._queue.put(event)

    # ---- logging.Handler interface ----

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        kind: str
        if record.levelno >= logging.ERROR:
            kind = "log"
        elif record.levelno >= logging.WARNING:
            kind = "warn"
        else:
            kind = "log"
        event: ProgressEvent = {
            "kind": kind,  # type: ignore[typeddict-item]
            "message": msg,
            "ts": record.created,
        }
        if self._step is not None:
            event["step"] = self._step
        self._queue.put(event)

    # ---- consumer ----

    def drain(self) -> list[ProgressEvent]:
        out: list[ProgressEvent] = []
        try:
            while True:
                out.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        return out

    # ---- lifecycle helpers (so the CLI is unaffected when both run in the
    # same Python process during development) ----

    def install(self, logger_names: tuple[str, ...] = ("",)) -> None:
        """Attach this handler to the named loggers (root by default)."""
        for name in logger_names:
            logging.getLogger(name).addHandler(self)
            # Make sure INFO records actually reach us
            target = logging.getLogger(name)
            if target.level == logging.NOTSET or target.level > logging.INFO:
                target.setLevel(logging.INFO)

    def uninstall(self, logger_names: tuple[str, ...] = ("",)) -> None:
        for name in logger_names:
            logging.getLogger(name).removeHandler(self)
