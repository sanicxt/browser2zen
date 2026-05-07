"""Build a minimal synthetic Safari Cookies.binarycookies for tests.

Run from repo root:
    python tests/fixtures/_build_safari_cookies_fixture.py

Produces tests/fixtures/safari/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies
with one cookie on .example.com.

The format is the same one safari_cookies_importer.parse_binarycookies()
reads, mirrored byte-for-byte.
"""

from __future__ import annotations

import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "safari/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies"


# Cocoa-epoch float64 of "now-ish in 2023". The importer converts to
# Unix seconds = cocoa + 978307200. 700_000_000 cocoa = ~2023-03.
_COCOA_EXPIRY = 1_000_000_000.0   # ~2032
_COCOA_CREATION = 700_000_000.0   # ~2023


def _build_cookie(host: str, name: str, path: str, value: str,
                  secure: bool, http_only: bool) -> bytes:
    flags = 0
    if secure:
        flags |= 0x1
    if http_only:
        flags |= 0x4

    # Layout (little-endian):
    # 0:4   size (filled in last)
    # 4:8   pad
    # 8:12  flags
    # 12:16 pad
    # 16:20 url offset
    # 20:24 name offset
    # 24:28 path offset
    # 28:32 value offset
    # 32:40 8-byte end-of-cookie marker (zeros)
    # 40:48 expiry (float64 cocoa)
    # 48:56 creation (float64 cocoa)
    # 56:    null-terminated UTF-8 strings

    header_size = 56
    url_b = host.encode("utf-8") + b"\x00"
    name_b = name.encode("utf-8") + b"\x00"
    path_b = path.encode("utf-8") + b"\x00"
    value_b = value.encode("utf-8") + b"\x00"

    url_off = header_size
    name_off = url_off + len(url_b)
    path_off = name_off + len(name_b)
    value_off = path_off + len(path_b)

    record = bytearray(header_size)
    struct.pack_into("<I", record, 8, flags)
    struct.pack_into("<I", record, 16, url_off)
    struct.pack_into("<I", record, 20, name_off)
    struct.pack_into("<I", record, 24, path_off)
    struct.pack_into("<I", record, 28, value_off)
    struct.pack_into("<d", record, 40, _COCOA_EXPIRY)
    struct.pack_into("<d", record, 48, _COCOA_CREATION)

    record += url_b + name_b + path_b + value_b
    # Patch in the size at offset 0.
    struct.pack_into("<I", record, 0, len(record))
    return bytes(record)


def _build_page(cookies: list[bytes]) -> bytes:
    # Page header (LE): 0x00000100, num cookies, then a uint32 offset
    # per cookie, then the cookie records concatenated.
    n = len(cookies)
    offsets_table_size = 8 + 4 * n   # header (4) + count (4) + N*4
    offsets = []
    cur = offsets_table_size
    for c in cookies:
        offsets.append(cur)
        cur += len(c)

    out = bytearray()
    out += b"\x00\x00\x01\x00"
    out += struct.pack("<I", n)
    for off in offsets:
        out += struct.pack("<I", off)
    for c in cookies:
        out += c
    return bytes(out)


def build() -> bytes:
    cookie = _build_cookie(
        host=".example.com",
        name="session",
        path="/",
        value="abc123",
        secure=True,
        http_only=False,
    )
    page = _build_page([cookie])
    out = bytearray()
    out += b"cook"
    out += struct.pack(">I", 1)        # one page
    out += struct.pack(">I", len(page))  # page size
    out += page
    return bytes(out)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(build())
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
