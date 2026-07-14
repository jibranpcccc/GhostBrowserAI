"""
GhostBrowser AI - Extension Management
Install, manage, and sync browser extensions across profiles.
"""
import os
import json
import time
import zipfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger

EXTENSIONS_DIR = os.path.join(get_data_dir("profiles_data"), "extensions")
os.makedirs(EXTENSIONS_DIR, exist_ok=True)

EXTENSIONS_DB = os.path.join(get_data_dir("profiles_data"), "extensions.json")

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


KNOWN_EXTENSIONS = [
    {"id": "adblock", "name": "uBlock Origin", "version": "1.58.0",
     "description": "Ad blocker", "chrome_id": "cjpalhdlnbpafiamejdnhcphjbkeiagm",
     "category": "privacy", "size_kb": 2400},
    {"id": "lastpass", "name": "LastPass", "version": "4.145.0",
     "description": "Password manager", "chrome_id": "hdokiejnpjakedmadminhbghfgojaphhli",
     "category": "productivity", "size_kb": 5200},
    {"id": "vpn", "name": "Windscribe VPN", "version": "4.2.0",
     "description": "Free VPN extension", "chrome_id": "hnclnikingpbadmnkmamjhhimfolnljao",
     "category": "privacy", "size_kb": 3100},
    {"id": "useragent", "name": "User-Agent Switcher", "version": "2.0.0",
     "description": "Switch user agent string", "chrome_id": "mooikfkahabckopjjpohlamaipceehpm",
     "category": "stealth", "size_kb": 800},
    {"id": "cookie-editor", "name": "Cookie Editor", "version": "1.14.0",
     "description": "Edit cookies", "chrome_id": "hlebojanmmbokiincjjigifpbfdmkeme",
     "category": "privacy", "size_kb": 600},
    {"id": "clearcache", "name": "Clear Cache", "version": "1.5.0",
     "description": "Clear browser cache", "chrome_id": "amaabknmjofpakjinonbbfmbpfccagod",
     "category": "privacy", "size_kb": 300},
    {"id": "foxyproxy", "name": "FoxyProxy", "version": "8.9",
     "description": "Proxy manager", "chrome_id": "gxbahlmnlkobehflpgcfnajmfhhcfhkj",
     "category": "proxy", "size_kb": 450},
    {"id": "octoparse", "name": "Octoparse", "version": "5.8.0",
     "description": "Web scraper", "chrome_id": "lgpfehhkdnhkbhmkhfkjmfkghgahkfk",
     "category": "automation", "size_kb": 7200},
    {"id": "tampermonkey", "name": "Tampermonkey", "version": "5.3.0",
     "description": "User script manager", "chrome_id": "dhdgffkkebhmkfjojejmpbldmpobfkfo",
     "category": "automation", "size_kb": 1100},
    {"id": "grammarly", "name": "Grammarly", "version": "14.1234",
     "description": "Writing assistant", "chrome_id": "kbfnbchronplbdbjapanccckajdhognce",
     "category": "productivity", "size_kb": 8500},
]


