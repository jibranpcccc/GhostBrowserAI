"""Capture and compare local native-Chromium device baselines.

Baseline files intentionally exclude IP addresses, cookies, storage, account
identifiers and browsing history.  They contain only coarse browser/device
capabilities needed to validate a cohort template.
"""

from __future__ import annotations

import json
import ctypes
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from backend.config import get_installed_chromium_version
from backend.device_cohorts import COHORTS, host_os


CAPTURE_SCRIPT = """
() => {
  const gl = document.createElement('canvas').getContext('webgl');
  const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
  return {
    platform: navigator.platform,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory || null,
    screen_resolution: `${screen.width}x${screen.height}`,
    screen_color_depth: screen.colorDepth,
    device_scale_factor: devicePixelRatio,
    webgl_vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
    webgl_renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
    color_gamut: ['rec2020','p3','srgb'].find(v => matchMedia(`(color-gamut: ${v})`).matches) || null,
    hdr: matchMedia('(dynamic-range: high)').matches,
    webgpu_available: !!navigator.gpu,
    media_capabilities_available: !!navigator.mediaCapabilities,
    service_worker_available: !!navigator.serviceWorker
  };
}
"""


def host_display_metrics() -> dict | None:
    """Return the primary Windows display's logical dimensions and scale."""
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    try:
        dpi = int(user32.GetDpiForSystem())
    except Exception:
        dpi = 96
    return {
        "screen_resolution": f"{width}x{height}",
        "device_scale_factor": round(dpi / 96.0, 2),
    }


async def capture_native_baseline(output_path: str | Path) -> dict:
    from backend.engine_resolver import get_chromium_executable_path_async
    executable_path = await get_chromium_executable_path_async()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=executable_path)
        try:
            page = await browser.new_page()
            await page.goto("data:text/html,<title>GhostBrowser baseline</title>")
            surfaces = await page.evaluate(CAPTURE_SCRIPT)
        finally:
            await browser.close()
    baseline = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "host_os": host_os(),
        "host_release": platform.release(),
        "chromium_version": get_installed_chromium_version(),
        "surfaces": surfaces,
    }
    display = host_display_metrics()
    if display:
        baseline["surfaces"].update(display)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    return baseline


def compare_baseline_to_cohorts(baseline: dict) -> dict:
    os_name = baseline.get("host_os")
    surfaces = baseline.get("surfaces") or {}
    candidates = []
    for cohort in COHORTS.get(os_name, ()):
        mismatches = []
        for field in ("cpu_cores", "memory_gb", "screen_resolution", "device_scale_factor"):
            observed_field = {
                "cpu_cores": "hardwareConcurrency",
                "memory_gb": "deviceMemory",
            }.get(field, field)
            observed = surfaces.get(observed_field)
            if observed is not None and observed != cohort[field]:
                mismatches.append({"field": field, "observed": observed, "cohort": cohort[field]})
        candidates.append({
            "cohort_id": cohort["id"],
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        })
    candidates.sort(key=lambda item: (item["mismatch_count"], item["cohort_id"]))
    return {
        "host_os": os_name,
        "chromium_version": baseline.get("chromium_version"),
        "best_match": candidates[0] if candidates else None,
        "candidates": candidates,
    }
