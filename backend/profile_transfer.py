"""
Profile Transfer module — import / export profiles as JSON or CSV.
Supports encrypted export and "safe" mode that strips proxy credentials.
"""

import base64
import csv
import io
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from pydantic import BaseModel

from backend.auth import require_admin_token
from backend.config import get_data_dir
from backend.logging_config import logger
from backend.profile_manager import profile_manager, PROFILES_DIR, KEY_FILE

try:
    from cryptography.fernet import Fernet
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

EXPORT_DIR = os.path.join(get_data_dir("profiles_data"), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

# Lazily cached module-level cipher. Replaces per-instance Fernet creation.
_CIPHER = None


def _get_cipher():
    global _CIPHER
    if _CIPHER is not None or not _HAS_CRYPTO:
        return _CIPHER
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            _CIPHER = Fernet(f.read())
    return _CIPHER


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class ExportRequest(BaseModel):
    profile_ids: List[str] = []
    encrypt: bool = True
    safe_mode: bool = True


class ImportRequest(BaseModel):
    data: str = ""        # base64-encoded JSON payload
    encrypted: bool = False
    overwrite: bool = False


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
async def export_profiles(profile_ids: List[str] = None,
                          encrypt: bool = True,
                          safe_mode: bool = True) -> Dict[str, Any]:
    """
    Export the given profile IDs (or all if empty) as a dict payload.
    When *encrypt* is True the full payload is encrypted with Fernet
    and returned as {"encrypted": True, "data": "<b64>"}.
    When *safe_mode* is True, proxy credentials are stripped.
    """
    all_profiles = getattr(profile_manager, "profiles", {})
    ids = profile_ids if profile_ids else list(all_profiles.keys())

    cipher = _get_cipher()
    effective_safe_mode = safe_mode or not (encrypt and cipher)
    exported: List[Dict[str, Any]] = []
    for pid in ids:
        pdata = all_profiles.get(pid)
        if not pdata:
            logger.warning(f"Export: profile {pid} not found, skipping")
            continue

        entry = await _extract_profile(pid, pdata, effective_safe_mode)
        exported.append(entry)

    payload = {
        "format_version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat() + "Z",
        "safe_mode": effective_safe_mode,
        "profile_count": len(exported),
        "profiles": exported,
    }

    if encrypt and cipher:
        raw = json.dumps(payload).encode()
        token = cipher.encrypt(raw)
        return {
            "encrypted": True,
            "data": base64.b64encode(token).decode(),
        }

    return {"encrypted": False, "data": payload}


async def _extract_profile(pid: str, pdata: Dict[str, Any],
                           safe_mode: bool) -> Dict[str, Any]:
    """Build a clean export dict for a single profile."""
    proxy = pdata.get("proxy")
    if safe_mode and isinstance(proxy, dict):
        proxy = {
            "server": proxy.get("server", ""),
            "type": proxy.get("type", ""),
        }
    elif safe_mode:
        proxy = None

    advanced = pdata.get("advanced")
    if isinstance(advanced, dict):
        advanced = dict(advanced)
        if safe_mode:
            for key in list(advanced):
                lowered = key.lower()
                if any(term in lowered for term in ("token", "secret", "password", "account_id")):
                    advanced.pop(key, None)

    return {
        "id": pid,
        "name": pdata.get("name", ""),
        "proxy": proxy,
        "timezone": pdata.get("timezone"),
        "locale": pdata.get("locale"),
        "advanced": advanced if isinstance(advanced, dict) else {},
        "fingerprint": pdata.get("fingerprint", {}),
        "created_at": pdata.get("created_at"),
        "cookies": [] if safe_mode else await _get_cookies(pid),
    }


async def _get_cookies(profile_id: str) -> List[Dict[str, Any]]:
    """Attempt to retrieve cookies for a profile. Best-effort."""
    try:
        from backend.browser_manager import get_profile_cookies, is_profile_running
        if not is_profile_running(profile_id):
            return []
        cookies = await get_profile_cookies(profile_id)
        return cookies if cookies else []
    except Exception:
        return []


async def export_to_file(profile_ids: List[str] = None,
                         encrypt: bool = True,
                         safe_mode: bool = True) -> str:
    """Export to a file in EXPORT_DIR and return the file path."""
    payload = await export_profiles(profile_ids, encrypt, safe_mode)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ext = "enc" if payload.get("encrypted") else "json"
    filename = f"profiles_export_{ts}.{ext}"
    filepath = os.path.join(EXPORT_DIR, filename)

    if payload.get("encrypted"):
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(payload["data"]))
    else:
        with open(filepath, "w") as f:
            json.dump(payload["data"], f, indent=2)

    logger.info(f"Profiles exported to {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def import_profiles(data: Any,
                    encrypted: bool = False,
                    overwrite: bool = False) -> Dict[str, Any]:
    """
    Import profiles from a dict payload or base64-encoded encrypted blob.
    Returns summary stats.
    """
    cipher = _get_cipher()
    if encrypted and cipher:
        token = base64.b64decode(data)
        raw = cipher.decrypt(token)
        payload = json.loads(raw)
    else:
        payload = data if isinstance(data, dict) else json.loads(data)

    if not isinstance(payload, dict) or "profiles" not in payload:
        raise ValueError("Invalid import payload: missing 'profiles' key")

    imported = 0
    skipped = 0
    errors: List[str] = []

    for prof in payload["profiles"]:
        try:
            result = _import_single(prof, overwrite)
            if result:
                imported += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append(f"{prof.get('id', '?')}: import failed")
            logger.error(f"Import error for {prof.get('id')}: {exc}")

    logger.info(f"Import complete: {imported} imported, {skipped} skipped, {len(errors)} errors")
    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }


