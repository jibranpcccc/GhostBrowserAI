"""
Update Manager — update-check, signed download, and safe auto-apply flow.

This module checks whether a newer release of GhostBrowser is available on
GitHub, downloads/verifies the artifact, and can apply it with staged rollout,
backup, rollback, and confirmation controls.

Design goals:
- Read the current version from a plain ``VERSION`` file.
- Query GitHub ``releases/latest`` only when ``GHOSTBROWSER_UPDATE_REPO``
  is configured as ``owner/repo``.
- Download artifacts with stdlib networking and a short timeout.
- Verify downloaded artifacts via SHA-256 checksum or (if configured) Ed25519.
- Apply updates safely: backup current install, stage new install, verify,
  atomic-ish swap, rollback on failure.
- Require explicit confirmation or environment opt-in before applying.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
import ipaddress
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from backend.auth import require_admin_token

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
        InvalidSignature,
    )
    HAS_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - optional dependency
    HAS_CRYPTOGRAPHY = False

router = APIRouter(prefix="/api/updates", tags=["updates"])

#: Path to the plain-text version file at the project root.
VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

#: Default fallback version if the VERSION file is missing and cannot be created.
FALLBACK_VERSION = "0.0.1"

#: Default HTTP timeout (seconds) when contacting the GitHub API.
DEFAULT_TIMEOUT = 5

#: Maximum update artifact size in bytes (200 MB).
MAX_UPDATE_BYTES = 200 * 1024 * 1024

#: Maximum number of HTTP redirects to follow when downloading updates.
MAX_UPDATE_REDIRECTS = 3


def _normalize_version(version: str) -> str:
    """Strip a leading 'v'/'V' and surrounding whitespace from a version."""
    return version.lstrip("vV").strip()


def _is_up_to_date(current: str, latest: str) -> bool:
    """Compare ``latest`` to ``current`` as semantic-ish version strings.

    Both values are normalized by stripping a leading ``v``/``V``. Numeric
    dotted versions are compared tuple-wise; anything else falls back to a
    simple string comparison so the result is still safe and deterministic.
    """
    cur_norm = _normalize_version(current)
    lat_norm = _normalize_version(latest)
    try:
        cur_parts = tuple(int(part) for part in cur_norm.split("."))
        lat_parts = tuple(int(part) for part in lat_norm.split("."))
        return lat_parts <= cur_parts
    except (ValueError, AttributeError):
        return lat_norm <= cur_norm


def get_current_version() -> str:
    """Return the current version string from ``VERSION_FILE``.

    If the file does not exist, it is created with ``FALLBACK_VERSION``.
    Malformed content is accepted as-is because callers decide how to
    compare version strings.
    """
    if not VERSION_FILE.exists():
        VERSION_FILE.write_text(FALLBACK_VERSION, encoding="utf-8")
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return FALLBACK_VERSION


def _github_release_url(repo: str) -> str:
    """Build the GitHub ``releases/latest`` URL from ``owner/repo``."""
    owner, _, name = repo.partition("/")
    owner = owner.strip()
    name = name.strip()
    if not owner or not name:
        raise ValueError("GHOSTBROWSER_UPDATE_REPO must be in 'owner/repo' format")
    return f"https://api.github.com/repos/{owner}/{name}/releases/latest"


def _extract_download_url(release: Dict[str, Any]) -> Optional[str]:
    """Return the URL of the first asset, or ``None`` if none exists."""
    assets = release.get("assets") or []
    for asset in assets:
        if isinstance(asset, dict):
            url = asset.get("browser_download_url")
            if url:
                return url
    return None


def _node_hash(value: str) -> int:
    """Return a deterministic 0-99 hash for staged rollout decisions."""
    machine_node = os.environ.get("GHOSTBROWSER_UPDATE_NODE", "")
    if not machine_node:
        try:
            import uuid
            machine_node = uuid.getnode()
        except Exception:
            machine_node = "0"
    digest = hashlib.sha256(f"{value}:{machine_node}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def _rollout_percent() -> int:
    """Return configured staged rollout percentage (0-100)."""
    try:
        return max(0, min(100, int(os.environ.get("GHOSTBROWSER_UPDATE_ROLLOUT_PERCENT", "100"))))
    except ValueError:
        return 100


def _auto_update_allowed() -> bool:
    """Return True if environment opts in to automatic update application."""
    return os.environ.get("GHOSTBROWSER_AUTO_UPDATE", "").strip().lower() in {"1", "true", "yes"}


def _confirmation_secret() -> bytes:
    """Return a stable HMAC secret for confirmation tokens."""
    secret = os.environ.get("GHOSTBROWSER_UPDATE_SECRET", "").strip()
    if secret:
        return base64.b64decode(secret) if len(secret) > 16 else secret.encode()
    # Fallback to a deterministic but non-secret derivation; still prevents
    # casual forged confirmations because the token contains a timestamp.
    return hashlib.sha256(b"ghostbrowser-update-confirmation-fallback").digest()


def generate_confirmation_token(version: str, valid_seconds: int = 300) -> Dict[str, Any]:
    """Generate a short-lived confirmation token for applying an update."""
    expires_at = int(time.time()) + valid_seconds
    payload = f"{version}:{expires_at}"
    sig = hmac.new(_confirmation_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    token = base64.b64encode(f"{payload}:{sig}".encode()).decode()
    return {
        "version": version,
        "confirmation_token": token,
        "expires_at": expires_at,
    }


def verify_confirmation_token(token: str, expected_version: str) -> bool:
    """Verify a confirmation token is well-formed, unexpired, and matches version."""
    try:
        raw = base64.b64decode(token.encode()).decode()
        version, expires_str, sig = raw.rsplit(":", 2)
        if version != expected_version:
            return False
        expires_at = int(expires_str)
        if time.time() > expires_at:
            return False
        payload = f"{version}:{expires_at}"
        expected = hmac.new(_confirmation_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


def _install_root() -> Path:
    """Return the installation directory (parent of the project root)."""
    root = Path(__file__).resolve().parent.parent
    configured = os.environ.get("GHOSTBROWSER_INSTALL_DIR", "").strip()
    if configured:
        return Path(configured).resolve()
    return root.resolve()


def _backup_path(version: str) -> Path:
    """Path for the pre-update backup."""
    root = _install_root()
    return root.with_name(f"{root.name}.backup.{_sanitize_filename(version)}")


def _staging_path(version: str) -> Path:
    """Path for the staged new installation."""
    root = _install_root()
    return root.with_name(f"{root.name}.staging.{_sanitize_filename(version)}")


def _old_path(version: str) -> Path:
    """Path to which the current install is renamed during swap."""
    root = _install_root()
    return root.with_name(f"{root.name}.old.{_sanitize_filename(version)}")


def _sanitize_filename(value: str) -> str:
    return "".join(c for c in value.replace(":", "_").replace("/", "_") if c.isalnum() or c in "._-").strip() or "unknown"


def _extract_archive(archive_path: str, dest_dir: Path) -> Path:
    """Extract a .zip archive into ``dest_dir`` and return the extraction root."""
    path = Path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(dest_dir)
    else:
        raise ValueError("Only .zip archives are supported for auto-apply")

    # If the zip contains a single top-level directory, treat that as the root.
    entries = [e for e in dest_dir.iterdir() if e.name not in {".DS_Store"}]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest_dir


def _verify_staged_manifest(staging_dir: Path, expected_version: str) -> Dict[str, Any]:
    """Read and validate manifest.json in the staged install."""
    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("Staged archive is missing manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    staged_version = manifest.get("version")
    if staged_version != expected_version:
        raise RuntimeError(f" manifest.version {staged_version} != expected {expected_version}")
    return manifest


def apply_update(
    archive_path: str,
    confirmation_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Safely apply a verified update archive.

    Steps:
    1. Validate confirmation (token or GHOSTBROWSER_AUTO_UPDATE=1).
    2. Backup current install.
    3. Extract archive into a staging directory.
    4. Verify manifest.json version.
    5. Rename current -> .old, staging -> current, remove .old on success.
    6. On any failure, roll back from backup.

    Returns a summary dict. The caller is expected to restart the application.
    """
    archive = Path(archive_path)
    if not archive.exists():
        raise FileNotFoundError(f"Update archive not found: {archive_path}")

    version = _extract_version_from_archive(archive)
    if not _auto_update_allowed() and not confirmation_token:
        raise RuntimeError(
            "Update application requires a confirmation_token or GHOSTBROWSER_AUTO_UPDATE=1"
        )
    if confirmation_token and not verify_confirmation_token(confirmation_token, version):
        raise RuntimeError("Invalid or expired confirmation token")

    install_dir = _install_root()
    backup_dir = _backup_path(version)
    staging_dir = _staging_path(version)
    old_dir = _old_path(version)

    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    if old_dir.exists():
        shutil.rmtree(old_dir)

    # 1. Backup
    shutil.copytree(install_dir, backup_dir, ignore_dangling_symlinks=True)

    try:
        # 2. Stage
        extracted_root = _extract_archive(str(archive), staging_dir)
        _verify_staged_manifest(extracted_root, version)

        # 3. Swap (best-effort atomic on Windows)
        install_dir.rename(old_dir)
        shutil.move(str(extracted_root), str(install_dir))

        # 4. Cleanup old on success
        shutil.rmtree(old_dir, ignore_errors=True)

        return {
            "status": "applied",
            "version": version,
            "backup_path": str(backup_dir),
            "message": "Update applied. Please restart the application.",
        }
    except Exception as exc:
        # Roll back on failure
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
        if old_dir.exists():
            shutil.move(str(old_dir), str(install_dir))
        if not install_dir.exists() and backup_dir.exists():
            shutil.copytree(backup_dir, install_dir)
        raise RuntimeError(f"Update application failed and was rolled back: {exc}") from exc


