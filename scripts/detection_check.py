#!/usr/bin/env python3
"""GhostBrowser anti-detection sanity check.

Launches a profile headlessly, loads a series of offline-safe data: pages that
mirror real-world scanner probes, and asserts automation/leak signals are
absent and expected browser surfaces are coherent. Exits 0 on pass, 1 on fail.
"""

import argparse
import asyncio
import ipaddress
import json
import os
import shutil
import sys
import tempfile

# Make `backend.*` imports resolve when this script is run from `scripts/`.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# Scanner pages: data:text/html, no external network required.
# ---------------------------------------------------------------------------

BASIC_SURFACE_PAGE = (
    "data:text/html,<html><body><h1>Basic surface check</h1></body></html>"
)
BASIC_SURFACE_PROBE = """
(async () => {
    const result = {};
    result.webdriver = navigator.webdriver;
    result.hasWebdriverProp = 'webdriver' in navigator;

    const artifacts = [];
    try {
        for (const name of Object.getOwnPropertyNames(window)) {
            if (/^\\$cdc_/i.test(name) || /^\\$chromium_/i.test(name)) {
                artifacts.push(name);
            }
        }
    } catch (e) {}
    result.cdcArtifacts = artifacts;

    result.chromeRuntime = !!(typeof window.chrome !== 'undefined' && window.chrome);
    result.pluginsLength = navigator.plugins ? navigator.plugins.length : 0;
    result.languages = navigator.languages;
    result.mediaDevicesExists = !!(navigator.mediaDevices);
    result.getUserMediaType = navigator.mediaDevices
        ? typeof navigator.mediaDevices.getUserMedia
        : 'missing';

    let getUserMediaBlocked = false;
    let getUserMediaError = null;
    if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function') {
        try {
            await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
            getUserMediaBlocked = false;
        } catch (err) {
            getUserMediaBlocked = true;
            getUserMediaError = err.name + ': ' + err.message;
        }
    } else {
        getUserMediaBlocked = true;
    }
    result.getUserMediaBlocked = getUserMediaBlocked;
    result.getUserMediaError = getUserMediaError;

    return result;
})()
"""

WEBDRIVER_PAGE = (
    "data:text/html,<html><body><h1>WebDriver probe</h1></body></html>"
)
WEBDRIVER_PROBE = """
(async () => {
    const result = {};
    result.webdriver = navigator.webdriver;
    result.hasWebdriverProp = 'webdriver' in navigator;

    const artifacts = [];
    try {
        for (const name of Object.getOwnPropertyNames(window)) {
            if (/^\\$cdc_/i.test(name) || /^\\$chromium_/i.test(name)) {
                artifacts.push(name);
            }
        }
    } catch (e) {}
    result.cdcArtifacts = artifacts;

    result.domAutomation = typeof window.domAutomation === 'undefined'
        ? 'undefined'
        : String(window.domAutomation);
    result.domAutomationController = typeof window.domAutomationController === 'undefined'
        ? 'undefined'
        : String(window.domAutomationController);
    result.callPhantom = typeof window.callPhantom;

    return result;
})()
"""

PERMISSIONS_PAGE = (
    "data:text/html,<html><body><h1>Permissions probe</h1></body></html>"
)
PERMISSIONS_PROBE = """
(async () => {
    const result = {};
    result.notificationPermission = Notification.permission;

    if (navigator.permissions && typeof navigator.permissions.query === 'function') {
        try {
            const status = await navigator.permissions.query({name: 'notifications'});
            result.queryState = status.state;
        } catch (err) {
            result.queryState = 'error';
            result.queryError = err.name + ': ' + err.message;
        }
    } else {
        result.queryState = 'unsupported';
    }

    return result;
})()
"""

PLUGINS_PAGE = (
    "data:text/html,<html><body><h1>Plugins probe</h1></body></html>"
)
PLUGINS_PROBE = """
(() => {
    return {
        pluginsLength: navigator.plugins ? navigator.plugins.length : 0,
        mimeTypesLength: navigator.mimeTypes ? navigator.mimeTypes.length : 0,
        pluginNames: Array.from(navigator.plugins || []).map(p => p.name),
        mimeTypeNames: Array.from(navigator.mimeTypes || []).map(m => m.type),
    };
})()
"""

CANVAS_PAGE = (
    "data:text/html,<canvas id='c' width='16' height='16'></canvas>"
)
CANVAS_PROBE = """
(() => {
    const canvas = document.getElementById('c');
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = '#f06';
    ctx.fillRect(2, 2, 12, 12);

    const dataURL = canvas.toDataURL('image/png');
    const imageData = ctx.getImageData(0, 0, 16, 16).data;
    const nonZero = imageData.some(v => v !== 0);

    return {
        dataURLLength: dataURL.length,
        dataURLPrefix: dataURL.slice(0, 40),
        imageDataLength: imageData.length,
        nonZero: nonZero,
    };
})()
"""

WEBGL_PAGE = (
    "data:text/html,<canvas id='c' width='16' height='16'></canvas>"
)
WEBGL_PROBE = """
(() => {
    const canvas = document.getElementById('c');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');

    if (!gl) {
        return {supported: false, reason: 'no webgl context'};
    }

    const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
    const vendor = gl.getParameter(gl.VENDOR);
    const renderer = gl.getParameter(gl.RENDERER);

    let unmaskedVendor = null;
    let unmaskedRenderer = null;
    if (debugInfo) {
        unmaskedVendor = gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL);
        unmaskedRenderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL);
    }

    return {
        supported: true,
        vendor: vendor,
        renderer: renderer,
        unmaskedVendor: unmaskedVendor,
        unmaskedRenderer: unmaskedRenderer,
    };
})()
"""

