from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uvicorn
import os
import sys
import asyncio
import logging
import secrets
import hashlib as _hashlib

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from backend.profile_manager import profile_manager
from backend.browser_manager import launch_profile, close_profile, is_profile_running, active_browsers, get_profile_cookies, set_profile_cookies
from backend.macro_manager import macro_manager
from backend.macro_runner import MacroRunner
from backend.config import get_data_dir
from backend.scheduler_manager import SchedulerManager
# H4+H5 FIX: Move system_monitor import to top of file (was at line 569, after its first use in lifespan)
from backend.system_monitor import system_monitor

# --- Agent 1: New API Routers ---
from backend.api_automation import router as api_automation_router
from backend.synchronizer import router as synchronizer_router
from backend.rpa_recorder import router as rpa_recorder_router
from backend.profile_folders import router as profile_folders_router
from backend.bulk_operations import router as bulk_operations_router

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
    scheduler_manager.runner.browser_manager = bm
    await system_monitor.start()
    scheduler_manager.start()
    yield
    # Shutdown
    system_monitor.stop()
    scheduler_manager.stop()
    # Close shared httpx client to prevent resource leak (CRIT-07)
    from backend.ai_generator import _shared_client
    await _shared_client.aclose()

# --- Rate limiter for profile creation (max 5 concurrent) ---
_profile_create_sem = asyncio.Semaphore(5)

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
async def log_500_errors(request, call_next):
    response = await call_next(request)
    if response.status_code >= 500:
        _ghost_logger.error(
            "500-error | path=%s | method=%s",
            request.url.path,
            request.method,
        )
    return response

