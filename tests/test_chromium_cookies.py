"""Tests for Chromium cookie decryption and import on Linux."""

from __future__ import annotations

import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from chromium_cookies_importer import (
    CookiesImporter,
    _decrypt_v10_v11_linux,
    _derive_aes_key_linux,
    _read_kwallet_password,
    _read_linux_password,
    _read_secret_service_password,
    _strip_host_hash,
)
from extractors.base import BrowserExtractorError
from extractors.brave import BraveExtractor


def _encrypt_cbc(pt: bytes, key: bytes, prefix: bytes = b"v11") -> bytes:
    """Helper to produce Chromium-like AES-128-CBC encrypted cookie payload."""
    pad_len = 16 - (len(pt) % 16)
    padded = pt + bytes([pad_len]) * pad_len
    iv = b" " * 16
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    return prefix + enc.update(padded) + enc.finalize()


def test_derive_aes_key_linux():
    key1 = _derive_aes_key_linux("peanuts")
    assert len(key1) == 16
    assert isinstance(key1, bytes)

    # Deterministic check
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=16,
        salt=b"saltysalt",
        iterations=1,
    )
    expected = kdf.derive(b"peanuts")
    assert key1 == expected


def test_decrypt_v10_v11_linux():
    secret_key = _derive_aes_key_linux("my_secret_password")
    peanuts_key = _derive_aes_key_linux("peanuts")

    # 1. v11 cookie encrypted with secret_key
    payload_v11 = _encrypt_cbc(b"session_token_12345", secret_key, prefix=b"v11")
    decrypted = _decrypt_v10_v11_linux(payload_v11, secret_key)
    assert decrypted == b"session_token_12345"

    # 2. v10 cookie encrypted with peanuts_key
    payload_v10 = _encrypt_cbc(b"fallback_session_abcde", peanuts_key, prefix=b"v10")
    # Even if primary key is secret_key, v10 decrypts with peanuts key
    decrypted_v10 = _decrypt_v10_v11_linux(payload_v10, secret_key)
    assert decrypted_v10 == b"fallback_session_abcde"

    # 3. Invalid prefix or length
    assert _decrypt_v10_v11_linux(b"v20_something_else", secret_key) is None
    assert _decrypt_v10_v11_linux(b"v11short", secret_key) is None


def test_strip_host_hash():
    # 1. Cookie with 32-byte non-printable SHA256 prefix + ASCII value
    host_hash = b"\x01" * 32
    cookie_value = b"test_value_123"
    assert _strip_host_hash(host_hash + cookie_value) == cookie_value

    # 2. Empty cookie value: exactly 32-byte non-printable host hash
    assert _strip_host_hash(host_hash) == b""

    # 3. Regular string without prefix
    plain = b"regular_cookie_value"
    assert _strip_host_hash(plain) == plain

    # 4. Short non-printable
    short = b"\x01\x02\x03"
    assert _strip_host_hash(short) == short


def test_read_secret_service_password_success(monkeypatch):
    class MockResult:
        returncode = 0
        stdout = "mock_secret_key_123\n"
        stderr = ""

    def mock_run(cmd, **kwargs):
        if "secret-tool" in cmd and "brave" in cmd:
            return MockResult()
        return MagicMock(returncode=1, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)
    pwd = _read_secret_service_password(["brave"])
    assert pwd == "mock_secret_key_123"


def test_read_secret_service_password_missing(monkeypatch):
    def mock_run(cmd, **kwargs):
        return MagicMock(returncode=1, stdout="", stderr="not found")

    monkeypatch.setattr("subprocess.run", mock_run)
    # Ensure gi does not interfere if present
    with patch.dict(sys.modules, {"gi": None, "gi.repository": None}):
        pwd = _read_secret_service_password(["nonexistent_browser"])
        assert pwd is None