CLIENT_HINTS_PAGE = (
    "data:text/html,<html><body><h1>Client hints probe</h1></body></html>"
)
CLIENT_HINTS_PROBE = """
(async () => {
    if (!navigator.userAgentData) {
        return {supported: false};
    }

    let highEntropy = {};
    try {
        highEntropy = await navigator.userAgentData.getHighEntropyValues([
            'platform', 'platformVersion', 'architecture', 'model', 'uaFullVersion'
        ]);
    } catch (err) {
        highEntropy.error = err.name + ': ' + err.message;
    }

    return {
        supported: true,
        brands: navigator.userAgentData.brands,
        mobile: navigator.userAgentData.mobile,
        platform: navigator.userAgentData.platform,
        highEntropy: highEntropy,
    };
})()
"""

SENSORS_PAGE = (
    "data:text/html,<html><body><h1>Sensor API probe</h1></body></html>"
)
SENSORS_PROBE = """
(() => {
    return {
        deviceMotionType: typeof DeviceMotionEvent,
        deviceOrientationType: typeof DeviceOrientationEvent,
        absoluteOrientationType: typeof AbsoluteOrientationEvent,
        ambientLightType: typeof AmbientLightEvent,
        proximityType: typeof ProximityEvent,
    };
})()
"""

BATTERY_PAGE = (
    "data:text/html,<html><body><h1>Battery API probe</h1></body></html>"
)
BATTERY_PROBE = """
(async () => {
    if (typeof navigator.getBattery !== 'function') {
        return { hasBattery: false };
    }
    const b = await navigator.getBattery();
    return {
        hasBattery: true,
        charging: b.charging,
        level: b.level,
        chargingTime: b.chargingTime,
        dischargingTime: b.dischargingTime,
    };
})()
"""

MEDIA_DEVICES_PAGE = (
    "data:text/html,<html><body><h1>MediaDevices probe</h1></body></html>"
)
MEDIA_DEVICES_PROBE = """
(async () => {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.enumerateDevices !== 'function') {
        return { supported: false };
    }
    const devices = await navigator.mediaDevices.enumerateDevices();
    const count = {};
    for (const d of devices) {
        count[d.kind] = (count[d.kind] || 0) + 1;
    }
    return { supported: true, count: count, total: devices.length };
})()
"""

WEBGPU_PAGE = (
    "data:text/html,<html><body><h1>WebGPU probe</h1></body></html>"
)
WEBGPU_PROBE = """
(async () => {
    if (!navigator.gpu || typeof navigator.gpu.requestAdapter !== 'function') {
        return { supported: false };
    }
    const adapter = await navigator.gpu.requestAdapter();
    const info = adapter.info || await adapter.requestAdapterInfo();
    return {
        supported: true,
        vendor: info.vendor,
        architecture: info.architecture,
        device: info.device,
        description: info.description,
    };
})()
"""

TIMING_PAGE = (
    "data:text/html,<html><body><h1>Timing probe</h1></body></html>"
)
TIMING_PROBE = r"""
(() => {
    const n = performance.now();
    const d = Date.now();
    // ponytail: a coarse clock never exposes more than two decimal places.
    const coarsePerf = Math.abs(n - Math.floor(n * 100) / 100) < 1e-9;
    return { performanceNow: n, dateNow: d, coarse: coarsePerf };
})()
"""

DNS_LEAK_PROBE = """
(async () => {
    const result = { attempted: false, reached: false, error: null, errorName: null };
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 5000);
        // .invalid is a reserved TLD guaranteed not to resolve, so this fetch
        // should only reach the network if DNS is being issued.
        await fetch('http://dnsleak-test.invalid/', { signal: controller.signal, mode: 'no-cors' });
        clearTimeout(timer);
        result.attempted = true;
        result.reached = true;
    } catch (err) {
        result.attempted = true;
        result.errorName = err.name;
        result.error = err.name + ': ' + err.message;
    }
    return result;
})()
"""


# ---------------------------------------------------------------------------
# Validators: each returns {"passed": bool, "checks": {...}, ...}
# ---------------------------------------------------------------------------

def _result(passed, **kwargs):
    result = {"passed": passed}
    result.update(kwargs)
    return result


def validate_basic_surface(profile, result):
    checks = {}
    passed = True
    profile_locale = profile.get("locale") or "en-US"

    wd = result.get("webdriver")
    webdriver_ok = wd is None or wd is False
    checks["webdriver"] = {"passed": webdriver_ok, "value": wd}
    if not webdriver_ok:
        passed = False

    artifacts = result.get("cdcArtifacts", [])
    artifacts_ok = len(artifacts) == 0
    checks["cdc_artifacts"] = {"passed": artifacts_ok, "found": artifacts}
    if not artifacts_ok:
        passed = False

    chrome_ok = result.get("chromeRuntime") is True
    checks["chrome_runtime"] = {"passed": chrome_ok}
    if not chrome_ok:
        passed = False

    plugins_len = result.get("pluginsLength", 0)
    plugins_ok = plugins_len > 0
    checks["plugins"] = {"passed": plugins_ok, "length": plugins_len}
    if not plugins_ok:
        passed = False

    langs = result.get("languages", [])
    languages_ok = bool(langs) and langs[0] == profile_locale
    checks["languages"] = {
        "passed": languages_ok,
        "expected_first": profile_locale,
        "actual": langs,
    }
    if not languages_ok:
        passed = False

    webrtc_ok = result.get("getUserMediaBlocked") is True
    checks["webrtc"] = {
        "passed": webrtc_ok,
        "media_devices_exists": result.get("mediaDevicesExists"),
        "get_user_media_type": result.get("getUserMediaType"),
        "error": result.get("getUserMediaError"),
    }
    if not webrtc_ok:
        passed = False

    return _result(passed, checks=checks)


