"""
Team Management & Roles/Permissions module.
Provides TeamMember, role-based access control, profile locking, and
API endpoints for team member CRUD operations.
"""

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger

# ---------------------------------------------------------------------------
# Data file
# ---------------------------------------------------------------------------
TEAM_DATA_FILE = os.path.join(get_data_dir("profiles_data"), "team_data.json")
LOCKED_PROFILES_FILE = os.path.join(get_data_dir("profiles_data"), "locked_profiles.json")

# Ensure parent directory exists
os.makedirs(os.path.dirname(TEAM_DATA_FILE), exist_ok=True)


# ---------------------------------------------------------------------------
# Roles & Permissions
# ---------------------------------------------------------------------------
class Role(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    OPERATOR = "operator"
    VIEWER = "viewer"


# Permission matrix: role -> set of allowed actions
PERMISSIONS: Dict[Role, set] = {
    Role.ADMIN: {
        "create_profile", "edit_profile", "delete_profile", "launch_profile",
        "close_profile", "view_profile", "manage_team", "lock_profile",
        "export_profile", "import_profile", "manage_api_keys",
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
# Pydantic models for API
# ---------------------------------------------------------------------------
class TeamMemberModel(BaseModel):
    name: str
    email: str
    role: str = "viewer"


class UpdateRoleModel(BaseModel):
    role: str


# ---------------------------------------------------------------------------
# TeamMember domain object
# ---------------------------------------------------------------------------
class TeamMember:
    """Represents a single team member with identity, role and metadata."""

    def __init__(self, name: str, email: str, role: str = "viewer",
                 member_id: Optional[str] = None,
                 created_at: Optional[str] = None):
        self.id = member_id or str(uuid.uuid4())
        self.name = name
        self.email = email
        self.role = role
        self.created_at = created_at or datetime.utcnow().isoformat() + "Z"

    @property
    def role_enum(self) -> Optional[Role]:
        try:
            return Role(self.role)
        except ValueError:
            return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TeamMember":
        return cls(
            name=data.get("name", ""),
            email=data.get("email", ""),
            role=data.get("role", "viewer"),
            member_id=data.get("id"),
            created_at=data.get("created_at"),
        )


# ---------------------------------------------------------------------------
# TeamManager — persistence & business logic
# ---------------------------------------------------------------------------
class TeamManager:
    """Manages team members, roles, permissions and profile locking."""

    def __init__(self):
        self._members: Dict[str, TeamMember] = {}
        self._locked_profiles: Dict[str, str] = {}  # profile_id -> locked_by member_id
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self):
        if os.path.exists(TEAM_DATA_FILE):
            try:
                with open(TEAM_DATA_FILE, "r") as f:
                    raw = json.load(f)
                self._members = {
                    mid: TeamMember.from_dict(mdata)
                    for mid, mdata in raw.get("members", {}).items()
                }
            except (json.JSONDecodeError, KeyError) as exc:
                logger.error(f"Failed to load team data: {exc}")
                self._members = {}

        if os.path.exists(LOCKED_PROFILES_FILE):
            try:
                with open(LOCKED_PROFILES_FILE, "r") as f:
                    self._locked_profiles = json.load(f)
            except json.JSONDecodeError as exc:
                logger.error(f"Failed to load locked profiles: {exc}")
                self._locked_profiles = {}

    def _save_members(self):
        data = {"members": {mid: m.to_dict() for mid, m in self._members.items()}}
        try:
            with open(TEAM_DATA_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except OSError as exc:
            logger.error(f"Failed to save team data: {exc}")

    def _save_locked(self):
        try:
            with open(LOCKED_PROFILES_FILE, "w") as f:
                json.dump(self._locked_profiles, f, indent=4)
        except OSError as exc:
            logger.error(f"Failed to save locked profiles: {exc}")

    # -- member CRUD --------------------------------------------------------
    def add_member(self, name: str, email: str, role: str = "viewer") -> TeamMember:
        if role not in [r.value for r in Role]:
            raise ValueError(f"Invalid role: {role}")
        # SECURITY FIX: Sanitize name and email to prevent stored XSS
        import html, re
        safe_name = html.escape(str(name).strip(), quote=True)[:100]
        safe_email = html.escape(str(email).strip(), quote=True)[:200]
        # Validate email format
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', safe_email):
            raise ValueError(f"Invalid email format: {safe_email}")
        member = TeamMember(name=safe_name, email=safe_email, role=role)
        self._members[member.id] = member
        self._save_members()
        logger.info(f"Team member added: {member.email} ({role})")
        return member

    def get_member(self, member_id: str) -> Optional[TeamMember]:
        return self._members.get(member_id)

    def list_members(self) -> List[TeamMember]:
        return list(self._members.values())

    def update_role(self, member_id: str, new_role: str) -> TeamMember:
        if new_role not in [r.value for r in Role]:
            raise ValueError(f"Invalid role: {new_role}")
        member = self._members.get(member_id)
        if not member:
            raise KeyError(f"Member not found: {member_id}")
        member.role = new_role
        self._save_members()
        logger.info(f"Member {member_id} role changed to {new_role}")
        return member

    def remove_member(self, member_id: str) -> bool:
        if member_id not in self._members:
            return False
        del self._members[member_id]
        # Remove any locks held by this member
        to_remove = [pid for pid, lock_by in self._locked_profiles.items()
                     if lock_by == member_id]
        for pid in to_remove:
            del self._locked_profiles[pid]
        self._save_members()
        self._save_locked()
        logger.info(f"Team member removed: {member_id}")
        return True

    # -- permissions --------------------------------------------------------
    def check_permission(self, member_id: str, action: str) -> bool:
        """Return True if *member_id* is allowed to perform *action*."""
        member = self._members.get(member_id)
        if not member:
            return False
        role_enum = member.role_enum
        if not role_enum:
            return False
        return action in PERMISSIONS.get(role_enum, set())

    # -- profile locking ----------------------------------------------------
    def lock_profile(self, profile_id: str, member_id: str) -> bool:
        """Lock a profile so only *member_id* can use it. Returns True on success."""
        member = self._members.get(member_id)
        if not member:
            raise KeyError(f"Member not found: {member_id}")
        if not self.check_permission(member_id, "lock_profile"):
            raise PermissionError(f"Member {member_id} does not have lock permission")
        if profile_id in self._locked_profiles and self._locked_profiles[profile_id] != member_id:
            # Already locked by someone else
            return False
        self._locked_profiles[profile_id] = member_id
        self._save_locked()
        logger.info(f"Profile {profile_id} locked by {member_id}")
        return True

    def unlock_profile(self, profile_id: str, member_id: str) -> bool:
        """Unlock a profile. Only the locker or an admin can unlock."""
        locked_by = self._locked_profiles.get(profile_id)
        if locked_by is None:
            return True  # Already unlocked
        if locked_by == member_id:
            del self._locked_profiles[profile_id]
            self._save_locked()
            logger.info(f"Profile {profile_id} unlocked by {member_id}")
            return True
        # Admin can force-unlock
        if self.check_permission(member_id, "manage_team"):
            del self._locked_profiles[profile_id]
            self._save_locked()
            logger.info(f"Profile {profile_id} force-unlocked by admin {member_id}")
            return True
        return False

    def get_locked_profiles(self) -> Dict[str, str]:
        """Return mapping of profile_id -> member_id for locked profiles."""
        return dict(self._locked_profiles)


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------
team_manager = TeamManager()


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/team", tags=["team"])


@router.post("/members")
def add_member(payload: TeamMemberModel):
    """Add a new team member."""
    try:
        member = team_manager.add_member(
            name=payload.name, email=payload.email, role=payload.role
        )
        return member.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/members")
def list_members():
    """List all team members."""
    return [m.to_dict() for m in team_manager.list_members()]


@router.put("/members/{member_id}/role")
def update_member_role(member_id: str, payload: UpdateRoleModel):
    """Change a member's role."""
    try:
        member = team_manager.update_role(member_id, payload.role)
        return member.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/members/{member_id}")
def remove_member(member_id: str):
    """Remove a team member."""
    removed = team_manager.remove_member(member_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"status": "removed", "id": member_id}


@router.get("/members/{member_id}/permissions")
def get_permissions(member_id: str):
    """Return the set of allowed actions for a member."""
    member = team_manager.get_member(member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    role_enum = member.role_enum
    actions = list(PERMISSIONS.get(role_enum, set())) if role_enum else []
    return {"member_id": member_id, "role": member.role, "permissions": actions}


@router.post("/profiles/{profile_id}/lock")
def lock_profile(profile_id: str, member_id: str = ""):
    """Lock a profile to a specific member."""
    try:
        success = team_manager.lock_profile(profile_id, member_id)
        if not success:
            raise HTTPException(status_code=409, detail="Profile already locked by another member")
        return {"status": "locked", "profile_id": profile_id, "locked_by": member_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.post("/profiles/{profile_id}/unlock")
def unlock_profile(profile_id: str, member_id: str = ""):
    """Unlock a profile."""
    success = team_manager.unlock_profile(profile_id, member_id)
    if not success:
        raise HTTPException(status_code=403, detail="You do not have permission to unlock this profile")
    return {"status": "unlocked", "profile_id": profile_id}


@router.get("/profiles/locked")
def get_locked_profiles():
    """List all locked profiles."""
    return team_manager.get_locked_profiles()
