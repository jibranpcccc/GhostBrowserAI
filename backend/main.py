from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel, field_validator
from typing import Optional, List, Dict, Any
import uvicorn
import base64
import json
import os
import sys
import asyncio
import logging
import re
import hmac

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from backend.profile_manager import is_valid_new_pin, profile_manager
from backend.browser_manager import launch_profile, close_profile, is_profile_running, active_browsers, get_profile_cookies, set_profile_cookies, parse_proxy_string
from backend.macro_manager import macro_manager
from backend.macro_runner import run_macro_bulk
from backend.config import get_data_dir
from backend.scheduler_manager import SchedulerManager
# H4+H5 FIX: Move system_monitor import to top of file (was at line 569, after its first use in lifespan)
from backend.system_monitor import system_monitor
from backend.device_cohorts import host_os
from backend.credential_store import store_status

# --- API Routers ---
from backend.synchronizer import router as synchronizer_router
from backend.profile_folders import router as profile_folders_router
from backend.bulk_operations import (
    router as bulk_operations_router,
    BulkCreateRequest,
    bulk_create_profiles,
    _CREATE_SEM,
)
from backend.cloud_sync import cloud_sync_manager, CloudSyncClient, _validate_sync_id, _get_remote_sync_client
from backend.auth import RATE_LIMITERS, check_pin_rate_limit, get_client_key, require_admin_token
from backend.error_codes import PUBLIC_CODE_MESSAGES, public_message_for
from backend.update_manager import router as update_manager_router
from backend.sbom import router as sbom_router
from backend.detection_score import router as detection_score_router

scheduler_manager = SchedulerManager(
    browser_manager=None,
    profile_manager=profile_manager,
    macro_manager=macro_manager
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    import backend.browser_manager as bm
    scheduler_manager.browser_manager = bm
    await system_monitor.start()
    scheduler_manager.start()
    yield
    # Shutdown
    system_monitor.stop()
    scheduler_manager.stop()
    # Close shared httpx client to prevent resource leak (CRIT-07)
    from backend.ai_generator import _shared_client
    try:
        await _shared_client.aclose()
    except Exception:
        pass

# Rate limiter for profile creation: single create and bulk create share one
# global semaphore (A06) so combined concurrency never exceeds the limit.
_profile_create_sem = _CREATE_SEM

_is_production = os.environ.get("GHOSTBROWSER_PROD") == "1"
app = FastAPI(
    title="AI Anti-Detect Browser API",
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

# --- 500-error logging middleware (no request body logged — may contain secrets) ---
_ghost_logger = logging.getLogger("ghostbrowser")

@app.middleware("http")
async def add_security_headers(request, call_next):
    """Add security headers to ALL responses, including errors and early rejections."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    return response


@app.middleware("http")
async def log_500_errors(request, call_next):
    response = await call_next(request)
    # Only log actual unhandled 5xx, not expected 503 auth-configuration failures
    if response.status_code >= 500 and response.status_code != 503:
        _ghost_logger.error(
            "500-error | path=%s | method=%s",
            request.url.path,
            request.method,
        )
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply the general per-IP quota and expose quota state on every reply."""
    # PIN attempts and administrative credential checks have additional,
    # stricter dependencies; all requests still consume the global quota.
    limiter = RATE_LIMITERS["default"]
    client_key = get_client_key(request)
    if not limiter.check(client_key):
        response = JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers=limiter.get_headers(client_key),
        )
        # Ensure security headers are present on rate-limit rejections
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        return response

    response = await call_next(request)
    # A dependency may have returned a more restrictive rate-limit response.
    if response.status_code != 429 or "RateLimit-Limit" not in response.headers:
        response.headers.update(limiter.get_headers(client_key))
    return response

# --- CSRF double-submit protection ---
_CSRF_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_PUBLIC_ALLOWLIST = frozenset({"/api/system/health", "/api/system/csrf-token"})


def _validate_csrf(request: Request) -> bool:
    cookie_token = request.cookies.get("XSRF-TOKEN", "")
    header_token = request.headers.get("X-XSRF-Token", "")
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


@app.middleware("http")
async def csrf_protection_middleware(request: Request, call_next):
    if (
        request.method in _CSRF_UNSAFE_METHODS
        and request.url.path.startswith("/api/")
        and request.url.path not in _CSRF_PUBLIC_ALLOWLIST
    ):
        if not _validate_csrf(request):
            response = JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )
            # Ensure security headers are present on CSRF rejections
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
            return response
    response = await call_next(request)
    return response