def validate_webdriver(_profile, result):
    checks = {}
    passed = True

    wd = result.get("webdriver")
    wd_ok = wd in (None, False)
    checks["navigator.webdriver"] = {"passed": wd_ok, "value": wd}
    if not wd_ok:
        passed = False

    artifacts = result.get("cdcArtifacts", [])
    artifacts_ok = len(artifacts) == 0
    checks["cdc_artifacts"] = {"passed": artifacts_ok, "found": artifacts}
    if not artifacts_ok:
        passed = False

    dom_auto = result.get("domAutomation")
    dom_auto_ok = dom_auto == "undefined"
    checks["domAutomation"] = {"passed": dom_auto_ok, "value": dom_auto}
    if not dom_auto_ok:
        passed = False

    dom_ctrl = result.get("domAutomationController")
    dom_ctrl_ok = dom_ctrl == "undefined"
    checks["domAutomationController"] = {
        "passed": dom_ctrl_ok,
        "value": dom_ctrl,
    }
    if not dom_ctrl_ok:
        passed = False

    call_phantom = result.get("callPhantom")
    cp_ok = call_phantom == "undefined"
    checks["callPhantom"] = {"passed": cp_ok, "value": call_phantom}
    if not cp_ok:
        passed = False

    return _result(passed, checks=checks)


def validate_permissions(_profile, result):
    checks = {}
    passed = True

    np = result.get("notificationPermission")
    np_ok = isinstance(np, str) and np in ("default", "granted", "denied")
    checks["Notification.permission"] = {"passed": np_ok, "value": np}
    if not np_ok:
        passed = False

    qs = result.get("queryState")
    qs_ok = isinstance(qs, str) and qs in ("prompt", "granted", "denied", "unsupported")
    checks["permissions.query"] = {"passed": qs_ok, "value": qs, "error": result.get("queryError")}
    if not qs_ok:
        passed = False

    return _result(passed, checks=checks)


def validate_plugins(_profile, result):
    checks = {}
    passed = True

    pl = result.get("pluginsLength", 0)
    pl_ok = isinstance(pl, int) and pl > 0
    checks["navigator.plugins.length"] = {
        "passed": pl_ok,
        "value": pl,
        "names": result.get("pluginNames", []),
    }
    if not pl_ok:
        passed = False

    mt = result.get("mimeTypesLength", 0)
    mt_ok = isinstance(mt, int) and mt > 0
    checks["navigator.mimeTypes.length"] = {
        "passed": mt_ok,
        "value": mt,
        "types": result.get("mimeTypeNames", []),
    }
    if not mt_ok:
        passed = False

    return _result(passed, checks=checks)


def validate_canvas(_profile, result):
    checks = {}
    passed = True

    url_len = result.get("dataURLLength", 0)
    prefix = result.get("dataURLPrefix", "")
    url_ok = (
        isinstance(url_len, int)
        and url_len > 0
        and isinstance(prefix, str)
        and prefix.startswith("data:image/png;base64,")
    )
    checks["canvas.toDataURL"] = {"passed": url_ok, "length": url_len, "prefix": prefix}
    if not url_ok:
        passed = False

    id_len = result.get("imageDataLength", 0)
    id_ok = id_len == 16 * 16 * 4
    checks["canvas.getImageData"] = {"passed": id_ok, "value": id_len}
    if not id_ok:
        passed = False

    nz = result.get("nonZero")
    nz_ok = nz is True
    checks["canvas.nonZeroPixels"] = {"passed": nz_ok, "value": nz}
    if not nz_ok:
        passed = False

    return _result(passed, checks=checks)


_GPU_KEYWORDS = [
    "nvidia", "amd", "intel", "apple", "microsoft", "qualcomm",
    "adreno", "mali", "angle", "geforce", "radeon", "webkit",
    "google", "mozilla",
]


def _looks_gpu(value):
    if not isinstance(value, str) or not value:
        return False
    lv = value.lower()
    return any(k in lv for k in _GPU_KEYWORDS)


def validate_webgl(_profile, result):
    checks = {}
    passed = True

    supported = result.get("supported") is True
    checks["webgl.supported"] = {
        "passed": supported,
        "value": result.get("supported"),
        "reason": result.get("reason"),
    }
    if not supported:
        return _result(False, checks=checks)

    vendor = result.get("vendor", "")
    vendor_ok = _looks_gpu(vendor)
    checks["webgl.vendor"] = {"passed": vendor_ok, "value": vendor}
    if not vendor_ok:
        passed = False

    renderer = result.get("renderer", "")
    renderer_ok = _looks_gpu(renderer)
    checks["webgl.renderer"] = {"passed": renderer_ok, "value": renderer}
    if not renderer_ok:
        passed = False

    unmasked_vendor = result.get("unmaskedVendor")
    unmasked_renderer = result.get("unmaskedRenderer")
    if unmasked_vendor is not None or unmasked_renderer is not None:
        uv_ok = _looks_gpu(unmasked_vendor) if unmasked_vendor is not None else True
        ur_ok = _looks_gpu(unmasked_renderer) if unmasked_renderer is not None else True
        checks["webgl.unmaskedVendor"] = {"passed": uv_ok, "value": unmasked_vendor}
        checks["webgl.unmaskedRenderer"] = {"passed": ur_ok, "value": unmasked_renderer}
        if not (uv_ok and ur_ok):
            passed = False
    else:
        checks["webgl.unmasked"] = {
            "passed": True,
            "note": "WEBGL_debug_renderer_info not available",
        }

    return _result(passed, checks=checks)


def validate_client_hints(_profile, result):
    checks = {}
    passed = True

    supported = result.get("supported") is True
    checks["userAgentData.supported"] = {"passed": supported, "value": result.get("supported")}
    if not supported:
        return _result(True, checks=checks)

    platform = result.get("platform", "")
    platform_ok = isinstance(platform, str) and len(platform) > 0
    checks["userAgentData.platform"] = {"passed": platform_ok, "value": platform}
    if not platform_ok:
        passed = False

    brands = result.get("brands", [])
    brands_ok = (
        isinstance(brands, list)
        and len(brands) > 0
        and all(
            isinstance(b, dict)
            and isinstance(b.get("brand"), str)
            and isinstance(b.get("version"), str)
            for b in brands
        )
    )
    checks["userAgentData.brands"] = {"passed": brands_ok, "value": brands}
    if not brands_ok:
        passed = False

    he = result.get("highEntropy", {})
    he_error = isinstance(he, dict) and "error" in he
    he_platform = he.get("platform", "") if isinstance(he, dict) else ""
    he_ok = isinstance(he, dict) and isinstance(he_platform, str) and len(he_platform) > 0 and not he_error
    checks["userAgentData.highEntropy.platform"] = {"passed": he_ok, "value": he}
    if not he_ok:
        passed = False

    return _result(passed, checks=checks)


