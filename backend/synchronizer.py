"""
Multi-Window Action Synchronizer.

Broadcasts browser actions (navigate, click, type, scroll, …) to multiple
running browser profiles simultaneously so they act in unison.

Endpoints:
  POST /api/sync/start   → start a sync session with a set of profile IDs
  POST /api/sync/stop    → stop the active sync session
  POST /api/sync/action  → broadcast a single action to all synced profiles
  GET  /api/sync/status  → current sync session status
"""

import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.logging_config import logger
from backend.browser_manager import active_browsers, is_profile_running

router = APIRouter(tags=["synchronizer"])

# ---------------------------------------------------------------------------
# In-memory state — single active sync session at a time
# ---------------------------------------------------------------------------

_sync_state: dict | None = None  # {"profile_ids": [...], "started_at": ...}

# Supported action types and their required fields
_ACTION_VALIDATORS = {
    "navigate": {"url"},
    "click": {"selector"},
    "type": {"selector", "text"},
    "scroll": set(),  # optional x/y
    "wait": {"ms"},
    "reload": set(),
    "go_back": set(),
    "go_forward": set(),
    "evaluate": {"script"},
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class SyncStartRequest(BaseModel):
    profile_ids: List[str]


class SyncActionRequest(BaseModel):
    action_type: str
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    ms: Optional[int] = None
    script: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_synced_pages():
    """Return a list of (profile_id, page) for all synced & running profiles."""
    if not _sync_state:
        return []
    pages = []
    for pid in _sync_state["profile_ids"]:
        bd = active_browsers.get(pid)
        if bd and bd.get("page"):
            pages.append((pid, bd["page"]))
    return pages


async def _execute_action(page, action: SyncActionRequest):
    """Execute a single action on one Playwright Page object."""
    at = action.action_type

    if at == "navigate":
        await page.goto(action.url, wait_until="domcontentloaded", timeout=30000)
    elif at == "click":
        await page.click(action.selector, timeout=10000)
    elif at == "type":
        await page.fill(action.selector, action.text, timeout=10000)
    elif at == "scroll":
        x = action.x or 0
        y = action.y or 0
        await page.evaluate(f"window.scrollBy({x}, {y})")
    elif at == "wait":
        await asyncio.sleep(action.ms / 1000.0 if action.ms else 1)
    elif at == "reload":
        await page.reload(wait_until="domcontentloaded", timeout=30000)
    elif at == "go_back":
        await page.go_back(timeout=15000)
    elif at == "go_forward":
        await page.go_forward(timeout=15000)
    elif at == "evaluate":
        await page.evaluate(action.script)
    else:
        raise ValueError(f"Unknown action type: {at}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/sync/start")
def start_sync(req: SyncStartRequest):
    global _sync_state
    if _sync_state:
        raise HTTPException(status_code=409, detail="A sync session is already active. Stop it first.")
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one profile_id is required.")

    _sync_state = {
        "profile_ids": req.profile_ids,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "actions_sent": 0,
    }
    logger.info(f"Sync session started for {len(req.profile_ids)} profiles")
    return {"status": "success", "message": f"Syncing {len(req.profile_ids)} profiles", "session": _sync_state}


@router.post("/api/sync/stop")
def stop_sync():
    global _sync_state
    if not _sync_state:
        return {"status": "success", "message": "No active sync session."}
    stopped = _sync_state
    _sync_state = None
    logger.info("Sync session stopped")
    return {"status": "success", "message": "Sync session stopped", "session": stopped}


@router.get("/api/sync/status")
def sync_status():
    if not _sync_state:
        return {"active": False}
    running = [pid for pid in _sync_state["profile_ids"] if is_profile_running(pid)]
    return {
        "active": True,
        "session": _sync_state,
        "running_profiles": running,
        "running_count": len(running),
        "total_synced": len(_sync_state["profile_ids"]),
    }


@router.post("/api/sync/action")
async def broadcast_action(req: SyncActionRequest):
    if not _sync_state:
        raise HTTPException(status_code=400, detail="No active sync session. Call /api/sync/start first.")

    # Validate action type
    required = _ACTION_VALIDATORS.get(req.action_type)
    if required is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action_type '{req.action_type}'. Supported: {list(_ACTION_VALIDATORS.keys())}",
        )

    # Check required fields
    for field in required:
        val = getattr(req, field, None)
        if val is None:
            raise HTTPException(status_code=400, detail=f"Action '{req.action_type}' requires field '{field}'")

    pages = _get_synced_pages()
    if not pages:
        raise HTTPException(status_code=400, detail="No synced profiles are currently running.")

    results = []
    tasks = []

    async def run_single(pid: str, page):
        try:
            await _execute_action(page, req)
            return {"profile_id": pid, "status": "success"}
        except Exception as exc:
            return {"profile_id": pid, "status": "error", "message": str(exc)}

    for pid, page in pages:
        tasks.append(run_single(pid, page))

    results = await asyncio.gather(*tasks)

    _sync_state["actions_sent"] += 1
    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Sync action '{req.action_type}' → {success_count}/{len(results)} profiles succeeded")

    return {
        "status": "success",
        "action_type": req.action_type,
        "total": len(results),
        "succeeded": success_count,
        "failed": len(results) - success_count,
        "results": results,
    }
