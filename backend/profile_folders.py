"""
Profile Folders & Tag System.

Provides a lightweight folder/tag organization layer on top of the existing
profile metadata.  Folders are stored in a JSON file and profiles reference
a folder by its ID via a `folder_id` field.

Endpoints:
  GET    /api/folders              → list all folders
  POST   /api/folders              → create a new folder
  DELETE /api/folders/{folder_id}  → delete a folder (profiles unassigned)
  PUT    /api/profiles/{id}/folder → assign a profile to a folder
  GET    /api/folders/{folder_id}/profiles → list profiles in a folder
"""

import os
import json
import uuid
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger
from backend.profile_manager import profile_manager

router = APIRouter(tags=["folders"])

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_FOLDERS_FILE = os.path.join(get_data_dir("api_data"), "folders.json")


def _load_folders() -> list[dict]:
    if not os.path.exists(_FOLDERS_FILE):
        return []
    try:
        with open(_FOLDERS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_folders(folders: list[dict]) -> None:
    os.makedirs(os.path.dirname(_FOLDERS_FILE), exist_ok=True)
    with open(_FOLDERS_FILE, "w") as f:
        json.dump(folders, f, indent=4)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CreateFolderRequest(BaseModel):
    name: str
    color: Optional[str] = None  # hex color like #4A90D9
    tags: Optional[List[str]] = None


class AssignFolderRequest(BaseModel):
    folder_id: Optional[str] = None  # None = remove from folder


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/folders")
def list_folders():
    folders = _load_folders()
    profiles = profile_manager.list_profiles()

    # Annotate each folder with its profile count
    for folder in folders:
        count = sum(1 for p in profiles if p.get("folder_id") == folder["id"])
        folder["profile_count"] = count

    return {"status": "success", "folders": folders, "total": len(folders)}


@router.post("/api/folders")
def create_folder(req: CreateFolderRequest):
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Folder name is required")

    # SECURITY FIX: Sanitize folder name to prevent stored XSS
    import html
    import re
    safe_name = html.escape(req.name.strip(), quote=True)
    # Also strip any remaining HTML tags
    safe_name = re.sub(r'<[^>]+>', '', safe_name)
    # Limit length
    safe_name = safe_name[:100]

    folder = {
        "id": "fld_" + uuid.uuid4().hex[:12],
        "name": safe_name,
        "color": req.color or "#4A90D9",
        "tags": req.tags or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    folders = _load_folders()
    folders.append(folder)
    _save_folders(folders)

    logger.info(f"Folder '{folder['name']}' created (id={folder['id']})")
    return {"status": "success", "folder": folder}


@router.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: str):
    folders = _load_folders()
    original = len(folders)
    folders = [f for f in folders if f["id"] != folder_id]

    if len(folders) == original:
        raise HTTPException(status_code=404, detail="Folder not found")

    _save_folders(folders)

    # Unassign profiles that referenced this folder
    for p in profile_manager.list_profiles():
        if p.get("folder_id") == folder_id:
            profile_manager.update_profile(p["id"], {"folder_id": None})

    logger.info(f"Folder {folder_id} deleted and profiles unassigned")
    return {"status": "success", "message": "Folder deleted, profiles unassigned"}


@router.put("/api/profiles/{profile_id}/folder")
def assign_profile_to_folder(profile_id: str, req: AssignFolderRequest):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    # Validate folder exists if provided
    if req.folder_id:
        folders = _load_folders()
        if not any(f["id"] == req.folder_id for f in folders):
            raise HTTPException(status_code=404, detail="Folder not found")

    success = profile_manager.update_profile(profile_id, {"folder_id": req.folder_id})
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update profile")

    action = "assigned to folder" if req.folder_id else "removed from folder"
    logger.info(f"Profile {profile_id} {action}")
    return {"status": "success", "profile_id": profile_id, "folder_id": req.folder_id}


@router.get("/api/folders/{folder_id}/profiles")
def list_profiles_in_folder(folder_id: str):
    folders = _load_folders()
    if not any(f["id"] == folder_id for f in folders):
        raise HTTPException(status_code=404, detail="Folder not found")

    all_profiles = profile_manager.list_profiles()
    folder_profiles = [p for p in all_profiles if p.get("folder_id") == folder_id]

    return {"status": "success", "folder_id": folder_id, "profiles": folder_profiles, "count": len(folder_profiles)}