def test_read_kwallet_password_success(monkeypatch):
    class MockResult:
        returncode = 0
        stdout = "kwallet_secret_key_456\n"
        stderr = ""

    def mock_run(cmd, **kwargs):
        if "kwallet-query" in cmd and "Brave Keys" in cmd:
            return MockResult()
        return MagicMock(returncode=1, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)
    pwd = _read_kwallet_password(["brave"])
    assert pwd == "kwallet_secret_key_456"


def test_read_kwallet_password_dbus_fallback(monkeypatch):
    # Simulate kwallet-query not found
    def mock_run(cmd, **kwargs):
        raise FileNotFoundError("kwallet-query not found")

    monkeypatch.setattr("subprocess.run", mock_run)

    # Mock dbus module
    mock_dbus = MagicMock()
    mock_bus = MagicMock()
    mock_dbus.SessionBus.return_value = mock_bus
    mock_bus.name_has_owner.side_effect = lambda name: name == "org.kde.kwalletd6"

    mock_iface = MagicMock()
    mock_iface.networkWallet.return_value = "kdewallet"
    mock_iface.open.return_value = 42
    mock_iface.hasFolder.return_value = True
    mock_iface.hasEntry.return_value = True
    mock_iface.readPassword.return_value = "dbus_kwallet_secret_789"
    mock_dbus.Interface.return_value = mock_iface

    monkeypatch.setitem(sys.modules, "dbus", mock_dbus)

    pwd = _read_kwallet_password(["brave"])
    assert pwd == "dbus_kwallet_secret_789"
    mock_iface.close.assert_called_once_with(42, False, "browser2zen")


def test_read_linux_password_fallback_to_kwallet(monkeypatch):
    monkeypatch.setattr("chromium_cookies_importer._read_secret_service_password", lambda candidates: None)
    monkeypatch.setattr("chromium_cookies_importer._read_kwallet_password", lambda candidates: "kwallet_val")
    assert _read_linux_password(["brave"]) == "kwallet_val"


