"""
GhostBrowser AI - Authentication Manager
JWT-based login with user accounts, session tokens, and role-based access.
"""
import json
import os
import secrets
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from enum import Enum

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger

USERS_FILE = os.path.join(get_data_dir("profiles_data"), "users.json")
os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)

JWT_SECRET = os.environ.get("GHOSTBROWSER_JWT_SECRET", secrets.token_urlsafe(32))
JWT_EXPIRY_HOURS = 24 * 7  # 7 days

router = APIRouter(prefix="/api/auth", tags=["auth"])


class UserRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


ROLE_PERMISSIONS = {
    UserRole.OWNER: {
        "create_profile", "edit_profile", "delete_profile", "launch_profile",
        "close_profile", "view_profile", "manage_team", "manage_api_keys",
        "lock_profile", "export_profile", "import_profile", "manage_settings",
        "manage_proxies", "run_macros", "manage_extensions",
    },
    UserRole.ADMIN: {
        "create_profile", "edit_profile", "delete_profile", "launch_profile",
        "close_profile", "view_profile", "manage_team", "manage_api_keys",
        "export_profile", "import_profile", "manage_settings",
        "manage_proxies", "run_macros", "manage_extensions",
    },
    UserRole.MEMBER: {
        "create_profile", "edit_profile", "launch_profile",
        "close_profile", "view_profile", "export_profile",
        "run_macros",
    },
    UserRole.VIEWER: {
        "view_profile",
    },
}


class User:
    def __init__(self, username: str, password_hash: str, display_name: str = "",
                 role: str = "member", user_id: str = None, created_at: str = None,
                 last_login: str = None):
        self.id = user_id or str(int(time.time() * 1000))
        self.username = username
        self.password_hash = password_hash
        self.display_name = display_name or username
        self.role = role
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.last_login = last_login

    def to_dict(self, include_hash=False):
        d = {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "created_at": self.created_at,
            "last_login": self.last_login,
        }
        if include_hash:
            d["password_hash"] = self.password_hash
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        return cls(
            username=data.get("username", ""),
            password_hash=data.get("password_hash", ""),
            display_name=data.get("display_name", ""),
            role=data.get("role", "member"),
            user_id=data.get("id"),
            created_at=data.get("created_at"),
            last_login=data.get("last_login"),
        )

    def has_permission(self, action: str) -> bool:
        try:
            r = UserRole(self.role)
        except ValueError:
            return False
        return action in ROLE_PERMISSIONS.get(r, set())


class Session:
    def __init__(self, user_id: str, token: str, expires_at: float,
                 created_at: float = None, ip: str = ""):
        self.user_id = user_id
        self.token = token
        self.expires_at = expires_at
        self.created_at = created_at or time.time()
        self.ip = ip

    def is_expired(self):
        return time.time() > self.expires_at

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "token": self.token[:16] + "...",
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "ip": self.ip,
        }