from fastapi.responses import JSONResponse
import traceback

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Catch all unhandled exceptions and return a safe generic 500 response with security headers."""
    _ghost_logger.error("Unhandled exception: %s", traceback.format_exc())
    response = JSONResponse(
        status_code=500, 
        content={"detail": "Internal server error"}
    )
    # Ensure security headers are present on unhandled exceptions
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    return response

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    # CRIT-01 FIX: Restrict to localhost only. Wildcard + credentials is a CSRF vulnerability.
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ProxyModel(BaseModel):
    server: str
    username: Optional[str] = None
    password: Optional[str] = None

class AdvancedSettingsModel(BaseModel):
    os: str = "Windows"
    cpu_cores: int = 4
    memory_gb: int = 8
    screen_resolution: str = "1920x1080"
    webrtc_mode: str = "altered" # altered, disabled, real
    canvas_noise: bool = True
    webgl_noise: bool = True
    audio_noise: bool = True
    privacy_mode: str = "standard"
    block_service_workers: bool = False
    headless: bool = False

class CreateProfileModel(BaseModel):
    name: str
    proxy: Optional[ProxyModel] = None
    proxy_string: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    advanced: Optional[AdvancedSettingsModel] = None
    pin: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v):
        v = v.strip() if isinstance(v, str) else ""
        if not v:
            raise ValueError("name cannot be empty")
        if len(v) > 120:
            raise ValueError("name must be 120 characters or less")
        return v

    @field_validator("pin")
    @classmethod
    def _validate_pin(cls, v):
        if v is not None and not is_valid_new_pin(v):
            raise ValueError("PIN must be exactly 4-6 ASCII digits")
        return v

class ProfileProxyUpdateRequest(BaseModel):
    proxy_string: Optional[str] = None
    clear_proxy: bool = False

class EditProfileModel(BaseModel):
    name: str
    proxy: Optional[ProxyModel] = None
    proxy_string: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    advanced: Optional[AdvancedSettingsModel] = None

def parse_proxy_string_safe(proxy_str: str) -> Optional[dict]:
    """Parse a proxy string for API responses (credentials are redacted)."""
    try:
        parsed = parse_proxy_string(proxy_str)
    except ValueError:
        return None
    return {"server": parsed["server"], "authenticated": bool(parsed.get("username"))}

from backend.profile_creator import profile_creator

@app.post("/api/profiles")
async def create_profile(data: CreateProfileModel, _auth: None = Depends(require_admin_token)):
    """
    Creates a Zero-Leak profile using the full Kimi AI → Coherence → LeakScan pipeline.
    NO profile is ever created without Kimi AI successfully generating the fingerprint.
    """
    async with _profile_create_sem:
        try:
            proxy_dict = data.proxy.model_dump() if data.proxy else (parse_proxy_string(data.proxy_string) if data.proxy_string else None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        advanced_dict = data.advanced.model_dump() if data.advanced else None
        result = await profile_creator.create_zero_leak_profile(name=data.name, proxy=proxy_dict, advanced_ui=advanced_dict, pin=data.pin)

    if result["status"] == "error":
        code = result.get("code")
        if code in PUBLIC_CODE_MESSAGES:
            detail = PUBLIC_CODE_MESSAGES[code]
        else:
            # Controlled client-safe validation text (static strings raised by
            # profile_creator._validate_inputs) is preserved; everything else
            # collapses to the catalog default so internals never leak.
            message = result.get("message", "")
            detail = message if message.startswith("Validation failed:") else "Profile creation failed"
        raise HTTPException(
            status_code=503 if code == "KIMI_UNAVAILABLE" else 400,
            detail=detail,
        )

    return _redact_sensitive_api_data(result["profile"])

@app.post("/api/profiles/generate")
async def generate_profile(data: CreateProfileModel, _auth: None = Depends(require_admin_token)):
    """Alias for POST /api/profiles — triggers full Kimi AI zero-leak creation."""
    return await create_profile(data)

class BulkCreateProfileModel(BaseModel):
    base_name: str
    count: int = 5
    proxy: Optional[dict] = None
    proxy_string: Optional[str] = None
    advanced: Optional[AdvancedSettingsModel] = None
    pin: Optional[str] = None

    @field_validator("base_name")
    @classmethod
    def _validate_base_name(cls, v):
        v = v.strip() if isinstance(v, str) else ""
        if not v:
            raise ValueError("base_name cannot be empty")
        if len(v) > 120:
            raise ValueError("base_name must be 120 characters or less")
        return v

    @field_validator("count")
    @classmethod
    def validate_count(cls, v):
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError("count must be an integer between 1 and 50")
        if v < 1 or v > 50:
            raise ValueError("count must be between 1 and 50")
        return v

    @field_validator("pin")
    @classmethod
    def _validate_pin(cls, v):
        if v is not None and not is_valid_new_pin(v):
            raise ValueError("PIN must be exactly 4-6 ASCII digits")
        return v

@app.post("/api/profiles/generate/bulk")
async def generate_bulk_profiles(data: BulkCreateProfileModel, _auth: None = Depends(require_admin_token)):
    """Generate multiple profiles via Kimi AI.

    Legacy alias of ``POST /api/profiles/bulk/create``. Both routes share a
    single implementation (``bulk_operations.bulk_create_profiles``) so error
    sanitization, the create semaphore, and ``skip_warming`` are identical.
    """
    req = BulkCreateRequest(
        base_name=data.base_name,
        count=data.count,
        proxy=data.proxy,
        proxy_string=data.proxy_string,
        pin=data.pin,
        advanced=data.advanced.model_dump() if data.advanced else None,
    )
    return await bulk_create_profiles(req)

@app.get("/api/profiles")
def list_profiles(_auth: None = Depends(require_admin_token)):
    profiles = profile_manager.list_profiles()
    for p in profiles:
        p["status"] = "Running" if is_profile_running(p["id"]) else "Stopped"
    return _redact_sensitive_api_data(profiles)

@app.post("/api/profiles/{profile_id}/clone")
async def clone_profile(profile_id: str, _auth: None = Depends(require_admin_token)):
    """Smart Duplicate a profile: Re-runs Kimi AI to generate a fresh, unique fingerprint but copies metadata, proxy, and tags."""
    original = profile_manager.get_profile(profile_id)
    if not original:
        raise HTTPException(status_code=404, detail="Original profile not found")

    name = original.get("name", "Unknown") + " (Clone)"
    proxy = original.get("proxy")
    advanced = original.get("advanced", {})

    async with _profile_create_sem:
        result = await profile_creator.create_zero_leak_profile(name=name, proxy=proxy, advanced_ui=advanced)

    if result["status"] == "error":
        code = result.get("code")
        if code in PUBLIC_CODE_MESSAGES:
            detail = PUBLIC_CODE_MESSAGES[code]
        else:
            message = result.get("message", "")
            detail = message if message.startswith("Validation failed:") else PUBLIC_CODE_MESSAGES["CREATE_FAILED"]
        raise HTTPException(status_code=400, detail=detail)

    new_profile = result["profile"]

    # Copy tags and notes
    updates = {}
    if original.get("tags"): updates["tags"] = original.get("tags")
    if original.get("notes"): updates["notes"] = original.get("notes")
    if original.get("proxy_pin"): updates["proxy_pin"] = original.get("proxy_pin")

    if updates:
        profile_manager.update_profile(new_profile["id"], updates)

    return _redact_sensitive_api_data({
        "status": "success", "profile": profile_manager.get_profile(new_profile["id"])
    })

@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: str, _auth: None = Depends(require_admin_token)):
    await close_profile(profile_id)
    success = profile_manager.delete_profile(profile_id)
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}

@app.post("/api/profiles/{profile_id}/clear-cache")
async def clear_profile_cache(profile_id: str, _auth: None = Depends(require_admin_token)):
    """Clear cookies and local storage without deleting profile fingerprint."""
    try:
        success = profile_manager.clear_profile_storage(profile_id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success", "message": "Profile cache and cookies cleared"}

@app.get("/api/profiles/export/json")
async def export_profiles_json(_auth: None = Depends(require_admin_token)):
    """Export all profiles as clean, portable JSON."""
    profiles = profile_manager.list_profiles()
    export_data = []
    for p in profiles:
        clean_p = dict(p)
        clean_p.pop("path", None)
        clean_p.pop("pin_hash", None)
        export_data.append(clean_p)
    return {"status": "success", "profiles": _redact_sensitive_api_data(export_data)}

class ImportProfilesRequest(BaseModel):
    profiles: List[Dict[str, Any]]

@app.post("/api/profiles/import/json")
async def import_profiles_json(req: ImportProfilesRequest, _auth: None = Depends(require_admin_token)):
    """Batch import profiles from JSON."""
    imported = 0
    errors = []
    for p in req.profiles:
        name = p.get("name")
        if not name:
            continue
        try:
            res = await profile_creator.create_zero_leak_profile(
                name=name,
                proxy=p.get("proxy"),
                advanced_ui=p.get("advanced")
            )
            if res.get("status") == "success":
                new_id = res["profile"]["id"]
                if p.get("tags") or p.get("notes"):
                    profile_manager.update_profile(new_id, {"tags": p.get("tags", []), "notes": p.get("notes", "")})
                imported += 1
            else:
                errors.append(f"{name}: {res.get('message')}")
        except Exception as e:
            errors.append(f"{name}: {str(e)}")
    return {"status": "success", "imported": imported, "errors": errors}


class RenameRequest(BaseModel):
    name: str

@app.patch("/api/profiles/{profile_id}/rename")
async def rename_profile(profile_id: str, req: RenameRequest, _auth: None = Depends(require_admin_token)):
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Invalid name")

    success = profile_manager.rename_profile(profile_id, req.name.strip())
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success", "name": req.name.strip()}

class UpdateMetadataRequest(BaseModel):
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    proxy_pin: Optional[str] = None
    pinned: Optional[bool] = None
    clear_proxy_pin: bool = False

@app.patch("/api/profiles/{profile_id}/metadata")
async def update_metadata(profile_id: str, req: UpdateMetadataRequest, _auth: None = Depends(require_admin_token)):
    updates = {}
    if req.tags is not None: updates["tags"] = req.tags
    if req.notes is not None: updates["notes"] = req.notes
    if req.proxy_pin is not None: updates["proxy_pin"] = req.proxy_pin
    if req.pinned is not None: updates["pinned"] = req.pinned
    if req.clear_proxy_pin: updates["proxy_pin"] = None
    success = profile_manager.update_profile(profile_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}

@app.put("/api/profiles/{profile_id}")
async def edit_profile(profile_id: str, data: EditProfileModel, _auth: None = Depends(require_admin_token)):
    """Full update for a profile's settings, proxy, and fingerprint."""
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    try:
        proxy_dict = data.proxy.model_dump() if data.proxy else (
            parse_proxy_string(data.proxy_string) if data.proxy_string else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    advanced_dict = data.advanced.model_dump() if data.advanced else None

    updates = {
        "name": data.name,
        "proxy": proxy_dict,
        "timezone": data.timezone,
        "locale": data.locale,
        "advanced": advanced_dict
    }

    # Remove None values so we don't accidentally wipe out stuff
    updates = {k: v for k, v in updates.items() if v is not None}

    success = profile_manager.update_profile(profile_id, updates)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save profile edits")

    return {"status": "success", "message": "Profile updated successfully"}

@app.patch("/api/profiles/{profile_id}/proxy")
async def update_profile_proxy(profile_id: str, req: ProfileProxyUpdateRequest, _auth: None = Depends(require_admin_token)):
    """Test and update a profile's proxy without leaking credentials."""
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if req.clear_proxy:
        profile_manager.update_profile(profile_id, {"proxy": None})
        return {"status": "success", "proxy": None}
    if not req.proxy_string:
        raise HTTPException(status_code=422, detail="proxy_string is required")
    try:
        proxy_dict = parse_proxy_string(req.proxy_string)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    is_healthy = await proxy_manager.check_proxy_health(proxy_dict, record_failure=False)
    if not is_healthy:
        raise HTTPException(status_code=502, detail="Proxy health check failed")
    profile_manager.update_profile(profile_id, {"proxy": proxy_dict})
    return {"status": "success", "proxy": parse_proxy_string_safe(req.proxy_string)}


class ProxyConnectionTestRequest(BaseModel):
    proxy_string: str


@app.post("/api/proxies/test-connection")
async def test_proxy_connection(req: ProxyConnectionTestRequest, _auth: None = Depends(require_admin_token)):
    try:
        proxy = parse_proxy_string(req.proxy_string)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not await proxy_manager.check_proxy_health(proxy, record_failure=False):
        raise HTTPException(status_code=502, detail="Proxy health check failed")
    return {"status": "success", "message": "Proxy connection verified", "proxy": parse_proxy_string_safe(req.proxy_string)}


class PrivacyModeRequest(BaseModel):
    privacy_mode: str


@app.patch("/api/profiles/{profile_id}/privacy-mode")
async def update_privacy_mode(profile_id: str, req: PrivacyModeRequest, _auth: None = Depends(require_admin_token)):
    mode = req.privacy_mode.strip().lower()
    if mode not in {"standard", "strict", "ephemeral", "high"}:
        raise HTTPException(status_code=422, detail="Invalid privacy mode")
    if not profile_manager.update_profile(profile_id, {"advanced": {"privacy_mode": mode}}):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success", "privacy_mode": mode}


class ProfileTagsRequest(BaseModel):
    add: List[str] = []
    remove: List[str] = []


@app.patch("/api/profiles/{profile_id}/tags")
async def update_profile_tags(profile_id: str, req: ProfileTagsRequest, _auth: None = Depends(require_admin_token)):
    if not profile_manager.get_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    try:
        profile_manager.add_tags(profile_id, req.add)
        profile_manager.remove_tags(profile_id, req.remove)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "success"}


