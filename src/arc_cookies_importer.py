#!/usr/bin/env python3
"""
Arc → Zen Cookies Importer

Decrypts Arc's Chromium-format cookies and writes them into Zen's
Firefox-format `cookies.sqlite` so login sessions carry over.

Supports macOS and Windows:

- **macOS**: master key fetched from the Keychain entry "Arc Safe Storage",
  derived via PBKDF2 (1003 iterations, salt "saltysalt") to a 16-byte
  AES-128 key. Cookie blobs prefixed `v10` use AES-128-CBC with an
  all-spaces IV and PKCS7 padding.
- **Windows**: master key read from `Local State` JSON, base64-decoded,
  unwrapped via Windows DPAPI (`crypt32!CryptUnprotectData` through
  ctypes). Cookie blobs prefixed `v10` use AES-256-GCM (12-byte nonce
  + ciphertext + 16-byte authentication tag).

The Firefox-side write (`moz_cookies` insert, container duplication,
ms-vs-seconds expiry handling) is identical across platforms. Re-running
merges new cookies without duplicating existing ones.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)


_WEBKIT_EPOCH_OFFSET_US = 11_644_473_600_000_000


def _chrome_to_unix_us(chrome_us: Optional[int]) -> int:
    if not chrome_us:
        return 0
    val = chrome_us - _WEBKIT_EPOCH_OFFSET_US
    return val if val > 0 else 0


def _read_keychain_password(service: str = "Arc Safe Storage", account: str = "Arc") -> Optional[str]:
    """Fetch the per-app Chromium safe-storage key from macOS Keychain.

    Arc registers the entry with svce="Arc Safe Storage" and acct="Arc".
    Searching by service alone falls back to account-blind lookup, but
    that's what triggers the "could not be found" error on some setups.
    """
    attempts = [
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        ["security", "find-generic-password", "-s", service, "-w"],
        ["security", "find-generic-password", "-wa", service],
    ]
    last_err = ""
    for cmd in attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            logger.error("Keychain lookup timed out (likely awaiting user approval).")
            return None
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        last_err = result.stderr.strip()
    logger.error(f"Keychain returned error for {service!r}: {last_err or 'denied'}")
    return None


def _derive_aes_key_macos(password: str) -> bytes:
    """Chromium on macOS: PBKDF2-HMAC-SHA1, salt='saltysalt', iterations=1003, keylen=16."""
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=16,
        salt=b"saltysalt",
        iterations=1003,
    )
    return kdf.derive(password.encode("utf-8"))


def _decrypt_v10_cbc(blob: bytes, key: bytes) -> Optional[bytes]:
    """macOS path. Strip 'v10' magic, AES-128-CBC decrypt with all-spaces IV, PKCS7 unpad."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not blob.startswith(b"v10"):
        return None
    ciphertext = blob[3:]
    if len(ciphertext) % 16 != 0:
        return None
    iv = b" " * 16
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad = padded[-1] if padded else 0
    if 0 < pad <= 16 and padded.endswith(bytes([pad]) * pad):
        padded = padded[:-pad]
    return padded


# ---------- Windows DPAPI master-key unwrap ----------

class _DpapiError(RuntimeError):
    """Raised when Windows DPAPI rejects the encrypted blob.

    The ``code`` attribute carries an orchestrator-friendly error key.
    """

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _arc_local_state_paths() -> list[Path]:
    """Return Local State JSON candidates across both Windows install vectors."""
    home = Path.home()
    if os.name != "nt":
        return []
    candidates = [
        home / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4"
             / "LocalCache/Local/Arc/User Data/Local State",
        home / "AppData/Local/Arc/User Data/Local State",
    ]
    return [p for p in candidates if p.is_file()]


def _crypt_unprotect_data(blob: bytes) -> bytes:
    """Call ``crypt32!CryptUnprotectData`` via ctypes and return the plaintext.

    Raises ``_DpapiError`` with a structured code on failure.
    """
    import ctypes
    from ctypes import wintypes  # type: ignore[import-not-found]

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    buf = ctypes.create_string_buffer(blob, len(blob))
    in_blob = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        err = ctypes.get_last_error()
        # ERROR_INVALID_DATA (13) is the canonical "different user" symptom.
        code = "dpapi_wrong_user" if err == 13 else "dpapi_failed"
        raise _DpapiError(code, f"CryptUnprotectData failed (GetLastError={err})")

    try:
        size = int(out_blob.cbData)
        plaintext = ctypes.string_at(out_blob.pbData, size)
        return plaintext
    finally:
        # Free the allocation Windows made for us.
        kernel32.LocalFree(out_blob.pbData)