app.add_middleware(
    CORSMiddleware,
    # CRIT-01 FIX: Restrict to localhost only. Wildcard + credentials is a CSRF vulnerability.
    allow_origins=["http://127.0.0.1:8888", "http://localhost:8888"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- Admin token authentication for mutation endpoints ---
ADMIN_TOKEN = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
if not ADMIN_TOKEN:
    ADMIN_TOKEN = "bootstrap_" + secrets.token_urlsafe(16)
    import logging as _log
    _log.getLogger("ghostbrowser").warning("GHOSTBROWSER_ADMIN_TOKEN not set — using bootstrap token (set env var for production)")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    # Allow unauthenticated reads for health/docs, but protect all profile/data endpoints
    if request.method in ("GET", "HEAD", "OPTIONS"):
        if request.url.path in ("/api/health", "/api/metrics"):
            return await call_next(request)
        # Check if this is a sensitive GET endpoint
        sensitive_prefixes = ("/api/profiles", "/api/proxies", "/api/cookies", "/api/cloudflare")
        if any(request.url.path.startswith(p) for p in sensitive_prefixes):
            token = request.headers.get("X-Admin-Token") or request.headers.get("Authorization", "").replace("Bearer ", "")
            if not _timing_safe_token_check(token, ADMIN_TOKEN):
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key/token"})
        return await call_next(request)

    token = request.headers.get("X-Admin-Token") or request.headers.get("Authorization", "").replace("Bearer ", "")

    if not _timing_safe_token_check(token, ADMIN_TOKEN):
        from backend.api_automation import _API_KEYS_FILE
        import json
        api_key_valid = False
        if os.path.exists(_API_KEYS_FILE):
            try:
                with open(_API_KEYS_FILE, "r") as f:
                    keys_data = json.load(f)
                key_hash = _hashlib.sha256(token.encode()).hexdigest()
                api_key_valid = key_hash in [k.get("key_hash") for k in keys_data.values()]
            except Exception:
                pass

        if not api_key_valid:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key/token"})

    return await call_next(request)


def _timing_safe_token_check(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return _hashlib.sha256(provided.encode()).digest() == _hashlib.sha256(expected.encode()).digest()



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
    headless: bool = False

class CreateProfileModel(BaseModel):
    name: str
    proxy: Optional[ProxyModel] = None
    proxy_string: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    advanced: Optional[AdvancedSettingsModel] = None

class UpdateProfileModel(BaseModel):
    name: str
    proxy: Optional[ProxyModel] = None
    proxy_string: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    advanced: Optional[AdvancedSettingsModel] = None
    pinned: Optional[bool] = None
    color: Optional[str] = None

def parse_proxy_string(proxy_str: str) -> Optional[dict]:
    if not proxy_str: return None
    parts = proxy_str.split(':')
    if len(parts) == 2:
        return {"server": f"http://{parts[0]}:{parts[1]}"}
    elif len(parts) == 4:
        return {
            "server": f"http://{parts[0]}:{parts[1]}",
            "username": parts[2],
            "password": parts[3]
        }
    return None

from backend.profile_creator import profile_creator

@app.post("/api/profiles")
async def create_profile(data: CreateProfileModel):
    """
    Creates a Zero-Leak profile using the full Kimi AI → Coherence → LeakScan pipeline.
    NO profile is ever created without Kimi AI successfully generating the fingerprint.
    """
    async with _profile_create_sem:
        proxy_dict = data.proxy.model_dump() if data.proxy else parse_proxy_string(data.proxy_string)

        # Run the full Zero-Leak Orchestrator
        advanced_dict = data.advanced.model_dump() if data.advanced else None
        result = await profile_creator.create_zero_leak_profile(
            name=data.name,
            proxy=proxy_dict,
            advanced_ui=advanced_dict
        )

        if result["status"] == "error":
            code = result.get("code", "CREATION_FAILED")
            raise HTTPException(status_code=503 if code == "KIMI_UNAVAILABLE" else 400, detail=result["message"])

        return result["profile"]

@app.post("/api/profiles/generate")
async def generate_profile(data: CreateProfileModel):
    """Alias for POST /api/profiles — triggers full Kimi AI zero-leak creation."""
    return await create_profile(data)

class BulkCreateProfileModel(BaseModel):
    base_name: str
    count: int = 5
    proxy: Optional[dict] = None
    advanced: Optional[dict] = None

@app.post("/api/profiles/generate/bulk")
async def generate_bulk_profiles(data: BulkCreateProfileModel):
    """Generate multiple profiles concurrently via Kimi AI, with concurrency limits."""
    count = min(data.count, 50) # Cap at 50 to prevent overload
    
    # Limit to 8 concurrent creations to prevent Playwright leak scanner from melting the CPU
    sem = asyncio.Semaphore(8)
    
    async def create_single(i):
        name = f"{data.base_name}_{i+1}"
        from backend.profile_creator import ProfileCreationOrchestrator
        orchestrator = ProfileCreationOrchestrator()
        try:
            async with sem:
                return await orchestrator.create_zero_leak_profile(name, data.proxy, data.advanced)
        except Exception as e:
            return {"status": "error", "message": str(e), "name": name}
            
    tasks = [create_single(i) for i in range(count)]
    results = await asyncio.gather(*tasks)
    
    success_count = sum(1 for r in results if r.get("status") == "success")
    
    return {
        "status": "success",
        "message": f"Successfully created {success_count} out of {count} profiles",
        "results": results
    }

@app.get("/api/profiles")
def list_profiles():
    profiles = profile_manager.list_profiles()
    for p in profiles:
        p["status"] = "Running" if is_profile_running(p["id"]) else "Stopped"
    return profiles

@app.post("/api/profiles/{profile_id}/clone")
async def clone_profile(profile_id: str):
    """Smart Duplicate a profile: Re-runs Kimi AI to generate a fresh, unique fingerprint but copies metadata, proxy, and tags."""
    original = profile_manager.get_profile(profile_id)
    if not original:
        raise HTTPException(status_code=404, detail="Original profile not found")
        
    name = original.get("name", "Unknown") + " (Clone)"
    proxy = original.get("proxy")
    advanced = original.get("advanced", {})
    
    from backend.profile_creator import ProfileCreationOrchestrator
    orchestrator = ProfileCreationOrchestrator()
    result = await orchestrator.create_zero_leak_profile(name, proxy, advanced)
    
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
        
    new_profile = result["profile"]
    
    # Copy tags and notes
    updates = {}
    if original.get("tags"): updates["tags"] = original.get("tags")
    if original.get("notes"): updates["notes"] = original.get("notes")
    if original.get("proxy_pin"): updates["proxy_pin"] = original.get("proxy_pin")
    
    if updates:
        profile_manager.update_profile(new_profile["id"], updates)
        
    return {"status": "success", "profile": profile_manager.get_profile(new_profile["id"])}

@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    await close_profile(profile_id)
    success = profile_manager.delete_profile(profile_id)
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}

class RenameRequest(BaseModel):
    name: str

@app.patch("/api/profiles/{profile_id}/rename")
async def rename_profile(profile_id: str, req: RenameRequest):
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

@app.patch("/api/profiles/{profile_id}/metadata")
async def update_metadata(profile_id: str, req: UpdateMetadataRequest):
    updates = {}
    if req.tags is not None: updates["tags"] = req.tags
    if req.notes is not None: updates["notes"] = req.notes
    if req.proxy_pin is not None: updates["proxy_pin"] = req.proxy_pin
    success = profile_manager.update_profile(profile_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}

@app.put("/api/profiles/{profile_id}")
async def edit_profile(profile_id: str, data: UpdateProfileModel):
    """Full update for a profile's settings, proxy, and fingerprint."""
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
        
    proxy_dict = data.proxy.model_dump() if data.proxy else parse_proxy_string(data.proxy_string)
    advanced_dict = data.advanced.model_dump() if data.advanced else {}
    
    updates = {
        "name": data.name,
        "proxy": proxy_dict,
        "timezone": data.timezone,
        "locale": data.locale,
        "advanced": advanced_dict
    }
    
    if data.pinned is not None:
        updates["pinned"] = data.pinned
    if data.color is not None:
        updates["color"] = data.color
    
    # Remove None values so we don't accidentally wipe out stuff
    updates = {k: v for k, v in updates.items() if v is not None}
    
    success = profile_manager.update_profile(profile_id, updates)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save profile edits")
        
    return {"status": "success", "message": "Profile updated successfully"}


@app.get("/api/profiles/{profile_id}/export")
async def export_profile(profile_id: str):
    """Export a single profile's full config as downloadable JSON"""
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    from fastapi.responses import JSONResponse
    export_data = {k: v for k, v in profile.items() if k not in ["path", "status"]}
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f"attachment; filename=profile_{profile_id[:8]}.json"}
    )