class NewProfilePinRequest(BaseModel):
    pin: str

    @field_validator("pin")
    @classmethod
    def _validate_pin(cls, v):
        if not is_valid_new_pin(v):
            raise ValueError("PIN must be exactly 4-6 ASCII digits")
        return v


class ProfilePinVerificationRequest(BaseModel):
    # Legacy hashes may have been created from PINs outside the current policy.
    # Keep this request intentionally unconstrained so their owners can unlock.
    pin: str


@app.post("/api/profiles/{profile_id}/pin/set")
async def set_profile_pin(profile_id: str, req: NewProfilePinRequest, _auth: None = Depends(require_admin_token)):
    if not profile_manager.set_profile_pin(profile_id, req.pin):
        raise HTTPException(status_code=404, detail="Profile not found or invalid PIN")
    return {"status": "success"}


@app.post("/api/profiles/{profile_id}/pin/verify")
async def verify_profile_pin(
    profile_id: str,
    req: ProfilePinVerificationRequest,
    _pin_limit: bool = Depends(check_pin_rate_limit),
    _auth: None = Depends(require_admin_token),
):
    if not profile_manager.get_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"verified": profile_manager.verify_profile_pin(profile_id, req.pin)}


@app.delete("/api/profiles/{profile_id}/pin")
async def delete_profile_pin(profile_id: str, _auth: None = Depends(require_admin_token)):
    if not profile_manager.clear_profile_pin(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}


