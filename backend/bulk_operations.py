"""
Bulk Profile Operations.

Create, launch, close, and delete multiple profiles in a single request.
All operations run concurrently with a semaphore to prevent resource exhaustion.

Endpoints:
  POST /api/profiles/bulk/create   → create N profiles
  POST /api/profiles/bulk/launch   → launch multiple profiles
  POST /api/profiles/bulk/close    → close multiple profiles
  POST /api/profiles/bulk/delete   → delete multiple profiles
  POST /api/profiles/bulk/assign-folder → assign multiple profiles to a folder
"""

import asyncio
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.logging_config import logger
from backend.profile_manager import profile_manager
from backend.browser_manager import launch_profile, close_profile, is_profile_running

router = APIRouter(tags=["bulk-operations"])

# Concurrency limits — prevent resource exhaustion
_LAUNCH_SEM = asyncio.Semaphore(8)
_CREATE_SEM = asyncio.Semaphore(8)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BulkCreateRequest(BaseModel):
    base_name: str
    count: int = 5
    proxy: Optional[dict] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    advanced: Optional[dict] = None


class BulkProfileIdsRequest(BaseModel):
    profile_ids: List[str]


class BulkAssignFolderRequest(BaseModel):
    profile_ids: List[str]
    folder_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/profiles/bulk/create")
async def bulk_create_profiles(req: BulkCreateRequest):
    """Create N profiles concurrently (does NOT use the Kimi AI pipeline)."""
    count = max(1, min(req.count, 100))  # cap at 100

    async def create_one(i: int):
        name = f"{req.base_name}_{i + 1}"
        async with _CREATE_SEM:
            try:
                profile = profile_manager.create_profile(
                    name=name,
                    proxy=req.proxy,
                    timezone=req.timezone,
                    locale=req.locale,
                    advanced=req.advanced,
                )
                return {"index": i, "name": name, "status": "success", "profile_id": profile["id"]}
            except Exception as exc:
                return {"index": i, "name": name, "status": "error", "message": str(exc)}

    tasks = [create_one(i) for i in range(count)]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Bulk create: {success_count}/{count} profiles created")

    return {
        "status": "success",
        "message": f"Created {success_count} out of {count} profiles",
        "total": count,
        "succeeded": success_count,
        "failed": count - success_count,
        "results": results,
    }


@router.post("/api/profiles/bulk/launch")
async def bulk_launch_profiles(req: BulkProfileIdsRequest):
    """Launch multiple profiles concurrently."""
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one profile_id is required")

    async def launch_one(pid: str):
        async with _LAUNCH_SEM:
            try:
                res = await launch_profile(pid)
                return {"profile_id": pid, "status": res["status"], "message": res.get("message", "")}
            except Exception as exc:
                return {"profile_id": pid, "status": "error", "message": str(exc)}

    tasks = [launch_one(pid) for pid in req.profile_ids]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Bulk launch: {success_count}/{len(req.profile_ids)} profiles launched")

    return {
        "status": "success",
        "message": f"Launched {success_count} out of {len(req.profile_ids)} profiles",
        "total": len(req.profile_ids),
        "succeeded": success_count,
        "failed": len(req.profile_ids) - success_count,
        "results": results,
    }


@router.post("/api/profiles/bulk/close")
async def bulk_close_profiles(req: BulkProfileIdsRequest):
    """Close multiple profiles concurrently."""
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one profile_id is required")

    async def close_one(pid: str):
        try:
            res = await close_profile(pid)
            return {"profile_id": pid, "status": res["status"], "message": res.get("message", "")}
        except Exception as exc:
            return {"profile_id": pid, "status": "error", "message": str(exc)}

    tasks = [close_one(pid) for pid in req.profile_ids]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Bulk close: {success_count}/{len(req.profile_ids)} profiles closed")

    return {
        "status": "success",
        "message": f"Closed {success_count} out of {len(req.profile_ids)} profiles",
        "total": len(req.profile_ids),
        "succeeded": success_count,
        "failed": len(req.profile_ids) - success_count,
        "results": results,
    }


@router.post("/api/profiles/bulk/delete")
async def bulk_delete_profiles(req: BulkProfileIdsRequest):
    """Delete multiple profiles — closes running browsers first, then deletes."""
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one profile_id is required")

    async def delete_one(pid: str):
        # Close if running
        if is_profile_running(pid):
            try:
                await close_profile(pid)
            except Exception:
                pass
        try:
            success = profile_manager.delete_profile(pid)
            if success:
                return {"profile_id": pid, "status": "success", "message": "Deleted"}
            return {"profile_id": pid, "status": "error", "message": "Profile not found"}
        except Exception as exc:
            return {"profile_id": pid, "status": "error", "message": str(exc)}

    tasks = [delete_one(pid) for pid in req.profile_ids]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Bulk delete: {success_count}/{len(req.profile_ids)} profiles deleted")

    return {
        "status": "success",
        "message": f"Deleted {success_count} out of {len(req.profile_ids)} profiles",
        "total": len(req.profile_ids),
        "succeeded": success_count,
        "failed": len(req.profile_ids) - success_count,
        "results": results,
    }


@router.post("/api/profiles/bulk/assign-folder")
def bulk_assign_folder(req: BulkAssignFolderRequest):
    """Assign multiple profiles to a folder in one call."""
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one profile_id is required")

    results = []
    success_count = 0
    for pid in req.profile_ids:
        profile = profile_manager.get_profile(pid)
        if not profile:
            results.append({"profile_id": pid, "status": "error", "message": "Not found"})
            continue
        ok = profile_manager.update_profile(pid, {"folder_id": req.folder_id})
        if ok:
            results.append({"profile_id": pid, "status": "success"})
            success_count += 1
        else:
            results.append({"profile_id": pid, "status": "error", "message": "Update failed"})

    logger.info(f"Bulk folder assign: {success_count}/{len(req.profile_ids)} profiles updated")
    return {
        "status": "success",
        "message": f"Assigned {success_count} out of {len(req.profile_ids)} profiles",
        "folder_id": req.folder_id,
        "total": len(req.profile_ids),
        "succeeded": success_count,
        "failed": len(req.profile_ids) - success_count,
        "results": results,
    }