@app.get("/api/profiles/{profile_id}/fingerprint")
async def get_fingerprint(profile_id: str):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile.get("advanced", {})

@app.get("/api/profiles/{profile_id}/scan")
async def scan_profile(profile_id: str):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
        
    fingerprint = profile.get("advanced", {})
    from backend.ai_auto_validator import AIAutoValidator
    validator = AIAutoValidator()
    result = await validator.validate_profile(profile, fingerprint)
    
    return {"status": "success", "scan": result}

class UpdateFingerprintRequest(BaseModel):
    advanced: dict

@app.patch("/api/profiles/{profile_id}/fingerprint")
async def update_fingerprint(profile_id: str, req: UpdateFingerprintRequest):
    success = profile_manager.update_profile(profile_id, {"advanced": req.advanced})
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}

class ScheduleRequest(BaseModel):
    macro_id: str
    profile_ids: List[str]
    cron: str

@app.get("/api/macros/schedule")
def list_schedules():
    return scheduler_manager.list_schedules()

@app.post("/api/macros/schedule")
def create_schedule(req: ScheduleRequest):
    return scheduler_manager.add_schedule(req.macro_id, req.profile_ids, req.cron)

@app.delete("/api/macros/schedule/{job_id}")
def delete_schedule(job_id: str):
    success = scheduler_manager.delete_schedule(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "success"}

@app.get("/api/profiles/{profile_id}/cookies")
async def get_cookies_api(profile_id: str):
    res = await get_profile_cookies(profile_id)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

class CookieImportRequest(BaseModel):
    cookies: List[Dict[str, Any]]

@app.post("/api/profiles/{profile_id}/cookies")
async def set_cookies_api(profile_id: str, req: CookieImportRequest):
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
def list_macros():
    return macro_manager.list_macros()

@app.post("/api/macros")
def create_macro(req: MacroCreateRequest):
    if not req.name.strip() or not req.steps:
        raise HTTPException(status_code=400, detail="Name and steps are required")
    return macro_manager.create_macro(req.name.strip(), req.description, req.steps)

@app.delete("/api/macros/{macro_id}")
def delete_macro(macro_id: str):
    return {"status": "success"} if macro_manager.delete_macro(macro_id) else {"status": "error"}

@app.get("/api/proxies/titan")
def get_titan_proxies():
    """Fetches the top proxies from the Titan proxy database"""
    import backend.db as db
    proxies = db.get_best_proxies(limit=100)
    return {"status": "success", "proxies": proxies}

class BulkMacroRunRequest(BaseModel):
    profile_ids: List[str]
    macro_id: str

@app.post("/api/macros/run/bulk")
async def run_bulk_macro(req: BulkMacroRunRequest):
    macro = macro_manager.get_macro(req.macro_id)
    if not macro:
        raise HTTPException(status_code=404, detail="Macro not found")
        
    # Launch in background
    asyncio.create_task(MacroRunner.run_macro_bulk(req.profile_ids, macro))
    return {"status": "success", "message": f"Macro {macro['name']} started on {len(req.profile_ids)} profiles"}

