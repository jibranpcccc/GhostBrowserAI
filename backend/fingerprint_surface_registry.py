"""Registry of fingerprinting/anti-detection surfaces and their protection status."""
from typing import List, Dict

SURFACES: List[Dict] = [
    {
        "name": "Per-profile storage isolation",
        "category": "isolation",
        "status": "protected",
        "notes": "Separate user-data directory per profile; verified by isolation_check.py",
    },
    {
        "name": "Cookie jar partitioning",
        "category": "isolation",
        "status": "protected",
        "notes": "Each profile has its own Chromium storage partition.",
    },
    {
        "name": "Proxy kill switch",
        "category": "network",
        "status": "protected",
        "notes": "Profile is closed if assigned proxy becomes unhealthy.",
    },
    {
        "name": "WebRTC leak prevention",
        "category": "network",
        "status": "protected",
        "notes": "disable_non_proxied_udp policy + RTCPeerConnection stub.",
    },
    {
        "name": "IPv6 leak prevention",
        "category": "network",
        "status": "protected",
        "notes": "--disable-ipv6 launch argument.",
    },
    {
        "name": "DNS leak prevention",
        "category": "network",
        "status": "partial",
        "notes": "--disable-quic, --dns-prefetch-disable, --disable-async-dns; no packet-capture proof in scanner.",
    },
    {
        "name": "Proxy time/zone/locale matching",
        "category": "network",
        "status": "protected",
        "notes": "guess_locale_timezone() uses ipapi.co to align timezone/locale with proxy.",
    },
    {
        "name": "TLS/JA3/JA4 matching",
        "category": "network",
        "status": "not_protected",
        "notes": "Requires proxy/TLS-layer control. Real Chromium JA3 is preserved unmodified.",
    },
    {
        "name": "HTTP/2 fingerprint matching",
        "category": "network",
        "status": "not_protected",
        "notes": "Controlled by upstream Chromium network stack; not customized.",
    },
    {
        "name": "User-Agent / platform",
        "category": "navigator",
        "status": "protected",
        "notes": "Generated per OS and reconciled to installed Chromium version.",
    },
    {
        "name": "navigator.webdriver masking",
        "category": "navigator",
        "status": "protected",
        "notes": "Deleted from navigator + Playwright stealth; returns false via Object.defineProperty getter.",
    },
    {
        "name": "navigator.vendor / product / productSub",
        "category": "navigator",
        "status": "protected",
        "notes": "vendor='Google Inc.', product='Gecko' set via safeDefineProperty spoofing script.",
    },
    {
        "name": "navigator.cookieEnabled / pdfViewerEnabled",
        "category": "navigator",
        "status": "protected",
        "notes": "Both return true via safeDefineProperty.",
    },
    {
        "name": "navigator.doNotTrack / javaEnabled",
        "category": "navigator",
        "status": "protected",
        "notes": "doNotTrack=null, javaEnabled()=false.",
    },
    {
        "name": "performance.memory",
        "category": "navigator",
        "status": "protected",
        "notes": "jsHeapSizeLimit/totalJSHeapSize/usedJSHeapSize spoofed per-profile hardware memory.",
    },
    {
        "name": "Chrome runtime / csi / loadTimes",
        "category": "navigator",
        "status": "protected",
        "notes": "Realistic stubs injected into page context.",
    },
    {
        "name": "Permissions API",
        "category": "navigator",
        "status": "protected",
        "notes": "Per-profile deterministic variation: 90% prompt, 5% granted, 5% denied for sensitive permissions. Non-sensitive permissions pass through to real query.",
    },
    {
        "name": "Client hints",
        "category": "navigator",
        "status": "protected",
        "notes": "CDP Emulation+Network UserAgentOverride plus JS navigator.userAgentData fallback; request headers aligned via Accept-CH opt-in routing.",
    },
    {
        "name": "Canvas fingerprint noise",
        "category": "rendering",
        "status": "protected",
        "notes": "Stable per-profile noise in toDataURL, getImageData, toBlob, OffscreenCanvas, and blob/network workers.",
    },
    {
        "name": "WebGL vendor / renderer / params",
        "category": "rendering",
        "status": "protected",
        "notes": "getParameter and getSupportedExtensions overridden.",
    },
    {
        "name": "WebGPU adapter info",
        "category": "rendering",
        "status": "protected",
        "notes": "navigator.gpu.requestAdapter returns spoofed adapter info.",
    },
    {
        "name": "AudioContext noise",
        "category": "rendering",
        "status": "protected",
        "notes": "Stable per-profile noise on audio readback APIs.",
    },
    {
        "name": "Font enumeration",
        "category": "rendering",
        "status": "protected",
        "notes": "document.fonts.check and navigator.plugins limited to OS-consistent sets.",
    },
    {
        "name": "Font metric spoofing",
        "category": "rendering",
        "status": "protected",
        "notes": "CanvasRenderingContext2D.measureText returns per-profile perturbed metrics matching OS cohort; configurable via experimental_measuretext.",
    },
    {
        "name": "DOM geometry / ClientRects",
        "category": "rendering",
        "status": "protected",
        "notes": "getBoundingClientRect and getClientRects apply deterministic ±1px offset.",
    },
    {
        "name": "Screen resolution / DPR",
        "category": "rendering",
        "status": "protected",
        "notes": "Cohort-based coherent screen/viewport/DPR selection.",
    },
    {
        "name": "CSS media query consistency",
        "category": "rendering",
        "status": "protected",
        "notes": "matchMedia overridden for pointer, hover, color-scheme, contrast, etc.",
    },
    {
        "name": "hardwareConcurrency / deviceMemory",
        "category": "hardware",
        "status": "protected",
        "notes": "Object.defineProperty enforced in init script.",
    },
    {
        "name": "Touch / pointer / mobile surface",
        "category": "hardware",
        "status": "protected",
        "notes": "maxTouchPoints, pointer coarse, TouchEvent set for mobile profiles.",
    },
    {
        "name": "Battery API",
        "category": "hardware",
        "status": "protected",
       "notes": "navigator.getBattery returns stable fake BatteryManager.",
    },
    {
        "name": "Sensor APIs",
        "category": "hardware",
        "status": "protected",
        "notes": "Desktop hides motion/orientation/light/proximity constructors; mobile stable values.",
    },
    {
        "name": "MediaDevices enumeration",
        "category": "hardware",
        "status": "protected",
        "notes": "enumerateDevices returns stable fake devices per profile.",
    },
    {
        "name": "Speech synthesis voices",
        "category": "navigator",
        "status": "protected",
        "notes": "getVoices returns OS/locale-consistent fake voice list.",
    },
    {
        "name": "Network information API",
        "category": "navigator",
        "status": "protected",
        "notes": "navigator.connection spoofed per device type.",
    },
    {
        "name": "MediaCapabilities",
        "category": "navigator",
        "status": "protected",
        "notes": "decodingInfo resolves to mocked support capabilities.",
    },
    {
        "name": "Web Share API",
        "category": "navigator",
        "status": "protected",
        "notes": "canShare/share reflect desktop vs mobile.",
    },
    {
        "name": "Timing precision",
        "category": "automation",
        "status": "protected",
        "notes": "performance.now and Date.now coarsened to centisecond precision.",
    },
    {
        "name": "CDP/headless artifacts",
        "category": "automation",
        "status": "partial",
        "notes": "Automation flags removed; CDP still active for Playwright but not debug port exposed.",
    },
    {
        "name": "Detection scanner (offline)",
        "category": "testing",
        "status": "protected",
        "notes": "7+ offline checks in detection_check.py.",
    },
    {
        "name": "Live public scanners",
        "category": "testing",
        "status": "protected",
        "notes": "browserleaks, whoer, creepjs, fingerprintjs modes.",
    },
    {
        "name": "Cross-profile isolation verifier",
        "category": "testing",
        "status": "protected",
        "notes": "isolation_check.py with local HTTP server proves no storage bleed.",
    },
    {
        "name": "Detection risk score UI",
        "category": "testing",
        "status": "protected",
        "notes": "Backend scoring + frontend badge.",
    },
    {
        "name": "Profile aging / cookie robot",
        "category": "behavior",
        "status": "protected",
        "notes": "cookie_robot.py warms profiles with micro-interactions.",
    },
    {
        "name": "Human-like automation engine",
        "category": "behavior",
        "status": "not_protected",
        "notes": "Intentionally out of scope; deterministic robotic movement is detectable.",
    },
    {
        "name": "Auto-healing fingerprint loop",
        "category": "testing",
        "status": "protected",
        "notes": "create_healed_profile mutates and rescans until consistency passes.",
    },
    {
        "name": "Encrypted cloud sync",
        "category": "security",
        "status": "partial",
        "notes": "Local encrypted archives, sync_server/ reference server, and remote client endpoints implemented. Cross-device key exchange/conflict resolution not implemented.",
    },
    {
        "name": "Signed updates",
        "category": "security",
        "status": "protected",
        "notes": "Update check, SHA/Ed25519 download verification, confirmation tokens, staged apply, and rollback implemented.",
    },
    {
        "name": "Reproducible builds / SBOM",
        "category": "security",
        "status": "protected",
        "notes": "SBOM endpoint parses requirements.txt; packaging script and build pipeline documented.",
    },
    {
        "name": "Privacy/threat model document",
        "category": "security",
        "status": "protected",
        "notes": "docs/PRIVACY_MODEL.md is the canonical privacy and threat model.",
    },
]


def get_surface_registry() -> List[Dict]:
    return [dict(s) for s in SURFACES]


def get_protection_score() -> Dict[str, float]:
    total = len(SURFACES)
    protected = sum(1 for s in SURFACES if s["status"] == "protected")
    partial = sum(1 for s in SURFACES if s["status"] == "partial")
    not_protected = sum(1 for s in SURFACES if s["status"] == "not_protected")
    return {
        "protected": protected,
        "partial": partial,
        "not_protected": not_protected,
        "total": total,
        "score_percent": round((protected / total) * 100, 1),
        "partial_credit_percent": round(((protected + partial * 0.5) / total) * 100, 1),
    }