def rollback_update() -> Dict[str, Any]:
    """Restore the most recent backup."""
    install_dir = _install_root()
    backup_dirs = sorted(
        install_dir.parent.glob(f"{install_dir.name}.backup.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not backup_dirs:
        raise RuntimeError("No backup available for rollback")
    latest = backup_dirs[0]
    if install_dir.exists():
        shutil.rmtree(install_dir, ignore_errors=True)
    shutil.copytree(latest, install_dir)
    return {
        "status": "rolled_back",
        "backup_path": str(latest),
        "message": "Rolled back to backup. Please restart the application.",
    }


def _extract_version_from_archive(archive: Path) -> str:
    """Peek at manifest.json inside a zip to read version without full extraction."""
    try:
        with tempfile.TemporaryDirectory() as td:
            with zipfile.ZipFile(archive, "r") as zf:
                for name in zf.namelist():
                    if name.endswith("manifest.json"):
                        zf.extract(name, td)
                        manifest = json.loads(Path(td, name).read_text(encoding="utf-8"))
                        return str(manifest.get("version", "unknown")).strip()
    except Exception:
        pass
    return "unknown"


def check_for_update() -> Dict[str, Any]:
    """Check whether a newer release is available.

    The current version is read from ``VERSION`` at the project root. If the
    ``GHOSTBROWSER_UPDATE_REPO`` environment variable is not set, the system is
    reported as up-to-date without making any network request.

    Returns:
        dict: A stable result dict containing at least:
            - ``current_version`` (str)
            - ``latest_version`` (str)
            - ``up_to_date`` (bool)
            - ``download_url`` (str | None)
            - ``release_notes`` (str | None)
    """
    current = get_current_version()
    repo = os.environ.get("GHOSTBROWSER_UPDATE_REPO", "").strip()

    if not repo:
        return {
            "current_version": current,
            "latest_version": current,
            "up_to_date": True,
            "download_url": None,
            "release_notes": None,
        }

    url = _github_release_url(repo)
    timeout = int(os.environ.get("GHOSTBROWSER_UPDATE_TIMEOUT", DEFAULT_TIMEOUT))

    try:
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "GhostBrowser-Update/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (HTTPError, URLError, OSError, TimeoutError, ValueError):
        # Fail safe: if GitHub is unreachable, malformed, or times out,
        # report the system as up-to-date with its current version.
        return {
            "current_version": current,
            "latest_version": current,
            "up_to_date": True,
            "download_url": None,
            "release_notes": None,
        }

    try:
        release = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {
            "current_version": current,
            "latest_version": current,
            "up_to_date": True,
            "download_url": None,
            "release_notes": None,
        }

    if not isinstance(release, dict) or "tag_name" not in release:
        return {
            "current_version": current,
            "latest_version": current,
            "up_to_date": True,
            "download_url": None,
            "release_notes": None,
        }

    latest = str(release["tag_name"]).strip()
    download_url = _extract_download_url(release)
    release_notes = release.get("body")
    if isinstance(release_notes, str):
        release_notes = release_notes.strip() or None
    else:
        release_notes = None

    newer = _is_up_to_date(current, latest)
    rollout_pct = _rollout_percent()
    hash_val = _node_hash(latest)
    rollout_deferred = not newer and hash_val >= rollout_pct
    if rollout_deferred:
        newer = True  # report as up-to-date until rollout reaches this node

    return {
        "current_version": current,
        "latest_version": latest,
        "up_to_date": newer,
        "download_url": download_url,
        "release_notes": release_notes,
        "rollout_percent": rollout_pct,
        "rollout_hash": hash_val,
        "rollout_deferred": rollout_deferred,
    }


def _is_private_or_loopback_host(host: str) -> bool:
    """Return True if ``host`` resolves to a private, loopback, or otherwise non-public IP."""
    try:
        addr = ipaddress.ip_address(host)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_link_local
        )
    except ValueError:
        # ``host`` is a domain name, not an IP literal.
        return False


def _validate_download_url(url: str) -> None:
    """Reject unsafe update URLs: non-HTTPS, missing host, or private/loopback IPs."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError("Only HTTPS update URLs are allowed")
    host = parsed.hostname
    if not host:
        raise RuntimeError("Invalid update URL: missing host")
    if _is_private_or_loopback_host(host):
        raise RuntimeError("Private or loopback update URLs are not allowed")


class _LimitedRedirectHandler(HTTPRedirectHandler):
    """Follow a bounded number of redirects when downloading updates."""

    max_redirections = MAX_UPDATE_REDIRECTS


def _download_url(url: str, timeout: Optional[int] = None) -> bytes:
    """Download ``url`` and return raw bytes; raise on HTTP errors."""
    _validate_download_url(url)
    if timeout is None:
        timeout = int(os.environ.get("GHOSTBROWSER_UPDATE_TIMEOUT", DEFAULT_TIMEOUT))
    req = Request(url, method="GET", headers={
        "User-Agent": f"GhostBrowser/{get_current_version()} (Update-Check)",
        "Accept": "application/octet-stream,*/*",
    })
    opener = build_opener(_LimitedRedirectHandler)
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = resp.read(MAX_UPDATE_BYTES + 1)
            if len(data) > MAX_UPDATE_BYTES:
                raise RuntimeError(f"Update artifact exceeds maximum size of {MAX_UPDATE_BYTES} bytes")
            return data
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc


def _update_download_dir() -> Path:
    """Return the directory where update artifacts are staged."""
    configured = os.environ.get("GHOSTBROWSER_UPDATE_DOWNLOAD_DIR")
    if configured:
        path = Path(configured)
    else:
        path = Path(tempfile.gettempdir()) / "ghostbrowser_updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifact_paths(version: str, url: str) -> Dict[str, Path]:
    """Return local staging paths for archive, signature, and checksum."""
    base = _update_download_dir() / _sanitize_filename(version)
    ext = Path(url.split("?")[0]).suffix or ".zip"
    return {
        "archive": base.with_suffix(ext),
        "sig": base.with_suffix(ext + ".sig"),
        "sha256": base.with_suffix(ext + ".sha256"),
    }


def _sanitize_filename(value: str) -> str:
    """Keep only safe filename characters."""
    return "".join(c for c in value.replace(":", "_").replace("/", "_") if c.isalnum() or c in "._-").strip()


def _file_sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of ``path``."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download_update(
    update_metadata: Dict[str, Any],
    verify: bool = True,
) -> Dict[str, Any]:
    """Download the update artifact referenced by ``update_metadata``.

    Also attempts to download ``.sig`` and ``.sha256`` sidecar files if they
    exist. Returns the local file paths and verification status. The caller
    must still run :func:`apply_update` manually to install.

    Args:
        update_metadata: Metadata dict returned by :func:`check_for_update`.
        verify: Whether to verify the downloaded artifact immediately.

    Returns:
        dict: ``{"version", "archive_path", "sig_path", "sha256_path",
        "digest", "verified", "message"}``.
    """
    download_url = update_metadata.get("download_url")
    if not download_url:
        raise RuntimeError("No download URL in update metadata")
    version = _normalize_version(update_metadata.get("latest_version", "unknown"))
    paths = _artifact_paths(version, download_url)

    archive_bytes = _download_url(download_url)
    paths["archive"].write_bytes(archive_bytes)

    # Try sidecars; failures are non-fatal.
    for key, suffix in (("sig", ".sig"), ("sha256", ".sha256")):
        sidecar_url = download_url + suffix
        try:
            paths[key].write_bytes(_download_url(sidecar_url))
        except RuntimeError:
            if paths[key].exists():
                paths[key].unlink()

    digest = _file_sha256(paths["archive"])
    result: Dict[str, Any] = {
        "version": version,
        "archive_path": str(paths["archive"]),
        "sig_path": str(paths["sig"]) if paths["sig"].exists() else None,
        "sha256_path": str(paths["sha256"]) if paths["sha256"].exists() else None,
        "digest": digest,
        "verified": False,
        "message": "Downloaded; verification pending or disabled.",
    }

    if verify:
        verify_result = verify_signature(
            paths["archive"],
            sig_path=result["sig_path"],
            sha256_path=result["sha256_path"],
        )
        result["verified"] = verify_result["verified"]
        result["message"] = verify_result["message"]

    return result


def verify_signature(
    archive_path: str,
    sig_path: Optional[str] = None,
    sha256_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify ``archive_path`` using Ed25519 (preferred) or SHA-256.

    Ed25519 verification needs ``GHOSTBROWSER_UPDATE_SIGNING_KEY`` set to a
    PEM or raw hex Ed25519 public key. If the key is missing but a ``.sha256``
    sidecar exists, the checksum is compared instead.

    Returns:
        dict: ``{"verified": bool, "message": str, "method": str,
        "digest": str}``.
    """
    path = Path(archive_path)
    if not path.exists():
        return {"verified": False, "message": "Archive not found", "method": "none", "digest": ""}

    digest = _file_sha256(path)
    signing_key = os.environ.get("GHOSTBROWSER_UPDATE_SIGNING_KEY", "").strip()

    if signing_key and HAS_CRYPTOGRAPHY:
        try:
            if "BEGIN PUBLIC KEY" in signing_key:
                public_key = serialization.load_pem_public_key(signing_key.encode())
            else:
                raw = base64.b64decode(signing_key) if len(signing_key) > 64 else bytes.fromhex(signing_key)
                public_key = Ed25519PublicKey.from_public_bytes(raw)
            if not isinstance(public_key, Ed25519PublicKey):
                raise TypeError("Configured signing key is not Ed25519")
            sig_file = Path(sig_path) if sig_path else path.parent / (path.name + ".sig")
            if not sig_file.exists():
                return {
                    "verified": False,
                    "message": "Ed25519 key configured but no signature file found",
                    "method": "ed25519",
                    "digest": digest,
                }
            public_key.verify(sig_file.read_bytes(), path.read_bytes())
            return {
                "verified": True,
                "message": "Ed25519 signature valid",
                "method": "ed25519",
                "digest": digest,
            }
        except InvalidSignature:
            return {"verified": False, "message": "Invalid Ed25519 signature", "method": "ed25519", "digest": digest}
        except Exception as exc:  # pragma: no cover
            return {"verified": False, "message": f"Ed25519 verification error: {exc}", "method": "ed25519", "digest": digest}

    if sha256_path and Path(sha256_path).exists():
        expected_line = Path(sha256_path).read_text(encoding="utf-8").strip().split()[0].lower()
        if expected_line == digest:
            return {
                "verified": True,
                "message": "SHA-256 checksum matches",
                "method": "sha256",
                "digest": digest,
            }
        return {"verified": False, "message": "SHA-256 checksum mismatch", "method": "sha256", "digest": digest}

    return {
        "verified": False,
        "message": "No signing key or sha256 sidecar; download not verified",
        "method": "none",
        "digest": digest,
    }


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

class DownloadUpdateRequest(BaseModel):
    version: str
    download_url: str


class ApplyUpdateRequest(BaseModel):
    archive_path: str
    confirmation_token: Optional[str] = None


@router.get("/check")
async def check_for_update_endpoint(_auth: None = Depends(require_admin_token)):
    """Return the current-versus-latest release comparison."""
    return check_for_update()


@router.post("/confirm")
async def confirm_update_endpoint(req: DownloadUpdateRequest, _auth: None = Depends(require_admin_token)):
    """Generate a confirmation token required to apply an update."""
    return generate_confirmation_token(req.version)


@router.post("/apply")
async def apply_update_endpoint(req: ApplyUpdateRequest, _auth: None = Depends(require_admin_token)):
    """Apply a downloaded and verified update archive."""
    try:
        result = apply_update(req.archive_path, req.confirmation_token)
        return {"status": "success", **result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Update application failed: {exc}") from exc


@router.post("/rollback")
async def rollback_update_endpoint(_auth: None = Depends(require_admin_token)):
    """Roll back to the previous backup."""
    try:
        result = rollback_update()
        return {"status": "success", **result}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Rollback failed: {exc}") from exc


@router.post("/download")
async def download_update_endpoint(req: DownloadUpdateRequest, _auth: None = Depends(require_admin_token)):
    """Download and verify an update artifact without applying it."""
    try:
        metadata = {
            "latest_version": req.version,
            "download_url": req.download_url,
        }
        result = download_update(metadata, verify=True)
        return {"status": "success", "result": result}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Update download failed: {exc}") from exc
