"""Encrypted profile backup/restore and remote sync client.

The cloud-sync design is client-side encryption of profile payloads.
This module implements the local half of that contract (encrypted, gzipped
tarballs) plus a thin remote sync client that talks to the standalone
``sync_server`` over HTTP using only stdlib ``urllib``.

The server only ever receives already-encrypted bytes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.auth import require_admin_token

# AES-GCM is preferred.  If cryptography is missing we fall back to a
# transparent, but insecure, XOR obfuscation layer and warn loudly.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    _HAS_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - dependency fallback
    AESGCM = None  # type: ignore
    _HAS_CRYPTOGRAPHY = False

from backend.profile_manager import profile_manager

logger = logging.getLogger("ghostbrowser.cloud_sync")

router = APIRouter(prefix="/api/cloud-sync", tags=["cloud-sync"])

_PROFILE_BACKUP_META = "__profile__.json"
_SALT_BYTES = 16
_NONCE_BYTES = 12
_PBKDF2_ITERATIONS = 100_000

# Self-describing envelope prefixes so decryption can pick the right path.
_HEADER_AESGCM = b"GBAESGCMv1:"
_HEADER_XORWARN = b"GBXORWARNv1:"


def _derive_key(passphrase: str, salt: bytes, length: int = 32) -> bytes:
    """Derive a key from *passphrase* using PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=length,
    )


def _encrypt(passphrase: str, plaintext: bytes) -> bytes:
    """Encrypt *plaintext* with a key derived from *passphrase*.

    Uses AES-256-GCM and fails closed if ``cryptography`` is not available.
    """
    if not _HAS_CRYPTOGRAPHY:
        raise RuntimeError(
            "cryptography is required for profile encryption. Install it with: pip install cryptography"
        )
    salt = os.urandom(_SALT_BYTES)
    key = _derive_key(passphrase, salt, 32)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return _HEADER_AESGCM + salt + nonce + ciphertext


def _decrypt(passphrase: str, payload: bytes) -> bytes:
    """Decrypt *payload* produced by :func:`_encrypt`."""
    if payload.startswith(_HEADER_AESGCM):
        if not _HAS_CRYPTOGRAPHY:
            raise RuntimeError(
                "Archive was encrypted with AES-GCM but cryptography is not installed"
            )
        rest = payload[len(_HEADER_AESGCM) :]
        salt, nonce, ciphertext = (
            rest[:_SALT_BYTES],
            rest[_SALT_BYTES : _SALT_BYTES + _NONCE_BYTES],
            rest[_SALT_BYTES + _NONCE_BYTES :],
        )
        key = _derive_key(passphrase, salt, 32)
        if len(nonce) != _NONCE_BYTES:
            raise ValueError("Invalid archive")
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise ValueError("Invalid passphrase or corrupted archive") from exc

    if payload.startswith(_HEADER_XORWARN):
        if os.environ.get("GHOSTBROWSER_ALLOW_LEGACY_XOR", "").strip().lower() not in ("1", "true"):
            raise RuntimeError(
                "Legacy XOR-encrypted archive rejected. Set GHOSTBROWSER_ALLOW_LEGACY_XOR=1 to "
                "temporarily enable decryption, then re-encrypt with AES-GCM."
            )
        rest = payload[len(_HEADER_XORWARN) :]
        salt = rest[:_SALT_BYTES]
        obfuscated = rest[_SALT_BYTES:]
        key = _derive_key(passphrase, salt, 32)
        stream = _derive_key(key.hex(), salt, len(obfuscated))
        return bytes(a ^ b for a, b in zip(obfuscated, stream))

    raise ValueError("Unrecognized archive envelope")


def _validate_passphrase(passphrase: str) -> None:
    if not isinstance(passphrase, str) or len(passphrase) < 8:
        raise ValueError("Passphrase must be at least 8 characters")


def _validate_profile_id(profile_id: str) -> None:
    if not isinstance(profile_id, str) or not profile_id:
        raise ValueError("Invalid profile id")
    if any(bad in profile_id for bad in ("..", "/", "\\", os.sep)):
        raise ValueError("Invalid profile id")