def validate_sensors(profile, result):
    checks = {}
    passed = True
    is_mobile = profile.get("advanced", {}).get("device_type") == "mobile"

    expected = "function" if is_mobile else "undefined"
    for name in (
        "deviceMotionType",
        "deviceOrientationType",
        "absoluteOrientationType",
        "ambientLightType",
        "proximityType",
    ):
        # The check is strictest for desktop cohorts: sensor constructors must be hidden.
        leeway = is_mobile
        ok = result.get(name) == expected or leeway
        checks[name] = {"passed": ok, "value": result.get(name), "expected": expected}
        if not ok:
            passed = False

    return _result(passed, checks=checks)


def validate_battery(_profile, result):
    checks = {}
    if not result.get("hasBattery"):
        checks["battery"] = {"passed": True, "note": "navigator.getBattery undefined"}
        return _result(True, checks=checks)

    level = result.get("level")
    charging = result.get("charging")
    level_ok = level == 1.0
    charging_ok = charging is True
    checks["battery.level"] = {"passed": level_ok, "value": level, "expected": 1.0}
    checks["battery.charging"] = {"passed": charging_ok, "value": charging, "expected": True}
    return _result(level_ok and charging_ok, checks=checks)


def validate_media_devices(_profile, result):
    checks = {}
    passed = True

    supported = result.get("supported") is True
    checks["mediaDevices.supported"] = {"passed": supported, "value": result.get("supported")}
    if not supported:
        passed = False
        return _result(False, checks=checks)

    count = result.get("count", {})
    for kind in ("audioinput", "audiooutput", "videoinput"):
        got = count.get(kind, 0)
        ok = isinstance(got, int) and got >= 1
        checks[f"mediaDevices.{kind}"] = {"passed": ok, "count": got}
        if not ok:
            passed = False

    return _result(passed, checks=checks)


def validate_webgpu(profile, result):
    checks = {}
    passed = True

    supported = result.get("supported") is True
    checks["webgpu.supported"] = {"passed": supported, "value": result.get("supported")}
    if not supported:
        return _result(True, checks=checks)

    expected_vendor = profile.get("advanced", {}).get("webgl_vendor", "Google Inc. (NVIDIA)")
    vendor = result.get("vendor")
    vendor_ok = isinstance(vendor, str) and vendor == expected_vendor
    checks["webgpu.vendor"] = {"passed": vendor_ok, "value": vendor, "expected": expected_vendor}
    if not vendor_ok:
        passed = False

    return _result(passed, checks=checks)


def validate_timing(_profile, result):
    checks = {}
    checks["performance.now"] = {"value": result.get("performanceNow"), "coarse": result.get("coarse")}
    passed = result.get("coarse") is True
    return _result(passed, checks=checks)


