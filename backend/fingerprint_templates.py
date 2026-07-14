"""
GhostBrowser AI - Fingerprint Template Library
Pre-built fingerprint configurations for common use cases.
"""
import json
import os
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger

TEMPLATES_FILE = os.path.join(get_data_dir("profiles_data"), "fingerprint_templates.json")

router = APIRouter(prefix="/api/fingerprint-templates", tags=["fingerprint-templates"])


BUILTIN_TEMPLATES = [
    {
        "id": "win11_chrome_rtx4090",
        "name": "Windows 11 / Chrome / RTX 4090",
        "description": "High-end Windows desktop with latest Chrome and RTX 4090 GPU",
        "category": "desktop",
        "builtin": True,
        "config": {
            "os": "Windows",
            "os_version": "11",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 24,
            "device_memory": 32,
            "screen_width": 2560,
            "screen_height": 1440,
            "device_pixel_ratio": 1.0,
            "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D12 vs_5_0 ps_5_0, D3D12)",
            "fonts": ["Arial", "Calibri", "Segoe UI", "Times New Roman", "Verdana"],
            "plugins": ["Chrome PDF Plugin", "Chrome PDF Viewer", "Chromium PDF Viewer"],
        }
    },
    {
        "id": "win11_chrome_rtx3060",
        "name": "Windows 11 / Chrome / RTX 3060",
        "description": "Mid-range Windows desktop with RTX 3060",
        "category": "desktop",
        "builtin": True,
        "config": {
            "os": "Windows",
            "os_version": "11",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 12,
            "device_memory": 16,
            "screen_width": 1920,
            "screen_height": 1080,
            "device_pixel_ratio": 1.0,
            "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "fonts": ["Arial", "Segoe UI", "Times New Roman", "Verdana", "Tahoma"],
            "plugins": ["Chrome PDF Plugin", "Chrome PDF Viewer", "Chromium PDF Viewer"],
        }
    },
    {
        "id": "macbook_pro_m3",
        "name": "MacBook Pro 16\" / M3 Pro",
        "description": "Apple MacBook Pro with M3 Pro chip",
        "category": "desktop",
        "builtin": True,
        "config": {
            "os": "Mac",
            "os_version": "Sonoma",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 12,
            "device_memory": 18,
            "screen_width": 3456,
            "screen_height": 2234,
            "device_pixel_ratio": 2.0,
            "webgl_vendor": "Apple",
            "webgl_renderer": "Apple M3 Pro",
            "fonts": ["Arial", "Helvetica Neue", "Times New Roman", "SF Pro Text"],
            "plugins": ["Chrome PDF Plugin", "Chrome PDF Viewer"],
        }
    },
    {
        "id": "macbook_air_m2",
        "name": "MacBook Air 13\" / M2",
        "description": "Apple MacBook Air with M2 chip",
        "category": "desktop",
        "builtin": True,
        "config": {
            "os": "Mac",
            "os_version": "Ventura",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 8,
            "device_memory": 8,
            "screen_width": 2560,
            "screen_height": 1600,
            "device_pixel_ratio": 2.0,
            "webgl_vendor": "Apple",
            "webgl_renderer": "Apple M2",
            "fonts": ["Arial", "Helvetica Neue", "Times New Roman"],
            "plugins": ["Chrome PDF Plugin", "Chrome PDF Viewer"],
        }
    },
    {
        "id": "linux_ubuntu_chrome",
        "name": "Ubuntu 24.04 / Chrome / Intel",
        "description": "Linux Ubuntu desktop with Intel GPU",
        "category": "desktop",
        "builtin": True,
        "config": {
            "os": "Linux",
            "os_version": "Ubuntu 24.04",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 8,
            "device_memory": 16,
            "screen_width": 1920,
            "screen_height": 1080,
            "device_pixel_ratio": 1.0,
            "webgl_vendor": "Intel Open Source Technology Center",
            "webgl_renderer": "Mesa Intel(R) UHD Graphics 770",
            "fonts": ["Arial", "DejaVu Sans", "Liberation Sans", "Noto Sans"],
            "plugins": ["Chrome PDF Plugin", "Chrome PDF Viewer"],
        }
    },
    {
        "id": "iphone_15_pro",
        "name": "iPhone 15 Pro / Safari",
        "description": "Apple iPhone 15 Pro mobile browser",
        "category": "mobile",
        "builtin": True,
        "config": {
            "os": "iOS",
            "os_version": "17.5",
            "browser": "Safari",
            "hardware_concurrency": 6,
            "device_memory": 8,
            "screen_width": 393,
            "screen_height": 852,
            "device_pixel_ratio": 3.0,
            "max_touch_points": 5,
            "webgl_vendor": "Apple",
            "webgl_renderer": "Apple GPU",
            "mobile": True,
            "plugins": [],
        }
    },
    {
        "id": "samsung_s24_ultra",
        "name": "Samsung Galaxy S24 Ultra / Chrome",
        "description": "Samsung Galaxy S24 Ultra mobile browser",
        "category": "mobile",
        "builtin": True,
        "config": {
            "os": "Android",
            "os_version": "14",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 8,
            "device_memory": 12,
            "screen_width": 412,
            "screen_height": 915,
            "device_pixel_ratio": 3.5,
            "max_touch_points": 10,
            "webgl_vendor": "Qualcomm",
            "webgl_renderer": "Adreno (TM) 750",
            "mobile": True,
            "plugins": [],
        }
    },
    {
        "id": "ipad_pro_m2",
        "name": "iPad Pro 12.9\" / M2",
        "description": "Apple iPad Pro with M2 chip",
        "category": "tablet",
        "builtin": True,
        "config": {
            "os": "iPadOS",
            "os_version": "17.5",
            "browser": "Safari",
            "hardware_concurrency": 8,
            "device_memory": 8,
            "screen_width": 1024,
            "screen_height": 1366,
            "device_pixel_ratio": 2.0,
            "max_touch_points": 5,
            "webgl_vendor": "Apple",
            "webgl_renderer": "Apple M2",
            "mobile": True,
            "plugins": [],
        }
    },
    {
        "id": "stealth_max",
        "name": "Maximum Stealth",
        "description": "Maximum privacy: minimal fingerprint, common hardware, VPN-friendly",
        "category": "stealth",
        "builtin": True,
        "config": {
            "os": "Windows",
            "os_version": "11",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 4,
            "device_memory": 8,
            "screen_width": 1366,
            "screen_height": 768,
            "device_pixel_ratio": 1.0,
            "webgl_vendor": "Google Inc. (Intel)",
            "webgl_renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.5)",
            "fonts": ["Arial", "Times New Roman", "Courier New"],
            "plugins": ["Chrome PDF Plugin"],
            "canvas_noise": True,
            "webgl_noise": True,
            "audio_noise": True,
            "dom_rect_noise": True,
        }
    },
    {
        "id": "eCommerce_us",
        "name": "US E-commerce / Shopping",
        "description": "US-based shopping profile with common retail configuration",
        "category": "use_case",
        "builtin": True,
        "config": {
            "os": "Windows",
            "os_version": "11",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 8,
            "device_memory": 16,
            "screen_width": 1920,
            "screen_height": 1080,
            "device_pixel_ratio": 1.0,
            "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "fonts": ["Arial", "Segoe UI", "Roboto"],
            "plugins": ["Chrome PDF Plugin", "Chrome PDF Viewer"],
            "timezone": "America/New_York",
            "locale": "en-US",
        }
    },
    {
        "id": "eCommerce_uk",
        "name": "UK E-commerce / Shopping",
        "description": "UK-based shopping profile",
        "category": "use_case",
        "builtin": True,
        "config": {
            "os": "Windows",
            "os_version": "11",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 8,
            "device_memory": 16,
            "screen_width": 1920,
            "screen_height": 1080,
            "device_pixel_ratio": 1.0,
            "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "timezone": "Europe/London",
            "locale": "en-GB",
        }
    },
    {
        "id": "social_media",
        "name": "Social Media Manager",
        "description": "Optimized for social media platforms with consistent fingerprint",
        "category": "use_case",
        "builtin": True,
        "config": {
            "os": "Windows",
            "os_version": "11",
            "browser": "Chrome",
            "chrome_version": "136",
            "hardware_concurrency": 8,
            "device_memory": 16,
            "screen_width": 1920,
            "screen_height": 1080,
            "device_pixel_ratio": 1.0,
            "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "canvas_noise": False,
            "webgl_noise": False,
            "audio_noise": False,
        }
    },
]


