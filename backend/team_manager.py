"""
Team Management & Roles/Permissions module.
Provides TeamMember, role-based access control, profile locking, and
API endpoints for team member CRUD operations.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, Field

from backend.auth import require_admin_token
from backend.config import get_data_dir
from backend.logging_config import logger

# ---------------------------------------------------------------------------
# Data file
# ---------------------------------------------------------------------------
TEAM_DATA_FILE = os.path.join(get_data_dir("profiles_data"), "team_data.json")
LOCKED_PROFILES_FILE = os.path.join(get_data_dir("profiles_data"), "locked_profiles.json")

os.makedirs(os.path.dirname(TEAM_DATA_FILE), exist_ok=True)


# ---------------------------------------------------------------------------
# Roles & Permissions
# ---------------------------------------------------------------------------
class Role(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    OPERATOR = "operator"
    VIEWER = "viewer"


PERMISSIONS: Dict[Role, set] = {
    Role.ADMIN: {
        "create_profile", "edit_profile", "delete_profile", "launch_profile",
        "close_profile", "view_profile", "manage_team", "lock_profile",
        "export_profile", "import_profile",
    },
    Role.MANAGER: {
        "create_profile", "edit_profile", "launch_profile",
        "close_profile", "view_profile", "export_profile", "import_profile",
    },
    Role.OPERATOR: {
        "launch_profile", "close_profile", "view_profile",
    },
    Role.VIEWER: {
        "view_profile",
    },
}


# ---------------------------------------------------------------------------
# Generic JSON persistence
# ---------------------------------------------------------------------------
def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to load JSON from {path}: {exc}")
        return default


def _save_json(path: str, data) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        return True
    except OSError as exc:
        logger.error(f"Failed to save JSON to {path}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class TeamMember(BaseModel):
    name: str
    email: str
    role: str = "viewer"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    @property
    def role_enum(self) -> Optional[Role]:
        try:
            return Role(self.role)
        except ValueError:
            return None


class TeamMemberModel(BaseModel):
    name: str
    email: EmailStr
    role: str = "viewer"


class UpdateRoleModel(BaseModel):
    role: str


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_members: Dict[str, TeamMember] = {
    mid: TeamMember(**mdata)
    for mid, mdata in _load_json(TEAM_DATA_FILE, {"members": {}}).get("members", {}).items()
}
_locked_profiles: Dict[str, str] = _load_json(LOCKED_PROFILES_FILE, {})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_role(role: str) -> str:
    if role not in Role._value2member_map_:
        raise ValueError(f"Invalid role: {role}")
    return role


def _save_members() -> None:
    _save_json(TEAM_DATA_FILE, {"members": {mid: m.model_dump() for mid, m in _members.items()}})


# ---------------------------------------------------------------------------
# Member CRUD
# ---------------------------------------------------------------------------
def _add_member(name: str, email: str, role: str = "viewer") -> TeamMember:
    _validate_role(role)
    member = TeamMember(name=name.strip()[:100], email=str(email).strip(), role=role)
    _members[member.id] = member
    _save_members()
    logger.info(f"Team member added: {member.email} ({role})")
    return member


def _get_member(member_id: str) -> Optional[TeamMember]:
    return _members.get(member_id)


def _list_members() -> List[TeamMember]:
    return list(_members.values())


def _update_role(member_id: str, new_role: str) -> TeamMember:
    _validate_role(new_role)
    member = _members.get(member_id)
    if not member:
        raise KeyError(f"Member not found: {member_id}")
    member.role = new_role
    _save_members()
    logger.info(f"Member {member_id} role changed to {new_role}")
    return member


def _remove_member(member_id: str) -> bool:
    if member_id not in _members:
        return False
    del _members[member_id]
    for pid in [pid for pid, lock_by in _locked_profiles.items() if lock_by == member_id]:
        del _locked_profiles[pid]
    _save_members()
    _save_json(LOCKED_PROFILES_FILE, _locked_profiles)
    logger.info(f"Team member removed: {member_id}")
    return True


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def _check_permission(member_id: str, action: str) -> bool:
    member = _members.get(member_id)
    if not member:
        return False
    role_enum = member.role_enum
    if not role_enum:
        return False
    return action in PERMISSIONS.get(role_enum, set())


# ---------------------------------------------------------------------------
# Profile locking
# ---------------------------------------------------------------------------
def _lock_profile(profile_id: str, member_id: str) -> bool:
    member = _members.get(member_id)
    if not member:
        raise KeyError(f"Member not found: {member_id}")
    if not _check_permission(member_id, "lock_profile"):
        raise PermissionError(f"Member {member_id} does not have lock permission")
    if profile_id in _locked_profiles and _locked_profiles[profile_id] != member_id:
        return False
    _locked_profiles[profile_id] = member_id
    _save_json(LOCKED_PROFILES_FILE, _locked_profiles)
    logger.info(f"Profile {profile_id} locked by {member_id}")
    return True


def _unlock_profile(profile_id: str, member_id: str) -> bool:
    locked_by = _locked_profiles.get(profile_id)
    if locked_by is None:
        return True
    if locked_by == member_id or _check_permission(member_id, "manage_team"):
        del _locked_profiles[profile_id]
        _save_json(LOCKED_PROFILES_FILE, _locked_profiles)
        logger.info(f"Profile {profile_id} unlocked by {member_id}")
        return True
    return False


def _get_locked_profiles() -> Dict[str, str]:
    return dict(_locked_profiles)


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/team", tags=["team"])


@router.post("/members")
def add_member(payload: TeamMemberModel, _auth: None = Depends(require_admin_token)):
    """Add a new team member."""
    try:
        member = _add_member(name=payload.name, email=payload.email, role=payload.role)
        return member.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/members")
def list_members(_auth: None = Depends(require_admin_token)):
    """List all team members."""
    return [m.model_dump() for m in _list_members()]


@router.put("/members/{member_id}/role")
def update_member_role(member_id: str, payload: UpdateRoleModel, _auth: None = Depends(require_admin_token)):
    """Change a member's role."""
    try:
        member = _update_role(member_id, payload.role)
        return member.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/members/{member_id}")
def remove_member(member_id: str, _auth: None = Depends(require_admin_token)):
    """Remove a team member."""
    removed = _remove_member(member_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"status": "removed", "id": member_id}


@router.get("/members/{member_id}/permissions")
def get_permissions(member_id: str, _auth: None = Depends(require_admin_token)):
    """Return the set of allowed actions for a member."""
    member = _get_member(member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    role_enum = member.role_enum
    actions = list(PERMISSIONS.get(role_enum, set())) if role_enum else []
    return {"member_id": member_id, "role": member.role, "permissions": actions}


@router.post("/profiles/{profile_id}/lock")
def lock_profile(profile_id: str, member_id: str = "", _auth: None = Depends(require_admin_token)):
    """Lock a profile to a specific member."""
    try:
        success = _lock_profile(profile_id, member_id)
        if not success:
            raise HTTPException(status_code=409, detail="Profile already locked by another member")
        return {"status": "locked", "profile_id": profile_id, "locked_by": member_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.post("/profiles/{profile_id}/unlock")
def unlock_profile(profile_id: str, member_id: str = "", _auth: None = Depends(require_admin_token)):
    """Unlock a profile."""
    success = _unlock_profile(profile_id, member_id)
    if not success:
        raise HTTPException(status_code=403, detail="You do not have permission to unlock this profile")
    return {"status": "unlocked", "profile_id": profile_id}


@router.get("/profiles/locked")
def get_locked_profiles(_auth: None = Depends(require_admin_token)):
    """List all locked profiles."""
    return _get_locked_profiles()