class ExtensionManager:
    def __init__(self):
        self._installed: Dict[str, dict] = {}
        self._profile_exts: Dict[str, List[str]] = {}  # profile_id -> [ext_ids]
        self._load()

    def _load(self):
        if os.path.exists(EXTENSIONS_DB):
            try:
                with open(EXTENSIONS_DB, "r") as f:
                    data = json.load(f)
                self._installed = data.get("installed", {})
                self._profile_exts = data.get("profile_extensions", {})
            except Exception as e:
                logger.error(f"Failed to load extensions DB: {e}")

    def _save(self):
        try:
            data = {
                "installed": self._installed,
                "profile_extensions": self._profile_exts,
            }
            with open(EXTENSIONS_DB, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save extensions DB: {e}")

    def list_available(self) -> List[dict]:
        available = list(KNOWN_EXTENSIONS)
        for ext_id, ext_data in self._installed.items():
            for avail in available:
                if avail["id"] == ext_id:
                    avail["installed"] = True
                    avail["installed_at"] = ext_data.get("installed_at")
                    break
        return available

    def get_installed(self) -> List[dict]:
        result = []
        for ext_id, ext_data in self._installed.items():
            template = next((e for e in KNOWN_EXTENSIONS if e["id"] == ext_id), {})
            result.append({**template, **ext_data, "installed": True})
        return result

    def install_extension(self, ext_id: str, profile_ids: List[str] = None) -> dict:
        ext_template = next((e for e in KNOWN_EXTENSIONS if e["id"] == ext_id), None)
        if not ext_template:
            return {"success": False, "error": f"Unknown extension: {ext_id}"}

        self._installed[ext_id] = {
            "name": ext_template["name"],
            "version": ext_template["version"],
            "installed_at": time.time(),
            "chrome_id": ext_template.get("chrome_id", ""),
        }

        if profile_ids:
            for pid in profile_ids:
                if pid not in self._profile_exts:
                    self._profile_exts[pid] = []
                if ext_id not in self._profile_exts[pid]:
                    self._profile_exts[pid].append(ext_id)

        self._save()
        logger.info(f"Extension installed: {ext_template['name']} (profiles: {profile_ids or 'all'})")
        return {"success": True, "extension": ext_template}

    def uninstall_extension(self, ext_id: str) -> bool:
        if ext_id in self._installed:
            del self._installed[ext_id]
            for pid in self._profile_exts:
                if ext_id in self._profile_exts[pid]:
                    self._profile_exts[pid].remove(ext_id)
            self._save()
            return True
        return False

    def get_profile_extensions(self, profile_id: str) -> List[str]:
        return self._profile_exts.get(profile_id, [])

    def assign_to_profile(self, ext_id: str, profile_id: str) -> bool:
        if ext_id not in self._installed:
            return False
        if profile_id not in self._profile_exts:
            self._profile_exts[profile_id] = []
        if ext_id not in self._profile_exts[profile_id]:
            self._profile_exts[profile_id].append(ext_id)
            self._save()
        return True

    def remove_from_profile(self, ext_id: str, profile_id: str) -> bool:
        if profile_id in self._profile_exts and ext_id in self._profile_exts[profile_id]:
            self._profile_exts[profile_id].remove(ext_id)
            self._save()
            return True
        return False

    def get_extension_launch_args(self, profile_id: str) -> List[str]:
        ext_ids = self.get_profile_extensions(profile_id)
        chrome_ids = []
        for eid in ext_ids:
            ext = next((e for e in self._installed.values() if e.get("chrome_id")), None)
            if ext:
                chrome_ids.append(ext["chrome_id"])
        if chrome_ids:
            return [f"--load-extension={','.join(chrome_ids)}"]
        return []


extension_manager = ExtensionManager()


# --- Pydantic models ---
class InstallExtensionModel(BaseModel):
    ext_id: str
    profile_ids: List[str] = []


class AssignExtensionModel(BaseModel):
    ext_id: str
    profile_id: str


@router.get("/available")
def list_available():
    return extension_manager.list_available()


@router.get("/installed")
def list_installed():
    return extension_manager.get_installed()


@router.post("/install")
def install_extension(payload: InstallExtensionModel):
    return extension_manager.install_extension(payload.ext_id, payload.profile_ids)


@router.post("/uninstall/{ext_id}")
def uninstall_extension(ext_id: str):
    if not extension_manager.uninstall_extension(ext_id):
        raise HTTPException(status_code=404, detail="Extension not found")
    return {"status": "uninstalled"}


@router.get("/profile/{profile_id}")
def get_profile_extensions(profile_id: str):
    return extension_manager.get_profile_extensions(profile_id)


@router.post("/assign")
def assign_to_profile(payload: AssignExtensionModel):
    if not extension_manager.assign_to_profile(payload.ext_id, payload.profile_id):
        raise HTTPException(status_code=404, detail="Extension or profile not found")
    return {"status": "assigned"}


@router.post("/remove")
def remove_from_profile(payload: AssignExtensionModel):
    extension_manager.remove_from_profile(payload.ext_id, payload.profile_id)
    return {"status": "removed"}


@router.post("/upload")
async def upload_extension(file: UploadFile = File(...)):
    if not file.filename.endswith((".crx", ".zip", ".xpi")):
        raise HTTPException(status_code=400, detail="Only .crx, .zip, or .xpi files supported")
    ext_id = f"custom_{int(time.time() * 1000)}"
    save_path = os.path.join(EXTENSIONS_DIR, f"{ext_id}_{file.filename}")
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)
    return {"status": "uploaded", "ext_id": ext_id, "filename": file.filename, "size_kb": len(content) // 1024}