def _redact_sensitive_api_data(data):
    """Recursively remove proxy credentials from API responses."""
    if isinstance(data, dict):
        redacted = {}
        had_credential = False
        for key, value in data.items():
            if key in ("proxy_pin", "pin_hash"):
                continue
            if key in ("username", "password"):
                had_credential = True
                continue
            redacted[key] = _redact_sensitive_api_data(value)
        if "server" in data and isinstance(data["server"], str):
            safe_proxy = redact_proxy_record(data)
            if safe_proxy["server"]:
                redacted["server"] = safe_proxy["server"]
            had_credential = had_credential or safe_proxy["authenticated"]
        if had_credential and "server" in data:
            redacted["authenticated"] = True
        if "pin_hash" in data:
            redacted["has_pin"] = True
        if data.get("proxy_pin"):
            redacted["has_pinned_proxy"] = True
        return redacted
    if isinstance(data, list):
        return [_redact_sensitive_api_data(item) for item in data]
    return data


@app.get("/api/profiles/{profile_id}/fingerprint")
async def get_fingerprint(profile_id: str, _auth: None = Depends(require_admin_token)):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _redact_sensitive_api_data(profile.get("advanced", {}))

@app.get("/api/profiles/{profile_id}/scan")
async def scan_profile(profile_id: str, _auth: None = Depends(require_admin_token)):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    fingerprint = profile.get("advanced", {})
    from backend.ai_auto_validator import AIAutoValidator
    validator = AIAutoValidator()
    result = await validator.validate_profile(profile, fingerprint)

    return _redact_sensitive_api_data({"status": "success", "scan": result})

class UpdateFingerprintRequest(BaseModel):
    advanced: dict

@app.patch("/api/profiles/{profile_id}/fingerprint")
async def update_fingerprint(profile_id: str, req: UpdateFingerprintRequest, _auth: None = Depends(require_admin_token)):
    success = profile_manager.update_profile(profile_id, {"advanced": req.advanced})
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}