def _read_local_state_key_windows() -> bytes:
    """Read Arc's ``Local State`` and unwrap the master key.

    Raises ``_DpapiError`` with a structured code on any failure path.
    """
    paths = _arc_local_state_paths()
    if not paths:
        raise _DpapiError(
            "arc_local_state_missing",
            "Arc has not been launched on this Windows account yet.",
        )
    state = json.loads(paths[0].read_text(encoding="utf-8"))
    encrypted_b64 = state.get("os_crypt", {}).get("encrypted_key")
    if not encrypted_b64:
        raise _DpapiError(
            "arc_no_encrypted_key",
            "Arc Local State has no os_crypt.encrypted_key entry.",
        )

    raw = base64.b64decode(encrypted_b64)
    if raw.startswith(b"APPB"):
        # Chromium v20 / Brave / Edge app-bound encryption; not defeatable
        # without invoking Chromium's elevated COM service.
        raise _DpapiError(
            "arc_appbound_encryption",
            "Arc cookies use app-bound (v20) encryption.",
        )
    if not raw.startswith(b"DPAPI"):
        raise _DpapiError(
            "arc_unknown_key_prefix",
            f"Unexpected key prefix {raw[:5]!r}",
        )

    key = _crypt_unprotect_data(raw[5:])
    if len(key) != 32:
        raise _DpapiError(
            "arc_unexpected_key_length",
            f"Got {len(key)}-byte key from DPAPI; expected 32 bytes.",
        )
    return key