def _import_single(prof: Dict[str, Any], overwrite: bool) -> bool:
    """Import a single profile dict. Returns True if imported, False if skipped."""
    pid = prof.get("id")
    all_profiles = getattr(profile_manager, "profiles", {})

    if pid and pid in all_profiles and not overwrite:
        logger.info(f"Profile {pid} already exists, skipping (overwrite=False)")
        return False

    if not pid:
        pid = str(uuid.uuid4())

    advanced = prof.get("advanced", {})
    if not isinstance(advanced, dict):
        advanced = {}

    advanced = advanced.copy()

    if "webrtc_mode" not in advanced:
        advanced["webrtc_mode"] = "protected"
    else:
        webrtc_raw = advanced["webrtc_mode"]
        if not isinstance(webrtc_raw, str):
            raise ValueError("WebRTC mode must be a string")
        webrtc_raw = webrtc_raw.strip().lower()
        if not webrtc_raw:
            raise ValueError("WebRTC mode cannot be empty")
        if webrtc_raw in ["altered", "protected"]:
            advanced["webrtc_mode"] = "protected"
        else:
            raise ValueError(f"Invalid WebRTC mode in imported profile: {webrtc_raw}")

    imported_fingerprint = prof.get("fingerprint", {})
    if not isinstance(imported_fingerprint, dict):
        imported_fingerprint = {}
    else:
        imported_fingerprint = json.loads(json.dumps(imported_fingerprint))
        imported_fingerprint.pop("_source", None)
        imported_fingerprint.pop("_provenance", None)
        imported_fingerprint["_is_fallback"] = True

    profile_path = os.path.abspath(os.path.join(PROFILES_DIR, pid))

    entry: Dict[str, Any] = {
        "name": prof.get("name", f"Imported-{pid[:8]}"),
        "path": profile_path,
        "proxy": prof.get("proxy"),
        "timezone": prof.get("timezone"),
        "locale": prof.get("locale"),
        "advanced": advanced,
        "fingerprint": imported_fingerprint,
        "verification_status": "unverified",
        "ai_provenance": {
            "verified": False,
            "source": "profile_import",
            "validation_status": "unverified_import",
            "requested_model": None,
            "reported_model": None,
        },
        "created_at": prof.get("created_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    all_profiles[pid] = entry
    created_directory = not os.path.exists(profile_path)
    os.makedirs(profile_path, exist_ok=True)
    try:
        profile_manager._save_metadata()
    except Exception:
        all_profiles.pop(pid, None)
        if created_directory:
            try:
                os.rmdir(profile_path)
            except OSError:
                pass
        raise

    cookies = prof.get("cookies")
    if cookies:
        try:
            from backend.browser_manager import set_profile_cookies
            set_profile_cookies(pid, cookies)
        except Exception as exc:
            logger.warning(f"Could not set cookies for {pid}: {exc}")

    return True


def import_from_csv(csv_content: str,
                    overwrite: bool = False) -> Dict[str, Any]:
    """
    Bulk-import from CSV.
    Expected columns: name,proxy_server,proxy_username,proxy_password,
                      timezone,locale,os,cpu_cores,memory_gb,webrtc_mode
    """
    reader = csv.DictReader(io.StringIO(csv_content))
    imported = 0
    skipped = 0
    errors: List[str] = []

    for row in reader:
        try:
            proxy = None
            if row.get("proxy_server"):
                proxy = {
                    "server": row["proxy_server"],
                    "username": row.get("proxy_username", ""),
                    "password": row.get("proxy_password", ""),
                }
            if "webrtc_mode" not in row:
                webrtc_mode = "protected"
            else:
                webrtc_raw = row["webrtc_mode"]
                if webrtc_raw is None:
                    webrtc_raw = ""
                if not isinstance(webrtc_raw, str):
                    raise ValueError("WebRTC mode must be a string")
                webrtc_raw = webrtc_raw.strip().lower()
                if not webrtc_raw:
                    raise ValueError("WebRTC mode cannot be empty")
                if webrtc_raw in ["altered", "protected"]:
                    webrtc_mode = "protected"
                else:
                    raise ValueError(f"Invalid WebRTC mode in CSV: {webrtc_raw}")

            advanced = {
                "os": row.get("os", "Windows"),
                "cpu_cores": int(row.get("cpu_cores", 4)),
                "memory_gb": int(row.get("memory_gb", 8)),
                "webrtc_mode": webrtc_mode,
            }
            prof = {
                "name": row.get("name", f"CSV-Import-{imported+1}"),
                "proxy": proxy,
                "timezone": row.get("timezone"),
                "locale": row.get("locale"),
                "advanced": advanced,
                "fingerprint": {},
                "cookies": [],
            }
            result = _import_single(prof, overwrite)
            if result:
                imported += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append(f"row {imported+skipped+1}: {exc}")

    logger.info(f"CSV import: {imported} imported, {skipped} skipped, {len(errors)} errors")
    return {"imported": imported, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Backwards-compatible wrapper
# ponytail: kept because existing tests instantiate ProfileTransfer directly.
# All state and behavior have moved to the module-level functions above.
# ---------------------------------------------------------------------------
class ProfileTransfer:
    """Export and import browser profiles with optional encryption."""

    export_profiles = staticmethod(export_profiles)
    export_to_file = staticmethod(export_to_file)
    import_profiles = staticmethod(import_profiles)
    import_profile = staticmethod(import_profiles)
    import_from_csv = staticmethod(import_from_csv)
    _import_single = staticmethod(_import_single)

    @staticmethod
    def export_profile(profile_id: str,
                       encrypt: bool = True,
                       safe_mode: bool = True):
        return export_profiles([profile_id] if profile_id else [], encrypt, safe_mode)


profile_transfer = ProfileTransfer()


# ---------------------------------------------------------------------------
# API router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/profiles", tags=["transfer"])


@router.post("/export")
async def export_profiles_endpoint(payload: ExportRequest, _auth: None = Depends(require_admin_token)):
    """Export profiles as JSON (optionally encrypted)."""
    result = await export_profiles(
        profile_ids=payload.profile_ids,
        encrypt=payload.encrypt,
        safe_mode=payload.safe_mode,
    )
    return result


@router.post("/import")
def import_profiles_endpoint(payload: ImportRequest, _auth: None = Depends(require_admin_token)):
    """Import profiles from JSON (optionally encrypted)."""
    try:
        if payload.encrypted:
            result = import_profiles(
                payload.data, encrypted=True, overwrite=payload.overwrite
            )
        else:
            data = json.loads(payload.data) if isinstance(payload.data, str) else payload.data
            result = import_profiles(data, overwrite=payload.overwrite)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Import failed: {exc}")
        raise HTTPException(status_code=500, detail="Import failed")


@router.post("/import/csv")
async def import_csv_endpoint(file: UploadFile = File(...), overwrite: bool = False, _auth: None = Depends(require_admin_token)):
    """Bulk import profiles from a CSV file."""
    content = (await file.read()).decode("utf-8")
    result = import_from_csv(content, overwrite=overwrite)
    return result


@router.get("/export/file")
async def export_to_file_endpoint(encrypt: bool = True, safe_mode: bool = True, _auth: None = Depends(require_admin_token)):
    """Export all profiles to a file and return the file path."""
    filepath = await export_to_file(
        profile_ids=[], encrypt=encrypt, safe_mode=safe_mode
    )
    return {"file": filepath}
