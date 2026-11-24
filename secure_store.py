# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Henrique Magalhaes
"""Privacy helpers for SenseMaker: DPAPI history and log redaction."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
from ctypes import wintypes
from pathlib import Path
from typing import Any

HISTORY_MAGIC = b"SMH1"
HISTORY_FILENAME = "transcript_history.dat"
LEGACY_HISTORY_FILENAME = "transcript_history.json"
SETTINGS_FILENAME = "settings.json"
DPAPI_DESCRIPTION = "SenseMaker transcript history"


class SecureStoreError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob_from_bytes(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)),
    )
    return blob, buffer


def _blob_bytes(blob: DATA_BLOB) -> bytes:
    if not blob.pbData or not blob.cbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect_bytes(data: bytes, entropy: bytes = b"SenseMaker/history/v2") -> bytes:
    """Encrypt bytes with Windows DPAPI for the current Windows user."""
    if os.name != "nt":
        raise SecureStoreError("Windows DPAPI is required to protect transcript history.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    input_blob, _input_buffer = _blob_from_bytes(data)
    entropy_blob, _entropy_buffer = _blob_from_bytes(entropy)
    output_blob = DATA_BLOB()
    try:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            DPAPI_DESCRIPTION,
            ctypes.byref(entropy_blob),
            None,
            None,
            0x1,  # CRYPTPROTECT_UI_FORBIDDEN
            ctypes.byref(output_blob),
        )
        if not ok:
            raise SecureStoreError(
                f"CryptProtectData failed ({ctypes.get_last_error()})."
            )
        return _blob_bytes(output_blob)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def unprotect_bytes(data: bytes, entropy: bytes = b"SenseMaker/history/v2") -> bytes:
    """Decrypt a DPAPI blob created by protect_bytes for this Windows user."""
    if os.name != "nt":
        raise SecureStoreError("Windows DPAPI is required to read transcript history.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    input_blob, _input_buffer = _blob_from_bytes(data)
    entropy_blob, _entropy_buffer = _blob_from_bytes(entropy)
    description = wintypes.LPWSTR()
    output_blob = DATA_BLOB()
    try:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            ctypes.byref(description),
            ctypes.byref(entropy_blob),
            None,
            None,
            0x1,
            ctypes.byref(output_blob),
        )
        if not ok:
            raise SecureStoreError(
                f"CryptUnprotectData failed ({ctypes.get_last_error()}). "
                "History may belong to a different Windows account."
            )
        return _blob_bytes(output_blob)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))


class HistoryStore:
    """Read/write encrypted transcript history with one-way legacy migration."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / HISTORY_FILENAME
        self.legacy_path = self.data_dir / LEGACY_HISTORY_FILENAME
        self.log = logging.getLogger(__name__)

    def _read_encrypted(self) -> dict[str, Any] | None:
        try:
            blob = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SecureStoreError(f"Could not read encrypted history: {exc}") from exc
        if not blob.startswith(HISTORY_MAGIC):
            raise SecureStoreError("Encrypted history file has an unknown format.")
        plaintext = unprotect_bytes(blob[len(HISTORY_MAGIC) :])
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise SecureStoreError("Encrypted history is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise SecureStoreError("Encrypted history has an unexpected structure.")
        return payload

    def _write_encrypted(self, payload: dict[str, Any]) -> None:
        plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        encrypted = protect_bytes(plaintext)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(HISTORY_MAGIC + encrypted)
        os.replace(temporary, self.path)

    def read(self) -> dict[str, Any] | None:
        """Load encrypted history, migrating legacy plaintext if needed.

        The plaintext file is deleted only after the encrypted copy has been
        successfully written and read back.
        """
        try:
            payload = self._read_encrypted()
        except SecureStoreError:
            self.log.exception("Could not read encrypted transcript history")
            raise

        if payload is not None:
            self._remove_legacy_plaintext()
            return payload

        if not self.legacy_path.is_file():
            return None

        try:
            raw = self.legacy_path.read_text(encoding="utf-8")
            legacy = json.loads(raw)
            if not isinstance(legacy, dict):
                raise ValueError("The legacy history is not a JSON object.")
            self._write_encrypted(legacy)
            check = self._read_encrypted()
            if check is None:
                raise SecureStoreError("Encrypted history migration did not produce a file.")
        except Exception:
            self.log.exception("Could not migrate legacy plaintext transcript history")
            raise SecureStoreError(
                "Existing transcript history could not be encrypted. "
                "It was left untouched so no data is lost."
            )

        self._remove_legacy_plaintext()
        self.log.info("Migrated transcript history to DPAPI encryption.")
        return legacy

    def _remove_legacy_plaintext(self) -> None:
        try:
            self.legacy_path.unlink(missing_ok=True)
        except OSError:
            self.log.warning("Could not remove the legacy plaintext history file.")

    def write(self, payload: dict[str, Any]) -> None:
        self._write_encrypted(payload)
        self._remove_legacy_plaintext()

    def clear(self) -> None:
        """Remove every history representation, including legacy plaintext."""
        for path in (self.path, self.legacy_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                self.log.warning("Could not delete history file %s", path)
        try:
            self.path.with_suffix(self.path.suffix + ".tmp").unlink(missing_ok=True)
        except OSError:
            pass

    def exists(self) -> bool:
        return self.path.exists() or self.legacy_path.exists()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)
_WINDOWS_ABS_RE = re.compile(r"(?i)(?<![\w])[a-z]:[\\/][^\r\n\"'<>|:]*")
_UNC_RE = re.compile(r"(?:\\\\|//)[^\r\n\"'<>|:]*")


def _scrub_path(match: re.Match[str]) -> str:
    raw = match.group(0)
    if raw.endswith(("\\", "/")):
        return "[path]"
    stripped = raw.rstrip("\\/")
    if not stripped:
        return "[path]"
    separator = "\\" if "\\" in stripped else "/"
    name = re.split(r"[\\/]+", stripped)[-1]
    if not name or "." not in name:
        return "[path]"
    return f"[path]{separator}{name}"


def redact_text(text: str) -> str:
    """Remove URLs, directory paths, and usernames from log output."""
    if not text:
        return text

    text = _URL_RE.sub("[url]", text)
    text = _WINDOWS_ABS_RE.sub(_scrub_path, text)
    text = _UNC_RE.sub(_scrub_path, text)

    for home in {str(Path.home()), str(Path.home()).replace("\\", "/")}:
        if home:
            text = text.replace(home, "%USERPROFILE%")
    return text


class RedactingFormatter(logging.Formatter):
    """Log formatter that keeps audio directory paths out of diagnostics."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))
