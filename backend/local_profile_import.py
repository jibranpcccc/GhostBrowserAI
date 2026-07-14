"""
GhostBrowser AI - Local Browser Profile Import
Import Chrome, Firefox, and Edge profiles into GhostBrowser.
"""
import os
import json
import shutil
import sqlite3
import uuid
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger

router = APIRouter(prefix="/api/import", tags=["import"])


def _get_chrome_profiles_dir() -> List[dict]:
    profiles = []
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    if not base.exists():
        base = Path(os.path.expanduser("~")) / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    if base.exists():
        for p in base.iterdir():
            if p.is_dir() and (p / "Preferences").exists():
                profiles.append({
                    "browser": "chrome",
                    "name": p.name,
                    "path": str(p),
                    "has_cookies": (p / "Default" / "Cookies").exists() or (p / "Cookies").exists(),
                    "has_history": (p / "Default" / "History").exists() or (p / "History").exists(),
                })
    return profiles


def _get_firefox_profiles_dir() -> List[dict]:
    profiles = []
    profiles_ini = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "profiles.ini"
    if not profiles_ini.exists():
        return profiles
    try:
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(str(profiles_ini))
        for section in cfg.sections():
            if section.startswith("Profile"):
                name = cfg.get(section, "Name", fallback="Unknown")
                path = cfg.get(section, "Path", fallback="")
                is_relative = cfg.getboolean(section, "IsRelative", fallback=True)
                if is_relative:
                    full_path = profiles_ini.parent / path
                else:
                    full_path = Path(path)
                profiles.append({
                    "browser": "firefox",
                    "name": name,
                    "path": str(full_path),
                    "has_cookies": (full_path / "cookies.sqlite").exists(),
                    "has_history": (full_path / "places.sqlite").exists(),
                })
    except Exception as e:
        logger.error(f"Failed to read Firefox profiles.ini: {e}")
    return profiles


def _get_edge_profiles_dir() -> List[dict]:
    profiles = []
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"
    if base.exists():
        for p in base.iterdir():
            if p.is_dir() and (p / "Preferences").exists():
                profiles.append({
                    "browser": "edge",
                    "name": p.name,
                    "path": str(p),
                    "has_cookies": (p / "Default" / "Cookies").exists() or (p / "Cookies").exists(),
                    "has_history": (p / "Default" / "History").exists() or (p / "History").exists(),
                })
    return profiles


def _import_cookies_from_chrome(profile_path: str) -> list:
    cookies = []
    for cookie_file in ["Default/Cookies", "Cookies"]:
        fp = Path(profile_path) / cookie_file
        if fp.exists():
            try:
                tmp = fp.parent / f"_import_cookies_{int(time.time())}.db"
                shutil.copy2(str(fp), str(tmp))
                conn = sqlite3.connect(str(tmp))
                cursor = conn.execute(
                    "SELECT name, value, host_key, path, expires_utc, is_secure, is_httponly "
                    "FROM cookies WHERE host_key NOT LIKE '%google%' LIMIT 200"
                )
                for row in cursor.fetchall():
                    cookies.append({
                        "name": row[0], "value": row[1] or row[0],
                        "domain": row[2], "path": row[3],
                        "expires": row[4], "secure": bool(row[5]),
                        "httpOnly": bool(row[6]),
                    })
                conn.close()
                tmp.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Cookie import failed for {fp}: {e}")
    return cookies


def _import_cookies_from_firefox(profile_path: str) -> list:
    cookies = []
    db_path = Path(profile_path) / "cookies.sqlite"
    if not db_path.exists():
        return cookies
    try:
        tmp = db_path.parent / f"_import_cookies_{int(time.time())}.db"
        shutil.copy2(str(db_path), str(tmp))
        conn = sqlite3.connect(str(tmp))
        cursor = conn.execute(
            "SELECT name, value, host, path, expiry, isSecure, isHttpOnly "
            "FROM moz_cookies WHERE host NOT LIKE '%mozilla%' LIMIT 200"
        )
        for row in cursor.fetchall():
            cookies.append({
                "name": row[0], "value": row[1],
                "domain": row[2], "path": row[3],
                "expires": row[4], "secure": bool(row[5]),
                "httpOnly": bool(row[6]),
            })
        conn.close()
        tmp.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Firefox cookie import failed: {e}")
    return cookies


class ImportRequestModel(BaseModel):
    browser: str
    name: str
    path: str
    import_cookies: bool = True
    import_extensions: bool = False


@router.get("/browsers")
def list_browser_profiles():
    all_profiles = []
    all_profiles.extend(_get_chrome_profiles_dir())
    all_profiles.extend(_get_firefox_profiles_dir())
    all_profiles.extend(_get_edge_profiles_dir())
    return all_profiles


@router.post("")
def import_browser_profile(payload: ImportRequestModel):
    from backend.profile_manager import profile_manager
    import uuid as _uuid

    browser_id = str(_uuid.uuid4())
    profile_data = {
        "id": browser_id,
        "name": f"Imported from {payload.browser.title()} - {payload.name}",
        "status": "idle",
        "created_at": time.time(),
        "browser": payload.browser,
        "source_path": payload.path,
    }

    advanced = profile_manager.generate_advanced_config(payload.browser.lower())

    profile = profile_manager.create_profile(
        name=profile_data["name"],
        advanced=advanced,
    )

    if payload.import_cookies:
        try:
            if payload.browser.lower() == "chrome":
                cookies = _import_cookies_from_chrome(payload.path)
            elif payload.browser.lower() == "firefox":
                cookies = _import_cookies_from_firefox(payload.path)
            elif payload.browser.lower() == "edge":
                cookies = _import_cookies_from_chrome(payload.path)
            else:
                cookies = []

            if cookies:
                cookie_path = os.path.join(profile["path"], "imported_cookies.json")
                with open(cookie_path, "w") as f:
                    json.dump(cookies, f, indent=2)
                logger.info(f"Imported {len(cookies)} cookies from {payload.browser}")
        except Exception as e:
            logger.warning(f"Cookie import failed: {e}")

    return {
        "status": "imported",
        "profile_id": profile["id"],
        "name": profile["name"],
        "cookies_imported": payload.import_cookies,
    }


@router.post("/batch")
def import_batch(payload: list):
    results = []
    for item in payload:
        try:
            req = ImportRequestModel(**item)
            result = import_browser_profile(req)
            results.append(result)
        except Exception as e:
            results.append({"status": "error", "error": str(e)})
    return {"imported": len([r for r in results if r.get("status") == "imported"]), "results": results}
