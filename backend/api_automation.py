"""
API Automation Module — Selenium/Puppeteer connection endpoints + API key system.

Provides:
  POST   /api/profiles/{id}/connect   → returns ws:// URL for Selenium/Puppeteer
  GET    /api/api-keys                → list all API keys
  POST   /api/api-keys                → generate a new API key
  DELETE /api/api-keys/{key}          → revoke (delete) an API key
"""

import os
import json
import secrets
import hashlib
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger
from backend.profile_manager import profile_manager
from backend.browser_manager import active_browsers, is_profile_running, launch_profile

router = APIRouter(tags=["api-automation"])

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_API_KEYS_FILE = os.path.join(get_data_dir("api_data"), "api_keys.json")


def _load_keys() -> list[dict]:
    if not os.path.exists(_API_KEYS_FILE):
        return []
    try:
        with open(_API_KEYS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_keys(keys: list[dict]) -> None:
    os.makedirs(os.path.dirname(_API_KEYS_FILE), exist_ok=True)
    with open(_API_KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=4)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw key so we never store plaintext."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CreateApiKeyRequest(BaseModel):
    name: str = "default"
    scopes: List[str] = ["connect", "rpa", "sync"]


class ConnectRequest(BaseModel):
    headless: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/profiles/{profile_id}/connect")
async def connect_profile(profile_id: str, req: Optional[ConnectRequest] = None):
    """
    Launch (or reuse) a browser for the given profile and return a CDP
    websocket endpoint URL suitable for Selenium 4 / Puppeteer / Playwright
    remote connections.

    Playwright's persistent-context Chromium exposes a CDP endpoint that we
    can read from the underlying browser process.  When that is unavailable we
    fall back to a well-known local debug port.
    """
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    headless = req.headless if req else False

    if not is_profile_running(profile_id):
        launch_result = await launch_profile(profile_id, force_headless=headless)
        if launch_result.get("status") == "error":
            raise HTTPException(status_code=400, detail=launch_result["message"])

    browser_data = active_browsers.get(profile_id)
    if not browser_data:
        raise HTTPException(status_code=500, detail="Failed to acquire browser session")

    ws_endpoint = None

    # Attempt 1: Playwright CDP session via the first page
    try:
        page = browser_data.get("page")
        if page:
            cdp = page.context.new_cdp_session  # method on context
            # Use the page's CDP session to retrieve the target info
            cdp_session = await page.context.new_cdp_session(page)
            target_info = await cdp_session.send("Target.getTargetInfo")
            target_id = target_info.get("targetInfo", {}).get("targetId")
            ws_endpoint = f"ws://127.0.0.1:9222/devtools/page/{target_id}"
    except Exception as exc:
        logger.warning(f"CDP session extraction failed: {exc}")

    # Attempt 2: Fallback — try reading from the browser process debug port
    if not ws_endpoint:
        try:
            browser = browser_data.get("browser")
            if browser and hasattr(browser, "ws_endpoint"):
                ws_endpoint = browser.ws_endpoint
        except Exception:
            pass

    # Attempt 3: Fallback — standard local endpoint
    if not ws_endpoint:
        ws_endpoint = "ws://127.0.0.1:9222"

    logger.info(
        f"Connect endpoint generated for profile {profile_id}: {ws_endpoint}",
        extra={"profile_id": profile_id},
    )

    return {
        "status": "success",
        "profile_id": profile_id,
        "ws_endpoint": ws_endpoint,
        "instructions": {
            "selenium": "Use RemoteWebDriver with endpoint http://127.0.0.1:9222 and 'goog:chromeOptions' debuggerAddress '127.0.0.1:9222'",
            "puppeteer": "puppeteer.connect({ browserWSEndpoint: '<ws_endpoint>' })",
            "playwright": "chromium.connectOverCDP('http://127.0.0.1:9222')",
        },
    }


@router.get("/api/api-keys")
def list_api_keys():
    """List all API keys (hashed, never returns raw key values)."""
    keys = _load_keys()
    safe = [
        {
            "id": k["id"],
            "name": k["name"],
            "key_prefix": k.get("key_prefix", "gb_******"),
            "scopes": k.get("scopes", []),
            "created_at": k.get("created_at"),
            "last_used": k.get("last_used"),
        }
        for k in keys
    ]
    return {"status": "success", "keys": safe}


@router.post("/api/api-keys")
def create_api_key(req: CreateApiKeyRequest):
    """Generate a new random API key and store its hash."""
    raw_key = "gb_" + secrets.token_urlsafe(32)
    key_id = "key_" + secrets.token_hex(8)
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "id": key_id,
        "name": req.name,
        "key_prefix": raw_key[:12] + "...",
        "key_hash": _hash_key(raw_key),
        "scopes": req.scopes,
        "created_at": now,
        "last_used": None,
    }

    keys = _load_keys()
    keys.append(entry)
    _save_keys(keys)

    logger.info(f"API key '{req.name}' created (id={key_id})")

    # Return the raw key ONCE — it is never retrievable again
    return {
        "status": "success",
        "id": key_id,
        "name": req.name,
        "api_key": raw_key,
        "message": "Save this key securely — it will not be shown again.",
    }


@router.delete("/api/api-keys/{key_id}")
def delete_api_key(key_id: str):
    """Revoke an API key by its ID (or by its raw value)."""
    keys = _load_keys()
    original_len = len(keys)

    # Allow deletion by ID, by key prefix, or by raw key value
    keys = [
        k for k in keys
        if k["id"] != key_id
        and not k.get("key_prefix", "").startswith(key_id)
        and k.get("key_hash") != _hash_key(key_id)
    ]

    if len(keys) == original_len:
        raise HTTPException(status_code=404, detail="API key not found")

    _save_keys(keys)
    logger.info(f"API key {key_id} revoked")
    return {"status": "success", "message": "API key revoked"}