def _path_under_profiles(path: str) -> bool:
    """Return True if *path* is contained inside the current profiles dir."""
    base = os.path.normcase(os.path.realpath(profile_manager.PROFILES_DIR))
    target = os.path.normcase(os.path.realpath(path))
    if not target:
        return False
    if target == base:
        return False
    return target.startswith(base + os.sep)


def _sync_dir() -> str:
    """Return the configured local sync directory, or empty string if unset."""
    return os.environ.get("GHOSTBROWSER_SYNC_DIR", "").strip()


def _path_under_sync_dir(path: str, base: str) -> bool:
    """Return True if *path* is contained inside *base* (the sync dir)."""
    base_real = os.path.normcase(os.path.realpath(base))
    target = os.path.normcase(os.path.realpath(path))
    if not target:
        return False
    if target == base_real:
        return False
    return target.startswith(base_real + os.sep)


def _sync_file_path(profile_id: str) -> str:
    """Resolve and validate the on-disk path for *profile_id*."""
    _validate_profile_id(profile_id)
    sync_dir = _sync_dir()
    if not sync_dir:
        raise RuntimeError("GHOSTBROWSER_SYNC_DIR is not configured")
    target = os.path.join(sync_dir, f"{profile_id}.ghost")
    real = os.path.realpath(target)
    if not _path_under_sync_dir(real, sync_dir):
        raise ValueError("Sync target path is outside the configured sync directory")
    return real


