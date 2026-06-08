"""Tests for the Chromium SNSS session-store parser.

The parser reverse-engineers Chromium's SessionService file format
(``<profile>/Sessions/Session_<ts>``) so we can recover the *actual*
tab-strip pinned and open tabs — which is where Chrome/Edge/Brave keep
them (NOT in Bookmarks). Format ground-truthed against real Chrome
session files: 8-byte header (``SNSS`` + int32 version) followed by
``[uint16 size][uint8 cmd_id][size-1 payload]`` command records.
"""

from __future__ import annotations

import struct

import pytest

from extractors._snss import SessionTab, find_session_file, read_session_tabs

# --- SNSS byte builder (mirrors the real on-disk layout) ------------------
#
# Command ids match Chromium's session_service_commands.cc:
#   0  SetTabWindow            {int32 window_id, int32 tab_id}
#   2  SetTabIndexInWindow     {int32 tab_id, int32 index}
#   6  UpdateTabNavigation     pickle{int32 tab_id, int32 nav_index,
#                                     string url, string16 title, ...}
#   7  SetSelectedNavigationIndex {int32 tab_id, int32 index}
#   9  SetWindowType           {int32 window_id, int32 type}  (0 == normal)
#   12 SetPinnedState          {int32 tab_id, int32 pinned}
#   16 TabClosed               {int32 tab_id, ...}
#   17 WindowClosed            {int32 window_id, ...}

def _cmd(cmd_id: int, payload: bytes) -> bytes:
    size = 1 + len(payload)
    return struct.pack("<H", size) + bytes([cmd_id]) + payload


def _pad4(data: bytes) -> bytes:
    rem = len(data) % 4
    return data + (b"\x00" * (4 - rem) if rem else b"")


def _wstring(s: str) -> bytes:
    raw = s.encode("utf-8")
    return _pad4(struct.pack("<i", len(raw)) + raw)


