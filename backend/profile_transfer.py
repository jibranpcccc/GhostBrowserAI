"""
Profile Transfer module — import / export profiles as JSON or CSV.
Supports encrypted export and "safe" mode that strips proxy credentials.
"""

import base64
import csv
import io
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger
from backend.profile_manager import profile_manager, PROFILES_DIR, KEY_FILE

# Reuse the same Fernet cipher from ProfileManager
try:
    from cryptography.fernet import Fernet
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

EXPORT_DIR = os.path.join(get_data_dir("profiles_data"), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class ExportRequest(BaseModel):
    profile_ids: List[str] = []
    encrypt: bool = True
    safe_mode: bool = False  # strip proxy credentials


class ImportRequest(BaseModel):
    data: str = ""        # base64-encoded JSON payload
    encrypted: bool = False
    overwrite: bool = False


# ---------------------------------------------------------------------------
# ProfileTransfer
# ---------------------------------------------------------------------------
class ProfileTransfer:
    """Export and import browser profiles with optional encryption."""

    def __init__(self):
        self.cipher = None
        if _HAS_CRYPTO and os.path.exists(KEY_FILE):
            with open(KEY_FILE, "rb") as f:
                key = f.read()
            self.cipher = Fernet(key)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_profiles(self, profile_ids: List[str],
                        encrypt: bool = True,
                        safe_mode: bool = False) -> Dict[str, Any]:
        """
        Export the given profile IDs (or all if empty) as a dict payload.
        When *encrypt* is True the full payload is encrypted with Fernet
        and returned as {"encrypted": True, "data": "<b64>"}.
        When *safe_mode* is True, proxy credentials are stripped.
        """
        all_profiles = getattr(profile_manager, "profiles", {})
        ids = profile_ids if profile_ids else list(all_profiles.keys())

        exported: List[Dict[str, Any]] = []
        for pid in ids:
            pdata = all_profiles.get(pid)
            if not pdata:
                logger.warning(f"Export: profile {pid} not found, skipping")
                continue

            entry = self._extract_profile(pid, pdata, safe_mode)
            exported.append(entry)

        payload = {
            "format_version": "1.0",
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "safe_mode": safe_mode,
            "profile_count": len(exported),
            "profiles": exported,
        }

        if encrypt and self.cipher:
            raw = json.dumps(payload).encode()
            token = self.cipher.encrypt(raw)
            return {
                "encrypted": True,
                "data": base64.b64encode(token).decode(),
            }

        return {"encrypted": False, "data": payload}

    def _extract_profile(self, pid: str, pdata: Dict[str, Any],
                         safe_mode: bool) -> Dict[str, Any]:
        """Build a clean export dict for a single profile."""
        proxy = pdata.get("proxy")
        if safe_mode and isinstance(proxy, dict):
            proxy = {
                "server": proxy.get("server", ""),
                "type": proxy.get("type", ""),
                # Strip username/password
            }
        elif safe_mode:
            proxy = None  # proxy stored as encrypted str — skip entirely in safe mode

        advanced = pdata.get("advanced")
        if isinstance(advanced, dict):
            # Already decrypted at runtime
            pass

        return {
            "id": pid,
            "name": pdata.get("name", ""),
            "proxy": proxy,
            "timezone": pdata.get("timezone"),
            "locale": pdata.get("locale"),
            "advanced": advanced if isinstance(advanced, dict) else {},
            "fingerprint": pdata.get("fingerprint", {}),
            "created_at": pdata.get("created_at"),
            "cookies": self._get_cookies(pid),
        }

    def _get_cookies(self, profile_id: str) -> List[Dict[str, Any]]:
        """Attempt to retrieve cookies for a profile. Best-effort."""
        try:
            from backend.browser_manager import get_profile_cookies
            cookies = get_profile_cookies(profile_id)
            return cookies if cookies else []
        except Exception:
            return []

    def export_to_file(self, profile_ids: List[str],
                       encrypt: bool = True,
                       safe_mode: bool = False) -> str:
        """Export to a file in EXPORT_DIR and return the file path."""
        payload = self.export_profiles(profile_ids, encrypt, safe_mode)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
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

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------
    def import_profiles(self, data: Any,
                       encrypted: bool = False,
                       overwrite: bool = False) -> Dict[str, Any]:
        """
        Import profiles from a dict payload or base64-encoded encrypted blob.
        Returns summary stats.
        """
        if encrypted and self.cipher:
            # *data* should be a base64 string of the Fernet token
            token = base64.b64decode(data)
            raw = self.cipher.decrypt(token)
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
                result = self._import_single(prof, overwrite)
                if result:
                    imported += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors.append(f"{prof.get('id', '?')}: {exc}")
                logger.error(f"Import error for {prof.get('id')}: {exc}")

        logger.info(f"Import complete: {imported} imported, {skipped} skipped, {len(errors)} errors")
        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
        }

    def _import_single(self, prof: Dict[str, Any], overwrite: bool) -> bool:
        """Import a single profile dict. Returns True if imported, False if skipped."""
        pid = prof.get("id")
        all_profiles = getattr(profile_manager, "profiles", {})

        if pid and pid in all_profiles and not overwrite:
            logger.info(f"Profile {pid} already exists, skipping (overwrite=False)")
            return False

        # If no ID, generate one
        if not pid:
            import uuid
            pid = str(uuid.uuid4())

        # Build the profile entry mirroring ProfileManager's internal format
        entry: Dict[str, Any] = {
            "name": prof.get("name", f"Imported-{pid[:8]}"),
            "proxy": prof.get("proxy"),
            "timezone": prof.get("timezone"),
            "locale": prof.get("locale"),
            "advanced": prof.get("advanced", {}),
            "fingerprint": prof.get("fingerprint", {}),
            "created_at": prof.get("created_at") or datetime.utcnow().isoformat() + "Z",
        }

        all_profiles[pid] = entry

        # Persist cookies if provided
        cookies = prof.get("cookies")
        if cookies:
            try:
                from backend.browser_manager import set_profile_cookies
                set_profile_cookies(pid, cookies)
            except Exception as exc:
                logger.warning(f"Could not set cookies for {pid}: {exc}")

        return True

    def import_from_csv(self, csv_content: str,
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
                advanced = {
                    "os": row.get("os", "Windows"),
                    "cpu_cores": int(row.get("cpu_cores", 4)),
                    "memory_gb": int(row.get("memory_gb", 8)),
                    "webrtc_mode": row.get("webrtc_mode", "altered"),
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
                result = self._import_single(prof, overwrite)
                if result:
                    imported += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors.append(f"row {imported+skipped+1}: {exc}")

        logger.info(f"CSV import: {imported} imported, {skipped} skipped, {len(errors)} errors")
        return {"imported": imported, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
profile_transfer = ProfileTransfer()


# ---------------------------------------------------------------------------
# API router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/profiles", tags=["transfer"])


@router.post("/export")
def export_profiles_endpoint(payload: ExportRequest):
    """Export profiles as JSON (optionally encrypted)."""
    result = profile_transfer.export_profiles(
        profile_ids=payload.profile_ids,
        encrypt=payload.encrypt,
        safe_mode=payload.safe_mode,
    )
    return result


@router.post("/import")
def import_profiles_endpoint(payload: ImportRequest):
    """Import profiles from JSON (optionally encrypted)."""
    try:
        if payload.encrypted:
            result = profile_transfer.import_profiles(
                payload.data, encrypted=True, overwrite=payload.overwrite
            )
        else:
            data = json.loads(payload.data) if isinstance(payload.data, str) else payload.data
            result = profile_transfer.import_profiles(data, overwrite=payload.overwrite)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Import failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/import/csv")
async def import_csv_endpoint(file: UploadFile = File(...), overwrite: bool = False):
    """Bulk import profiles from a CSV file."""
    content = (await file.read()).decode("utf-8")
    result = profile_transfer.import_from_csv(content, overwrite=overwrite)
    return result


@router.get("/export/file")
def export_to_file_endpoint(encrypt: bool = True, safe_mode: bool = False):
    """Export all profiles to a file and return the file path."""
    filepath = profile_transfer.export_to_file(
        profile_ids=[], encrypt=encrypt, safe_mode=safe_mode
    )
    return {"file": filepath}