class ScheduleRequest(BaseModel):
    macro_id: str
    profile_ids: List[str]
    cron: str

@app.get("/api/macros/schedule")
def list_schedules(_auth: None = Depends(require_admin_token)):
    return scheduler_manager.list_schedules()

@app.post("/api/macros/schedule")
def create_schedule(req: ScheduleRequest, _auth: None = Depends(require_admin_token)):
    return scheduler_manager.add_schedule(req.macro_id, req.profile_ids, req.cron)

@app.delete("/api/macros/schedule/{job_id}")
def delete_schedule(job_id: str, _auth: None = Depends(require_admin_token)):
    success = scheduler_manager.delete_schedule(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "success"}

@app.get("/api/profiles/{profile_id}/cookies")
async def get_cookies_api(profile_id: str, _auth: None = Depends(require_admin_token)):
    res = await get_profile_cookies(profile_id)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

MAX_COOKIE_COUNT = 5000
MAX_COOKIE_JSON_BYTES = 2 * 1024 * 1024


class CookieImportRequest(BaseModel):
    cookies: List[Dict[str, Any]]

    @field_validator("cookies")
    @classmethod
    def _validate_cookie_payload(cls, v):
        if len(v) > MAX_COOKIE_COUNT:
            raise ValueError(f"Too many cookies (max {MAX_COOKIE_COUNT})")
        payload_size = len(json.dumps(v).encode("utf-8"))
        if payload_size > MAX_COOKIE_JSON_BYTES:
            raise ValueError(f"Cookie payload too large (max {MAX_COOKIE_JSON_BYTES // 1024 // 1024}MB)")
        return v

@app.post("/api/profiles/{profile_id}/cookies")
async def set_cookies_api(profile_id: str, req: CookieImportRequest, _auth: None = Depends(require_admin_token)):
    res = await set_profile_cookies(profile_id, req.cookies)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

# --- Macros API ---
class MacroCreateRequest(BaseModel):
    name: str
    description: str = ""
    steps: list

@app.get("/api/macros")
def list_macros(_auth: None = Depends(require_admin_token)):
    return macro_manager.list_macros()

@app.post("/api/macros")
def create_macro(req: MacroCreateRequest, _auth: None = Depends(require_admin_token)):
    if not req.name.strip() or not req.steps:
        raise HTTPException(status_code=400, detail="Name and steps are required")
    return macro_manager.create_macro(req.name.strip(), req.description, req.steps)

@app.delete("/api/macros/{macro_id}")
def delete_macro(macro_id: str, _auth: None = Depends(require_admin_token)):
    return {"status": "success"} if macro_manager.delete_macro(macro_id) else {"status": "error"}

@app.get("/api/proxies/titan")
def get_titan_proxies(_auth: None = Depends(require_admin_token)):
    """Fetches the top proxies from the Titan proxy database"""
    import backend.db as db
    proxies = db.get_best_proxies(limit=100)
    return _redact_sensitive_api_data({"status": "success", "proxies": proxies})

class BulkMacroRunRequest(BaseModel):
    profile_ids: List[str]
    macro_id: str

@app.post("/api/macros/run/bulk")
async def run_bulk_macro(req: BulkMacroRunRequest, _auth: None = Depends(require_admin_token)):
    macro = macro_manager.get_macro(req.macro_id)
    if not macro:
        raise HTTPException(status_code=404, detail="Macro not found")

    # Launch in background
    asyncio.create_task(run_macro_bulk(req.profile_ids, macro))
    return {"status": "success", "message": f"Macro {macro['name']} started on {len(req.profile_ids)} profiles"}

# --- End Macros API ---

class LaunchProfileRequest(BaseModel):
    pin: Optional[str] = None
    url: Optional[str] = None


@app.post("/api/profiles/{profile_id}/launch")
async def launch_profile_api(profile_id: str, req: LaunchProfileRequest = None, _auth: None = Depends(require_admin_token)):
    try:
        res = await launch_profile(profile_id, pin=req.pin) if req and req.pin else await launch_profile(profile_id)
        if req and req.url and res.get("status") == "success":
            from backend.browser_manager import active_browsers
            if profile_id in active_browsers and active_browsers[profile_id].get("page"):
                try:
                    await active_browsers[profile_id]["page"].goto(req.url, timeout=15000)
                except Exception:
                    pass
    except HTTPException:
        raise
    except Exception as exc:
        _ghost_logger.exception("launch_profile_api unhandled error for profile %s", profile_id)
        raise HTTPException(status_code=500, detail="Internal launch error") from exc

    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@app.get("/api/profiles/{profile_id}/cdp")
async def get_profile_cdp(profile_id: str, _auth: None = Depends(require_admin_token)):
    """Return the CDP endpoint for a running profile (requires GHOSTBROWSER_CDP_TEST=1).

    Fails closed: no synthetic WS path is ever fabricated, and the endpoint is
    refused unless CDP test mode is enabled right now.
    """
    from backend.browser_manager import active_browsers, _cdp_test_mode_enabled
    if not _cdp_test_mode_enabled():
        raise HTTPException(status_code=403, detail="CDP test mode is disabled")
    browser_data = active_browsers.get(profile_id)
    if not browser_data:
        raise HTTPException(status_code=400, detail="Profile not running")
    cdp_port = browser_data.get("cdp_port")
    cdp_ws_path = browser_data.get("cdp_ws_path")
    if not cdp_port or not cdp_ws_path:
        raise HTTPException(status_code=400, detail="CDP endpoint unavailable for this profile")
    return {
        "status": "success",
        "profile_id": profile_id,
        "cdp_url": f"http://127.0.0.1:{cdp_port}",
        "cdp_ws_url": f"ws://127.0.0.1:{cdp_port}{cdp_ws_path}",
    }

