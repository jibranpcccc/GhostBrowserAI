"""Host-compatible, internally coherent device templates.

Profiles select from a small set of common device classes instead of combining
independently random hardware values.  The selected template is stable for a
profile identifier and is intentionally not a unique identifier by itself.
"""

from __future__ import annotations

import hashlib
import platform
from copy import deepcopy


COHORTS = {
    "Windows": (
        {"id": "win-mainstream-1080p", "cpu_cores": 8, "memory_gb": 16,
         "screen_resolution": "1920x1080", "device_scale_factor": 1.0,
         "webgl_vendor": "Google Inc. (Intel)",
         "webgl_renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
        {"id": "win-performance-1440p", "cpu_cores": 16, "memory_gb": 32,
         "screen_resolution": "2560x1440", "device_scale_factor": 1.0,
         "webgl_vendor": "Google Inc. (NVIDIA)",
         "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
        {"id": "win-workstation-1200p", "cpu_cores": 16, "memory_gb": 32,
         "screen_resolution": "1920x1200", "device_scale_factor": 1.0,
         "webgl_vendor": "Google Inc. (NVIDIA)",
         "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
        {"id": "win-laptop-125", "cpu_cores": 8, "memory_gb": 16,
         "screen_resolution": "1536x864", "device_scale_factor": 1.25,
         "webgl_vendor": "Google Inc. (Intel)",
         "webgl_renderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    ),
    "Mac": (
        {"id": "macbook-air", "cpu_cores": 8, "memory_gb": 16,
         "screen_resolution": "1280x832", "device_scale_factor": 2.0,
         "webgl_vendor": "Apple",
         "webgl_renderer": "Apple M2"},
        {"id": "macbook-pro", "cpu_cores": 10, "memory_gb": 16,
         "screen_resolution": "1512x982", "device_scale_factor": 2.0,
         "webgl_vendor": "Apple",
         "webgl_renderer": "Apple M2 Pro"},
    ),
    "Linux": (
        {"id": "linux-mainstream-1080p", "cpu_cores": 8, "memory_gb": 16,
         "screen_resolution": "1920x1080", "device_scale_factor": 1.0,
         "webgl_vendor": "Google Inc. (Intel)",
         "webgl_renderer": "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630, OpenGL 4.6)"},
        {"id": "linux-workstation-1440p", "cpu_cores": 16, "memory_gb": 32,
         "screen_resolution": "2560x1440", "device_scale_factor": 1.0,
         "webgl_vendor": "Google Inc. (NVIDIA)",
         "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, OpenGL 4.5)"},
    ),
}

MOBILE_COHORTS = {
    "Mac": (
        {"id": "ios-phone-sm", "cpu_cores": 6, "memory_gb": 4,
         "screen_resolution": "375x667", "device_scale_factor": 2.0,
         "webgl_vendor": "Apple Inc.",
         "webgl_renderer": "Apple GPU",
         "max_touch_points": 5},
        {"id": "ios-phone-md", "cpu_cores": 6, "memory_gb": 6,
         "screen_resolution": "390x844", "device_scale_factor": 3.0,
         "webgl_vendor": "Apple Inc.",
         "webgl_renderer": "Apple GPU",
         "max_touch_points": 5},
        {"id": "ios-phone-lg", "cpu_cores": 8, "memory_gb": 8,
         "screen_resolution": "414x896", "device_scale_factor": 3.0,
         "webgl_vendor": "Apple Inc.",
         "webgl_renderer": "Apple GPU",
         "max_touch_points": 5},
    ),
    "Linux": (
        {"id": "android-phone-sm", "cpu_cores": 6, "memory_gb": 4,
         "screen_resolution": "360x640", "device_scale_factor": 2.0,
         "webgl_vendor": "Qualcomm",
         "webgl_renderer": "Adreno (TM) 610",
         "max_touch_points": 5},
        {"id": "android-phone-md", "cpu_cores": 8, "memory_gb": 6,
         "screen_resolution": "393x851", "device_scale_factor": 2.75,
         "webgl_vendor": "Qualcomm",
         "webgl_renderer": "Adreno (TM) 730",
         "max_touch_points": 5},
        {"id": "android-phone-lg", "cpu_cores": 8, "memory_gb": 8,
         "screen_resolution": "414x896", "device_scale_factor": 3.0,
         "webgl_vendor": "Qualcomm",
         "webgl_renderer": "Adreno (TM) 740",
         "max_touch_points": 5},
    ),
}


def host_os() -> str:
    name = platform.system()
    return "Mac" if name == "Darwin" else (name if name in COHORTS else "Linux")


def require_host_compatible(requested_os: str | None) -> str:
    actual = host_os()
    requested = requested_os or actual
    if requested != actual:
        raise ValueError(
            f"Unsupported OS: {requested} on {actual} host. "
            "Use a machine or VM running the requested operating system."
        )
    return actual


def select_cohort(
    profile_id: str,
    os_name: str,
    preferred_resolution: str | None = None,
    device_type: str = "desktop",
) -> dict:
    if device_type == "mobile":
        cohort_source = MOBILE_COHORTS
        allowed_os = ("Mac", "Linux")
        os_name = os_name if os_name in allowed_os else "Linux"
    else:
        cohort_source = COHORTS
    candidates = list(cohort_source.get(os_name, ()))
    if not candidates:
        candidates = list(cohort_source.get("Linux", ()))
    if preferred_resolution:
        matching = [item for item in candidates if item["screen_resolution"] == preferred_resolution]
        if matching:
            candidates = matching
    digest = hashlib.sha256(str(profile_id).encode("utf-8")).digest()
    return deepcopy(candidates[int.from_bytes(digest[:4], "big") % len(candidates)])


def apply_cohort(
    fingerprint: dict,
    profile_id: str,
    os_name: str,
    preferred_resolution: str | None = None,
    device_type: str = "desktop",
) -> tuple[dict, dict]:
    cohort = select_cohort(profile_id, os_name, preferred_resolution, device_type)
    normalized = deepcopy(fingerprint)
    for field in ("cpu_cores", "memory_gb", "screen_resolution", "webgl_vendor", "webgl_renderer"):
        normalized[field] = cohort[field]
    normalized["hardwareConcurrency"] = cohort["cpu_cores"]
    normalized["deviceMemory"] = cohort["memory_gb"]
    normalized["device_scale_factor"] = cohort["device_scale_factor"]
    normalized["max_touch_points"] = cohort.get("max_touch_points", 5 if device_type == "mobile" else 0)
    normalized["device_cohort"] = cohort["id"]
    normalized["device_type"] = device_type
    normalized["is_mobile_profile"] = device_type == "mobile"
    return normalized, cohort