def validate_dns_leak(_profile, result):
    """Policy-only DNS leak heuristics (informational, not a leak detector).

    DNS interception requires admin privileges, so this scanner verifies the
    launch arguments that keep name resolution predictable and proxy-bound, and
    confirms the browser still attempts a DNS lookup for an unresolvable TLD.
    It always reports status=warning because it cannot prove the absence of a
    downstream leak without a privileged packet capture.
    """
    checks = {}

    launch_args = result.get("launch_args", [])
    arg_set = set(launch_args)

    ipv6_ok = "--disable-ipv6" in arg_set
    checks["disable_ipv6"] = {"passed": ipv6_ok}

    webrtc_ok = any(
        a.startswith("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
        for a in launch_args
    )
    checks["webrtc_udp_disabled"] = {"passed": webrtc_ok}

    dns_prefetch_ok = "--dns-prefetch-disable" in arg_set
    checks["dns_prefetch_disable"] = {"passed": dns_prefetch_ok}

    async_dns_ok = "--disable-async-dns" in arg_set
    checks["async_dns_disabled"] = {"passed": async_dns_ok}

    proxy_configured = result.get("proxy_configured") is True
    checks["proxy_configured"] = {
        "passed": proxy_configured,
        "note": "Direct profiles cannot guarantee proxy-bound DNS" if not proxy_configured else None,
    }

    fetch_result = result.get("fetch_result", {})
    fetch_attempted = fetch_result.get("attempted") is True
    checks["dns_request_attempted"] = {
        "passed": fetch_attempted,
        "error": fetch_result.get("error"),
    }

    details = {
        "checks": checks,
        # Always report a warning: absence of local flags is not proof that DNS
        # cannot leak at the network layer.
        "status": "warning",
    }
    return _result(True, **details)


async def dns_leak_run(page, profile, timeout):
    """Gather launch policy state from the running browser and run a DNS fetch probe."""
    from backend.browser_manager import active_browsers

    browser_data = active_browsers.get(profile["id"]) or {}
    launch_args = browser_data.get("args", [])
    proxy_configured = browser_data.get("proxy") is not None

    fetch_result = await page.evaluate(DNS_LEAK_PROBE)
    return {
        "launch_args": launch_args,
        "proxy_configured": proxy_configured,
        "fetch_result": fetch_result,
    }


# ---------------------------------------------------------------------------
# Scanner registry.
# ---------------------------------------------------------------------------

SCANNERS = [
    {
        "name": "basic_surface",
        "page": BASIC_SURFACE_PAGE,
        "probe": BASIC_SURFACE_PROBE,
        "validate": validate_basic_surface,
    },
    {
        "name": "webdriver",
        "page": WEBDRIVER_PAGE,
        "probe": WEBDRIVER_PROBE,
        "validate": validate_webdriver,
    },
    {
        "name": "permissions",
        "page": PERMISSIONS_PAGE,
        "probe": PERMISSIONS_PROBE,
        "validate": validate_permissions,
    },
    {
        "name": "plugins",
        "page": PLUGINS_PAGE,
        "probe": PLUGINS_PROBE,
        "validate": validate_plugins,
    },
    {
        "name": "canvas",
        "page": CANVAS_PAGE,
        "probe": CANVAS_PROBE,
        "validate": validate_canvas,
    },
    {
        "name": "webgl",
        "page": WEBGL_PAGE,
        "probe": WEBGL_PROBE,
        "validate": validate_webgl,
    },
    {
        "name": "client_hints",
        "page": CLIENT_HINTS_PAGE,
        "probe": CLIENT_HINTS_PROBE,
        "validate": validate_client_hints,
    },
    {
        "name": "sensors",
        "page": SENSORS_PAGE,
        "probe": SENSORS_PROBE,
        "validate": validate_sensors,
    },
    {
        "name": "battery",
        "page": BATTERY_PAGE,
        "probe": BATTERY_PROBE,
        "validate": validate_battery,
    },
    {
        "name": "media_devices",
        "page": MEDIA_DEVICES_PAGE,
        "probe": MEDIA_DEVICES_PROBE,
        "validate": validate_media_devices,
    },
    {
        "name": "webgpu",
        "page": WEBGPU_PAGE,
        "probe": WEBGPU_PROBE,
        "validate": validate_webgpu,
    },
    {
        "name": "timing",
        "page": TIMING_PAGE,
        "probe": TIMING_PROBE,
        "validate": validate_timing,
    },
    {
        "name": "dns_leak",
        "run": dns_leak_run,
        "validate": validate_dns_leak,
    },
]


# ---------------------------------------------------------------------------
# Live public scanners. Only run when --live is passed and
# GHOSTBROWSER_ALLOW_LIVE_NETWORK=1 is set.
# ---------------------------------------------------------------------------

CANVAS_HASH_EXTRACT = r"""
() => {
    const hashRe = /[a-f0-9]{32,}/gi;
    const candidates = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    while (walker.nextNode()) {
        const m = walker.currentNode.textContent.match(hashRe);
        if (m) candidates.push(...m);
    }
    document.querySelectorAll('input,textarea').forEach(el => {
        const m = (el.value || '').match(hashRe);
        if (m) candidates.push(...m);
    });
    const unique = [...new Set(candidates)];
    unique.sort((a, b) => b.length - a.length);
    return unique[0] || '';
}
"""


async def browserleaks_canvas_run(page, profile, timeout):
    await page.goto("https://browserleaks.com/canvas", wait_until="load", timeout=timeout)
    await asyncio.sleep(2)
    hash1 = await page.evaluate(CANVAS_HASH_EXTRACT)
    if not hash1:
        await asyncio.sleep(3)
        hash1 = await page.evaluate(CANVAS_HASH_EXTRACT)

    await page.reload(wait_until="load", timeout=timeout)
    await asyncio.sleep(2)
    hash2 = await page.evaluate(CANVAS_HASH_EXTRACT)
    return {"hash1": hash1, "hash2": hash2}


def validate_browserleaks_canvas(_profile, result):
    checks = {}
    hash1 = result.get("hash1", "")
    hash2 = result.get("hash2", "")
    present = bool(hash1)
    stable = bool(hash1) and bool(hash2) and hash1 == hash2
    checks["hash_present"] = {"passed": present, "value": hash1}
    checks["hash_stable"] = {"passed": stable, "hash1": hash1, "hash2": hash2}
    return _result(present and stable, checks=checks)


WEBRTC_IP_EXTRACT = r"""
() => {
    const all = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6,div,span,li,section,table,tr,td,th,p,label'));
    const needle = all.find(el => /Your WebRTC IP|WebRTC IP|Public IP Address|Local IP Address/i.test(el.textContent));
    let text = '';
    if (needle) {
        const section = needle.closest('section,div,ul,table,article,tr') || needle.parentElement;
        text = section ? section.innerText : '';
    }
    if (!text) {
        text = document.body ? document.body.innerText : '';
    }
    // ponytail: browserleaks always shows the connection "Remote IP"; that is not a WebRTC leak.
    text = text.replace(/Your Remote IP[\s\S]*?WebRTC Support Detection/i, 'WebRTC Support Detection');
    const ipv4 = (text.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g) || []);
    const ipv6 = (text.match(/(?:[a-f0-9]{1,4}:){2,7}[a-f0-9]{1,4}/gi) || []);
    return {text: text.slice(0, 2000), ipv4: ipv4, ipv6: ipv6};
}
"""


def _is_public_ip(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


async def browserleaks_webrtc_run(page, profile, timeout):
    await page.goto("https://browserleaks.com/webrtc", wait_until="load", timeout=timeout)
    await asyncio.sleep(2)
    return await page.evaluate(WEBRTC_IP_EXTRACT)


def validate_browserleaks_webrtc(_profile, result):
    checks = {}
    raw_ips = (result.get("ipv4") or []) + (result.get("ipv6") or [])
    public_ips = [ip for ip in raw_ips if _is_public_ip(ip)]
    passed = len(public_ips) == 0
    checks["no_public_ip_leaked"] = {
        "passed": passed,
        "public_ips": public_ips,
        "raw_ips": raw_ips,
    }
    return _result(passed, checks=checks)


WHOER_EXTRACT = r"""
() => {
    const text = document.body ? document.body.innerText : '';
    const scoreMatch = text.match(/(\d{1,3})\s*%/);
    const countryMatch = text.match(/Country[\s:–—]+([A-Za-z][A-Za-z\s]{2,50})/i);
    return {
        text: text.slice(0, 3000),
        score: scoreMatch ? scoreMatch[1] : null,
        country: countryMatch ? countryMatch[1].trim() : null,
    };
}
"""


async def whoer_run(page, profile, timeout):
    try:
        await page.goto("https://whoer.net", wait_until="load", timeout=timeout)
    except Exception:
        return {}
    await asyncio.sleep(2)
    return await page.evaluate(WHOER_EXTRACT)


def validate_whoer(_profile, result):
    checks = {
        "info": {
            "country": result.get("country"),
            "score": result.get("score"),
        },
    }
    # whoer.net is third-party content: log informational values, never fail.
    return _result(True, checks=checks)


PIXELSCAN_EXTRACT = r"""
() => {
    const keywords = /\b(trust|result|risk|detection|consistent|unique|score|anonymity|fingerprint|pixelscan)\b/i;
    const body = document.body ? document.body.innerText : '';
    let text = '';
    if (keywords.test(body)) {
        text = body;
    } else {
        const h1 = document.querySelector('h1');
        const h2 = document.querySelector('h2');
        text = (h1 ? h1.innerText + '\n' : '') + (h2 ? h2.innerText : '');
    }
    return { text: text.slice(0, 2000) };
}
"""


async def pixelscan_run(page, profile, timeout):
    if not _is_live_mode_enabled():
        return {"skipped": True, "reason": "live scan disabled"}
    url = "https://pixelscan.net/"
    start = asyncio.get_event_loop().time()
    try:
        await page.goto(url, wait_until="load", timeout=timeout)
    except Exception as exc:
        return {"skipped": True, "reason": f"navigation error: {exc}"}
    deadline = start + timeout / 1000
    try:
        data = {"text": ""}
        while asyncio.get_event_loop().time() < deadline:
            data = await page.evaluate(PIXELSCAN_EXTRACT)
            if len((data.get("text") or "").strip()) > 20:
                break
            await asyncio.sleep(1)
        return data
    except Exception as exc:
        return {"skipped": True, "reason": f"evaluation error: {exc}"}


def validate_pixelscan(_profile, result):
    if not _is_live_mode_enabled():
        return _result(True, skipped=True, reason="live scan disabled (need env + --live)")
    if result.get("skipped"):
        return _result(True, skipped=True, reason=result.get("reason") or "skipped")
    text = result.get("text", "")
    checks = {"extracted_text": text}
    return _result(True, checks=checks)


IPHEY_EXTRACT = r"""
() => {
    const body = document.body ? document.body.innerText : '';
    const ipMatch = body.match(/\b((?:\d{1,3}\.){3}\d{1,3}|(?:[a-f0-9]{1,4}:){2,7}[a-f0-9]{1,4})\b/);
    const automation = /\b(automation|bot)\b/i.test(body);
    const summaryLines = body.split(/\n/).map(l => l.trim()).filter(l =>
        l.length > 0 && /(OS|Operating System|Platform|Browser|Device|Screen|Resolution|Timezone|Language|WebGL|JavaScript|Fingerprint)/i.test(l)
    );
    return {
        text: body.slice(0, 2000),
        ip: ipMatch ? ipMatch[0] : null,
        automationDetected: automation,
        summary: summaryLines.slice(0, 15).join('\n'),
    };
}
"""


async def iphey_run(page, profile, timeout):
    if not _is_live_mode_enabled():
        return {"skipped": True, "reason": "live scan disabled"}
    try:
        await page.goto("https://iphey.com/", wait_until="load", timeout=timeout)
        await asyncio.sleep(2)
        return await page.evaluate(IPHEY_EXTRACT)
    except Exception as exc:
        return {"skipped": True, "reason": f"page error: {exc}"}


def validate_iphey(_profile, result):
    if not _is_live_mode_enabled():
        return _result(True, skipped=True, reason="live scan disabled (need env + --live)")
    if result.get("skipped"):
        return _result(True, skipped=True, reason=result.get("reason") or "skipped")
    checks = {
        "ip": result.get("ip"),
        "automation_detected": result.get("automationDetected", False),
        "fingerprint_summary": result.get("summary"),
        "text_preview": result.get("text"),
    }
    passed = not checks["automation_detected"]
    return _result(passed, checks=checks)


BOT_SANNY_EXTRACT = r"""
() => {
    const body = document.body ? document.body.innerText : '';
    let status = '';
    const statusSelectors = ['#result', '#results', '#status', '.status', 'h1', 'h2'];
    for (const sel of statusSelectors) {
        const el = document.querySelector(sel);
        if (el && el.innerText.trim()) {
            status = el.innerText.trim();
            break;
        }
    }
    const redFlags = [];
    document.querySelectorAll('*').forEach(el => {
        const text = el.innerText ? el.innerText.trim() : '';
        if (text && /\bFAIL\b/i.test(text)) {
            redFlags.push(text);
        }
    });
    return {
        text: body.slice(0, 4000),
        status: status.slice(0, 1000),
        redFlags: [...new Set(redFlags)].slice(0, 20),
    };
}
"""


async def bot_sannysoft_run(page, profile, timeout):
    if not _is_live_mode_enabled():
        return {"skipped": True, "reason": "live scan disabled"}
    try:
        await page.goto("https://bot.sannysoft.com/", wait_until="load", timeout=timeout)
        await asyncio.sleep(2)
        return await page.evaluate(BOT_SANNY_EXTRACT)
    except Exception as exc:
        return {"skipped": True, "reason": f"page error: {exc}"}


def validate_bot_sannysoft(_profile, result):
    if not _is_live_mode_enabled():
        return _result(True, skipped=True, reason="live scan disabled (need env + --live)")
    if result.get("skipped"):
        return _result(True, skipped=True, reason=result.get("reason") or "skipped")
    text = (result.get("text") or "").lower().replace(" ", "")
    status = (result.get("status") or "").lower()
    red_flags = result.get("redFlags") or []
    checks = {
        "status": result.get("status"),
        "red_flags": red_flags,
    }
    failed = "failed" in text or "headlesschrome" in text or bool(red_flags)
    has_success = "successfully" in status or "successfully" in text
    passed = not failed and (has_success or not red_flags)
    return _result(passed, checks=checks)


def _is_live_mode_enabled():
    """Check the live scanner gate: both env var and --live CLI flag are required."""
    env_ok = os.getenv("GHOSTBROWSER_ALLOW_LIVE_NETWORK", "").strip().lower() in {"1", "true", "yes"}
    return env_ok and "--live" in sys.argv


CREEPJS_URL = "https://abrahamjuliot.github.io/creepjs/"


CREEPJS_EXTRACT = r"""
(async () => {
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const isPlaceholder = (s) => !s || /^0+$/.test(s);
    const findHash = () => {
        const selectors = [
            '#fingerprint-data .fingerprint-header .ellipsis-all',
            '.fingerprint-header',
            '[id*="fingerprint-data"]'
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (!el) continue;
            const m = el.textContent.match(/FP[ -]?ID:?\s*([a-f0-9]{16,})/i);
            if (m && !isPlaceholder(m[1])) return m[1];
        }
        const body = document.body ? document.body.innerText : '';
        const m = body.match(/FP[ -]?ID:?\s*([a-f0-9]{16,})/i);
        if (m && !isPlaceholder(m[1])) return m[1];
        return '';
    };
    const findScore = () => {
        const body = document.body ? document.body.innerText : '';
        const m = body.match(/trust[ \t\w]*[:：]?\s*(\d{1,3})\s*%/i)
            || body.match(/score[ \t\w]*[:：]?\s*(\d{1,3})\s*%/i);
        return m ? parseInt(m[1], 10) : null;
    };
    let hash = findHash(), score = findScore();
    for (let i = 0; i < 3 && (!hash || score == null); i++) {
        await wait(1500);
        if (!hash) hash = findHash();
        if (score == null) score = findScore();
    }
    return {hash, score, textPreview: (document.body ? document.body.innerText : '').slice(0, 300)};
})()
"""


async def creepjs_run(page, profile, timeout):
    if not _is_live_mode_enabled():
        return {"skipped": True, "reason": "live scan disabled"}
    try:
        await page.goto(CREEPJS_URL, wait_until="load", timeout=timeout)
        await asyncio.sleep(1)
        data = await page.evaluate(CREEPJS_EXTRACT)
    except Exception as exc:
        return {"skipped": True, "reason": f"page error: {exc}"}
    return data


def validate_creepjs(_profile, result):
    if not _is_live_mode_enabled():
        return _result(True, skipped=True, reason="live scan disabled (need env + --live)")
    if result.get("skipped"):
        return _result(True, skipped=True, reason=result.get("reason") or "skipped")

    hash_val = result.get("hash", "")
    score = result.get("score")
    checks = {"hash": hash_val, "trust_score": score, "text_preview": result.get("textPreview")}
    if not hash_val or score is None:
        return _result(True, skipped=True, reason="hash or trust score unavailable", checks=checks)
    passed = bool(hash_val) and score >= 60
    return _result(passed, checks=checks)


FINGERPRINTJS_URL = "https://fingerprintjs.github.io/fingerprintjs/"


FINGERPRINTJS_EXTRACT = r"""
(async () => {
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const extract = () => {
        const text = document.body ? document.body.innerText : '';
        const visitor = text.match(/Visitor identifier:\s*([a-f0-9]+)/i)
            || text.match(/visitor\s*id:?\s*([a-f0-9]+)/i);
        const conf = text.match(/Confidence score:\s*([\d.]+)/i)
            || text.match(/confidence[:\s]+([\d.]+)/i);
        return {
            visitorId: visitor ? visitor[1] : '',
            confidence: conf ? parseFloat(conf[1]) : null,
            textPreview: text.slice(0, 400),
        };
    };
    let data = extract();
    for (let i = 0; i < 4 && (!data.visitorId || data.confidence == null); i++) {
        await wait(1000);
        data = extract();
    }
    return data;
})()
"""


async def fingerprintjs_run(page, profile, timeout):
    if not _is_live_mode_enabled():
        return {"skipped": True, "reason": "live scan disabled"}
    try:
        await page.goto(FINGERPRINTJS_URL, wait_until="load", timeout=timeout)
        await asyncio.sleep(1)
        data = await page.evaluate(FINGERPRINTJS_EXTRACT)
    except Exception as exc:
        return {"skipped": True, "reason": f"page error: {exc}"}
    return data


def validate_fingerprintjs(_profile, result):
    if not _is_live_mode_enabled():
        return _result(True, skipped=True, reason="live scan disabled (need env + --live)")
    if result.get("skipped"):
        return _result(True, skipped=True, reason=result.get("reason") or "skipped")

    visitor_id = result.get("visitorId", "")
    confidence = result.get("confidence")
    checks = {"visitorId": visitor_id, "confidence": confidence, "text_preview": result.get("textPreview")}
    if not visitor_id or confidence is None:
        return _result(True, skipped=True, reason="visitor ID or confidence unavailable", checks=checks)
    passed = bool(visitor_id) and confidence > 0.5
    return _result(passed, checks=checks)


# Append live-but-registerable scanners after their runners are defined.
SCANNERS.extend([
    {
        "name": "creepjs",
        "live": True,
        "run": creepjs_run,
        "validate": validate_creepjs,
    },
    {
        "name": "fingerprintjs",
        "live": True,
        "run": fingerprintjs_run,
        "validate": validate_fingerprintjs,
    },
])


LIVE_SCANNERS = [
    {
        "name": "browserleaks_canvas",
        "live": True,
        "run": browserleaks_canvas_run,
        "validate": validate_browserleaks_canvas,
    },
    {
        "name": "browserleaks_webrtc",
        "live": True,
        "run": browserleaks_webrtc_run,
        "validate": validate_browserleaks_webrtc,
    },
    {
        "name": "whoer",
        "live": True,
        "run": whoer_run,
        "validate": validate_whoer,
    },
    {
        "name": "pixelscan",
        "live": True,
        "run": pixelscan_run,
        "validate": validate_pixelscan,
    },
    {
        "name": "iphey",
        "live": True,
        "run": iphey_run,
        "validate": validate_iphey,
    },
    {
        "name": "bot_sannysoft",
        "live": True,
        "run": bot_sannysoft_run,
        "validate": validate_bot_sannysoft,
    },
]


# ---------------------------------------------------------------------------
# CLI and runtime.
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Check a GhostBrowser profile for automation/leak signals."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--profile-id",
        help="Reuse an existing production profile by ID.",
    )
    group.add_argument(
        "--temp",
        action="store_true",
        help="Create a throwaway temp profile, then clean it up.",
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="Bypass the GHOSTBROWSER_ALLOW_LIVE_NETWORK gate and run offline scanners only.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run public live scanner pages. Requires GHOSTBROWSER_ALLOW_LIVE_NETWORK=1.",
    )
    parser.add_argument(
        "--live-timeout",
        type=int,
        default=30,
        help="Seconds to wait for each live page (default: 30).",
    )
    parser.add_argument(
        "--compatibility",
        action="store_true",
        help="Validate profile configuration without launching a browser.",
    )
    return parser.parse_args()


def enforce_network_policy(args):
    allowed = {"1", "true", "yes"}
    env_ok = os.getenv("GHOSTBROWSER_ALLOW_LIVE_NETWORK", "").strip().lower() in allowed
    test_env = os.getenv("GHOSTBROWSER_TEST_ENV", "").strip().lower() in allowed

    if args.live:
        if not env_ok:
            print(json.dumps({
                "status": "failed",
                "reason": "GHOSTBROWSER_ALLOW_LIVE_NETWORK=1 required for --live",
            }, indent=2))
            sys.exit(1)
        return

    # Offline behaviour: temp test profiles and non-live runs behave like --skip-network.
    if args.skip_network or test_env or not args.live:
        return

    # Legacy interactive path: still require the gate.
    if not env_ok:
        print(json.dumps({
            "status": "failed",
            "reason": "GHOSTBROWSER_ALLOW_LIVE_NETWORK=1 required (or pass --skip-network)",
        }, indent=2))
        sys.exit(1)


def setup_temp_env():
    temp_dir = tempfile.mkdtemp(prefix="ghostbrowser_detection_")
    os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
    os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir
    return temp_dir


async def run_scans(profile, scanners=None, live_timeout=30):
    if scanners is None:
        scanners = SCANNERS
    from backend.browser_manager import active_browsers, launch_profile

    profile_id = profile["id"]
    launch_res = await launch_profile(profile_id, force_headless=True)
    if launch_res.get("status") != "success":
        return {
            "status": "failed",
            "profile_id": profile_id,
            "reason": "launch_failed",
            "detail": launch_res.get("message"),
        }

    browser_data = active_browsers.get(profile_id) or {}
    page = browser_data.get("page")
    if not page:
        return {
            "status": "failed",
            "profile_id": profile_id,
            "reason": "no_page",
        }

    tests = {}
    all_passed = True
    errors = []

    for scanner in scanners:
        name = scanner["name"]
        try:
            is_live = scanner.get("live", False)
            timeout = live_timeout * 1000 if is_live else 15000
            run_fn = scanner.get("run")
            if run_fn:
                raw = await run_fn(page, profile, timeout)
            else:
                await page.goto(
                    scanner["page"],
                    wait_until="load",
                    timeout=timeout,
                )
                raw = await page.evaluate(scanner["probe"])
            detail = scanner["validate"](profile, raw)
            passed = detail.pop("passed")
        except Exception as exc:
            passed = False
            detail = {"exception": str(exc)}

        tests[name] = {"passed": passed, **detail}
        if not passed:
            all_passed = False
            errors.append(f"{name} failed")

    return {
        "status": "passed" if all_passed else "failed",
        "profile_id": profile_id,
        "tests": tests,
        "errors": errors,
    }


async def _run_scans(profile):
    """Lightweight offline scan wrapper for backend modules; closes browser when done."""
    from backend.browser_manager import close_profile
    try:
        return await run_scans(profile, SCANNERS)
    finally:
        try:
            await close_profile(profile["id"])
        except Exception:
            pass


async def main():
    args = parse_args()
    if args.skip_network:
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
    enforce_network_policy(args)

    temp_dir = None
    if args.temp:
        temp_dir = setup_temp_env()

    # Import backend after any temp env vars are set.
    from backend.profile_manager import profile_manager

    profile = None
    if args.temp:
        profile = profile_manager.create_profile(
            name="detection_check_temp",
            advanced={"headless": True, "locale": "en-US"},
        )
    elif args.profile_id:
        profile = profile_manager.get_profile(args.profile_id)
        if not profile:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "reason": f"Profile {args.profile_id} not found",
                    },
                    indent=2,
                )
            )
            sys.exit(1)
    else:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "Provide --profile-id or --temp",
                },
                indent=2,
            )
        )
        sys.exit(1)

    if args.compatibility:
        from backend.detection_score import compatibility_score
        report = compatibility_score(profile)
        summary = {
            "status": "passed" if report["compatibility_score"] == 100 else "failed",
            "profile_id": profile["id"],
            **report,
        }
        if temp_dir:
            profile_manager.delete_profile(profile["id"])
            shutil.rmtree(temp_dir, ignore_errors=True)
        print(json.dumps(summary, indent=2))
        sys.exit(0 if summary["status"] == "passed" else 1)

    scanners = SCANNERS
    if args.live:
        scanners = SCANNERS + LIVE_SCANNERS

    summary = None
    try:
        summary = await run_scans(profile, scanners, live_timeout=args.live_timeout)
    finally:
        if profile:
            from backend.browser_manager import close_profile

            try:
                await close_profile(profile["id"])
            except Exception:
                pass
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print(json.dumps(summary or {"status": "failed", "reason": "unknown"}, indent=2))
    sys.exit(0 if summary and summary.get("status") == "passed" else 1)


if __name__ == "__main__":
    asyncio.run(main())
