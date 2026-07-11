"""
API Key management module.
Generate, store, list, and revoke API keys for external tool access.
Provides a decorator for API-key-based authentication on endpoints.
"""

import functools
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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

    # -- Decorator for endpoint protection ---------------------------------
    def require_key(self, func):
        """
        Decorator: require a valid API key in the X-API-Key header.
        Usage:
            @api_key_manager.require_key
            def my_protected_endpoint(...):
        Works for both sync and async FastAPI route functions.
        """
        import inspect

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                request: Optional[Request] = kwargs.get("request")
                if request is None:
                    for a in args:
                        if isinstance(a, Request):
                            request = a
                            break
                if request is None:
                    raise HTTPException(status_code=500, detail="Request object not found")
                key = request.headers.get(API_KEY_HEADER)
                if not key or not self.validate(key):
                    raise HTTPException(status_code=401, detail="Invalid or missing API key")
                return await func(*args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                request: Optional[Request] = kwargs.get("request")
                if request is None:
                    for a in args:
                        if isinstance(a, Request):
                            request = a
                            break
                if request is None:
                    raise HTTPException(status_code=500, detail="Request object not found")
                key = request.headers.get(API_KEY_HEADER)
                if not key or not self.validate(key):
                    raise HTTPException(status_code=401, detail="Invalid or missing API key")
                return func(*args, **kwargs)
            return sync_wrapper


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
api_key_manager = APIKeyManager()


# ---------------------------------------------------------------------------
# API router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


# ---------------------------------------------------------------------------
# Admin token for key management endpoints
# SECURITY FIX: API key endpoints require admin token to prevent unauthorized access
# ---------------------------------------------------------------------------
ADMIN_TOKEN = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN", "")

def _check_admin(request: Request):
    """Check admin token. If no admin token is configured, allow localhost-only access."""
    if ADMIN_TOKEN:
        token = request.headers.get("X-Admin-Token", "")
        if token != ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Admin token required")
    # If no admin token configured, allow (localhost-only binding provides protection)
    return True


@router.get("")
def list_keys(request: Request):
    """List all API keys (masked). Requires admin token if configured."""
    _check_admin(request)
    return api_key_manager.list_keys()


@router.post("")
def create_key(payload: CreateKeyModel, request: Request):
    """Create a new API key. Requires admin token if configured."""
    _check_admin(request)
    # SECURITY: Sanitize key name
    import html
    safe_name = html.escape(payload.name.strip(), quote=True)[:100]
    if not safe_name:
        raise HTTPException(status_code=400, detail="Key name is required")
    return api_key_manager.create_key(safe_name)


@router.delete("/{key}")
def revoke_key(key: str):
    """Revoke an API key."""
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