class AuthManager:
    def __init__(self):
        self._users: Dict[str, User] = {}
        self._sessions: Dict[str, Session] = {}
        self._load()
        self._ensure_default_owner()

    def _load(self):
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r") as f:
                    data = json.load(f)
                for uid, udata in data.get("users", {}).items():
                    user = User.from_dict(udata)
                    self._users[user.username] = user
            except Exception as e:
                logger.error(f"Failed to load users: {e}")

    def _save(self):
        try:
            data = {
                "users": {u.id: u.to_dict(include_hash=True) for u in self._users.values()}
            }
            with open(USERS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save users: {e}")

    def _ensure_default_owner(self):
        if not self._users:
            default_pass = secrets.token_urlsafe(8)
            self.create_user("admin", default_pass, "Admin", "owner")
            logger.info(f"Default owner created: admin / {default_pass}")
            self._default_password = default_pass
        else:
            self._default_password = None

    @staticmethod
    def _hash_password(password: str, salt: str = None) -> str:
        if not salt:
            salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return f"{salt}${h.hex()}"

    @staticmethod
    def _verify_password(password: str, stored_hash: str) -> bool:
        parts = stored_hash.split("$", 1)
        if len(parts) != 2:
            return False
        salt, expected = parts
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return h.hex() == expected

    def create_user(self, username: str, password: str, display_name: str = "",
                    role: str = "member") -> User:
        if username in self._users:
            raise ValueError(f"Username '{username}' already exists")
        if len(password) < 4:
            raise ValueError("Password must be at least 4 characters")
        if role not in [r.value for r in UserRole]:
            raise ValueError(f"Invalid role: {role}")

        pwd_hash = self._hash_password(password)
        user = User(username=username, password_hash=pwd_hash,
                    display_name=display_name or username, role=role)
        self._users[username] = user
        self._save()
        logger.info(f"User created: {username} (role={role})")
        return user

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self._users.get(username)
        if not user:
            return None
        if not self._verify_password(password, user.password_hash):
            return None
        user.last_login = datetime.now(timezone.utc).isoformat()
        self._save()
        return user

    def create_session(self, user: User, ip: str = "") -> str:
        token = secrets.token_urlsafe(48)
        expires = time.time() + (JWT_EXPIRY_HOURS * 3600)
        session = Session(user_id=user.id, token=token, expires_at=expires, ip=ip)
        self._sessions[token] = session
        return token

    def validate_session(self, token: str) -> Optional[User]:
        if not token:
            return None
        session = self._sessions.get(token)
        if not session or session.is_expired():
            if session:
                del self._sessions[token]
            return None
        for u in self._users.values():
            if u.id == session.user_id:
                return u
        return None

    def get_user_from_token(self, token: str) -> Optional[User]:
        return self.validate_session(token)

    def logout(self, token: str):
        self._sessions.pop(token, None)

    def get_user(self, username: str) -> Optional[User]:
        return self._users.get(username)

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        for u in self._users.values():
            if u.id == user_id:
                return u
        return None

    def list_users(self) -> List[User]:
        return list(self._users.values())

    def update_user(self, username: str, **kwargs) -> Optional[User]:
        user = self._users.get(username)
        if not user:
            return None
        if "display_name" in kwargs:
            user.display_name = kwargs["display_name"]
        if "role" in kwargs:
            if kwargs["role"] in [r.value for r in UserRole]:
                user.role = kwargs["role"]
        if "password" in kwargs:
            user.password_hash = self._hash_password(kwargs["password"])
        self._save()
        return user

    def delete_user(self, username: str) -> bool:
        if username not in self._users:
            return False
        user = self._users[username]
        if user.role == "owner":
            raise ValueError("Cannot delete the owner account")
        del self._users[username]
        to_remove = [t for t, s in self._sessions.items() if s.user_id == user.id]
        for t in to_remove:
            del self._sessions[t]
        self._save()
        return True

    def check_permission(self, token: str, action: str) -> bool:
        user = self.validate_session(token)
        if not user:
            return False
        return user.has_permission(action)


auth_manager = AuthManager()


# --- Pydantic models ---
class LoginModel(BaseModel):
    username: str
    password: str

class RegisterModel(BaseModel):
    username: str
    password: str
    display_name: str = ""

class UpdateUserModel(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None


def _get_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    token = request.cookies.get("ghost_session")
    if token:
        return token
    return request.headers.get("X-Session-Token", "")


def get_current_user(request: Request) -> Optional[User]:
    token = _get_token(request)
    return auth_manager.validate_session(token)


# --- Routes ---
@router.post("/login")
def login(payload: LoginModel, request: Request):
    user = auth_manager.authenticate(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    ip = request.client.host if request.client else ""
    token = auth_manager.create_session(user, ip=ip)
    resp = JSONResponse({
        "token": token,
        "user": user.to_dict(),
        "permissions": list(ROLE_PERMISSIONS.get(UserRole(user.role), set())),
    })
    resp.set_cookie("ghost_session", token, httponly=True, samesite="lax",
                    max_age=JWT_EXPIRY_HOURS * 3600)
    return resp


@router.post("/register")
def register(payload: RegisterModel):
    try:
        user = auth_manager.create_user(payload.username, payload.password, payload.display_name)
        return {"user": user.to_dict(), "message": "Account created"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/logout")
def logout(request: Request):
    token = _get_token(request)
    auth_manager.logout(token)
    resp = JSONResponse({"status": "logged out"})
    resp.delete_cookie("ghost_session")
    return resp


@router.get("/me")
def get_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "user": user.to_dict(),
        "permissions": list(ROLE_PERMISSIONS.get(UserRole(user.role), set())),
    }


@router.get("/users")
def list_users(request: Request):
    user = get_current_user(request)
    if not user or not user.has_permission("manage_team"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return [u.to_dict() for u in auth_manager.list_users()]


@router.post("/users")
def create_user_api(payload: RegisterModel, request: Request, role: str = "member"):
    user = get_current_user(request)
    if not user or not user.has_permission("manage_team"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    try:
        new_user = auth_manager.create_user(payload.username, payload.password,
                                            payload.display_name, role)
        return new_user.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/users/{username}")
def update_user_api(username: str, payload: UpdateUserModel, request: Request):
    user = get_current_user(request)
    if not user or not user.has_permission("manage_team"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    kwargs = {k: v for k, v in payload.dict().items() if v is not None}
    updated = auth_manager.update_user(username, **kwargs)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return updated.to_dict()


@router.delete("/users/{username}")
def delete_user_api(username: str, request: Request):
    user = get_current_user(request)
    if not user or not user.has_permission("manage_team"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    try:
        deleted = auth_manager.delete_user(username)
        if not deleted:
            raise HTTPException(status_code=404, detail="User not found")
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/default-password")
def get_default_password():
    if auth_manager._default_password:
        return {"username": "admin", "password": auth_manager._default_password}
    return {"username": None, "password": None}