# --- End Macros API ---

@app.post("/api/profiles/{profile_id}/launch")
async def launch_profile_api(profile_id: str):
    res = await launch_profile(profile_id)
    if res["status"] == "error":
        raise HTTPException(status_code=400, detail=res["message"])
    return res

@app.post("/api/profiles/{profile_id}/close")
async def close_profile_api(profile_id: str):
    res = await close_profile(profile_id)
    if res["status"] == "error":
        raise HTTPException(status_code=400, detail=res["message"])
    return res

class CookieDataModel(BaseModel):
    cookies: list

@app.post("/api/profiles/{profile_id}/cookies/import")
async def import_cookies(profile_id: str, data: CookieDataModel):
    if profile_id not in active_browsers:
        raise HTTPException(status_code=400, detail="Profile must be running to import cookies.")
    browser_data = active_browsers[profile_id]
    context = browser_data["context"]
    try:
        await context.add_cookies(data.cookies)
        return {"status": "success", "message": "Cookies imported successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to import cookies: {e}")

@app.get("/api/profiles/{profile_id}/cookies/export")
async def export_cookies(profile_id: str):
    if profile_id not in active_browsers:
        raise HTTPException(status_code=400, detail="Profile must be running to export cookies.")
    browser_data = active_browsers[profile_id]
    context = browser_data["context"]
    try:
        cookies = await context.cookies()
        return {"status": "success", "cookies": cookies}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to export cookies: {e}")

@app.post("/api/profiles/bulk/tag")
async def bulk_tag_profiles(data: dict):
    """Add tags to multiple profiles"""
    profile_ids = data.get("profile_ids", [])
    tags = data.get("tags", [])
    if not profile_ids or not tags:
        raise HTTPException(status_code=400, detail="profile_ids and tags required")
    count = 0
    for pid in profile_ids:
        profile = profile_manager.get_profile(pid)
        if profile:
            existing = profile.get("tags", [])
            if isinstance(existing, str):
                existing = [t.strip() for t in existing.split(",") if t.strip()]
            new_tags = list(set(existing + tags))
            profile_manager.update_profile(pid, {"tags": new_tags})
            count += 1
    return {"status": "success", "message": f"Tagged {count} profiles"}

@app.post("/api/profiles/bulk/folder")
async def bulk_move_to_folder(data: dict):
    """Move multiple profiles to a folder"""
    profile_ids = data.get("profile_ids", [])
    folder_id = data.get("folder_id")
    if not profile_ids:
        raise HTTPException(status_code=400, detail="profile_ids required")
    count = 0
    for pid in profile_ids:
        if profile_manager.update_profile(pid, {"folder_id": folder_id}):
            count += 1
    return {"status": "success", "message": f"Moved {count} profiles"}

from backend.profile_rotator import rotator

class RotatorConfigModel(BaseModel):
    max_concurrent: int = 15

@app.post("/api/rotator/start")
async def start_rotator(config: RotatorConfigModel):
    rotator.max_concurrent = config.max_concurrent
    await rotator.start()
    return {"status": "success", "message": f"Rotator started with max {config.max_concurrent} profiles."}

@app.post("/api/rotator/stop")
def stop_rotator():
    rotator.stop()
    return {"status": "success", "message": "Rotator stopped."}

@app.get("/api/rotator/status")
def get_rotator_status():
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
def get_cloudflare_status():
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

@app.post("/api/cloudflare/test")
async def test_cloudflare_account():
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
                    "message": f"API returned success=false: {data.get('errors')}",
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
                "message": f"HTTP {response.status_code}: {response.text[:200]}",
                "http_code": response.status_code
            }
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