def _decrypt_v10_gcm(blob: bytes, key: bytes) -> Optional[bytes]:
    """Windows path. Strip 'v10' magic, AES-256-GCM decrypt with the embedded nonce.

    Layout: ``b"v10" + nonce[12] + ciphertext + tag[16]``.
    Returns the plaintext or ``None`` on prefix/format mismatch. The
    caller swallows individual decrypt failures and counts them.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag

    if not blob.startswith(b"v10") or len(blob) < 3 + 12 + 16:
        return None
    nonce = blob[3:15]
    ct_and_tag = blob[15:]
    try:
        return AESGCM(key).decrypt(nonce, ct_and_tag, None)
    except InvalidTag:
        return None


def _strip_host_hash(plaintext: bytes) -> bytes:
    """Some Chromium builds prefix the plaintext with a 32-byte SHA-256(host) tag.

    We don't know up-front which builds use it. Heuristic: if the first 32 bytes
    are non-printable and the remainder is printable, drop the tag.
    """
    if len(plaintext) < 33:
        return plaintext
    head, tail = plaintext[:32], plaintext[32:]
    if any(b < 0x20 or b > 0x7E for b in head) and all(0x09 <= b <= 0x7E or b == 0x0A for b in tail):
        return tail
    return plaintext


# Chromium SameSite (samesite enum in net/cookies/cookie_constants.h):
#   -1 UNSPECIFIED, 0 NO_RESTRICTION, 1 LAX, 2 STRICT
# Firefox sameSite (in nsICookie.idl):
#   0 SAMESITE_NONE, 1 SAMESITE_LAX, 2 SAMESITE_STRICT, 3 SAMESITE_UNSET
_SAMESITE_MAP = {-1: 3, 0: 0, 1: 1, 2: 2}


def _arc_cookie_dbs() -> list[Path]:
    """Locate every Arc profile's Cookies SQLite across both supported OSes.

    Newer Chromium builds nest the file as ``<profile>/Network/Cookies``;
    older builds keep it at ``<profile>/Cookies``. We accept either.
    """
    home = Path.home()
    roots: list[Path] = []
    if sys.platform == "darwin":
        roots.append(home / "Library/Application Support/Arc/User Data")
    elif os.name == "nt":
        roots.append(
            home / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4"
                 / "LocalCache/Local/Arc/User Data"
        )
        roots.append(home / "AppData/Local/Arc/User Data")

    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for profile in sorted(root.iterdir()):
            if not profile.is_dir():
                continue
            for candidate in (profile / "Network" / "Cookies", profile / "Cookies"):
                if candidate.is_file():
                    found.append(candidate)
                    break
    return found


class CookiesImporter:
    # Session cookies (Chromium has_expires=0) get this many days of fake
    # persistence in Firefox so Firefox's startup sweep doesn't immediately
    # purge them as expired.
    SESSION_COOKIE_DAYS = 30

    def __init__(
        self,
        zen_profile: Path,
        dry_run: bool = False,
        container_ids: Optional[list[int]] = None,
    ):
        self.zen_profile = Path(zen_profile)
        self.zen_cookies = self.zen_profile / "cookies.sqlite"
        self.dry_run = dry_run
        self.container_ids = container_ids or []
        self._tempdir: Optional[Path] = None

    def _snapshot(self, src: Path) -> Path:
        if self._tempdir is None:
            self._tempdir = Path(tempfile.mkdtemp(prefix="arc2zen_cookies_"))
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

    def _decrypt_rows(
        self,
        rows: Iterable[sqlite3.Row],
        key: bytes,
        decrypt_fn: Callable[[bytes, bytes], Optional[bytes]],
    ) -> Iterable[dict]:
        decrypt_fail = 0
        decoded = 0

        def s(b) -> str:
            if b is None:
                return ""
            if isinstance(b, str):
                return b
            try:
                return b.decode("utf-8")
            except UnicodeDecodeError:
                return b.decode("latin-1", errors="replace")

        for row in rows:
            raw_value = row["value"]
            value = s(raw_value)
            blob = row["encrypted_value"]
            if not value and blob:
                pt = decrypt_fn(bytes(blob), key)
                if pt is None:
                    decrypt_fail += 1
                    continue
                pt = _strip_host_hash(pt)
                try:
                    value = pt.decode("utf-8")
                except UnicodeDecodeError:
                    decrypt_fail += 1
                    continue
                decoded += 1
            elif not value and not blob:
                continue
            # Map Chromium source_scheme (0=unset, 1=http, 2=https) to
            # Firefox schemeMap (0=unset, 1=http, 2=https, 4=file).
            src_scheme = row["source_scheme"] if "source_scheme" in row.keys() else 0
            scheme_map = src_scheme if src_scheme in (1, 2) else (2 if row["is_secure"] else 1)

            yield {
                "host_key": s(row["host_key"]),
                "name": s(row["name"]),
                "value": value,
                "path": s(row["path"]),
                "expires_us": _chrome_to_unix_us(row["expires_utc"]),
                "creation_us": _chrome_to_unix_us(row["creation_utc"]),
                "last_access_us": _chrome_to_unix_us(row["last_access_utc"]),
                "is_secure": row["is_secure"],
                "is_httponly": row["is_httponly"],
                "samesite": _SAMESITE_MAP.get(row["samesite"], 3),
                "has_expires": row["has_expires"] if "has_expires" in row.keys() else 1,
                "scheme_map": scheme_map,
            }
        if decrypt_fail:
            logger.warning(f"⚠️  Skipped {decrypt_fail} cookies that could not be decrypted")
        if decoded:
            logger.info(f"🔓 Decrypted {decoded} encrypted cookie values")

    def _resolve_key_and_decrypt_fn(self) -> Tuple[bytes, Callable[[bytes, bytes], Optional[bytes]], Optional[str]]:
        """Return ``(key, decrypt_fn, None)`` on success or ``(b"", noop, error_code)``.

        Hides macOS Keychain vs Windows DPAPI behind a single dispatch.
        """
        if sys.platform == "darwin":
            password = _read_keychain_password()
            if password is None:
                return b"", _decrypt_v10_cbc, "keychain_denied"
            return _derive_aes_key_macos(password), _decrypt_v10_cbc, None

        if os.name == "nt":
            try:
                key = _read_local_state_key_windows()
            except _DpapiError as exc:
                logger.error(f"DPAPI key unwrap failed: {exc} ({exc.code})")
                return b"", _decrypt_v10_gcm, exc.code
            return key, _decrypt_v10_gcm, None

        return b"", _decrypt_v10_cbc, "unsupported_platform"

    def import_cookies(self) -> dict:
        result = {"read": 0, "imported": 0, "merged": 0, "skipped": 0}

        if not self.zen_cookies.exists():
            logger.error(f"Zen cookies.sqlite not found at {self.zen_cookies}")
            result["error"] = "cookies_db_missing"
            return result

        key, decrypt_fn, err = self._resolve_key_and_decrypt_fn()
        if err:
            result["error"] = err
            return result

        cookies: list[dict] = []
        try:
            for arc_db in _arc_cookie_dbs():
                logger.info(f"📖 Reading Arc cookies from {arc_db.parent.name}")
                snap = self._snapshot(arc_db)
                conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
                # encrypted_value is binary; some Chromium builds also stash
                # bytes in TEXT columns. Tell sqlite3 not to UTF-8-decode anything,
                # and we'll decode the str fields ourselves.
                conn.text_factory = bytes
                conn.row_factory = sqlite3.Row
                try:
                    cur = conn.cursor()
                    cur.execute(
                        """SELECT host_key, name, value, encrypted_value, path,
                                  expires_utc, creation_utc, last_access_utc,
                                  is_secure, is_httponly, samesite,
                                  has_expires, source_scheme
                           FROM cookies"""
                    )
                    rows = cur.fetchall()
                    result["read"] += len(rows)
                    cookies.extend(self._decrypt_rows(rows, key, decrypt_fn))
                finally:
                    conn.close()
        finally:
            self._cleanup()

        logger.info(f"🔍 Decoded {len(cookies)} of {result['read']} cookies from Arc")
        if not cookies:
            return result

        if self.dry_run:
            result["dry_run"] = True
            result["imported"] = len(cookies)
            return result

        backup = self.zen_cookies.with_name(f"{self.zen_cookies.name}.backup.{int(time.time())}")
        shutil.copy2(self.zen_cookies, backup)
        logger.info(f"💾 Backed up cookies.sqlite → {backup.name}")

        conn = sqlite3.connect(self.zen_cookies, timeout=30.0)
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            now_us = int(time.time() * 1_000_000)
            now_ms = now_us // 1000
            session_expiry_ms = now_ms + self.SESSION_COOKIE_DAYS * 86400 * 1000

            # Targets: default context (empty) plus every container we were told about.
            origin_targets = [""] + [f"^userContextId={cid}" for cid in self.container_ids]

            for c in cookies:
                # Modern Firefox stores `expiry` as MILLISECONDS since epoch
                # (legacy was seconds; switched ~v108). Surviving native cookies
                # in this profile confirmed the new format.
                if not c["has_expires"] or not c["expires_us"]:
                    expiry_ms = session_expiry_ms
                else:
                    expiry_ms = c["expires_us"] // 1000

                host = c["host_key"]
                last_accessed_us = c["last_access_us"] or now_us
                creation_us = c["creation_us"] or now_us
                for oa in origin_targets:
                    try:
                        cur.execute(
                            """INSERT INTO moz_cookies
                               (originAttributes, name, value, host, path, expiry,
                                lastAccessed, creationTime, isSecure, isHttpOnly,
                                sameSite, schemeMap, updateTime)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                oa,
                                c["name"],
                                c["value"],
                                host,
                                c["path"],
                                expiry_ms,
                                last_accessed_us,
                                creation_us,
                                int(bool(c["is_secure"])),
                                int(bool(c["is_httponly"])),
                                c["samesite"],
                                c["scheme_map"],
                                last_accessed_us,
                            ),
                        )
                        result["imported"] += 1
                    except sqlite3.IntegrityError:
                        cur.execute(
                            """UPDATE moz_cookies
                                  SET value = ?, expiry = ?, lastAccessed = ?,
                                      isSecure = ?, isHttpOnly = ?, sameSite = ?,
                                      schemeMap = ?, updateTime = ?
                               WHERE originAttributes = ? AND name = ?
                                 AND host = ? AND path = ?""",
                            (
                                c["value"],
                                expiry_ms,
                                last_accessed_us,
                                int(bool(c["is_secure"])),
                                int(bool(c["is_httponly"])),
                                c["samesite"],
                                c["scheme_map"],
                                last_accessed_us,
                                oa,
                                c["name"],
                                host,
                                c["path"],
                            ),
                        )
                        result["merged"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(
            f"✅ Cookies: imported {result['imported']}, merged {result['merged']} "
            f"across {len(origin_targets)} origin contexts"
        )
        return result


def _discover_user_containers(zen_profile: Path) -> list[int]:
    """Read containers.json and return userContextIds for non-internal identities."""
    import json
    cf = zen_profile / "containers.json"
    if not cf.exists():
        return []
    try:
        data = json.loads(cf.read_text())
    except Exception:
        return []
    out: list[int] = []
    for identity in data.get("identities", []):
        cid = identity.get("userContextId", 0)
        # Skip internal (l10nID starting with userContextIdInternal) and the
        # default (0) and the 4294967295 sentinel.
        l10n = identity.get("l10nID", "") or ""
        if cid and cid < 1_000_000 and not l10n.startswith("userContextIdInternal"):
            out.append(cid)
    return sorted(set(out))


def main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Import Arc cookies into Zen")
    parser.add_argument("--zen-profile", help="Zen profile name (partial match)")
    parser.add_argument(
        "--containers",
        help="Comma-separated userContextIds to also populate (so cookies work "
             "in container tabs). Pass 'auto' to read containers.json and use "
             "every non-internal identity. Default: auto.",
        default="auto",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    profiles_root = Path.home() / "Library/Application Support/zen/Profiles"
    profiles = [p for p in profiles_root.iterdir() if p.is_dir()] if profiles_root.exists() else []
    if args.zen_profile:
        profiles = [p for p in profiles if args.zen_profile.lower() in p.name.lower()]
    if not profiles:
        logger.error("No matching Zen profile found")
        return 1
    zen_profile = profiles[0]
    logger.info(f"Using Zen profile: {zen_profile.name}")

    container_ids: list[int] = []
    if args.containers == "auto":
        container_ids = _discover_user_containers(zen_profile)
    elif args.containers:
        container_ids = [int(x) for x in args.containers.split(",") if x.strip().isdigit()]
    if container_ids:
        logger.info(f"Will populate containers: {container_ids}")

    importer = CookiesImporter(zen_profile, dry_run=args.dry_run, container_ids=container_ids)
    summary = importer.import_cookies()
    logger.info(f"Summary: {summary}")
    return 0 if not summary.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
