"""
RPA Recorder & Replay Module.

Records browser interactions (navigate, click, type, scroll, evaluate) on a
source profile and replays the recorded steps on target profiles.

Endpoints:
  POST /api/rpa/record/start   → start recording on a running profile
  POST /api/rpa/record/stop    → stop recording, returns captured steps
  POST /api/rpa/replay         → replay recorded steps on target profiles
  GET  /api/rpa/status         → current recording/replay status
"""

import asyncio
import uuid
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.logging_config import logger
from backend.browser_manager import active_browsers, is_profile_running, launch_profile

router = APIRouter(tags=["rpa"])

# ---------------------------------------------------------------------------
# In-memory recording state
# ---------------------------------------------------------------------------

_recording: dict | None = None  # {"profile_id": ..., "started_at": ..., "steps": []}

# Store last completed recording for convenience
_last_recording: dict | None = None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RecordStartRequest(BaseModel):
    profile_id: str
    auto_launch: bool = True


class ReplayRequest(BaseModel):
    profile_ids: List[str]
    steps: Optional[List[dict]] = None  # if None, uses last recording
    auto_launch: bool = True
    delay_ms: int = 500  # delay between steps


# ---------------------------------------------------------------------------
# Step injection script — injected into the page to capture user interactions
# ---------------------------------------------------------------------------