class CloudSyncManager:
    """Local encrypted profile backup and restore manager.

    This implementation archives a profile directory into a gzip-compressed
    tar file, encrypts the tarball with AES-256-GCM (or the insecure XOR
    fallback when ``cryptography`` is unavailable), and reverses the process
    during import under a fresh profile id.
    """

    def __init__(self) -> None:
        """Initialize the backup manager."""

    @staticmethod
    def _profile_dir(profile_id: str) -> str:
        return os.path.join(profile_manager.PROFILES_DIR, profile_id)

    def export_profile(self, profile_id: str, passphrase: str) -> bytes:
        """Export *profile_id* as an encrypted, gzipped tarball.

        Args:
            profile_id: The id of the profile to archive.
            passphrase: User-supplied passphrase for encryption. Must be at
                least 8 characters long.

        Returns:
            bytes: The encrypted archive payload.
        """
        _validate_passphrase(passphrase)
        _validate_profile_id(profile_id)

        source_dir = self._profile_dir(profile_id)
        if not os.path.isdir(source_dir):
            raise ValueError(f"Profile directory not found: {profile_id}")
        if not _path_under_profiles(source_dir):
            raise ValueError("Profile directory is outside the profiles folder")

        # Always include the live profile metadata so import can restore it.
        profile_data = profile_manager.get_profile(profile_id)
        metadata_payload = (
            profile_data.encode("utf-8")
            if isinstance(profile_data, str)
            else b""
        )
        if profile_data is None:
            metadata_payload = b""
        elif isinstance(profile_data, dict):
            import json

            metadata_payload = json.dumps(profile_data, separators=(",", ":")).encode("utf-8")

        tarball = io.BytesIO()
        with tarfile.open(fileobj=tarball, mode="w:gz") as tar:
            tar.add(source_dir, arcname=profile_id)
            if metadata_payload:
                meta_info = tarfile.TarInfo(name=f"{profile_id}/{_PROFILE_BACKUP_META}")
                meta_info.size = len(metadata_payload)
                tar.addfile(meta_info, io.BytesIO(metadata_payload))

        tarball.seek(0)
        return _encrypt(passphrase, tarball.read())

    def import_profile(self, archive_bytes: bytes, passphrase: str) -> Dict[str, Any]:
        """Decrypt *archive_bytes* and restore it as a new profile.

        Args:
            archive_bytes: The encrypted archive produced by :meth:`export_profile`.
            passphrase: The passphrase used during export.

        Returns:
            dict: The newly created profile dictionary.
        """
        _validate_passphrase(passphrase)
        if not isinstance(archive_bytes, bytes):
            raise ValueError("Archive must be bytes")

        decrypted = _decrypt(passphrase, archive_bytes)

        # Extract into a temporary staging directory inside PROFILES_DIR so
        # cross-device moves are cheap and stay within the same security boundary.
        staging_dir = tempfile.mkdtemp(dir=profile_manager.PROFILES_DIR)
        try:
            with tarfile.open(fileobj=io.BytesIO(decrypted), mode="r:gz") as tar:
                if hasattr(tarfile, "extraction_filter"):
                    tar.extraction_filter = "data"
                members = tar.getmembers()
                for member in members:
                    # Reject malicious archive entries.
                    if os.path.isabs(member.name) or ".." in member.name.split("/"):
                        continue
                    target = os.path.realpath(os.path.join(staging_dir, member.name))
                    if not _path_under_profiles(target):
                        continue
                    tar.extract(member, staging_dir, numeric_owner=False)

            # Find the metadata file and locate the profile content root.
            meta_path: str | None = None
            root_dir = staging_dir
            for dirpath, _dirnames, filenames in os.walk(staging_dir):
                if _PROFILE_BACKUP_META in filenames:
                    meta_path = os.path.join(dirpath, _PROFILE_BACKUP_META)
                    # The directory containing __profile__.json is the profile root.
                    root_dir = dirpath
                    break

            original_profile = None
            if meta_path and os.path.isfile(meta_path):
                import json

                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        original_profile = json.load(f)
                except (json.JSONDecodeError, OSError):
                    original_profile = None

            new_id = str(uuid.uuid4())
            new_profile_dir = self._profile_dir(new_id)
            if not _path_under_profiles(new_profile_dir):
                raise RuntimeError("Generated profile directory would leave PROFILES_DIR")
            os.makedirs(new_profile_dir, exist_ok=True)

            # Move content from the extracted root into the new profile directory,
            # skipping the embedded metadata file.
            for name in os.listdir(root_dir):
                if name == _PROFILE_BACKUP_META:
                    continue
                src = os.path.join(root_dir, name)
                dst = os.path.join(new_profile_dir, name)
                if not _path_under_profiles(src) or not _path_under_profiles(dst):
                    raise ValueError("Archive entry would escape the profile directory")
                shutil.move(src, dst)

            name = original_profile.get("name", "Imported Profile") if isinstance(original_profile, dict) else "Imported Profile"

            # Register with the profile manager so it shows up in the app.
            profile = profile_manager.register_profile(
                profile_id=new_id,
                name=name,
                proxy=original_profile.get("proxy") if isinstance(original_profile, dict) else None,
                timezone=original_profile.get("timezone") if isinstance(original_profile, dict) else None,
                locale=original_profile.get("locale") if isinstance(original_profile, dict) else None,
                advanced=original_profile.get("advanced") if isinstance(original_profile, dict) else {},
                behavior=original_profile.get("behavior") if isinstance(original_profile, dict) else None,
                fingerprint=original_profile.get("fingerprint") if isinstance(original_profile, dict) else None,
                user_agent=original_profile.get("user_agent") if isinstance(original_profile, dict) else None,
            )

            # Preserve any additional metadata that register_profile didn't copy.
            if isinstance(original_profile, dict):
                preserved = {
                    k: v
                    for k, v in original_profile.items()
                    if k not in {"id", "path"}
                }
                profile_manager.update_profile(new_id, preserved)

            profile = profile_manager.get_profile(new_id)
            # Refresh path to the newly moved directory.
            profile["path"] = os.path.abspath(new_profile_dir)
            profile_manager._save_metadata()
            return profile
        finally:
            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)

    def sync_profile_to_disk(self, profile_id: str, passphrase: str) -> Dict[str, Any]:
        """Export *profile_id* and persist the encrypted archive to the sync dir.

        The archive is written to ``<GHOSTBROWSER_SYNC_DIR>/<profile_id>.ghost``.
        All writes are validated to stay inside the configured sync directory.
        """
        _validate_profile_id(profile_id)
        sync_dir = _sync_dir()
        if not sync_dir:
            raise RuntimeError("GHOSTBROWSER_SYNC_DIR is not configured")

        os.makedirs(sync_dir, exist_ok=True)
        archive = self.export_profile(profile_id, passphrase)
        file_path = _sync_file_path(profile_id)

        with open(file_path, "wb") as f:
            f.write(archive)

        return {
            "profile_id": profile_id,
            "path": file_path,
            "size": len(archive),
        }

    def sync_profile_from_disk(
        self, profile_id_or_archive_b64: str, passphrase: str
    ) -> Dict[str, Any]:
        """Import a profile from the sync dir, falling back to a base64 archive.

        If ``<GHOSTBROWSER_SYNC_DIR>/<profile_id_or_archive_b64>.ghost`` exists,
        it is decrypted and imported.  Otherwise the argument is treated as a
        base64-encoded archive produced by :meth:`export_profile`.
        """
        _validate_passphrase(passphrase)
        if not isinstance(profile_id_or_archive_b64, str):
            raise ValueError("profile_id_or_archive_b64 must be a string")

        sync_dir = _sync_dir()
        if not sync_dir:
            raise RuntimeError("GHOSTBROWSER_SYNC_DIR is not configured")

        if sync_dir:
            try:
                file_path = _sync_file_path(profile_id_or_archive_b64)
                if os.path.isfile(file_path):
                    with open(file_path, "rb") as f:
                        archive = f.read()
                    return self.import_profile(archive, passphrase)
            except ValueError:
                # profile id was rejected by path validation; skip file lookup
                pass

        try:
            archive = base64.b64decode(profile_id_or_archive_b64, validate=True)
        except Exception as exc:
            raise ValueError(
                "No synced archive found and argument is not valid base64"
            ) from exc
        return self.import_profile(archive, passphrase)

    def list_synced_profiles(self) -> List[Dict[str, Any]]:
        """List all ``.ghost`` archives in the configured sync directory."""
        sync_dir = _sync_dir()
        if not sync_dir:
            raise RuntimeError("GHOSTBROWSER_SYNC_DIR is not configured")
        if not os.path.isdir(sync_dir):
            return []

        results: List[Dict[str, Any]] = []
        for name in os.listdir(sync_dir):
            if not name.endswith(".ghost"):
                continue
            profile_id = name[:-6]
            if not profile_id:
                continue
            full_path = os.path.join(sync_dir, name)
            try:
                if not _path_under_sync_dir(full_path, sync_dir):
                    continue
                size = os.path.getsize(full_path)
            except OSError:
                continue
            results.append({
                "profile_id": profile_id,
                "file": name,
                "size": size,
            })
        return results


