"""
Bulk Profile Operations.

Create, launch, close, and delete multiple profiles in a single request.
All operations run concurrently with a per-operation semaphore (create/launch/
close/delete) to prevent resource exhaustion. Errors are returned as stable
codes and never leak internal exception details to API clients.

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

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, field_validator

from backend.auth import require_admin_token
from backend.logging_config import logger
from backend.profile_manager import profile_manager
from backend.profile_creator import profile_creator
from backend.error_codes import PUBLIC_CODE_MESSAGES, public_message_for
from backend.browser_manager import (
    launch_profile,
    close_profile,
    is_profile_running,
    parse_proxy_string,
)

# Backward-compatible alias for existing tests/callers.
_PUBLIC_CODE_MESSAGES = PUBLIC_CODE_MESSAGES

router = APIRouter(tags=["bulk-operations"])

# Concurrency limits — prevent resource exhaustion
_LAUNCH_SEM = asyncio.Semaphore(8)
_CREATE_SEM = asyncio.Semaphore(8)
_CLOSE_SEM = asyncio.Semaphore(8)
_DELETE_SEM = asyncio.Semaphore(8)


def _bulk_error(pid: str, code: str, message: str) -> dict:
    """Stable error payload. Never leaks exception details to API clients."""
    return {"profile_id": pid, "status": "error", "code": code, "message": message}


# Known public error codes that are safe to surface verbatim. Anything else is
# collapsed into the per-operation default code + message so internal details
# never reach API clients. Catalog lives in ``backend.error_codes`` (A05).
def _public_message_for(code: str, default: str) -> str:
    """Map a stable public code to its canonical message, collapsing unknowns."""
    return public_message_for(code, default)


def _normalize_op_result(pid: str, res, default_code: str, default_message: str) -> dict:
    """Normalize an underlying operation result into a safe per-item payload.

    Success passes through. Failures always carry a stable ``code`` and a
    whitelisted message; arbitrary lower-level ``message`` values are dropped.
    """
    if isinstance(res, dict) and res.get("status") == "success":
        return {"profile_id": pid, "status": "success", "message": res.get("message", "")}
    code = default_code
    message = default_message
    if isinstance(res, dict):
        known = res.get("code")
        if known in _PUBLIC_CODE_MESSAGES:
            code = known
            message = _PUBLIC_CODE_MESSAGES[known]
    return _bulk_error(pid, code, message)


def bulk_tag_profiles(profile_ids: List[str], tags: List[str]):
    """Add tags to multiple profiles in one call."""
    results = []
    success_count = 0
    for pid in profile_ids:
        profile = profile_manager.get_profile(pid)
        if not profile:
            results.append({"profile_id": pid, "status": "error", "message": "Not found"})
            continue
        ok = profile_manager.add_tags(pid, tags)
        if ok:
            results.append({"profile_id": pid, "status": "success", "tags": profile_manager.get_profile(pid).get("tags", [])})
            success_count += 1
        else:
            results.append({"profile_id": pid, "status": "error", "message": "Update failed"})

    logger.info(f"Bulk tag: {success_count}/{len(profile_ids)} profiles updated")
    return {
        "status": "success" if success_count == len(profile_ids) else ("partial" if success_count else "error"),
        "message": f"Tagged {success_count} out of {len(profile_ids)} profiles",
        "total": len(profile_ids),
        "succeeded": success_count,
        "failed": len(profile_ids) - success_count,
        "results": results,
    }


def bulk_untag_profiles(profile_ids: List[str], tags: List[str]):
    """Remove tags from multiple profiles in one call."""
    results = []
    success_count = 0
    for pid in profile_ids:
        profile = profile_manager.get_profile(pid)
        if not profile:
            results.append({"profile_id": pid, "status": "error", "message": "Not found"})
            continue
        ok = profile_manager.remove_tags(pid, tags)
        if ok:
            results.append({"profile_id": pid, "status": "success", "tags": profile_manager.get_profile(pid).get("tags", [])})
            success_count += 1
        else:
            results.append({"profile_id": pid, "status": "error", "message": "Update failed"})

    logger.info(f"Bulk untag: {success_count}/{len(profile_ids)} profiles updated")
    return {
        "status": "success" if success_count == len(profile_ids) else ("partial" if success_count else "error"),
        "message": f"Untagged {success_count} out of {len(profile_ids)} profiles",
        "total": len(profile_ids),
        "succeeded": success_count,
        "failed": len(profile_ids) - success_count,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BulkCreateRequest(BaseModel):
    base_name: str
    count: int = 5
    proxy: Optional[dict] = None
    proxy_string: Optional[str] = None
    pin: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    advanced: Optional[dict] = None

    @field_validator("base_name")
    @classmethod
    def _validate_base_name(cls, v):
        v = v.strip() if isinstance(v, str) else ""
        if not v:
            raise ValueError("base_name cannot be empty")
        if len(v) > 120:
            raise ValueError("base_name must be 120 characters or less")
        return v


class BulkProfileIdsRequest(BaseModel):
    profile_ids: List[str]

    @field_validator("profile_ids")
    @classmethod
    def _validate_profile_ids(cls, v):
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for pid in v:
            if not pid or not pid.strip():
                raise ValueError("profile_ids cannot contain empty values")
            pid = pid.strip()
            if pid not in seen:
                seen.add(pid)
                deduped.append(pid)
        if not deduped:
            raise ValueError("At least one profile_id is required")
        if len(deduped) > 100:
            raise ValueError("Maximum 100 profile_ids allowed per request")
        return deduped


class BulkAssignFolderRequest(BaseModel):
    profile_ids: List[str]
    folder_id: Optional[str] = None

    @field_validator("profile_ids")
    @classmethod
    def _validate_profile_ids(cls, v):
        return BulkProfileIdsRequest._validate_profile_ids.__func__(cls, v)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/profiles/bulk/create")
async def bulk_create_profiles(req: BulkCreateRequest, _auth: None = Depends(require_admin_token)):
    """Create profiles through the same strict Kimi pipeline as single-create."""
    count = max(1, min(req.count, 100))  # cap at 100

    proxy = req.proxy
    if proxy is None and req.proxy_string:
        try:
            proxy = parse_proxy_string(req.proxy_string)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def create_one(i: int):
        name = f"{req.base_name}_{i + 1}"
        async with _CREATE_SEM:
            try:
                advanced_ui = dict(req.advanced or {})
                if req.timezone:
                    advanced_ui["timezone"] = req.timezone
                if req.locale:
                    advanced_ui["locale"] = req.locale
                result = await profile_creator.create_zero_leak_profile(
                    name=name,
                    proxy=proxy,
                    advanced_ui=advanced_ui,
                    pin=req.pin,
                    skip_warming=True,
                )
                if result.get("status") != "success":
                    code = result.get("code", "CREATE_FAILED")
                    if code not in _PUBLIC_CODE_MESSAGES:
                        code = "CREATE_FAILED"
                    return {
                        "index": i,
                        "name": name,
                        "status": "error",
                        "code": code,
                        "message": _PUBLIC_CODE_MESSAGES[code],
                    }
                profile = result["profile"]
                return {"index": i, "name": name, "status": "success", "profile_id": profile["id"]}
            except Exception as exc:
                logger.error("Bulk strict profile creation failed for one item: %s", type(exc).__name__)
                return {"index": i, "name": name, "status": "error", "code": "CREATE_FAILED", "message": "Profile creation failed"}

    tasks = [create_one(i) for i in range(count)]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Bulk create: {success_count}/{count} profiles created")

    return {
        "status": "success" if success_count == count else ("partial" if success_count else "error"),
        "message": f"Created {success_count} out of {count} profiles",
        "total": count,
        "succeeded": success_count,
        "failed": count - success_count,
        "results": results,
    }


@router.post("/api/profiles/bulk/launch")
async def bulk_launch_profiles(req: BulkProfileIdsRequest, _auth: None = Depends(require_admin_token)):
    """Launch multiple profiles concurrently."""
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one profile_id is required")

    async def launch_one(pid: str):
        async with _LAUNCH_SEM:
            try:
                res = await launch_profile(pid)
                return _normalize_op_result(pid, res, "LAUNCH_FAILED", "Launch failed")
            except Exception as exc:
                logger.error("Bulk launch failed for profile %s: %s", pid, type(exc).__name__)
                return _bulk_error(pid, "LAUNCH_FAILED", "Launch failed")

    tasks = [launch_one(pid) for pid in req.profile_ids]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Bulk launch: {success_count}/{len(req.profile_ids)} profiles launched")

    return {
        "status": "success" if success_count == len(req.profile_ids) else ("partial" if success_count else "error"),
        "message": f"Launched {success_count} out of {len(req.profile_ids)} profiles",
        "total": len(req.profile_ids),
        "succeeded": success_count,
        "failed": len(req.profile_ids) - success_count,
        "results": results,
    }


@router.post("/api/profiles/bulk/close")
async def bulk_close_profiles(req: BulkProfileIdsRequest, _auth: None = Depends(require_admin_token)):
    """Close multiple profiles concurrently."""
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one profile_id is required")

    async def close_one(pid: str):
        async with _CLOSE_SEM:
            try:
                res = await close_profile(pid)
                return _normalize_op_result(pid, res, "CLOSE_FAILED", "Close failed")
            except Exception as exc:
                logger.error("Bulk close failed for profile %s: %s", pid, type(exc).__name__)
                return _bulk_error(pid, "CLOSE_FAILED", "Close failed")

    tasks = [close_one(pid) for pid in req.profile_ids]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Bulk close: {success_count}/{len(req.profile_ids)} profiles closed")

    return {
        "status": "success" if success_count == len(req.profile_ids) else ("partial" if success_count else "error"),
        "message": f"Closed {success_count} out of {len(req.profile_ids)} profiles",
        "total": len(req.profile_ids),
        "succeeded": success_count,
        "failed": len(req.profile_ids) - success_count,
        "results": results,
    }


@router.post("/api/profiles/bulk/delete")
async def bulk_delete_profiles(req: BulkProfileIdsRequest, _auth: None = Depends(require_admin_token)):
    """Delete multiple profiles — closes running browsers first, then deletes."""
    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="At least one profile_id is required")

    async def delete_one(pid: str):
        async with _DELETE_SEM:
            # Close if running. Fail-closed: never delete a profile whose
            # browser may still be up (locked files / orphaned Chromium).
            if is_profile_running(pid):
                try:
                    close_res = await close_profile(pid)
                except Exception as exc:
                    logger.error("Bulk delete: close failed for profile %s: %s", pid, type(exc).__name__)
                    return _bulk_error(pid, "CLOSE_FAILED", "Close failed before delete; aborting")
                if close_res.get("status") != "success":
                    return _bulk_error(pid, "CLOSE_FAILED", "Close failed before delete; aborting")
            try:
                success = profile_manager.delete_profile(pid)
                if success:
                    return {"profile_id": pid, "status": "success", "message": "Deleted"}
                return _bulk_error(pid, "NOT_FOUND", "Profile not found")
            except Exception as exc:
                logger.error("Bulk delete failed for profile %s: %s", pid, type(exc).__name__)
                return _bulk_error(pid, "DELETE_FAILED", "Delete failed")

    tasks = [delete_one(pid) for pid in req.profile_ids]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info(f"Bulk delete: {success_count}/{len(req.profile_ids)} profiles deleted")

    return {
        "status": "success" if success_count == len(req.profile_ids) else ("partial" if success_count else "error"),
        "message": f"Deleted {success_count} out of {len(req.profile_ids)} profiles",
        "total": len(req.profile_ids),
        "succeeded": success_count,
        "failed": len(req.profile_ids) - success_count,
        "results": results,
    }


@router.post("/api/profiles/bulk/assign-folder")
def bulk_assign_folder(req: BulkAssignFolderRequest, _auth: None = Depends(require_admin_token)):
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
        "status": "success" if success_count == len(req.profile_ids) else ("partial" if success_count else "error"),
        "message": f"Assigned {success_count} out of {len(req.profile_ids)} profiles",
        "folder_id": req.folder_id,
        "total": len(req.profile_ids),
        "succeeded": success_count,
        "failed": len(req.profile_ids) - success_count,
        "results": results,
    }