def _wstring16(s: str) -> bytes:
    raw = s.encode("utf-16-le")
    # length prefix is the number of UTF-16 code units, not bytes
    return _pad4(struct.pack("<i", len(raw) // 2) + raw)


class SnssBuilder:
    def __init__(self, version: int = 3):
        self.body = b""
        self.version = version

    def window_type(self, window_id, wtype=0):
        self.body += _cmd(9, struct.pack("<ii", window_id, wtype))
        return self

    def tab_window(self, window_id, tab_id):
        self.body += _cmd(0, struct.pack("<ii", window_id, tab_id))
        return self

    def tab_index(self, tab_id, index):
        self.body += _cmd(2, struct.pack("<ii", tab_id, index))
        return self

    def pinned(self, tab_id, value=1):
        self.body += _cmd(12, struct.pack("<ii", tab_id, value))
        return self

    def selected_nav(self, tab_id, index):
        self.body += _cmd(7, struct.pack("<ii", tab_id, index))
        return self

    def navigation(self, tab_id, nav_index, url, title):
        pickle_body = (
            struct.pack("<i", tab_id)
            + struct.pack("<i", nav_index)
            + _wstring(url)
            + _wstring16(title)
        )
        payload = struct.pack("<I", len(pickle_body)) + pickle_body
        self.body += _cmd(6, payload)
        return self

    def tab_closed(self, tab_id):
        self.body += _cmd(16, struct.pack("<i", tab_id) + struct.pack("<i", 0)
                          + struct.pack("<q", 0))
        return self

    def window_closed(self, window_id):
        self.body += _cmd(17, struct.pack("<i", window_id) + struct.pack("<i", 0)
                          + struct.pack("<q", 0))
        return self

    def bytes(self) -> bytes:
        return b"SNSS" + struct.pack("<i", self.version) + self.body


def _write(tmp_path, builder, name="Session_1"):
    p = tmp_path / name
    p.write_bytes(builder.bytes())
    return p


# --- read_session_tabs ----------------------------------------------------

def test_reads_pinned_and_open_tabs_in_window_order(tmp_path):
    b = (SnssBuilder()
         .window_type(100, 0)
         .tab_window(100, 1).tab_index(1, 0).pinned(1, 1)
         .navigation(1, 0, "https://a.com", "A")
         .tab_window(100, 2).tab_index(2, 1)
         .navigation(2, 0, "https://b.com", "B"))
    tabs = read_session_tabs(_write(tmp_path, b))

    assert [t.url for t in tabs] == ["https://a.com", "https://b.com"]
    assert [t.title for t in tabs] == ["A", "B"]
    assert [t.pinned for t in tabs] == [True, False]


def test_excludes_closed_tabs(tmp_path):
    b = (SnssBuilder()
         .window_type(100, 0)
         .tab_window(100, 1).tab_index(1, 0).navigation(1, 0, "https://a.com", "A")
         .tab_window(100, 2).tab_index(2, 1).navigation(2, 0, "https://b.com", "B")
         .tab_closed(2))
    tabs = read_session_tabs(_write(tmp_path, b))
    assert [t.url for t in tabs] == ["https://a.com"]


def test_excludes_tabs_in_closed_window(tmp_path):
    b = (SnssBuilder()
         .window_type(100, 0).window_type(200, 0)
         .tab_window(100, 1).tab_index(1, 0).navigation(1, 0, "https://keep.com", "K")
         .tab_window(200, 2).tab_index(2, 0).navigation(2, 0, "https://gone.com", "G")
         .window_closed(200))
    tabs = read_session_tabs(_write(tmp_path, b))
    assert [t.url for t in tabs] == ["https://keep.com"]


def test_excludes_non_normal_windows(tmp_path):
    # window 200 is a popup (type 1) — its tabs must not be migrated.
    b = (SnssBuilder()
         .window_type(100, 0).window_type(200, 1)
         .tab_window(100, 1).tab_index(1, 0).navigation(1, 0, "https://normal.com", "N")
         .tab_window(200, 2).tab_index(2, 0).navigation(2, 0, "https://popup.com", "P"))
    tabs = read_session_tabs(_write(tmp_path, b))
    assert [t.url for t in tabs] == ["https://normal.com"]


def test_uses_selected_navigation_entry(tmp_path):
    # A tab carries its whole back/forward history; we want the current entry.
    b = (SnssBuilder()
         .window_type(100, 0)
         .tab_window(100, 1).tab_index(1, 0)
         .navigation(1, 0, "https://old.com", "Old")
         .navigation(1, 1, "https://current.com", "Current")
         .selected_nav(1, 1))
    tabs = read_session_tabs(_write(tmp_path, b))
    assert [(t.url, t.title) for t in tabs] == [("https://current.com", "Current")]


def test_unicode_title_round_trips(tmp_path):
    b = (SnssBuilder()
         .window_type(100, 0)
         .tab_window(100, 1).tab_index(1, 0)
         .navigation(1, 0, "https://x.com", "Café — 日本語"))
    tabs = read_session_tabs(_write(tmp_path, b))
    assert tabs[0].title == "Café — 日本語"


def test_skips_tab_without_navigation(tmp_path):
    # A tab with a window association but no navigation entry has no URL
    # and must be dropped rather than emitted as a blank tab.
    b = (SnssBuilder()
         .window_type(100, 0)
         .tab_window(100, 1).tab_index(1, 0)
         .tab_window(100, 2).tab_index(2, 1).navigation(2, 0, "https://b.com", "B"))
    tabs = read_session_tabs(_write(tmp_path, b))
    assert [t.url for t in tabs] == ["https://b.com"]


def test_last_pinned_state_wins(tmp_path):
    b = (SnssBuilder()
         .window_type(100, 0)
         .tab_window(100, 1).tab_index(1, 0).navigation(1, 0, "https://a.com", "A")
         .pinned(1, 1).pinned(1, 0))   # pinned then unpinned
    tabs = read_session_tabs(_write(tmp_path, b))
    assert tabs[0].pinned is False


def test_rejects_non_snss_file(tmp_path):
    p = tmp_path / "Session_bad"
    p.write_bytes(b"NOPE" + struct.pack("<i", 3))
    with pytest.raises(ValueError):
        read_session_tabs(p)


# --- find_session_file ----------------------------------------------------

def test_find_session_file_picks_newest_by_suffix(tmp_path):
    sessions = tmp_path / "Sessions"
    sessions.mkdir()
    (sessions / "Session_100").write_bytes(b"x")
    (sessions / "Session_900").write_bytes(b"x")
    (sessions / "Session_500").write_bytes(b"x")
    # Tabs_* are the TabRestoreService (recently closed), not current tabs.
    (sessions / "Tabs_999").write_bytes(b"x")
    assert find_session_file(tmp_path).name == "Session_900"


def test_find_session_file_legacy_fallback(tmp_path):
    # Older Chromium kept the live session at the profile root.
    (tmp_path / "Current Session").write_bytes(b"x")
    assert find_session_file(tmp_path).name == "Current Session"


def test_find_session_file_missing(tmp_path):
    assert find_session_file(tmp_path) is None