class CloudSyncClient:
    """Thin stdlib-urllib client for the GhostBrowser sync server.

    The client uploads and downloads **already-encrypted** profile archives.
    It never sends plaintext profile data or user passphrases to the server.
    """

    def __init__(
        self,
        base_url: str,
        tenant_id: str,
        device_id: str,
        token: str,
        timeout: int = 60,
    ):
        parsed_url = urllib.parse.urlsplit(base_url)
        is_development_mode = os.environ.get("GHOSTBROWSER_DEV_MODE") == "1"
        if not parsed_url.scheme or not parsed_url.netloc:
            raise ValueError("Sync server URL must be an absolute HTTPS URL")
        if parsed_url.scheme.lower() != "https" and not is_development_mode:
            raise ValueError(
                "Sync server URL must use HTTPS unless GHOSTBROWSER_DEV_MODE=1"
            )
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.device_id = device_id
        self.token = token
        self.timeout = timeout

    def _url(self, profile_id: str, suffix: str = "") -> str:
        tenant = urllib.parse.quote(self.tenant_id, safe="")
        device = urllib.parse.quote(self.device_id, safe="")
        pid = urllib.parse.quote(profile_id, safe="")
        base = f"{self.base_url}/sync/v1/{tenant}/{pid}/{device}"
        if suffix:
            return f"{base}/{urllib.parse.quote(suffix.lstrip('/'), safe='')}"
        return base

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"
            try:
                body = json.loads(exc.read().decode("utf-8", errors="replace"))
                detail = body.get("detail") or detail
            except Exception:
                pass
            raise RuntimeError(f"Sync server error: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Sync server unreachable: {exc.reason}") from exc

        if raw:
            return json.loads(raw.decode("utf-8"))
        return {"status": "ok"}

    def upload_profile(self, profile_id: str, archive_b64: str) -> Dict[str, Any]:
        """Upload an encrypted archive to the remote sync server."""
        if not isinstance(archive_b64, str) or not archive_b64:
            raise ValueError("archive_b64 must be a non-empty string")
        return self._request(
            "POST",
            self._url(profile_id),
            {"archive_b64": archive_b64, "metadata": {"source": "ghostbrowser_client"}},
        )

    def download_profile(self, profile_id: str) -> Dict[str, Any]:
        """Download the latest encrypted archive from the remote sync server."""
        return self._request("GET", self._url(profile_id))

    def delete_profile(self, profile_id: str) -> Dict[str, Any]:
        """Delete the remote archive for this profile/device."""
        return self._request("DELETE", self._url(profile_id))

    def list_profile_devices(self, profile_id: str) -> Dict[str, Any]:
        """List all device versions stored for a profile."""
        url = f"{self.base_url}/sync/v1/{self.tenant_id}/{profile_id}"
        return self._request("GET", url)

    def revoke_device(self, profile_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Revoke this device's access to the profile archive."""
        payload = {"reason": reason} if reason else None
        return self._request("POST", self._url(profile_id, "/revoke"), payload)


# ---------------------------------------------------------------------------
# Remote sync API endpoints
# ---------------------------------------------------------------------------

class RemoteUploadRequest(BaseModel):
    profile_id: str
    archive_b64: str


class RemoteRevokeRequest(BaseModel):
    profile_id: str
    reason: Optional[str] = "operator request"


def _validate_sync_id(value: str, field: str = "id") -> str:
    """Reject identifiers that could alter URL paths or carry control chars."""
    if not value:
        raise HTTPException(status_code=422, detail=f"{field} must not be empty")
    if any(c in value for c in "/\\%?#&=+<>\x00\x0a\x0d"):
        raise HTTPException(status_code=422, detail=f"{field} contains forbidden characters")
    return value


def _get_remote_sync_client() -> CloudSyncClient:
    """Build a remote sync client from environment variables."""
    url = os.environ.get("GHOSTBROWSER_SYNC_REMOTE_URL", "").strip()
    tenant = os.environ.get("GHOSTBROWSER_SYNC_TENANT_ID", "").strip()
    device = os.environ.get("GHOSTBROWSER_SYNC_DEVICE_ID", "").strip()
    token = os.environ.get("GHOSTBROWSER_SYNC_REMOTE_TOKEN", "").strip()
    if not url or not tenant or not device or not token:
        raise HTTPException(
            status_code=501,
            detail="Remote sync is not configured. Set GHOSTBROWSER_SYNC_REMOTE_URL, TENANT_ID, DEVICE_ID, and REMOTE_TOKEN.",
        )
    return CloudSyncClient(
        base_url=url,
        tenant_id=tenant,
        device_id=device,
        token=token,
    )


@router.post("/upload")
async def upload_profile_remote(req: RemoteUploadRequest, _auth: None = Depends(require_admin_token)):
    """Upload an already-encrypted profile archive to the remote sync server."""
    _validate_sync_id(req.profile_id, "profile_id")
    client = _get_remote_sync_client()
    try:
        return await asyncio.to_thread(client.upload_profile, req.profile_id, req.archive_b64)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/download/{profile_id}")
async def download_profile_remote(profile_id: str, _auth: None = Depends(require_admin_token)):
    """Download the latest encrypted profile archive from the remote sync server."""
    _validate_sync_id(profile_id, "profile_id")
    client = _get_remote_sync_client()
    try:
        return await asyncio.to_thread(client.download_profile, profile_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/revoke-device")
async def revoke_device_remote(req: RemoteRevokeRequest, _auth: None = Depends(require_admin_token)):
    """Revoke this device's access to a profile archive on the remote sync server."""
    _validate_sync_id(req.profile_id, "profile_id")
    client = _get_remote_sync_client()
    try:
        return await asyncio.to_thread(client.revoke_device, req.profile_id, req.reason)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# Module-level instance used by backend.main for local backup endpoints.
cloud_sync_manager = CloudSyncManager()