_RECORDER_JS = """
(function() {
    if (window.__ghostRPARecorder) return;
    window.__ghostRPARecorder = true;
    window.__ghostRPASteps = [];

    function recordStep(step) {
        step.timestamp = Date.now();
        window.__ghostRPASteps.push(step);
    }

    // Navigation
    let lastUrl = location.href;
    const navObserver = new MutationObserver(() => {
        if (location.href !== lastUrl) {
            recordStep({ type: 'navigate', url: location.href });
            lastUrl = location.href;
        }
    });
    navObserver.observe(document, { subtree: true, childList: true });

    // Clicks
    document.addEventListener('click', function(e) {
        const sel = getSelector(e.target);
        if (sel) recordStep({ type: 'click', selector: sel });
    }, true);

    // Input typing
    document.addEventListener('input', function(e) {
        const sel = getSelector(e.target);
        if (sel && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
            recordStep({ type: 'type', selector: sel, text: e.target.value });
        }
    }, true);

    // Scroll (throttled)
    let scrollTimer;
    document.addEventListener('scroll', function(e) {
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(function() {
            recordStep({ type: 'scroll', x: window.scrollX, y: window.scrollY });
        }, 300);
    }, true);

    function getSelector(el) {
        if (!el || !el.tagName) return null;
        if (el.id) return '#' + el.id;
        if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
        const path = [];
        while (el && el.nodeType === 1) {
            let part = el.tagName.toLowerCase();
            if (el.parentNode) {
                const siblings = Array.from(el.parentNode.children).filter(c => c.tagName === el.tagName);
                if (siblings.length > 1) {
                    part += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
                }
            }
            path.unshift(part);
            el = el.parentNode;
            if (el === document.body) break;
        }
        return path.join(' > ');
    }

    console.log('[GhostBrowser RPA] Recorder injected');
})();
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _inject_recorder(page):
    """Inject the RPA recorder script into a page."""
    await page.evaluate(_RECORDER_JS)


async def _collect_steps(page) -> list[dict]:
    """Retrieve recorded steps from the page."""
    steps = await page.evaluate("window.__ghostRPASteps || []")
    # Clear them so a fresh recording doesn't accumulate
    await page.evaluate("window.__ghostRPASteps = []")
    return list(steps)


async def _execute_step(page, step: dict, delay_ms: int = 0):
    """Execute a single recorded step on a Playwright Page."""
    stype = step.get("type", "")
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000.0)

    if stype == "navigate":
        await page.goto(step["url"], wait_until="domcontentloaded", timeout=30000)
    elif stype == "click":
        await page.click(step["selector"], timeout=10000)
    elif stype == "type":
        await page.fill(step["selector"], step.get("text", ""), timeout=10000)
    elif stype == "scroll":
        x = step.get("x", 0)
        y = step.get("y", 0)
        await page.evaluate(f"window.scrollTo({x}, {y})")
    elif stype == "evaluate":
        await page.evaluate(step.get("script", ""))
    else:
        raise ValueError(f"Unknown step type: {stype}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/rpa/record/start")
async def start_recording(req: RecordStartRequest):
    global _recording

    if _recording:
        raise HTTPException(status_code=409, detail="Recording already in progress. Stop it first.")

    pid = req.profile_id

    if not is_profile_running(pid):
        if not req.auto_launch:
            raise HTTPException(status_code=400, detail="Profile is not running. Set auto_launch=true or launch it first.")
        res = await launch_profile(pid)
        if res.get("status") == "error":
            raise HTTPException(status_code=400, detail=res["message"])

    bd = active_browsers.get(pid)
    if not bd or not bd.get("page"):
        raise HTTPException(status_code=500, detail="Could not acquire page for recording.")

    await _inject_recorder(bd["page"])

    _recording = {
        "id": str(uuid.uuid4()),
        "profile_id": pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": [],
    }

    logger.info(f"RPA recording started on profile {pid}")
    return {"status": "success", "message": "Recording started", "recording_id": _recording["id"]}


@router.post("/api/rpa/record/stop")
async def stop_recording():
    global _recording, _last_recording

    if not _recording:
        raise HTTPException(status_code=400, detail="No active recording session.")

    pid = _recording["profile_id"]
    bd = active_browsers.get(pid)
    steps = []

    if bd and bd.get("page"):
        try:
            steps = await _collect_steps(bd["page"])
        except Exception as exc:
            logger.warning(f"Failed to collect RPA steps: {exc}")
    else:
        logger.warning(f"Profile {pid} not running during RPA stop — returning {len(_recording['steps'])} buffered steps")

    _recording["steps"] = steps
    _recording["stopped_at"] = datetime.now(timezone.utc).isoformat()
    _last_recording = _recording
    finished = _recording
    _recording = None

    logger.info(f"RPA recording stopped — {len(steps)} steps captured")
    return {"status": "success", "steps": steps, "total": len(steps), "recording": finished}


@router.post("/api/rpa/replay")
async def replay_steps(req: ReplayRequest):
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one target profile is required.")

    # Determine steps source
    steps = req.steps
    if not steps:
        if not _last_recording:
            raise HTTPException(status_code=400, detail="No steps provided and no previous recording found.")
        steps = _last_recording.get("steps", [])

    if not steps:
        raise HTTPException(status_code=400, detail="No steps to replay.")

    results = []

    async def replay_single(pid: str):
        # Ensure profile is running
        if not is_profile_running(pid):
            if not req.auto_launch:
                return {"profile_id": pid, "status": "error", "message": "Not running and auto_launch=false"}
            res = await launch_profile(pid)
            if res.get("status") == "error":
                return {"profile_id": pid, "status": "error", "message": res.get("message", "Launch failed")}

        bd = active_browsers.get(pid)
        if not bd or not bd.get("page"):
            return {"profile_id": pid, "status": "error", "message": "Could not acquire page"}

        page = bd["page"]
        step_results = []
        for i, step in enumerate(steps):
            try:
                await _execute_step(page, step, delay_ms=req.delay_ms if i > 0 else 0)
                step_results.append({"step": i, "type": step.get("type"), "status": "success"})
            except Exception as exc:
                step_results.append({"step": i, "type": step.get("type"), "status": "error", "message": str(exc)})
                break

        succeeded = sum(1 for s in step_results if s["status"] == "success")
        return {
            "profile_id": pid,
            "status": "success" if succeeded == len(steps) else "partial",
            "steps_executed": len(step_results),
            "steps_succeeded": succeeded,
            "steps_total": len(steps),
            "step_results": step_results,
        }

    tasks = [replay_single(pid) for pid in req.profile_ids]
    results = await asyncio.gather(*tasks)

    total_succeeded = sum(1 for r in results if r["status"] == "success")
    logger.info(f"RPA replay on {len(req.profile_ids)} profiles — {total_succeeded} fully succeeded")

    return {
        "status": "success",
        "profiles_targeted": len(req.profile_ids),
        "profiles_succeeded": total_succeeded,
        "steps_replayed": len(steps),
        "results": results,
    }


@router.get("/api/rpa/status")
def rpa_status():
    return {
        "recording": _recording is not None,
        "recording_session": _recording,
        "has_last_recording": _last_recording is not None,
        "last_recording_steps": len(_last_recording["steps"]) if _last_recording else 0,
    }
