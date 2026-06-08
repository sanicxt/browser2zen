"""
Chromium SNSS session-store reader.

Chrome / Edge / Brave keep their *actual* tab-strip pinned and open tabs
in the SessionService file, NOT in ``Bookmarks``. That file
(``<profile>/Sessions/Session_<ts>``, or legacy ``<profile>/Current
Session``) is a little-endian binary command stream:

    [4-byte "SNSS" magic][int32 version]
    then repeated: [uint16 size][uint8 command_id][size-1 payload bytes]

The current tab state is the *replay* of all commands in order
(last-writer-wins for mutable properties). We only decode the handful of
commands needed to recover, per tab: which window it's in, its order
within that window, whether it's pinned, and the URL/title of its current
navigation entry. Window type lets us drop popup/devtools/app windows.

Command ids and payload layouts were ground-truthed against real Chrome
session files and match Chromium's ``session_service_commands.cc``:

    0  SetTabWindow              {int32 window_id, int32 tab_id}
    2  SetTabIndexInWindow       {int32 tab_id,    int32 index}
    6  UpdateTabNavigation       pickle{int32 tab_id, int32 nav_index,
                                        String url, String16 title, ...}
    7  SetSelectedNavigationIndex{int32 tab_id,    int32 index}
    9  SetWindowType             {int32 window_id, int32 type}  (0 == normal)
    12 SetPinnedState            {int32 tab_id,    int32 pinned}
    16 TabClosed                 {int32 tab_id, ...}
    17 WindowClosed              {int32 window_id, ...}

The payloads of 0/2/7/9/12 are fixed-size POD structs; 6 is a
``base::Pickle`` (4-byte size header, then 4-byte-aligned fields).
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_MAGIC = b"SNSS"

_CMD_SET_TAB_WINDOW = 0
_CMD_SET_TAB_INDEX_IN_WINDOW = 2
_CMD_UPDATE_TAB_NAVIGATION = 6
_CMD_SET_SELECTED_NAVIGATION_INDEX = 7
_CMD_SET_WINDOW_TYPE = 9
_CMD_SET_PINNED_STATE = 12
_CMD_TAB_CLOSED = 16
_CMD_WINDOW_CLOSED = 17

_WINDOW_TYPE_NORMAL = 0


@dataclass
class SessionTab:
    """One live tab recovered from the session store."""
    url: str
    title: str
    pinned: bool
    window_id: int
    index: int


@dataclass
class _Tab:
    window: int | None = None
    index: int = 0
    pinned: bool = False
    selected: int | None = None
    navs: dict[int, tuple[str, str]] = field(default_factory=dict)


def _iter_commands(data: bytes):
    """Yield ``(command_id, payload)`` pairs from an SNSS byte stream."""
    if data[:4] != _MAGIC:
        raise ValueError(f"not an SNSS session file (magic={data[:4]!r})")
    off, n = 8, len(data)  # skip magic + version
    while off + 2 <= n:
        size = struct.unpack_from("<H", data, off)[0]
        off += 2
        if size == 0 or off + size > n:
            break
        yield data[off], data[off + 1:off + size]
        off += size


class _PickleReader:
    """Minimal reader for the subset of ``base::Pickle`` we need.

    A serialized pickle is ``[uint32 payload_size][payload]``; every field
    is aligned to a 4-byte boundary relative to the payload start.
    """

    def __init__(self, payload: bytes):
        size = struct.unpack_from("<I", payload, 0)[0]
        self.buf = payload[4:4 + size]
        self.pos = 0

    def _align(self) -> None:
        rem = self.pos % 4
        if rem:
            self.pos += 4 - rem

    def read_int(self) -> int:
        v = struct.unpack_from("<i", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def read_string(self) -> str:
        length = self.read_int()
        if length < 0 or self.pos + length > len(self.buf):
            raise ValueError("bad string length")
        s = self.buf[self.pos:self.pos + length].decode("utf-8", "replace")
        self.pos += length
        self._align()
        return s

    def read_string16(self) -> str:
        units = self.read_int()
        nbytes = units * 2
        if units < 0 or self.pos + nbytes > len(self.buf):
            raise ValueError("bad string16 length")
        s = self.buf[self.pos:self.pos + nbytes].decode("utf-16-le", "replace")
        self.pos += nbytes
        self._align()
        return s


def read_session_tabs(path: Path) -> list[SessionTab]:
    """Replay an SNSS session file and return its live tabs, in tab-strip
    order (windows in first-seen order, tabs by their in-window index).

    Tabs that were closed, live in a closed window, or live in a
    non-normal window (popup/devtools/app) are dropped, as are tabs with
    no navigable URL. Raises :class:`ValueError` if ``path`` is not SNSS.
    """
    data = Path(path).read_bytes()

    window_type: dict[int, int] = {}
    window_order: list[int] = []
    closed_windows: set[int] = set()
    closed_tabs: set[int] = set()
    tabs: dict[int, _Tab] = {}

    def tab(tid: int) -> _Tab:
        return tabs.setdefault(tid, _Tab())

    for cmd_id, payload in _iter_commands(data):
        if cmd_id == _CMD_SET_WINDOW_TYPE and len(payload) >= 8:
            wid, wtype = struct.unpack_from("<ii", payload, 0)
            window_type[wid] = wtype
            if wid not in window_order:
                window_order.append(wid)
        elif cmd_id == _CMD_SET_TAB_WINDOW and len(payload) >= 8:
            wid, tid = struct.unpack_from("<ii", payload, 0)
            tab(tid).window = wid
        elif cmd_id == _CMD_SET_TAB_INDEX_IN_WINDOW and len(payload) >= 8:
            tid, idx = struct.unpack_from("<ii", payload, 0)
            tab(tid).index = idx
        elif cmd_id == _CMD_SET_PINNED_STATE and len(payload) >= 8:
            tid, val = struct.unpack_from("<ii", payload, 0)
            tab(tid).pinned = bool(val)
        elif cmd_id == _CMD_SET_SELECTED_NAVIGATION_INDEX and len(payload) >= 8:
            tid, idx = struct.unpack_from("<ii", payload, 0)
            tab(tid).selected = idx
        elif cmd_id == _CMD_UPDATE_TAB_NAVIGATION:
            try:
                r = _PickleReader(payload)
                tid = r.read_int()
                nav_index = r.read_int()
                url = r.read_string()
                title = r.read_string16()
            except (struct.error, ValueError):
                continue
            tab(tid).navs[nav_index] = (url, title)
        elif cmd_id == _CMD_TAB_CLOSED and len(payload) >= 4:
            closed_tabs.add(struct.unpack_from("<i", payload, 0)[0])
        elif cmd_id == _CMD_WINDOW_CLOSED and len(payload) >= 4:
            closed_windows.add(struct.unpack_from("<i", payload, 0)[0])

    rank = {wid: i for i, wid in enumerate(window_order)}
    ordered: list[tuple[int, int, SessionTab]] = []
    for tid, t in tabs.items():
        if tid in closed_tabs or t.window is None or t.window in closed_windows:
            continue
        # Unknown window type defaults to normal so we never silently drop
        # a real window that lacked an explicit SetWindowType command.
        if window_type.get(t.window, _WINDOW_TYPE_NORMAL) != _WINDOW_TYPE_NORMAL:
            continue
        if not t.navs:
            continue
        if t.selected is not None and t.selected in t.navs:
            url, title = t.navs[t.selected]
        else:
            url, title = t.navs[max(t.navs)]
        if not url:
            continue
        ordered.append((
            rank.get(t.window, len(window_order)),
            t.index,
            SessionTab(url=url, title=title, pinned=t.pinned,
                       window_id=t.window, index=t.index),
        ))

    ordered.sort(key=lambda x: (x[0], x[1]))
    return [st for _, _, st in ordered]


def find_session_file(profile_dir: Path) -> Path | None:
    """Locate a profile's live session file.

    Modern Chromium keeps it at ``<profile>/Sessions/Session_<ts>`` (the
    newest timestamp is the current one); ``Tabs_<ts>`` siblings belong to
    the *recently-closed* TabRestoreService and are deliberately ignored.
    Older builds kept ``<profile>/Current Session`` at the profile root.
    """
    profile_dir = Path(profile_dir)
    sessions = profile_dir / "Sessions"
    newest: tuple[int, Path] | None = None
    if sessions.is_dir():
        for p in sessions.glob("Session_*"):
            try:
                ts = int(p.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            if newest is None or ts > newest[0]:
                newest = (ts, p)
    if newest is not None:
        return newest[1]
    legacy = profile_dir / "Current Session"
    return legacy if legacy.is_file() else None
