"""
Independent FINAL E2E observer audit for GhostBrowser antidetect.

Creates 3 profiles with distinct fingerprints, launches via production path,
captures full fingerprint surface, tests A→B→A switching, and hunts
cross-context leaks (main / popup / worker / CDP-visible).

Run from repo root:
  & venv/Scripts/python.exe tests/audit_final_e2e_observer.py

Does NOT modify production code. Exit 0 = all hard asserts pass.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _setup_env(temp_dir: str):
    os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir
    os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
    os.environ.setdefault("GHOSTBROWSER_REQUIRE_PROXY", "0")
    if os.getcwd() not in sys.path:
        sys.path.append(os.getcwd())


# ---------------------------------------------------------------------------
# Fingerprint fixtures
# ---------------------------------------------------------------------------

def make_fingerprint(overrides: dict) -> dict:
    from backend.config import get_installed_chromium_major_version, get_installed_chromium_version

    inst_maj = get_installed_chromium_major_version()
    inst_full = get_installed_chromium_version()
    fp = {
        "os": "Windows",
        "platform": "Win32",
        "userAgent": (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{inst_maj}.0.0.0 Safari/537.36"
        ),
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11)",
        "cpu_cores": 16,
        "hardwareConcurrency": 16,
        "memory_gb": 32,
        "deviceMemory": 32,
        "timezone": "America/New_York",
        "locale": "en-US",
        "languages": ["en-US", "en"],
        "screen_resolution": "2560x1440",
        "screen_color_depth": 24,
        "canvas_noise": True,
        "webgl_noise": True,
        "audio_noise": True,
        "sec_ch_ua": f'"Not A(Brand";v="99", "Chromium";v="{inst_maj}"',
        "sec_ch_ua_platform": '"Windows"',
        "client_hints": {
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "platformVersion": "10.0.0",
            "uaFullVersion": inst_full,
        },
        "notification_permission": "default",
        "battery_level": 0.98,
        "battery_charging": False,
        "battery_discharging": 46800,
        "behavior": {},
    }
    fp.update(overrides)
    return fp


FP_SPECS = [
    {
        "name": "obs-ny",
        "label": "A-America/New_York",
        "overrides": {
            "timezone": "America/New_York",
            "locale": "en-US",
            "languages": ["en-US", "en"],
            "cpu_cores": 8,
            "hardwareConcurrency": 8,
            "memory_gb": 16,
            "deviceMemory": 16,
            "screen_resolution": "1920x1080",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11)",
        },
    },
    {
        "name": "obs-berlin",
        "label": "B-Europe/Berlin",
        "overrides": {
            "timezone": "Europe/Berlin",
            "locale": "de-DE",
            "languages": ["de-DE", "de"],
            "cpu_cores": 12,
            "hardwareConcurrency": 12,
            "memory_gb": 32,
            "deviceMemory": 32,
            "screen_resolution": "2560x1440",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11)",
        },
    },
    {
        "name": "obs-tokyo",
        "label": "C-Asia/Tokyo",
        "overrides": {
            "timezone": "Asia/Tokyo",
            "locale": "ja-JP",
            "languages": ["ja-JP", "ja"],
            "cpu_cores": 16,
            "hardwareConcurrency": 16,
            "memory_gb": 64,
            "deviceMemory": 64,
            "screen_resolution": "3440x1440",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)",
        },
    },
]


# ---------------------------------------------------------------------------
# Probe JS — full surface + leak hunt
# ---------------------------------------------------------------------------

FULL_PROBE_JS = r"""
async () => {
    const safe = (fn, fallback=null) => { try { return fn(); } catch (e) { return fallback !== null ? fallback : 'ERR:' + e.message; } };
    const nativeStr = (fn) => safe(() => Function.prototype.toString.call(fn), '');
    const hasNative = (fn) => String(nativeStr(fn)).includes('[native code]');

    // Canvas — two samples for stability
    const canvasSample = () => safe(() => {
        const c = document.createElement('canvas');
        c.width = 256; c.height = 256;
        const ctx = c.getContext('2d');
        ctx.fillStyle = '#f00'; ctx.fillRect(0, 0, 256, 256);
        ctx.fillStyle = '#0f0'; ctx.fillRect(32, 32, 128, 128);
        ctx.fillStyle = '#00f'; ctx.font = '48px serif'; ctx.fillText('ghost-obs', 40, 120);
        ctx.strokeStyle = '#abc'; ctx.beginPath(); ctx.arc(128, 128, 60, 0, Math.PI*2); ctx.stroke();
        return c.toDataURL();
    }, 'ERR');
    const canvas1 = canvasSample();
    const canvas2 = canvasSample();

    // WebGL
    const webgl = safe(() => {
        const gl = document.createElement('canvas').getContext('webgl');
        if (!gl) return { error: 'no-webgl' };
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        return {
            vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
            renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
            version: gl.getParameter(gl.VERSION),
            shadingLang: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
            extensions: (gl.getSupportedExtensions() || []).length,
            getParameterNative: hasNative(gl.getParameter),
        };
    }, { error: 'webgl-fail' });

    // Audio hash
    let audioHash = null;
    try {
        const actx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = actx.createOscillator();
        osc.type = 'triangle';
        osc.frequency.value = 1000;
        const analyser = actx.createAnalyser();
        osc.connect(analyser);
        analyser.connect(actx.destination);
        osc.start();
        const arr = new Uint8Array(analyser.frequencyBinCount);
        await new Promise(r => setTimeout(r, 350));
        analyser.getByteFrequencyData(arr);
        let h = 2166136261;
        for (let i = 0; i < Math.min(300, arr.length); i++) h = Math.imul(h ^ arr[i], 16777619);
        await actx.close();
        audioHash = (h >>> 0);
    } catch (e) { audioHash = 'ERR:' + e.message; }

    // High-entropy UA data via Promise
    let uaHigh = null;
    try {
        if (navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
            uaHigh = await navigator.userAgentData.getHighEntropyValues([
                'architecture', 'bitness', 'model', 'platformVersion',
                'uaFullVersion', 'fullVersionList', 'wow64'
            ]);
        }
    } catch (e) { uaHigh = { error: e.message }; }

    // Permissions
    let permNotif = null, permGeo = null, permCam = null;
    try {
        if (navigator.permissions && navigator.permissions.query) {
            permNotif = (await navigator.permissions.query({ name: 'notifications' })).state;
            try { permGeo = (await navigator.permissions.query({ name: 'geolocation' })).state; } catch (_) {}
            try { permCam = (await navigator.permissions.query({ name: 'camera' })).state; } catch (_) {}
        }
    } catch (e) { permNotif = 'ERR:' + e.message; }

    // mediaDevices
    let mediaDevs = null;
    try {
        if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
            const devs = await navigator.mediaDevices.enumerateDevices();
            mediaDevs = devs.map(d => ({ kind: d.kind, label: d.label || '', deviceId: d.deviceId ? (d.deviceId.slice(0, 8) + '…') : '' }));
        } else {
            mediaDevs = 'absent';
        }
    } catch (e) { mediaDevs = 'ERR:' + e.message; }

    // connection
    const connection = safe(() => {
        const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
        if (!c) return null;
        return {
            effectiveType: c.effectiveType,
            downlink: c.downlink,
            rtt: c.rtt,
            saveData: c.saveData,
            type: c.type,
        };
    }, null);

    // ScreenOrientation
    const orientation = safe(() => {
        const o = screen.orientation;
        if (!o) return null;
        return { type: o.type, angle: o.angle };
    }, null);

    // performance navigation entries
    const navEntries = safe(() => {
        const e = performance.getEntriesByType('navigation');
        if (!e || !e.length) return [];
        return e.map(n => ({
            type: n.type,
            transferSize: n.transferSize,
            encodedBodySize: n.encodedBodySize,
            nextHopProtocol: n.nextHopProtocol,
        }));
    }, []);

    // navigator key inventory (sample of interesting keys)
    const navKeys = Object.getOwnPropertyNames(Navigator.prototype).sort();
    const interestingNav = {};
    for (const k of [
        'webdriver','vendor','vendorSub','product','productSub','appVersion','appName',
        'platform','userAgent','language','languages','hardwareConcurrency','deviceMemory',
        'maxTouchPoints','cookieEnabled','doNotTrack','pdfViewerEnabled','onLine',
        'oscpu','buildID','cpuClass','browserLanguage','systemLanguage','userLanguage'
    ]) {
        interestingNav[k] = safe(() => {
            const v = navigator[k];
            if (typeof v === 'function') return '[function]';
            if (Array.isArray(v)) return Array.from(v);
            return v === undefined ? '__undefined__' : v;
        }, '__error__');
    }

    // plugins detail
    const plugins = safe(() => {
        const list = [];
        for (let i = 0; i < navigator.plugins.length; i++) {
            const p = navigator.plugins[i];
            list.push({ name: p.name, filename: p.filename, description: p.description });
        }
        return { length: navigator.plugins.length, items: list };
    }, { length: -1, items: [] });

    const mimeTypes = safe(() => navigator.mimeTypes.length, -1);

    // chrome.runtime / webdriver CDP-ish
    const chromeSurface = safe(() => {
        const c = window.chrome;
        if (!c) return { present: false };
        return {
            present: true,
            typeofChrome: typeof c,
            keys: Object.keys(c).sort(),
            hasRuntime: !!c.runtime,
            hasCsi: typeof c.csi === 'function',
            hasLoadTimes: typeof c.loadTimes === 'function',
            hasApp: !!c.app,
            webstore: c.webstore ? typeof c.webstore : null,
        };
    }, { present: false, error: true });

    // sendBeacon
    const sendBeaconNative = safe(() => hasNative(navigator.sendBeacon), false);
    const sendBeaconType = safe(() => typeof navigator.sendBeacon, 'undefined');

    // FontFaceSet
    const fontCheck = safe(() => typeof document.fonts.check, 'undefined');
    const fontCheckArial = safe(() => document.fonts.check('12px Arial'), null);

    // matchMedia
    const mm = {
        pointerFine: matchMedia('(pointer: fine)').matches,
        hoverHover: matchMedia('(hover: hover)').matches,
        anyPointerCoarse: matchMedia('(any-pointer: coarse)').matches,
        resolutionDpr: matchMedia('(resolution: ' + window.devicePixelRatio + 'dppx)').matches,
        prefersColorSchemeDark: matchMedia('(prefers-color-scheme: dark)').matches,
    };

    // speechSynthesis
    const speech = safe(() => {
        if (!window.speechSynthesis) return { present: false };
        const voices = speechSynthesis.getVoices() || [];
        return { present: true, voiceCount: voices.length, sample: voices.slice(0, 3).map(v => v.name) };
    }, { present: false });

    // battery
    let battery = 'absent';
    try {
        if (navigator.getBattery) {
            const b = await navigator.getBattery();
            battery = { level: b.level, charging: b.charging, chargingTime: b.chargingTime, dischargingTime: b.dischargingTime };
        }
    } catch (e) { battery = 'ERR:' + e.message; }

    // WebGPU
    const webgpu = safe(() => navigator.gpu ? 'present' : 'absent', 'absent');

    // DPR / screen coherence
    const dpr = window.devicePixelRatio;
    const screenInfo = {
        width: screen.width, height: screen.height,
        availWidth: screen.availWidth, availHeight: screen.availHeight,
        colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth,
    };

    // timezone
    const tz = safe(() => Intl.DateTimeFormat().resolvedOptions().timeZone, null);
    const tzOffset = new Date().getTimezoneOffset();
    const localeList = safe(() => {
        try { return Intl.DateTimeFormat().resolvedOptions().locale; } catch (_) { return null; }
    }, null);

    // uaData low-entropy
    const uaData = safe(() => {
        if (!navigator.userAgentData) return null;
        return {
            brands: navigator.userAgentData.brands,
            mobile: navigator.userAgentData.mobile,
            platform: navigator.userAgentData.platform,
            architecture: navigator.userAgentData.architecture,
            bitness: navigator.userAgentData.bitness,
            model: navigator.userAgentData.model,
            platformVersion: navigator.userAgentData.platformVersion,
            uaFullVersion: navigator.userAgentData.uaFullVersion,
            fullVersionList: navigator.userAgentData.fullVersionList,
        };
    }, null);

    // perf.memory
    const perfMemory = safe(() => {
        const m = performance.memory;
        if (!m) return null;
        return {
            jsHeapSizeLimit: m.jsHeapSizeLimit,
            totalJSHeapSize: m.totalJSHeapSize,
            usedJSHeapSize: m.usedJSHeapSize,
        };
    }, null);

    // document.documentElement language
    const htmlLang = safe(() => document.documentElement.lang, null);

    // Worker context probe — critical antidetect failure mode
    let workerProbe = null;
    try {
        const workerCode = `
            self.onmessage = function() {
                try {
                    self.postMessage({
                        ua: navigator.userAgent,
                        platform: navigator.platform,
                        hardwareConcurrency: navigator.hardwareConcurrency,
                        language: navigator.language,
                        languages: navigator.languages ? Array.from(navigator.languages) : null,
                        webdriver: navigator.webdriver,
                        vendor: navigator.vendor,
                        product: navigator.product,
                        deviceMemory: navigator.deviceMemory,
                        userAgentData: navigator.userAgentData ? {
                            brands: navigator.userAgentData.brands,
                            platform: navigator.userAgentData.platform,
                            mobile: navigator.userAgentData.mobile,
                        } : null,
                    });
                } catch (e) {
                    self.postMessage({ error: e.message });
                }
            };
        `;
        const blob = new Blob([workerCode], { type: 'application/javascript' });
        const url = URL.createObjectURL(blob);
        workerProbe = await new Promise((resolve) => {
            const w = new Worker(url);
            const t = setTimeout(() => { try { w.terminate(); } catch(_){}; resolve({ error: 'timeout' }); }, 4000);
            w.onmessage = (ev) => { clearTimeout(t); w.terminate(); URL.revokeObjectURL(url); resolve(ev.data); };
            w.onerror = (ev) => { clearTimeout(t); try { w.terminate(); } catch(_){}; resolve({ error: String(ev.message || ev) }); };
            w.postMessage('go');
        });
    } catch (e) {
        workerProbe = { error: e.message };
    }

    // Popup window probe — new window.open context
    let popupProbe = null;
    try {
        const popup = window.open('about:blank', '_blank', 'width=400,height=300');
        if (!popup) {
            popupProbe = { error: 'popup-blocked' };
        } else {
            await new Promise(r => setTimeout(r, 400));
            popupProbe = {
                ua: popup.navigator.userAgent,
                platform: popup.navigator.platform,
                hardwareConcurrency: popup.navigator.hardwareConcurrency,
                language: popup.navigator.language,
                languages: popup.navigator.languages ? Array.from(popup.navigator.languages) : null,
                webdriver: popup.navigator.webdriver,
                vendor: popup.navigator.vendor,
                deviceMemory: popup.navigator.deviceMemory,
                dpr: popup.devicePixelRatio,
                screen: [popup.screen.width, popup.screen.height],
                chrome: typeof popup.chrome,
            };
            popup.close();
        }
    } catch (e) {
        popupProbe = { error: e.message };
    }

    // Request headers via fetch to data URL won't give CH; use performance resource timing is limited.
    // Capture what sec-ch-ua would look like from uaData brands.
    const brandsHeader = safe(() => {
        if (!navigator.userAgentData || !navigator.userAgentData.brands) return null;
        return navigator.userAgentData.brands.map(b => `"${b.brand}";v="${b.version}"`).join(', ');
    }, null);

    return {
        interestingNav,
        navKeyCount: navKeys.length,
        ua: navigator.userAgent,
        platform: navigator.platform,
        vendor: navigator.vendor,
        product: navigator.product,
        language: navigator.language,
        languages: navigator.languages ? Array.from(navigator.languages) : null,
        hardwareConcurrency: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory,
        maxTouchPoints: navigator.maxTouchPoints,
        webdriver: navigator.webdriver,
        cookieEnabled: navigator.cookieEnabled,
        pdfViewerEnabled: navigator.pdfViewerEnabled,
        doNotTrack: navigator.doNotTrack,
        plugins,
        mimeTypes,
        uaData,
        uaHigh,
        brandsHeader,
        canvas1,
        canvas2,
        canvasStable: canvas1 === canvas2 && !String(canvas1).startsWith('ERR'),
        canvasLen: typeof canvas1 === 'string' ? canvas1.length : 0,
        webgl,
        audioHash,
        dpr,
        screen: screenInfo,
        orientation,
        inner: [window.innerWidth, window.innerHeight],
        outer: [window.outerWidth, window.outerHeight],
        tz,
        tzOffset,
        localeList,
        htmlLang,
        perfMemory,
        connection,
        mediaDevs,
        permissions: { notifications: permNotif, geolocation: permGeo, camera: permCam },
        chromeSurface,
        sendBeaconNative,
        sendBeaconType,
        fontCheck,
        fontCheckArial,
        mm,
        speech,
        battery,
        webgpu,
        navEntries,
        workerProbe,
        popupProbe,
        // leak markers
        vendorSub: interestingNav.vendorSub,
        oscpu: interestingNav.oscpu,
        productSub: interestingNav.productSub,
    };
}
"""


IDENTITY_KEYS = [
    "ua", "platform", "hardwareConcurrency", "deviceMemory", "language",
    "languages", "tz", "tzOffset", "screen_wh", "dpr", "webgl_renderer",
    "audioHash", "canvas_hash", "plugins_len", "maxTouchPoints",
]


def _canvas_hash(data_url: str) -> str:
    if not isinstance(data_url, str) or data_url.startswith("ERR"):
        return str(data_url)
    return hashlib.sha256(data_url.encode("utf-8", errors="replace")).hexdigest()[:16]


def _identity_row(probe: dict) -> dict:
    scr = probe.get("screen") or {}
    webgl = probe.get("webgl") or {}
    return {
        "ua": probe.get("ua"),
        "platform": probe.get("platform"),
        "hardwareConcurrency": probe.get("hardwareConcurrency"),
        "deviceMemory": probe.get("deviceMemory"),
        "language": probe.get("language"),
        "languages": probe.get("languages"),
        "tz": probe.get("tz"),
        "tzOffset": probe.get("tzOffset"),
        "screen_wh": f"{scr.get('width')}x{scr.get('height')}",
        "dpr": probe.get("dpr"),
        "webgl_vendor": webgl.get("vendor"),
        "webgl_renderer": webgl.get("renderer"),
        "audioHash": probe.get("audioHash"),
        "canvas_hash": _canvas_hash(probe.get("canvas1") or ""),
        "plugins_len": (probe.get("plugins") or {}).get("length"),
        "maxTouchPoints": probe.get("maxTouchPoints"),
        "webdriver": probe.get("webdriver"),
        "vendor": probe.get("vendor"),
        "product": probe.get("product"),
        "perf_heap_limit": (probe.get("perfMemory") or {}).get("jsHeapSizeLimit"),
        "uaData_platform": (probe.get("uaData") or {}).get("platform"),
        "uaHigh_arch": (probe.get("uaHigh") or {}).get("architecture") if isinstance(probe.get("uaHigh"), dict) else None,
        "uaHigh_bitness": (probe.get("uaHigh") or {}).get("bitness") if isinstance(probe.get("uaHigh"), dict) else None,
        "uaHigh_fullVer": (probe.get("uaHigh") or {}).get("uaFullVersion") if isinstance(probe.get("uaHigh"), dict) else None,
        "connection": probe.get("connection"),
        "mm_pointer": (probe.get("mm") or {}).get("pointerFine"),
        "mm_hover": (probe.get("mm") or {}).get("hoverHover"),
        "chrome_present": (probe.get("chromeSurface") or {}).get("present"),
        "fontCheck": probe.get("fontCheck"),
        "speech": probe.get("speech"),
        "battery": probe.get("battery"),
        "mediaDevs": probe.get("mediaDevs"),
        "permissions": probe.get("permissions"),
        "worker": probe.get("workerProbe"),
        "popup": probe.get("popupProbe"),
        "vendorSub": probe.get("vendorSub"),
        "oscpu": probe.get("oscpu"),
        "productSub": probe.get("productSub"),
        "orientation": probe.get("orientation"),
        "canvasStable": probe.get("canvasStable"),
        "mimeTypes": probe.get("mimeTypes"),
        "plugins": probe.get("plugins"),
        "brandsHeader": probe.get("brandsHeader"),
        "sendBeaconNative": probe.get("sendBeaconNative"),
        "navEntries": probe.get("navEntries"),
    }


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------

class AuditResult:
    def __init__(self):
        self.checks: list[tuple[str, bool, str]] = []
        self.leaks: list[dict] = []
        self.risks: list[dict] = []
        self.tables: dict = {}
        self.raw: dict = {}

    def check(self, name: str, ok: bool, detail: str = ""):
        self.checks.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))

    def leak(self, severity: str, title: str, detail: str, values=None):
        entry = {"severity": severity, "title": title, "detail": detail, "values": values}
        self.leaks.append(entry)
        print(f"  [LEAK:{severity}] {title} — {detail}")

    def risk(self, level: str, title: str, detail: str):
        self.risks.append({"level": level, "title": title, "detail": detail})

    @property
    def failed(self) -> list[str]:
        return [n for n, ok, _ in self.checks if not ok]


async def run_audit() -> AuditResult:
    ar = AuditResult()
    temp = tempfile.TemporaryDirectory(prefix="obs_e2e_")
    _setup_env(temp.name)

    import backend.browser_manager as bm
    import backend.profile_creator as profile_creator
    import backend.proxy_manager as pm

    orig_probe = bm.probe_native_metadata
    orig_proxy = pm.proxy_manager.get_proxy_for_profile

    async def mock_probe_native_metadata(force_headless=True):
        from backend.config import get_installed_chromium_major_version, get_installed_chromium_version
        maj = get_installed_chromium_major_version()
        full = get_installed_chromium_version()
        return {
            "ua": (
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{maj}.0.0.0 Safari/537.36"
            ),
            "uadata": {
                "brands": [
                    {"brand": "Chromium", "version": str(maj)},
                    {"brand": "Not)A;Brand", "version": "24"},
                ],
                "mobile": False,
                "platform": "Windows",
                "architecture": "x86",
                "bitness": "64",
                "model": "",
                "platformVersion": "19.0.0",
                "uaFullVersion": full,
                "fullVersionList": [
                    {"brand": "Chromium", "version": full},
                    {"brand": "Not)A;Brand", "version": "24.0.0.0"},
                ],
            },
        }

    async def mock_get_proxy_for_profile(profile_id, force_new=False):
        return None

    bm.probe_native_metadata = mock_probe_native_metadata
    pm.proxy_manager.get_proxy_for_profile = mock_get_proxy_for_profile

    fps = [make_fingerprint(s["overrides"]) for s in FP_SPECS]
    fp_queue = list(fps)

    async def mock_ai_gen(*args, **kwargs):
        if not fp_queue:
            raise RuntimeError("fingerprint queue exhausted")
        return fp_queue.pop(0)

    created_ids: list[str] = []
    profiles: dict[str, dict] = {}
    probes: dict[str, dict] = {}
    identities: dict[str, dict] = {}

    try:
        print("=" * 72)
        print("OBSERVER FINAL E2E AUDIT — 3 profiles + switch + leak hunt")
        print("=" * 72)

        # ---- Create 3 profiles ----
        print("\n[1] Creating 3 distinct profiles via create_zero_leak_profile…")
        with patch("backend.profile_creator.generate_fingerprint_ai", mock_ai_gen):
            for spec in FP_SPECS:
                res = await profile_creator.create_zero_leak_profile(name=spec["name"])
                if res.get("status") != "success":
                    raise RuntimeError(f"create {spec['name']} failed: {res}")
                pid = res["profile"]["id"]
                created_ids.append(pid)
                profiles[spec["name"]] = res["profile"]
                print(f"  created {spec['name']} id={pid}")

        # ---- Launch each, probe, close (sequential to avoid resource pressure) ----
        print("\n[2] Launch + full-surface probe per profile…")
        for spec in FP_SPECS:
            name = spec["name"]
            pid = profiles[name]["id"]
            la = await bm.launch_profile(pid, force_headless=True)
            if la.get("status") != "success":
                raise RuntimeError(f"launch {name} failed: {la}")
            page = bm.active_browsers[pid]["page"]
            # navigate to blank to ensure document ready
            try:
                await page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(0.5)
            data = await page.evaluate(FULL_PROBE_JS)
            # second canvas sample after short wait for within-session stability
            data2 = await page.evaluate(FULL_PROBE_JS)
            data["canvas_reprobe_hash"] = _canvas_hash(data2.get("canvas1") or "")
            data["canvas_cross_probe_stable"] = (
                _canvas_hash(data.get("canvas1") or "") == data["canvas_reprobe_hash"]
            )
            probes[name] = data
            identities[name] = _identity_row(data)
            print(f"  probed {name}: tz={data.get('tz')} hw={data.get('hardwareConcurrency')} "
                  f"mem={data.get('deviceMemory')} screen={identities[name]['screen_wh']} "
                  f"canvas={identities[name]['canvas_hash']} audio={data.get('audioHash')}")
            await bm.close_profile(pid)
            await asyncio.sleep(0.3)

        ar.raw["identities"] = identities
        ar.raw["probes"] = {k: _summarize_probe(v) for k, v in probes.items()}
        ar.tables["fingerprint"] = identities

        # ---- Per-profile expected matches ----
        print("\n[3] Per-profile expected fingerprint matches…")
        for i, spec in enumerate(FP_SPECS):
            name = spec["name"]
            exp = fps[i]
            idn = identities[name]
            p = probes[name]
            ar.check(f"{name}: webdriver falsy", not p.get("webdriver"), repr(p.get("webdriver")))
            ar.check(f"{name}: vendor Google Inc.", p.get("vendor") == "Google Inc.", repr(p.get("vendor")))
            ar.check(f"{name}: product Gecko", p.get("product") == "Gecko", repr(p.get("product")))
            ar.check(
                f"{name}: hardwareConcurrency={exp['hardwareConcurrency']}",
                p.get("hardwareConcurrency") == exp["hardwareConcurrency"],
                f"got={p.get('hardwareConcurrency')}",
            )
            ar.check(
                f"{name}: deviceMemory={exp['deviceMemory']}",
                p.get("deviceMemory") == exp["deviceMemory"],
                f"got={p.get('deviceMemory')}",
            )
            ar.check(f"{name}: timezone={exp['timezone']}", p.get("tz") == exp["timezone"], f"got={p.get('tz')}")
            # timezone offset
            try:
                z = ZoneInfo(exp["timezone"])
                exp_off_min = datetime.now(z).utcoffset().total_seconds() / 60
                # JS getTimezoneOffset is opposite sign of UTC offset minutes
                got_off = -float(p.get("tzOffset") or 0)
                ar.check(
                    f"{name}: tzOffset matches IANA",
                    abs(got_off - exp_off_min) < 1,
                    f"got={got_off} exp={exp_off_min}",
                )
            except Exception as e:
                ar.check(f"{name}: tzOffset matches IANA", False, str(e))

            ar.check(
                f"{name}: language={exp['locale']}",
                p.get("language") == exp["locale"],
                f"got={p.get('language')} langs={p.get('languages')}",
            )
            langs = p.get("languages") or []
            ar.check(
                f"{name}: languages[0]==language",
                bool(langs) and langs[0] == p.get("language"),
                f"lang={p.get('language')} langs={langs}",
            )
            w, h = (int(x) for x in exp["screen_resolution"].lower().split("x"))
            scr = p.get("screen") or {}
            ar.check(
                f"{name}: screen={w}x{h}",
                scr.get("width") == w and scr.get("height") == h,
                f"got={scr.get('width')}x{scr.get('height')}",
            )
            ar.check(f"{name}: canvas stable (same probe)", p.get("canvasStable") is True, "")
            ar.check(
                f"{name}: canvas stable across re-probe",
                p.get("canvas_cross_probe_stable") is True,
                f"h1={idn['canvas_hash']} h2={p.get('canvas_reprobe_hash')}",
            )
            ar.check(f"{name}: plugins length==5", (p.get("plugins") or {}).get("length") == 5,
                     repr((p.get("plugins") or {}).get("length")))
            ar.check(f"{name}: window.chrome present", (p.get("chromeSurface") or {}).get("present") is True,
                     repr(p.get("chromeSurface")))
            ar.check(f"{name}: FontFaceSet.check present", p.get("fontCheck") == "function", repr(p.get("fontCheck")))
            ar.check(
                f"{name}: matchMedia pointer/hover desktop",
                (p.get("mm") or {}).get("pointerFine") is True and (p.get("mm") or {}).get("hoverHover") is True,
                repr(p.get("mm")),
            )
            # perf.memory
            pm_ = p.get("perfMemory") or {}
            exp_heap = exp["deviceMemory"] * 1073741824
            ar.check(
                f"{name}: perf.memory jsHeapSizeLimit matches RAM",
                pm_.get("jsHeapSizeLimit") == exp_heap,
                f"got={pm_.get('jsHeapSizeLimit')} exp={exp_heap}",
            )
            # uaData high entropy
            uh = p.get("uaHigh") if isinstance(p.get("uaHigh"), dict) else {}
            ar.check(
                f"{name}: getHighEntropyValues architecture/bitness",
                uh.get("architecture") == "x86" and uh.get("bitness") == "64",
                repr(uh),
            )
            ar.check(
                f"{name}: Chrome major in UA",
                "Chrome/" in (p.get("ua") or ""),
                repr(p.get("ua")),
            )
            # WebGL
            wg = p.get("webgl") or {}
            ar.check(
                f"{name}: WebGL vendor/renderer present",
                isinstance(wg.get("renderer"), str) and len(wg.get("renderer") or "") > 0,
                repr(wg),
            )
            # speech / connection presence
            ar.check(
                f"{name}: navigator.connection present",
                p.get("connection") is not None,
                repr(p.get("connection")),
            )

        # ---- Pairwise difference ----
        print("\n[4] Pairwise DIFFERENCE matrix…")
        names = [s["name"] for s in FP_SPECS]
        diff_matrix = {}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ia, ib = identities[a], identities[b]
                diffs = []
                for k in [
                    "hardwareConcurrency", "deviceMemory", "language", "tz", "tzOffset",
                    "screen_wh", "canvas_hash", "audioHash", "webgl_renderer", "perf_heap_limit",
                ]:
                    if ia.get(k) != ib.get(k):
                        diffs.append(k)
                same = [
                    k for k in [
                        "hardwareConcurrency", "deviceMemory", "language", "tz", "tzOffset",
                        "screen_wh", "canvas_hash", "audioHash", "webgl_renderer", "perf_heap_limit",
                    ]
                    if ia.get(k) == ib.get(k)
                ]
                key = f"{a} vs {b}"
                diff_matrix[key] = {"differ": diffs, "same": same}
                # Must differ on core identity dimensions
                must = {"hardwareConcurrency", "deviceMemory", "language", "tz", "screen_wh", "canvas_hash"}
                missing = must - set(diffs)
                ar.check(
                    f"pairwise {key}: core identity differs",
                    len(missing) == 0,
                    f"missing diffs={missing} same={same} differ={diffs}",
                )
                # audio should differ if noise enabled
                if ia.get("audioHash") == ib.get("audioHash"):
                    ar.leak(
                        "SHOULD-FIX",
                        f"audio hash identical {a}/{b}",
                        f"audioHash={ia.get('audioHash')}",
                        {"a": ia.get("audioHash"), "b": ib.get("audioHash")},
                    )
                    ar.check(f"pairwise {key}: audio differs", False, f"both={ia.get('audioHash')}")
                else:
                    ar.check(f"pairwise {key}: audio differs", True, f"{ia.get('audioHash')} vs {ib.get('audioHash')}")

        ar.tables["pairwise"] = diff_matrix

        # ---- Switch test A → B → A ----
        print("\n[5] Switch test: A → close → B → close → relaunch A…")
        name_a, name_b = names[0], names[1]
        pid_a, pid_b = profiles[name_a]["id"], profiles[name_b]["id"]
        baseline_a = identities[name_a]
        baseline_b = identities[name_b]

        la = await bm.launch_profile(pid_a, force_headless=True)
        ar.check("switch: launch A", la.get("status") == "success", repr(la))
        page = bm.active_browsers[pid_a]["page"]
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        probe_a1 = _identity_row(await page.evaluate(FULL_PROBE_JS))
        await bm.close_profile(pid_a)
        await asyncio.sleep(0.4)

        lb = await bm.launch_profile(pid_b, force_headless=True)
        ar.check("switch: launch B", lb.get("status") == "success", repr(lb))
        page = bm.active_browsers[pid_b]["page"]
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        probe_b1 = _identity_row(await page.evaluate(FULL_PROBE_JS))
        # B must match baseline B and differ from A
        ar.check(
            "switch: B distinct from A (hw/tz/screen)",
            (
                probe_b1["hardwareConcurrency"] != probe_a1["hardwareConcurrency"]
                and probe_b1["tz"] != probe_a1["tz"]
                and probe_b1["screen_wh"] != probe_a1["screen_wh"]
            ),
            f"A={probe_a1['tz']}/{probe_a1['hardwareConcurrency']}/{probe_a1['screen_wh']} "
            f"B={probe_b1['tz']}/{probe_b1['hardwareConcurrency']}/{probe_b1['screen_wh']}",
        )
        ar.check(
            "switch: B matches baseline B identity",
            (
                probe_b1["hardwareConcurrency"] == baseline_b["hardwareConcurrency"]
                and probe_b1["tz"] == baseline_b["tz"]
                and probe_b1["screen_wh"] == baseline_b["screen_wh"]
                and probe_b1["canvas_hash"] == baseline_b["canvas_hash"]
            ),
            f"got tz={probe_b1['tz']} canvas={probe_b1['canvas_hash']} "
            f"exp canvas={baseline_b['canvas_hash']}",
        )
        await bm.close_profile(pid_b)
        await asyncio.sleep(0.4)

        la2 = await bm.launch_profile(pid_a, force_headless=True)
        ar.check("switch: relaunch A", la2.get("status") == "success", repr(la2))
        page = bm.active_browsers[pid_a]["page"]
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        probe_a2 = _identity_row(await page.evaluate(FULL_PROBE_JS))
        await bm.close_profile(pid_a)

        ar.check(
            "switch: relaunch A restores OWN fingerprint (no bleed)",
            (
                probe_a2["hardwareConcurrency"] == baseline_a["hardwareConcurrency"]
                and probe_a2["deviceMemory"] == baseline_a["deviceMemory"]
                and probe_a2["tz"] == baseline_a["tz"]
                and probe_a2["language"] == baseline_a["language"]
                and probe_a2["screen_wh"] == baseline_a["screen_wh"]
                and probe_a2["canvas_hash"] == baseline_a["canvas_hash"]
            ),
            f"got={{{probe_a2['tz']},{probe_a2['hardwareConcurrency']},{probe_a2['screen_wh']},{probe_a2['canvas_hash']}}} "
            f"exp={{{baseline_a['tz']},{baseline_a['hardwareConcurrency']},{baseline_a['screen_wh']},{baseline_a['canvas_hash']}}}",
        )
        ar.tables["switch"] = {
            "A_first": {k: probe_a1[k] for k in IDENTITY_KEYS if k in probe_a1},
            "B": {k: probe_b1[k] for k in IDENTITY_KEYS if k in probe_b1},
            "A_relaunch": {k: probe_a2[k] for k in IDENTITY_KEYS if k in probe_a2},
            "A_baseline": {k: baseline_a[k] for k in IDENTITY_KEYS if k in baseline_a},
        }

        # ---- Leak hunt across contexts ----
        print("\n[6] Leak hunt (main / popup / worker / odd surfaces)…")
        # Use profile C for leak hunt (fresh launch)
        name_c = names[2]
        pid_c = profiles[name_c]["id"]
        lc = await bm.launch_profile(pid_c, force_headless=True)
        ar.check("leak-hunt: launch C", lc.get("status") == "success", repr(lc))
        page = bm.active_browsers[pid_c]["page"]
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        leak_probe = await page.evaluate(FULL_PROBE_JS)
        await bm.close_profile(pid_c)

        main_ua = leak_probe.get("ua")
        main_plat = leak_probe.get("platform")
        main_hw = leak_probe.get("hardwareConcurrency")
        main_lang = leak_probe.get("language")
        main_wd = leak_probe.get("webdriver")
        main_mem = leak_probe.get("deviceMemory")

        # CDP / webdriver
        if leak_probe.get("webdriver"):
            ar.leak("BLOCKER", "navigator.webdriver truthy", repr(main_wd), main_wd)
        ar.check("leak: webdriver falsy on main", not main_wd, repr(main_wd))

        # vendorSub / oscpu
        vs = leak_probe.get("vendorSub")
        oscpu = leak_probe.get("oscpu")
        # Empty string is normal for Chromium vendorSub; undefined/null also OK.
        # Non-empty vendorSub or any oscpu is a Gecko/Firefox leak.
        if oscpu not in (None, "", "__undefined__"):
            ar.leak("BLOCKER", "navigator.oscpu present (Firefox leak)", repr(oscpu), oscpu)
            ar.check("leak: oscpu absent", False, repr(oscpu))
        else:
            ar.check("leak: oscpu absent", True, repr(oscpu))
        if vs not in (None, "", "__undefined__"):
            # Chromium normally has vendorSub === ""
            ar.leak("SHOULD-FIX", "navigator.vendorSub non-empty", repr(vs), vs)
            ar.check("leak: vendorSub empty/absent", False, repr(vs))
        else:
            ar.check("leak: vendorSub empty/absent", True, repr(vs))

        # language vs languages coherence
        langs = leak_probe.get("languages") or []
        ar.check(
            "leak: language == languages[0]",
            bool(langs) and langs[0] == main_lang,
            f"language={main_lang} languages={langs}",
        )

        # ScreenOrientation
        ori = leak_probe.get("orientation")
        ar.check(
            "leak: ScreenOrientation present",
            isinstance(ori, dict) and ori.get("type") in (
                "landscape-primary", "landscape-secondary", "portrait-primary", "portrait-secondary"
            ),
            repr(ori),
        )

        # DPR vs screen
        dpr = leak_probe.get("dpr")
        mm = leak_probe.get("mm") or {}
        ar.check(
            "leak: DPR matchMedia coherent",
            mm.get("resolutionDpr") is True and isinstance(dpr, (int, float)) and dpr > 0,
            f"dpr={dpr} mm={mm}",
        )

        # mediaDevices
        md = leak_probe.get("mediaDevs")
        if md == "absent":
            ar.check("leak: mediaDevices handled", True, "absent")
        elif isinstance(md, list):
            # Real devices with empty labels is OK; host device labels would leak
            labeled = [d for d in md if d.get("label")]
            if labeled:
                ar.leak(
                    "SHOULD-FIX",
                    "mediaDevices exposes device labels",
                    repr(labeled),
                    labeled,
                )
                ar.check("leak: mediaDevices no real labels", False, repr(labeled))
            else:
                ar.check("leak: mediaDevices no real labels", True, f"count={len(md)}")
        else:
            ar.check("leak: mediaDevices handled", False, repr(md))

        # permissions
        perms = leak_probe.get("permissions") or {}
        ar.check(
            "leak: permissions.query works",
            perms.get("notifications") in ("prompt", "granted", "denied", "default")
            or perms.get("notifications") is not None,
            repr(perms),
        )

        # sendBeacon
        ar.check(
            "leak: sendBeacon present+native-looking",
            leak_probe.get("sendBeaconType") == "function" and leak_probe.get("sendBeaconNative") is True,
            f"type={leak_probe.get('sendBeaconType')} native={leak_probe.get('sendBeaconNative')}",
        )

        # connection downlink ranges
        conn = leak_probe.get("connection") or {}
        if conn:
            dl = conn.get("downlink")
            rtt = conn.get("rtt")
            et = conn.get("effectiveType")
            dl_ok = isinstance(dl, (int, float)) and 0.1 <= float(dl) <= 10000
            rtt_ok = rtt is None or (isinstance(rtt, (int, float)) and 0 <= float(rtt) <= 3000)
            et_ok = et in (None, "slow-2g", "2g", "3g", "4g")
            ar.check("leak: connection.downlink in sane range", dl_ok, repr(conn))
            ar.check("leak: connection.rtt sane", rtt_ok, repr(conn))
            ar.check("leak: connection.effectiveType sane", et_ok, repr(conn))
        else:
            ar.leak("SHOULD-FIX", "navigator.connection missing", "scanners expect NetworkInformation")
            ar.check("leak: connection present", False, "null")

        # nav entries
        ne = leak_probe.get("navEntries") or []
        ar.check("leak: performance navigation entries accessible", isinstance(ne, list), repr(ne))

        # Worker cross-context
        wp = leak_probe.get("workerProbe") or {}
        if wp.get("error"):
            ar.leak("BLOCKER", "Worker probe failed", repr(wp), wp)
            ar.check("leak: Worker context readable", False, repr(wp))
        else:
            ar.check("leak: Worker context readable", True, "")
            mismatches = []
            if wp.get("ua") != main_ua:
                mismatches.append(("ua", wp.get("ua"), main_ua))
            if wp.get("platform") != main_plat:
                mismatches.append(("platform", wp.get("platform"), main_plat))
            if wp.get("hardwareConcurrency") != main_hw:
                mismatches.append(("hardwareConcurrency", wp.get("hardwareConcurrency"), main_hw))
            if wp.get("language") != main_lang:
                mismatches.append(("language", wp.get("language"), main_lang))
            if wp.get("webdriver"):
                mismatches.append(("webdriver", wp.get("webdriver"), main_wd))
            # deviceMemory may be undefined in workers on some Chrome builds — note it
            if "deviceMemory" in wp and wp.get("deviceMemory") not in (None, main_mem) and main_mem is not None:
                if wp.get("deviceMemory") != main_mem:
                    mismatches.append(("deviceMemory", wp.get("deviceMemory"), main_mem))

            if mismatches:
                for m in mismatches:
                    ar.leak(
                        "BLOCKER",
                        f"Worker/{m[0]} mismatch vs main",
                        f"worker={m[1]!r} main={m[2]!r}",
                        {"field": m[0], "worker": m[1], "main": m[2]},
                    )
                ar.check("leak: Worker matches main spoof", False, repr(mismatches))
            else:
                ar.check(
                    "leak: Worker matches main spoof (ua/platform/hw/lang/webdriver)",
                    True,
                    f"worker_ua={str(wp.get('ua'))[:60]}… hw={wp.get('hardwareConcurrency')}",
                )

            # Worker webdriver specifically
            ar.check("leak: Worker webdriver falsy", not wp.get("webdriver"), repr(wp.get("webdriver")))

        # Popup cross-context
        pp = leak_probe.get("popupProbe") or {}
        if pp.get("error"):
            # headless may block popups — classify carefully
            if pp.get("error") == "popup-blocked":
                ar.leak(
                    "SHOULD-FIX",
                    "window.open popup blocked in headless — cannot verify popup spoof consistency",
                    repr(pp),
                    pp,
                )
                ar.check("leak: Popup context readable", False, "popup-blocked (headless limitation?)")
            else:
                ar.leak("SHOULD-FIX", "Popup probe error", repr(pp), pp)
                ar.check("leak: Popup context readable", False, repr(pp))
        else:
            ar.check("leak: Popup context readable", True, "")
            pmism = []
            if pp.get("ua") != main_ua:
                pmism.append(("ua", pp.get("ua"), main_ua))
            if pp.get("platform") != main_plat:
                pmism.append(("platform", pp.get("platform"), main_plat))
            if pp.get("hardwareConcurrency") != main_hw:
                pmism.append(("hardwareConcurrency", pp.get("hardwareConcurrency"), main_hw))
            if pp.get("language") != main_lang:
                pmism.append(("language", pp.get("language"), main_lang))
            if pp.get("webdriver"):
                pmism.append(("webdriver", pp.get("webdriver"), main_wd))
            if pp.get("deviceMemory") not in (None, main_mem) and pp.get("deviceMemory") != main_mem:
                pmism.append(("deviceMemory", pp.get("deviceMemory"), main_mem))
            if pmism:
                for m in pmism:
                    ar.leak(
                        "BLOCKER",
                        f"Popup/{m[0]} mismatch vs main",
                        f"popup={m[1]!r} main={m[2]!r}",
                        {"field": m[0], "popup": m[1], "main": m[2]},
                    )
                ar.check("leak: Popup matches main spoof", False, repr(pmism))
            else:
                ar.check(
                    "leak: Popup matches main spoof",
                    True,
                    f"popup_hw={pp.get('hardwareConcurrency')} wd={pp.get('webdriver')}",
                )
            ar.check("leak: Popup webdriver falsy", not pp.get("webdriver"), repr(pp.get("webdriver")))
            ar.check(
                "leak: Popup chrome present",
                pp.get("chrome") in ("object", "function"),
                repr(pp.get("chrome")),
            )

        # UA vs brands header coherence
        brands = leak_probe.get("brandsHeader") or ""
        ua = main_ua or ""
        m = re.search(r"Chrome/(\d+)", ua)
        if m and brands:
            maj = m.group(1)
            ar.check(
                "leak: sec-ch-ua brands include Chromium major from UA",
                maj in brands or f'v="{maj}"' in brands or any(
                    str(b.get("version")) == maj
                    for b in ((leak_probe.get("uaData") or {}).get("brands") or [])
                ),
                f"ua_maj={maj} brands={brands}",
            )
        else:
            ar.check("leak: sec-ch-ua brands coherent with UA", False, f"ua={ua} brands={brands}")

        # productSub Chromium is typically "20030107"
        psub = leak_probe.get("productSub")
        if psub not in ("20030107", "__undefined__", None):
            ar.leak("NICE-TO-HAVE", "productSub unusual", repr(psub), psub)

        ar.tables["leak_probe_summary"] = {
            "main": {
                "ua": main_ua,
                "platform": main_plat,
                "hw": main_hw,
                "lang": main_lang,
                "webdriver": main_wd,
                "mem": main_mem,
                "vendorSub": vs,
                "oscpu": oscpu,
                "productSub": psub,
                "connection": conn,
                "permissions": perms,
                "orientation": ori,
            },
            "worker": wp,
            "popup": pp,
        }

        # ---- Risk synthesis ----
        print("\n[7] Risk synthesis…")
        hard_fails = ar.failed
        blockers = [l for l in ar.leaks if l["severity"] == "BLOCKER"]
        should = [l for l in ar.leaks if l["severity"] == "SHOULD-FIX"]
        nice = [l for l in ar.leaks if l["severity"] == "NICE-TO-HAVE"]

        if any("Worker" in f or "worker" in f.lower() for f in hard_fails) or any(
            "Worker" in l["title"] for l in blockers
        ):
            ar.risk("BLOCKER", "Cross-context Worker spoof mismatch",
                    "Real scanners fingerprint Workers; main-thread-only spoof is detected.")
        if any("Popup" in l["title"] and l["severity"] == "BLOCKER" for l in ar.leaks):
            ar.risk("BLOCKER", "Popup window spoof mismatch",
                    "window.open contexts must inherit spoofed navigator.")
        if any("webdriver" in f.lower() for f in hard_fails):
            ar.risk("BLOCKER", "webdriver leak", "navigator.webdriver must be falsy everywhere.")
        if any("canvas" in f.lower() and "stable" in f.lower() for f in hard_fails):
            ar.risk("BLOCKER", "Canvas instability", "Unstable canvas breaks session continuity.")
        if any("relaunch A restores" in f for f in hard_fails):
            ar.risk("BLOCKER", "Profile identity bleed on switch",
                    "Relaunch must restore deterministic per-profile identity.")
        if any("core identity differs" in f for f in hard_fails):
            ar.risk("BLOCKER", "Profiles not distinct enough",
                    "Pairwise look-and-feel must differ across profiles.")

        # always surface residual risks
        ar.risk(
            "SHOULD-FIX",
            "No proxy in this audit path",
            "GHOSTBROWSER_REQUIRE_PROXY=0; production IP/geo/WebRTC path not fully exercised with real proxy.",
        )
        ar.risk(
            "SHOULD-FIX",
            "Headless-only audit",
            "force_headless=True; headed UI chrome/window chrome differences not fully covered.",
        )
        ar.risk(
            "NICE-TO-HAVE",
            "TLS/JA3 and HTTP/2 fingerprint not probed here",
            "Client-hints header wire format and TLS stack need external scanner (e.g. creepjs/browserleaks).",
        )

        ar.raw["hard_fails"] = hard_fails
        ar.raw["blockers"] = blockers
        ar.raw["should_fix"] = should
        ar.raw["nice"] = nice

        return ar

    finally:
        # cleanup browsers
        for pid in list(getattr(bm, "active_browsers", {}).keys()):
            try:
                await bm.close_profile(pid)
            except Exception:
                pass
        for pid in created_ids:
            try:
                await bm.close_profile(pid)
            except Exception:
                pass
        bm.probe_native_metadata = orig_probe
        pm.proxy_manager.get_proxy_for_profile = orig_proxy
        try:
            temp.cleanup()
        except Exception:
            pass


def _summarize_probe(p: dict) -> dict:
    """Drop huge canvas data URLs from saved summary."""
    out = dict(p)
    for k in ("canvas1", "canvas2"):
        if k in out and isinstance(out[k], str) and len(out[k]) > 80:
            out[k] = f"<dataurl len={len(out[k])} hash={_canvas_hash(out[k])}>"
    return out


def print_report(ar: AuditResult):
    print("\n" + "=" * 72)
    print("REPORT (a) PER-PROFILE FINGERPRINT TABLE")
    print("=" * 72)
    for name, idn in (ar.tables.get("fingerprint") or {}).items():
        print(f"\n--- {name} ---")
        keys = [
            "ua", "platform", "vendor", "product", "webdriver",
            "hardwareConcurrency", "deviceMemory", "language", "languages",
            "tz", "tzOffset", "screen_wh", "dpr",
            "webgl_vendor", "webgl_renderer", "audioHash", "canvas_hash",
            "plugins_len", "mimeTypes", "maxTouchPoints",
            "perf_heap_limit", "uaData_platform", "uaHigh_arch", "uaHigh_bitness",
            "uaHigh_fullVer", "connection", "mm_pointer", "mm_hover",
            "chrome_present", "fontCheck", "canvasStable",
            "vendorSub", "oscpu", "productSub",
        ]
        for k in keys:
            v = idn.get(k)
            if k == "ua" and isinstance(v, str) and len(v) > 100:
                v = v[:100] + "…"
            print(f"  {k:22s} = {v!r}")
        # plugins names
        plugs = idn.get("plugins") or {}
        if isinstance(plugs, dict):
            names = [i.get("name") for i in (plugs.get("items") or [])]
            print(f"  {'plugin_names':22s} = {names!r}")
        sp = idn.get("speech")
        print(f"  {'speech':22s} = {sp!r}")
        print(f"  {'battery':22s} = {idn.get('battery')!r}")
        print(f"  {'mediaDevs':22s} = {idn.get('mediaDevs')!r}")
        print(f"  {'permissions':22s} = {idn.get('permissions')!r}")
        print(f"  {'orientation':22s} = {idn.get('orientation')!r}")

    print("\n" + "=" * 72)
    print("REPORT (b) PAIRWISE DIFFERENCE + STABILITY + SWITCH")
    print("=" * 72)
    for k, v in (ar.tables.get("pairwise") or {}).items():
        print(f"  {k}:")
        print(f"    DIFFER: {v['differ']}")
        print(f"    SAME:   {v['same']}")
    sw = ar.tables.get("switch") or {}
    if sw:
        print("\n  Switch A→B→A:")
        for label in ("A_baseline", "A_first", "B", "A_relaunch"):
            row = sw.get(label) or {}
            print(
                f"    {label:12s} tz={row.get('tz')} hw={row.get('hardwareConcurrency')} "
                f"mem={row.get('deviceMemory')} screen={row.get('screen_wh')} "
                f"lang={row.get('language')} canvas={row.get('canvas_hash')} audio={row.get('audioHash')}"
            )

    print("\n" + "=" * 72)
    print("REPORT (c) LEAKS (main / popup / worker)")
    print("=" * 72)
    if not ar.leaks:
        print("  No leaks recorded.")
    else:
        for L in ar.leaks:
            print(f"  [{L['severity']}] {L['title']}")
            print(f"           {L['detail']}")
            if L.get("values") is not None:
                vs = json.dumps(L["values"], default=str)[:500]
                print(f"           values={vs}")

    ls = ar.tables.get("leak_probe_summary") or {}
    if ls:
        print("\n  Main context snapshot:")
        print(f"    {json.dumps(ls.get('main'), default=str, indent=4)[:2000]}")
        print("\n  Worker context:")
        print(f"    {json.dumps(ls.get('worker'), default=str, indent=4)[:2000]}")
        print("\n  Popup context:")
        print(f"    {json.dumps(ls.get('popup'), default=str, indent=4)[:2000]}")

    print("\n" + "=" * 72)
    print("REPORT (d) VERDICT")
    print("=" * 72)
    n_pass = sum(1 for _, ok, _ in ar.checks if ok)
    n_fail = sum(1 for _, ok, _ in ar.checks if not ok)
    blockers = [l for l in ar.leaks if l["severity"] == "BLOCKER"]
    print(f"  Checks: {n_pass} PASS / {n_fail} FAIL (total {len(ar.checks)})")
    print(f"  Leaks:  {len(blockers)} BLOCKER / "
          f"{sum(1 for l in ar.leaks if l['severity']=='SHOULD-FIX')} SHOULD-FIX / "
          f"{sum(1 for l in ar.leaks if l['severity']=='NICE-TO-HAVE')} NICE-TO-HAVE")

    ready = n_fail == 0 and len(blockers) == 0
    # soften: if only popup-blocked in headless and no other fails, note it
    if not ready and n_fail > 0:
        only_popup = all(
            "popup" in f.lower() or "Popup" in f
            for f in ar.failed
        ) and not blockers
        if only_popup:
            verdict = "READY WITH CAVEATS (headless popup blocked; core surfaces green)"
        else:
            verdict = "NOT READY"
    elif ready:
        verdict = "READY"
    else:
        verdict = "NOT READY"
    # if blockers exist always NOT READY
    if blockers:
        verdict = "NOT READY"
    print(f"  VERDICT: {verdict}")
    if ar.failed:
        print(f"  Failed checks: {ar.failed}")

    print("\n" + "=" * 72)
    print("REPORT (e) TOP RISKS")
    print("=" * 72)
    # merge leak-derived + synthetic risks, de-dupe by title
    seen = set()
    for r in ar.risks:
        key = (r["level"], r["title"])
        if key in seen:
            continue
        seen.add(key)
        print(f"  [{r['level']}] {r['title']}: {r['detail']}")
    for L in ar.leaks:
        key = (L["severity"], L["title"])
        if key in seen:
            continue
        seen.add(key)
        print(f"  [{L['severity']}] {L['title']}: {L['detail']}")

    print("\n" + "=" * 72)
    print("REPORT (f) COMMANDS TO USE PRODUCT 100%")
    print("=" * 72)
    print("""
  # 1. Activate venv + env
  cd "C:\\Users\\jibra\\Desktop\\1\\browser ai"
  .\\venv\\Scripts\\Activate.ps1
  # Ensure .env has ZEN_API_KEY (AI fingerprints) and GHOSTBROWSER_REQUIRE_PROXY=0
  #    or set a real proxy and leave REQUIRE_PROXY=1 for production.

  # 2. Start API server
  & venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

  # 3. Admin token (from .env ADMIN_TOKEN / GHOSTBROWSER_ADMIN_TOKEN)
  $TOKEN = "<your-admin-token>"
  $H = @{ Authorization = "Bearer $TOKEN"; "Content-Type" = "application/json" }

  # 4. Create profile (AI fingerprint, zero-leak pipeline)
  Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/profiles \\
    -Headers $H -Body (@{ name = "daily-1" } | ConvertTo-Json)

  # 5. Launch profile
  Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/profiles/<id>/launch -Headers $H

  # 6. Close profile
  Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/profiles/<id>/close -Headers $H

  # 7. Optional: re-run this observer audit anytime
  & venv/Scripts/python.exe tests/audit_final_e2e_observer.py
  & venv/Scripts/python.exe tests/verify_runtime_checklist.py