@app.post("/api/profiles/{profile_id}/close")
async def close_profile_api(profile_id: str, _auth: None = Depends(require_admin_token)):
    res = await close_profile(profile_id)
    if res["status"] == "error":
        raise HTTPException(status_code=400, detail=res["message"])
    return res

class CookieDataModel(BaseModel):
    cookies: list

    @field_validator("cookies")
    @classmethod
    def _validate_cookie_payload(cls, v):
        if len(v) > MAX_COOKIE_COUNT:
            raise ValueError(f"Too many cookies (max {MAX_COOKIE_COUNT})")
        payload_size = len(json.dumps(v).encode("utf-8"))
        if payload_size > MAX_COOKIE_JSON_BYTES:
            raise ValueError(f"Cookie payload too large (max {MAX_COOKIE_JSON_BYTES // 1024 // 1024}MB)")
        return v

@app.post("/api/profiles/{profile_id}/cookies/import")
async def import_cookies(profile_id: str, data: CookieDataModel, _auth: None = Depends(require_admin_token)):
    if profile_id not in active_browsers:
        raise HTTPException(status_code=400, detail="Profile must be running to import cookies.")
    browser_data = active_browsers[profile_id]
    context = browser_data["context"]
    try:
        await context.add_cookies(data.cookies)
        return {"status": "success", "message": "Cookies imported successfully"}
    except Exception as e:
        _ghost_logger.error("Cookie import failed for %s: %s", profile_id, type(e).__name__)
        raise HTTPException(status_code=400, detail="Failed to import cookies")

@app.get("/api/profiles/{profile_id}/cookies/export")
async def export_cookies(profile_id: str, _auth: None = Depends(require_admin_token)):
    if profile_id not in active_browsers:
        raise HTTPException(status_code=400, detail="Profile must be running to export cookies.")
    browser_data = active_browsers[profile_id]
    context = browser_data["context"]
    try:
        cookies = await context.cookies()
        return {"status": "success", "cookies": cookies}
    except Exception as e:
        _ghost_logger.error("Cookie export failed for %s: %s", profile_id, type(e).__name__)
        raise HTTPException(status_code=400, detail="Failed to export cookies")

from backend.profile_rotator import rotator

class RotatorConfigModel(BaseModel):
    max_concurrent: int = 15

@app.post("/api/rotator/start")
async def start_rotator(config: RotatorConfigModel, _auth: None = Depends(require_admin_token)):
    rotator.max_concurrent = config.max_concurrent
    await rotator.start()
    return {"status": "success", "message": f"Rotator started with max {config.max_concurrent} profiles."}

@app.post("/api/rotator/stop")
def stop_rotator(_auth: None = Depends(require_admin_token)):
    rotator.stop()
    return {"status": "success", "message": "Rotator stopped."}

@app.get("/api/rotator/status")
def get_rotator_status(_auth: None = Depends(require_admin_token)):
    import time
    active_times = {pid: round(time.time() - st, 1) for pid, st in rotator.profile_session_times.items()}
    return {
        "is_running": rotator.is_running,
        "max_concurrent": rotator.max_concurrent,
        "active_profiles_count": len(active_times),
        "active_sessions": active_times
    }

from backend.cloudflare_manager import cloudflare_manager

@app.get("/api/cloudflare/status")
def get_cloudflare_status(_auth: None = Depends(require_admin_token)):
    """Returns health status of all Cloudflare AI accounts in the pool."""
    # Reload from file each time so new accounts appear immediately
    cloudflare_manager.load_accounts()

    return {
        "total_accounts": cloudflare_manager.total_accounts,
        "healthy_count": cloudflare_manager.healthy_count,
        "cooldown_count": cloudflare_manager.cooldown_count,
        "model": "@cf/moonshotai/kimi-k2.7-code",
        "accounts": cloudflare_manager.get_all_status()
    }

def _redact_cloudflare_error(message: Any) -> str:
    """Remove credential-like values before returning Cloudflare error details."""
    text = str(message)
    # Drop any Authorization Bearer token that may appear in raw responses/exceptions.
    text = re.sub(r"Bearer\s+\S+", "Bearer <redacted>", text, flags=re.IGNORECASE)
    # Redact Cloudflare account IDs (32-character hex) when embedded in URL paths.
    text = re.sub(r"/accounts/[0-9a-f]{32}/", "/accounts/<redacted>/", text, flags=re.IGNORECASE)
    return text