@app.post("/api/cloudflare/import")
def import_cloudflare_accounts(data: dict):
    """
    Import Cloudflare accounts from text.
    Expects {"text": "ACCOUNT_ID\tAPI_TOKEN\\n..."} format (tab-separated, one per line).
    """
    raw = data.get("text", "")
    if not raw.strip():
        return {"status": "error", "message": "Empty text"}
    
    accounts_file = os.path.join(os.path.dirname(__file__), "..", "cloudflare_accounts.txt")
    os.makedirs(os.path.dirname(accounts_file), exist_ok=True)
    
    count = 0
    skipped = 0
    lines = []
    
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t", 1)]
        elif "," in line:
            parts = [p.strip() for p in line.split(",", 1)]
        elif ":" in line:
            parts = [p.strip() for p in line.split(":", 1)]
        else:
            skipped += 1
            continue
        
        parts = [p for p in parts if p]
        
        if len(parts) >= 2:
            account_id = parts[0]
            api_token = parts[1]
            if any(x in account_id.lower() for x in ["your_real", "example", "account_id"]):
                skipped += 1
                continue
            if any(x in api_token.lower() for x in ["your_real", "sk-kimi-token", "api_token"]):
                skipped += 1
                continue
            lines.append(f"{account_id}:{api_token}")
            count += 1
        else:
            skipped += 1
    
    if count == 0:
        return {"status": "error", "message": f"No valid accounts found. Skipped {skipped} lines."}
    
    with open(accounts_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    
    cloudflare_manager.load_accounts()
    
    return {
        "status": "success",
        "imported": count,
        "skipped": skipped,
        "total_loaded": cloudflare_manager.total_accounts
    }

from backend.proxy_manager import proxy_manager

class AddProxiesModel(BaseModel):
    proxies: list[ProxyModel]

@app.post("/api/proxies")
def add_proxies(data: AddProxiesModel):
    # Convert Pydantic models to dicts
    proxy_dicts = [p.model_dump() for p in data.proxies]
    added = proxy_manager.add_proxies(proxy_dicts)
    return {"status": "success", "added": added}

@app.get("/api/proxies")
def get_proxies():
    # Merge manager proxies with the free pool for the UI
    try:
        import json
        pool_file = os.path.join(os.path.dirname(__file__), "..", "profiles_data", "proxy_pool.json")
        if os.path.exists(pool_file):
            with open(pool_file, "r") as f:
                return json.load(f)
    except Exception: pass
    return proxy_manager._get_active_proxies()

class ScrapeConfigModel(BaseModel):
    target_count: int = 20
    
@app.post("/api/proxies/scrape")
async def scrape_free_proxies(config: ScrapeConfigModel):
    from backend.proxy_scraper import proxy_scraper
    # Run it asynchronously so we don't block the server fully, but we wait for it to return
    added = await proxy_scraper.run_scraper(target_count=config.target_count)
    
    # Reload proxy manager so it picks up the new proxies
    proxy_manager._load_proxies()
    return {"status": "success", "message": f"Scraped and validated {added} free proxies"}

# H2 FIX: Add missing POST /api/proxies/test endpoint that frontend app.js calls
@app.post("/api/proxies/test")
async def test_all_proxies():
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

from backend.config import get_data_dir, get_bundled_dir

@app.get("/api/metrics")
def get_metrics():
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

    return {
        "active_profiles": len(active_browsers),
        "total_profiles": len(profile_manager.list_profiles()),
        "quarantined_profiles": quarantine_count,
        "total_anomalies": anomaly_count,
        "memory_usage_percent": system_monitor.ram_usage
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
async def cookie_robot_start(data: CookieRobotStartModel):
    """Start cookie warming for one or more profiles."""
    result = await cookie_robot.start_warming(data.profile_ids, data.min_sites, data.max_sites)
    return result

@app.get("/api/cookie-robot/status/{profile_id}")
def cookie_robot_status(profile_id: str):
    """Get cookie warming status for a specific profile."""
    return cookie_robot.get_status(profile_id)

@app.get("/api/cookie-robot/status")
def cookie_robot_all_status():
    """Get cookie warming status for all profiles."""
    return cookie_robot.get_all_status()

@app.post("/api/cookie-robot/stop/{profile_id}")
async def cookie_robot_stop(profile_id: str):
    """Stop a running cookie warming task for a profile."""
    return await cookie_robot.stop_warming(profile_id)


@app.post("/api/folders")
async def create_folder(data: dict):
    """Create a new folder"""
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name required")
    from backend.profile_folders import profile_folders
    folder = profile_folders.create_folder(name)
    return {"status": "success", "folder": folder}
# --- Register new API routers (Agent 1) ---
app.include_router(api_automation_router)
app.include_router(synchronizer_router)
app.include_router(rpa_recorder_router)
app.include_router(profile_folders_router)
app.include_router(bulk_operations_router)

# --- Register Agent 4 routers (team, profile transfer, api keys) ---
from backend.team_manager import router as team_router
from backend.profile_transfer import router as profile_transfer_router
from backend.api_keys import router as api_keys_router
app.include_router(team_router)
app.include_router(profile_transfer_router)
app.include_router(api_keys_router)

# Mount frontend
frontend_dir = get_bundled_dir("frontend")
os.makedirs(frontend_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8888, reload=True)