""")

    # write JSON artifact
    out_path = Path("tests") / "audit_final_e2e_observer_results.json"
    try:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
            "pass": n_pass,
            "fail": n_fail,
            "failed_checks": ar.failed,
            "tables": {
                "fingerprint": {
                    k: {kk: vv for kk, vv in v.items() if kk not in ("plugins",) or True}
                    for k, v in (ar.tables.get("fingerprint") or {}).items()
                },
                "pairwise": ar.tables.get("pairwise"),
                "switch": ar.tables.get("switch"),
                "leak_probe_summary": ar.tables.get("leak_probe_summary"),
            },
            "leaks": ar.leaks,
            "risks": ar.risks,
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in ar.checks],
        }
        # scrub huge fields
        def scrub(o):
            if isinstance(o, dict):
                return {k: scrub(v) for k, v in o.items()}
            if isinstance(o, list):
                return [scrub(x) for x in o]
            if isinstance(o, str) and o.startswith("data:image") and len(o) > 120:
                return f"<dataurl len={len(o)} hash={_canvas_hash(o)}>"
            return o
        out_path.write_text(json.dumps(scrub(payload), indent=2, default=str), encoding="utf-8")
        print(f"\n  JSON results → {out_path}")
    except Exception as e:
        print(f"\n  (could not write JSON results: {e})")

    return verdict, n_fail, len(blockers)


def main():
    try:
        ar = asyncio.run(run_audit())
        verdict, n_fail, n_block = print_report(ar)
        # Exit non-zero on hard failures or blockers
        if n_fail or n_block:
            return 1
        return 0
    except Exception as e:
        print(f"[FATAL] audit crashed: {e}")
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
