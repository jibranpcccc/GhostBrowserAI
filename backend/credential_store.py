"""OS-protected storage for Cloudflare account credentials.

On Windows, payloads are encrypted with DPAPI and can only be decrypted by the
same Windows user.  Plaintext account files are supported only by the explicit
migration command, never by the normal application startup path.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Iterable

from backend.config import get_data_dir


STORE_VERSION = 1
DEFAULT_STORE_PATH = Path(get_data_dir("api_data")) / "cloudflare_accounts.secure.json"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_encrypt(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Secure credential storage currently requires Windows DPAPI")
    source, source_buffer = _blob(data)
    output = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "GhostBrowser credentials", None, None, None, 0,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _dpapi_decrypt(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Secure credential storage currently requires Windows DPAPI")
    source, source_buffer = _blob(data)
    output = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def save_accounts(accounts: Iterable[dict], path: Path | str | None = None) -> int:
    destination = Path(path) if path is not None else DEFAULT_STORE_PATH
    normalized = []
    seen = set()
    for account in accounts:
        account_id = str(account.get("account_id", "")).strip()
        token = str(account.get("token", "")).strip()
        if not account_id or not token or account_id in seen:
            continue
        seen.add(account_id)
        normalized.append({
            "account_id": account_id,
            "token": token,
            "priority": bool(account.get("priority")),
        })
    payload = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    envelope = {
        "version": STORE_VERSION,
        "provider": "windows-dpapi-user",
        "payload": base64.b64encode(_dpapi_encrypt(payload)).decode("ascii"),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return len(normalized)


def load_accounts(path: Path | str | None = None) -> list[dict]:
    source = Path(path) if path is not None else DEFAULT_STORE_PATH
    if not source.exists():
        return []
    envelope = json.loads(source.read_text(encoding="utf-8"))
    if envelope.get("version") != STORE_VERSION:
        raise RuntimeError("Unsupported secure credential-store version")
    encrypted = base64.b64decode(envelope["payload"], validate=True)
    accounts = json.loads(_dpapi_decrypt(encrypted).decode("utf-8"))
    if not isinstance(accounts, list):
        raise RuntimeError("Secure credential store contains invalid data")
    return accounts


def store_status(path: Path | str | None = None) -> dict:
    source = Path(path) if path is not None else DEFAULT_STORE_PATH
    if not source.exists():
        return {"configured": False, "provider": "windows-dpapi-user", "count": 0}
    accounts = load_accounts(source)
    return {"configured": True, "provider": "windows-dpapi-user", "count": len(accounts)}