@app.post("/api/cloudflare/test")
async def test_cloudflare_account(_auth: None = Depends(require_admin_token)):
    """
    Makes a REAL test call to Cloudflare Workers AI to verify credentials work.
    Returns success/failure with detailed diagnosis.
    """
    import httpx
    cloudflare_manager.load_accounts()

    if not cloudflare_manager.accounts:
        return {
            "status": "error",
            "message": "No accounts loaded. Add real accounts to cloudflare_accounts.txt",
            "format": "ACCOUNT_ID:API_TOKEN (one per line)"
        }

    account = cloudflare_manager.get_account()
    if not account:
        return {"status": "error", "message": "All accounts on cooldown."}

    account_id = account["account_id"]
    token = account["token"]
    model = "@cf/moonshotai/kimi-k2.7-code"
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"messages": [{"role": "user", "content": "Reply with: OK"}]}
            )

        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                return {
                    "status": "success",
                    "message": f"✅ Account {account_id[:8]}... is working! Kimi AI responded correctly.",
                    "account_id_prefix": account_id[:8] + "...",
                    "http_code": 200
                }
            else:
                return {
                    "status": "error",
                    "message": f"API returned success=false: {_redact_cloudflare_error(data.get('errors'))}",
                    "http_code": 200
                }
        elif response.status_code == 401:
            return {
                "status": "error",
                "message": "❌ 401 Unauthorized — your API Token is invalid or expired.",
                "fix": "Go to dash.cloudflare.com > My Profile > API Tokens and create a new Workers AI token.",
                "http_code": 401
            }
        elif response.status_code == 403:
            return {
                "status": "error",
                "message": "❌ 403 Forbidden — token exists but lacks Workers AI permission.",
                "fix": "Create a new token with 'Workers AI:Run' permission.",
                "http_code": 403
            }
        elif response.status_code == 404:
            return {
                "status": "error",
                "message": f"❌ 404 Not Found — Account ID '{account_id[:8]}...' may be wrong, or Workers AI not enabled.",
                "fix": "1. Go to dash.cloudflare.com. Your Account ID is shown top-right (32-char hex). 2. Make sure Workers AI is enabled for your account.",
                "http_code": 404
            }
        else:
            return {
                "status": "error",
                "message": f"HTTP {response.status_code}: {_redact_cloudflare_error(response.text[:200])}",
                "http_code": response.status_code
            }
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {_redact_cloudflare_error(e)}"}
from backend.proxy_manager import proxy_manager, redact_proxy_record

class AddProxiesModel(BaseModel):
    proxies: list[ProxyModel]

@app.post("/api/proxies")
def add_proxies(data: AddProxiesModel, _auth: None = Depends(require_admin_token)):
    # Convert Pydantic models to dicts
    proxy_dicts = [p.model_dump() for p in data.proxies]
    added = proxy_manager.add_proxies(proxy_dicts)
    return {"status": "success", "added": added}

@app.get("/api/proxies")
def get_proxies(_auth: None = Depends(require_admin_token)):
    # Merge manager proxies with the free pool for the UI
    try:
        import json
        pool_file = os.path.join(os.path.dirname(__file__), "..", "profiles_data", "proxy_pool.json")
        if os.path.exists(pool_file):
            with open(pool_file, "r") as f:
                return _redact_sensitive_api_data(json.load(f))
    except Exception: pass
    return _redact_sensitive_api_data(proxy_manager._get_active_proxies())

class ScrapeConfigModel(BaseModel):
    target_count: int = 20

@app.post("/api/proxies/scrape")
async def scrape_free_proxies(config: ScrapeConfigModel, _auth: None = Depends(require_admin_token)):
    from backend.proxy_scraper import proxy_scraper
    # Run it asynchronously so we don't block the server fully, but we wait for it to return
    added = await proxy_scraper.run_scraper(target_count=config.target_count)

    # Reload proxy manager so it picks up the new proxies
    proxy_manager._load_proxies()
    return {"status": "success", "message": f"Scraped and validated {added} free proxies"}

# H2 FIX: Add missing POST /api/proxies/test endpoint that frontend app.js calls
@app.post("/api/proxies/test")
async def test_all_proxies(_auth: None = Depends(require_admin_token)):
    """Run health checks on all active proxies and return results."""
    import asyncio
    proxies = proxy_manager._get_active_proxies()
    if not proxies:
        return {"status": "success", "message": "No active proxies to test.", "alive": 0, "total": 0}

    alive = 0
    dead = 0

    async def check_one(p):
        nonlocal alive, dead
        server = p.get("server", "")
        if not server:
            return
        ok = await proxy_manager.check_proxy_health(p)
        if ok:
            alive += 1
        else:
            dead += 1

    # Run checks concurrently in batches of 20 to avoid overwhelming
    batch_size = 20
    for i in range(0, len(proxies), batch_size):
        batch = proxies[i:i+batch_size]
        await asyncio.gather(*[check_one(p) for p in batch])

    total = len(proxies)
    msg = f"Health check complete: {alive}/{total} alive, {dead} dead."
    return {"status": "success", "message": msg, "alive": alive, "total": total, "dead": dead}

# system_monitor now imported at top of file (H4+H5 FIX)

# LOW-09 FIX: startup/shutdown are now handled by the lifespan context manager above.

@app.get("/api/system/health")
def get_system_health():
    return system_monitor.get_health()


@app.get("/api/system/admin-token-hint")
def get_admin_token_hint(request: Request):
    """Return the configured admin token for loopback clients only.

    The management UI is served by this same local process, so it cannot know
    the admin token by itself.  This convenience endpoint hands the token to
    the dashboard over loopback (127.0.0.1 / ::1) so team members never have
    to paste it manually.  Remote clients always receive 403.
    """
    host = getattr(request.client, "host", "") or ""
    # testclient host is only used by TestClient in the test suite; the peer
    # host of a real request is always the TCP origin, so it cannot spoof this.
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Admin token hint is loopback-only")
    token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="Admin token is not configured")
    return {"token": token}


@app.get("/api/sites/access-log")
def get_site_access_log(_auth: None = Depends(require_admin_token)):
    from backend.api_access_logger import get_site_log
    return {"sites": get_site_log()}