def test_cookies_importer_resolve_key_linux_v11_missing_key(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("chromium_cookies_importer._read_linux_password", lambda *a, **kw: None)

    # Create a source DB containing a v11 cookie
    src_db = tmp_path / "Cookies"
    conn = sqlite3.connect(src_db)
    conn.execute("CREATE TABLE cookies (encrypted_value BLOB)")
    conn.execute("INSERT INTO cookies VALUES (?)", (b"v11some_ciphertext_data",))
    conn.commit()
    conn.close()

    importer = CookiesImporter(
        zen_profile=tmp_path,
        cookie_dbs=[src_db],
        linux_app_names=["brave"],
    )
    key, fn, err = importer._resolve_key_and_decrypt_fn()
    assert err == "secret_service_missing"
    assert key == b""


def test_cookies_importer_resolve_key_linux_v10_peanuts_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("chromium_cookies_importer._read_linux_password", lambda *a, **kw: None)

    # Create a source DB containing only v10 cookies
    src_db = tmp_path / "Cookies"
    conn = sqlite3.connect(src_db)
    conn.execute("CREATE TABLE cookies (encrypted_value BLOB)")
    conn.execute("INSERT INTO cookies VALUES (?)", (b"v10some_ciphertext_data",))
    conn.commit()
    conn.close()

    importer = CookiesImporter(
        zen_profile=tmp_path,
        cookie_dbs=[src_db],
        linux_app_names=["brave"],
    )
    key, fn, err = importer._resolve_key_and_decrypt_fn()
    assert err is None
    assert key == _derive_aes_key_linux("peanuts")


def test_cookies_importer_end_to_end_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    secret_pwd = "test_password_for_brave"
    secret_key = _derive_aes_key_linux(secret_pwd)
    monkeypatch.setattr("chromium_cookies_importer._read_linux_password", lambda *a, **kw: secret_pwd)

    # 1. Prepare source Chromium Cookies SQLite database
    src_db = tmp_path / "Cookies"
    conn = sqlite3.connect(src_db)
    conn.execute(
        """CREATE TABLE cookies (
            host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT,
            expires_utc INTEGER, creation_utc INTEGER, last_access_utc INTEGER,
            is_secure INTEGER, is_httponly INTEGER, samesite INTEGER,
            has_expires INTEGER, source_scheme INTEGER
        )"""
    )

    host_tag = b"\x02" * 32
    enc_value1 = _encrypt_cbc(host_tag + b"secret_token_val", secret_key, prefix=b"v11")
    enc_empty = _encrypt_cbc(host_tag, secret_key, prefix=b"v11")

    # Insert one cookie with value and one empty cookie
    conn.execute(
        """INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (".example.com", "auth_tok", "", enc_value1, "/", 13350000000000000, 13340000000000000, 13345000000000000, 1, 1, 1, 1, 2),
    )
    conn.execute(
        """INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (".example.com", "empty_tok", "", enc_empty, "/", 13350000000000000, 13340000000000000, 13345000000000000, 1, 0, 0, 1, 2),
    )
    conn.commit()
    conn.close()

    # 2. Prepare Zen profile with cookies.sqlite
    zen_profile = tmp_path / "zen_profile"
    zen_profile.mkdir()
    zen_cookies_db = zen_profile / "cookies.sqlite"
    zconn = sqlite3.connect(zen_cookies_db)
    zconn.execute(
        """CREATE TABLE moz_cookies (
            id INTEGER PRIMARY KEY,
            originAttributes TEXT NOT NULL DEFAULT '',
            name TEXT,
            value TEXT,
            host TEXT,
            path TEXT,
            expiry INTEGER,
            lastAccessed INTEGER,
            creationTime INTEGER,
            isSecure INTEGER,
            isHttpOnly INTEGER,
            sameSite INTEGER,
            schemeMap INTEGER,
            updateTime INTEGER,
            CONSTRAINT moz_uniqueid UNIQUE (originAttributes, name, host, path)
        )"""
    )
    zconn.commit()
    zconn.close()

    # 3. Run CookiesImporter
    importer = CookiesImporter(
        zen_profile=zen_profile,
        cookie_dbs=[src_db],
        linux_app_names=["brave"],
    )
    res = importer.import_cookies()
    assert "error" not in res
    assert res["read"] == 2
    assert res["imported"] == 2

    # 4. Verify in Zen cookies.sqlite
    zconn = sqlite3.connect(zen_cookies_db)
    rows = zconn.execute("SELECT name, value, host, isSecure, isHttpOnly FROM moz_cookies ORDER BY name").fetchall()
    zconn.close()

    assert len(rows) == 2
    assert rows[0] == ("auth_tok", "secret_token_val", ".example.com", 1, 1)
    assert rows[1] == ("empty_tok", "", ".example.com", 1, 0)


def test_brave_extractor_cookie_master_key_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("chromium_cookies_importer._read_linux_password", lambda candidates: "brave_secret")

    ext = BraveExtractor()
    key = ext.cookie_master_key()
    assert key == _derive_aes_key_linux("brave_secret")


def test_brave_extractor_cookie_master_key_missing_v11(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("chromium_cookies_importer._read_linux_password", lambda candidates: None)

    ext = BraveExtractor()
    # Mock cookie_db_paths returning a DB with v11 cookies
    src_db = tmp_path / "Cookies"
    conn = sqlite3.connect(src_db)
    conn.execute("CREATE TABLE cookies (encrypted_value BLOB)")
    conn.execute("INSERT INTO cookies VALUES (?)", (b"v11some_data",))
    conn.commit()
    conn.close()

    monkeypatch.setattr(ext, "cookie_db_paths", lambda: [src_db])

    with pytest.raises(BrowserExtractorError) as exc_info:
        ext.cookie_master_key()
    assert exc_info.value.code == "secret_service_missing"