class FingerprintTemplateManager:
    def __init__(self):
        self._templates: Dict[str, dict] = {}
        self._load()
        self._ensure_builtin()

    def _load(self):
        if os.path.exists(TEMPLATES_FILE):
            try:
                with open(TEMPLATES_FILE, "r") as f:
                    data = json.load(f)
                self._templates = {t["id"]: t for t in data.get("templates", [])}
            except Exception as e:
                logger.error(f"Failed to load fingerprint templates: {e}")

    def _save(self):
        try:
            data = {"templates": list(self._templates.values())}
            with open(TEMPLATES_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save fingerprint templates: {e}")

    def _ensure_builtin(self):
        for bt in BUILTIN_TEMPLATES:
            if bt["id"] not in self._templates:
                self._templates[bt["id"]] = bt
        self._save()

    def list_templates(self, category: str = None) -> List[dict]:
        templates = list(self._templates.values())
        if category:
            templates = [t for t in templates if t.get("category") == category]
        return templates

    def get_template(self, template_id: str) -> Optional[dict]:
        return self._templates.get(template_id)

    def create_template(self, data: dict) -> dict:
        tid = data.get("id", f"custom_{int(time.time() * 1000)}")
        template = {
            "id": tid,
            "name": data.get("name", "Custom Template"),
            "description": data.get("description", ""),
            "category": data.get("category", "custom"),
            "builtin": False,
            "config": data.get("config", {}),
            "created_at": time.time(),
        }
        self._templates[tid] = template
        self._save()
        return template

    def update_template(self, template_id: str, data: dict) -> Optional[dict]:
        template = self._templates.get(template_id)
        if not template or template.get("builtin"):
            return None
        for k, v in data.items():
            if k != "id":
                template[k] = v
        self._save()
        return template

    def delete_template(self, template_id: str) -> bool:
        template = self._templates.get(template_id)
        if not template or template.get("builtin"):
            return False
        del self._templates[template_id]
        self._save()
        return True


template_manager = FingerprintTemplateManager()


class TemplateModel(BaseModel):
    name: str
    description: str = ""
    category: str = "custom"
    config: dict = {}


@router.get("")
def list_templates(category: str = None):
    return template_manager.list_templates(category)


@router.get("/{template_id}")
def get_template(template_id: str):
    t = template_manager.get_template(template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@router.post("")
def create_template(payload: TemplateModel):
    return template_manager.create_template(payload.dict())


@router.put("/{template_id}")
def update_template(template_id: str, payload: TemplateModel):
    updated = template_manager.update_template(template_id, payload.dict())
    if not updated:
        raise HTTPException(status_code=404, detail="Template not found or is builtin")
    return updated


@router.delete("/{template_id}")
def delete_template(template_id: str):
    if not template_manager.delete_template(template_id):
        raise HTTPException(status_code=404, detail="Template not found or is builtin")
    return {"status": "deleted"}


@router.post("/{template_id}/apply")
def apply_template(template_id: str):
    t = template_manager.get_template(template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"status": "applied", "config": t.get("config", {})}