@app.delete("/api/sites/access-log")
def clear_site_access_log(_auth: None = Depends(require_admin_token)):
    from backend.api_access_logger import clear_site_log
    clear_site_log()
    return {"status": "success"}

from backend.config import get_data_dir, get_bundled_dir

@app.get("/api/metrics")
def get_metrics(_auth: None = Depends(require_admin_token)):
    import json

    # Count total quarantined
    quarantine_meta = os.path.join(get_data_dir("quarantined_profiles"), "quarantine_meta.json")
    quarantine_count = 0
    if os.path.exists(quarantine_meta):
        try:
            with open(quarantine_meta, "r") as f:
                quarantine_count = len(json.load(f))
        except Exception:
            pass

    # Count total anomalies logged
    log_file = os.path.join(get_data_dir("logs"), "app.log")
    anomaly_count = 0
    if os.path.exists(log_file):
        try:
            with open(log_file, "r") as f:
                for line in f:
                    if "anomaly" in line:
                        anomaly_count += 1
        except Exception:
            pass

    # Deliberately project the store status into this public contract rather
    # than returning its provider, location, or encrypted account material.
    try:
        credential_status = store_status()
        credential_store = {
            "configured": bool(credential_status.get("configured", False)),
            "count": int(credential_status.get("count", 0)),
        }
    except Exception:
        credential_store = {"configured": False, "count": 0}

    return {
        "active_profiles": len(active_browsers),
        "total_profiles": len(profile_manager.list_profiles()),
        "quarantined_profiles": quarantine_count,
        "total_anomalies": anomaly_count,
        "memory_usage_percent": system_monitor.ram_usage,
        "host_os": host_os(),
        "credential_store": credential_store,
    }

# ---------------------------------------------------------------------------
# Cookie Robot — smart geo-targeted cookie warming
# ---------------------------------------------------------------------------

from backend.cookie_robot import cookie_robot

class CookieRobotStartModel(BaseModel):
    profile_ids: List[str]
    min_sites: int = 10
    max_sites: int = 20

@app.post("/api/cookie-robot/start")
async def cookie_robot_start(data: CookieRobotStartModel, _auth: None = Depends(require_admin_token)):
    """Start cookie warming for one or more profiles."""
    result = await cookie_robot.start_warming(data.profile_ids, data.min_sites, data.max_sites)
    return result

@app.get("/api/cookie-robot/status/{profile_id}")
def cookie_robot_status(profile_id: str, _auth: None = Depends(require_admin_token)):
    """Get cookie warming status for a specific profile."""
    return cookie_robot.get_status(profile_id)

@app.get("/api/cookie-robot/status")
def cookie_robot_all_status(_auth: None = Depends(require_admin_token)):
    """Get cookie warming status for all profiles."""
    return cookie_robot.get_all_status()

@app.post("/api/cookie-robot/stop/{profile_id}")
async def cookie_robot_stop(profile_id: str, _auth: None = Depends(require_admin_token)):
    """Stop a running cookie warming task for a profile."""
    return await cookie_robot.stop_warming(profile_id)

@app.get("/api/system/csrf-token")
async def csrf_token():
    from fastapi.responses import JSONResponse
    import secrets
    token = secrets.token_hex(32)
    response = JSONResponse({"token": token})
    response.set_cookie(key="XSRF-TOKEN", value=token, samesite="strict", httponly=False)
    # Ensure security headers are present
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    return response

@app.get("/api/system/network/tcpip")
async def get_tcpip_manager(_auth: None = Depends(require_admin_token)):
    from backend.tcpip_manager import get_manager
    mgr = get_manager()
    if mgr is None:
        return {"enabled": False}
    return {
        "enabled": mgr.enable,
        "dns_host": mgr.dns_host,
        "dns_port": mgr.dns_port,
        "proxy_relay_port": mgr.proxy_relay_port,
        "drop_aaaa": mgr.drop_aaaa,
        "upstream_proxy": mgr.upstream_proxy,
    }

class LegacyRemoteSyncRequest(BaseModel):
    passphrase: str


@app.post("/api/profiles/{profile_id}/sync/remote")
async def legacy_remote_sync(profile_id: str, req: LegacyRemoteSyncRequest, _auth: None = Depends(require_admin_token)):
    """Legacy one-click remote backup endpoint for a single profile."""
    try:
        archive_bytes = cloud_sync_manager.export_profile(profile_id, req.passphrase)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    archive_b64 = base64.b64encode(archive_bytes).decode("utf-8")
    client = _get_remote_sync_client()
    try:
        result = await asyncio.to_thread(client.upload_profile, profile_id, archive_b64)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError:
        _ghost_logger.exception("Remote sync upload failed for %s", profile_id)
        raise HTTPException(status_code=502, detail="Remote sync upload failed")
    return {"remote": result}


# --- API Routers ---
from backend.team_manager import router as team_router
from backend.profile_transfer import router as profile_transfer_router
from backend.cloud_sync import router as cloud_sync_router

app.include_router(synchronizer_router)
app.include_router(profile_folders_router)
app.include_router(bulk_operations_router)
app.include_router(team_router)
app.include_router(profile_transfer_router)
app.include_router(cloud_sync_router)
app.include_router(update_manager_router)
app.include_router(sbom_router)
app.include_router(detection_score_router)

# Mount frontend
frontend_dir = get_bundled_dir("frontend")
os.makedirs(frontend_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
