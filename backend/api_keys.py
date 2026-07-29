"""
API Key management module.
Generate, store, list, and revoke API keys for external tool access.
"""

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from backend.auth import require_admin_token
from backend.config import get_data_dir
from backend.logging_config import logger

# ---------------------------------------------------------------------------
# Data file
# ---------------------------------------------------------------------------
API_KEYS_FILE = os.path.join(get_data_dir("profiles_data"), "api_keys.json")
os.makedirs(os.path.dirname(API_KEYS_FILE), exist_ok=True)

# Header name used for API key auth
API_KEY_HEADER = "X-API-Key"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class CreateKeyModel(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _sanitize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Key name is required")
        return value[:100]


# ---------------------------------------------------------------------------
# APIKeyManager
# ---------------------------------------------------------------------------
class APIKeyManager:
    """Manages generation, storage, revocation and usage tracking of API keys."""

    def __init__(self):
        self._keys: Dict[str, Dict[str, Any]] = {}  # key -> metadata
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self):
        if os.path.exists(API_KEYS_FILE):
            try:
                with open(API_KEYS_FILE, "r") as f:
                    self._keys = json.load(f)
            except json.JSONDecodeError as exc:
                logger.error(f"Failed to load API keys: {exc}")
                self._keys = {}

    def _save(self):
        try:
            with open(API_KEYS_FILE, "w") as f:
                json.dump(self._keys, f, indent=4)
        except OSError as exc:
            logger.error(f"Failed to save API keys: {exc}")

    # -- CRUD ---------------------------------------------------------------
    def create_key(self, name: str) -> Dict[str, Any]:
        """Generate a new secure API key and store it with metadata."""
        raw_key = secrets.token_urlsafe(32)
        metadata = {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat() + "Z",
            "last_used": None,
            "request_count": 0,
            "active": True,
        }
        self._keys[raw_key] = metadata
        self._save()
        logger.info(f"API key created: name={name}")
        return {"key": raw_key, **metadata}

    def revoke_key(self, key: str) -> bool:
        """Delete an API key. Returns True if it existed."""
        if key not in self._keys:
            return False
        del self._keys[key]
        self._save()
        logger.info(f"API key revoked: {key[:8]}...")
        return True

    def get_key_info(self, key: str) -> Optional[Dict[str, Any]]:
        """Return metadata for a key (without the key itself)."""
        return self._keys.get(key)

    def list_keys(self) -> List[Dict[str, Any]]:
        """Return all keys with metadata (key is masked)."""
        result = []
        for key, meta in self._keys.items():
            entry = {"key": key[:8] + "...", **meta}
            result.append(entry)
        return result

    def validate(self, key: str) -> bool:
        """Check if a key is valid and active. Updates usage stats."""
        meta = self._keys.get(key)
        if not meta or not meta.get("active", True):
            return False
        meta["last_used"] = datetime.now(timezone.utc).isoformat() + "Z"
        meta["request_count"] = meta.get("request_count", 0) + 1
        self._save()
        return True

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
api_key_manager = APIKeyManager()


# ---------------------------------------------------------------------------
# API router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


# ---------------------------------------------------------------------------
# Admin token for key management endpoints — FAIL CLOSED when not configured
# ---------------------------------------------------------------------------
@router.get("")
def list_keys(_auth: None = Depends(require_admin_token)):
    """List all API keys (masked). Requires admin token."""
    return api_key_manager.list_keys()


@router.post("")
def create_key(payload: CreateKeyModel, _auth: None = Depends(require_admin_token)):
    """Create a new API key. Requires admin token."""
    return api_key_manager.create_key(payload.name)


@router.delete("/{key}")
def revoke_key(key: str, _auth: None = Depends(require_admin_token)):
    """Revoke an API key. Requires admin token."""
    # Accept the full key or the masked version for convenience
    full_key = key
    if key.endswith("..."):
        # Find a key that starts with the unmasked prefix
        prefix = key[:-3]
        for k in api_key_manager._keys:
            if k.startswith(prefix):
                full_key = k
                break
    revoked = api_key_manager.revoke_key(full_key)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "revoked", "key": key[:8] + "..."}
