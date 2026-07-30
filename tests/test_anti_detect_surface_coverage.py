"""Static analysis: every declared fingerprint surface MUST be addressed in the spoofing/anti-detect scripts.
No browser launch required — pure AST/source checks for regression prevention."""
import ast
import os
import sys
import json
import re

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from backend.fingerprint_surface_registry import SURFACES

BM_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend", "browser_manager.py")

REQUIRED_JS_PATTERNS = {
    "navigator.webdriver": [r"navigator\.webdriver", r"__makeNative.*webdriver"],
    "navigator.userAgentData": [r"userAgentData", r"getHighEntropyValues"],
    "navigator.plugins": [r"navigator\.plugins", r"__fakePlugins", r"__pluginArray"],
    "navigator.mimeTypes": [r"navigator\.mimeTypes", r"__fakeMimeTypes", r"__mimeTypeArray"],
    "navigator.hardwareConcurrency": [r"hardwareConcurrency"],
    "navigator.deviceMemory": [r"deviceMemory"],
    "navigator.languages": [r"safeDefineProperty\(navigator.*language"],
    "navigator.userAgent": [r"safeDefineProperty\(navigator.*userAgent"],
    "navigator.maxTouchPoints": [r"maxTouchPoints"],
    "navigator.vendor": [r"safeDefineProperty\(navigator.*vendor", r"Google Inc"],
    "navigator.product": [r"safeDefineProperty\(navigator.*product", r"Gecko"],
    "navigator.cookieEnabled": [r"cookieEnabled"],
    "navigator.pdfViewerEnabled": [r"pdfViewerEnabled"],
    "navigator.doNotTrack": [r"doNotTrack"],
    "navigator.javaEnabled": [r"javaEnabled"],
    "performance.memory": [r"performance\.memory", r"jsHeapSizeLimit"],
    "screen resolution": [r"safeDefineProperty\(window\.screen", r"window\.screen"],
    "Intl timezone": [r"Intl\.DateTimeFormat", r"timeZone"],
    "AudioContext noise": [r"startRendering", r"OfflineAudioContext"],
    "AnalyserNode noise": [r"getFloatFrequencyData", r"getByteFrequencyData"],
    "Canvas noise": [r"getImageData", r"toDataURL", r"toBlob"],
    "OffscreenCanvas noise": [r"OffscreenCanvas.*convertToBlob", r"OffscreenCanvasRenderingContext2D"],
    "Worker canvas bootstrap": [r"workerBootstrap", r"__ghostWorkerPatchInstalled"],
    "WebGL renderer/vendor": [r"37445", r"37446", r"WEBGL_debug_renderer_info"],
    "WebGL shader precision": [r"getShaderPrecisionFormat"],
    "WebGL extensions": [r"getSupportedExtensions"],
    "WebGL readPixels noise": [r"__addWebGLReadPixelsNoise", r"readPixels"],
    "WebGPU adapter": [r"gpu\.requestAdapter", r"__gpuAdapterInfo"],
    "Battery API": [r"getBattery", r"__batteryManager"],
    "Sensor APIs": [r"DeviceMotionEvent", r"DeviceOrientationEvent"],
    "MediaDevices enumeration": [r"enumerateDevices", r"__fakeDeviceList"],
    "RTCPeerConnection stub": [r"RTCPeerConnectionStub", r"RTCPeerConnection"],
    "Permissions API": [r"permissions\.query", r"__permHash"],
    "Notification permission": [r"Notification\.permission", r"requestPermission"],
    "Timing coarsening": [r"performance\.now", r"Date\.now"],
    "chrome.loadTimes/csi": [r"__chromeLoadTimes", r"__chromeCsi"],
    "matchMedia spoof": [r"matchMedia", r"__featureAnswerMap", r"prefers-color-scheme"],
    "Font availability spoof": [r"document\.fonts\.check", r"__commonFontNames"],
    "Font metric spoofing": [r"measureText", r"__measureTextHash", r"__makeFakeTextMetrics"],
    "Speech synthesis": [r"speechSynthesis\.getVoices", r"__voiceList"],
    "Network information": [r"navigator\.connection", r"effectiveType"],
    "MediaCapabilities": [r"mediaCapabilities\.decodingInfo"],
    "Web Share API": [r"navigator\.share", r"navigator\.canShare"],
    "DOM geometry offsets": [r"getBoundingClientRect", r"getClientRects", r"__geomOffset"],
    "Browser automation cleanup": [r"__webdriver_script_fn", r"callPhantom", r"cdc_adoQpoasnfa76pfcZLmcfl"],
    "Function toString protection": [r"__spoofedFuncNames", r"spoofedFunctions", r"makeNative"],
    "window.outerWidth/Height": [r"outerWidth", r"outerHeight"],
    "Plugins/MimeType cleanup": [r"navigator\.plugins", r"navigator\.mimeTypes"],
    "Storage isolation": [r"user-data-dir", r"GHOSTBROWSER_TEST_PROFILES_DIR"],
}

REQUIRED_PYTHON_CDP_PATTERNS = {
    "CDP Emulation.setUserAgentOverride": [r"Emulation\.setUserAgentOverride", r"Emulation"],
    "CDP Network.setUserAgentOverride": [r"Network\.setUserAgentOverride"],
    "add_init_script injection": [r"add_init_script"],
    "page.evaluate fallback": [r"p\.evaluate\(anti_detect_script"],
    "Context routing barrier": [r"global_routing_barrier"],
}

_all_passed = True
_failures = []

def check(label, cond, detail=""):
    global _all_passed
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} — {detail}")
        _all_passed = False
        _failures.append(label)

def test_js_patterns_in_source(source, patterns_dict, source_label):
    for surface, patterns in patterns_dict.items():
        found = any(re.search(p, source) for p in patterns)
        check(f"{source_label} covers [{surface}]", found,
              f"None of {patterns} matched in {source_label[:60]}...")

def test_surface_registry_consistency():
    required_surfaces = [
        "Per-profile storage isolation", "Cookie jar partitioning", "Proxy kill switch",
        "WebRTC leak prevention", "User-Agent / platform", "navigator.webdriver masking",
        "navigator.vendor / product / productSub", "navigator.cookieEnabled / pdfViewerEnabled",
        "navigator.doNotTrack / javaEnabled", "performance.memory",
        "Permissions API", "Client hints", "Canvas fingerprint noise",
        "WebGL vendor / renderer / params", "WebGPU adapter info",
        "AudioContext noise", "Font enumeration", "Font metric spoofing",
        "DOM geometry / ClientRects", "CSS media query consistency",
        "hardwareConcurrency / deviceMemory", "Battery API", "Sensor APIs",
        "MediaDevices enumeration", "Speech synthesis voices",
        "Network information API", "MediaCapabilities", "Web Share API",
        "Timing precision", "Detection scanner (offline)",
        "Profile aging / cookie robot", "Auto-healing fingerprint loop",
        "Cross-profile isolation verifier",
    ]
    surface_names = {s["name"] for s in SURFACES}
    for name in required_surfaces:
        check(f"Surface registry contains [{name}]", name in surface_names,
              f"Missing from SURFACES list in fingerprint_surface_registry.py")

def test_measuretext_on_by_default():
    with open(BM_PATH, "r") as f:
        src = f.read()
    match = re.search(r'_ad_experimental_measuretext\s*=\s*_ad_advanced\.get\("experimental_measuretext",\s*(True|False)\)', src)
    if match:
        check("measureText spoof enabled by default (experimental_measuretext=True)",
              match.group(1) == "True",
              f"Got {match.group(1)}, expected True")
    else:
        check("measureText spoof setting found", False, "Could not find _ad_experimental_measuretext assignment")

def test_permissions_variation():
    with open(BM_PATH, "r") as f:
        src = f.read()
    has_perm_hash = "__permHash" in src
    has_roll = "roll < 5" in src or "__permSeed" in src
    check("Permissions API has per-profile variation", has_perm_hash and has_roll,
          "__permHash/__permSeed/roll not found — permissions may be uniform")

def test_function_tostring_integrity():
    """Function.prototype.toString must be proxied in both scripts."""
    with open(BM_PATH, "r") as f:
        src = f.read()
    spoofing_count = src.count("__spoofedFuncNames")
    anti_count = src.count("spoofedFunctions")
    check("Function.toString proxied in anti_detect_script", spoofing_count >= 2,
          f"Found {spoofing_count} __spoofedFuncNames references")
    check("Function.toString proxied in spoofing_script", anti_count >= 2,
          f"Found {anti_count} spoofedFunctions references")

def test_surface_registry_protection_scores():
    scores = {s["status"] for s in SURFACES}
    protected = sum(1 for s in SURFACES if s["status"] == "protected")
    partial = sum(1 for s in SURFACES if s["status"] == "partial")
    not_protected = sum(1 for s in SURFACES if s["status"] == "not_protected")
    total = len(SURFACES)
    score_pct = round((protected / total) * 100, 1)
    print(f"\n  Registry score: {protected}/{total} protected ({score_pct}%), "
          f"{partial} partial, {not_protected} not_protected")
    check("At least 85% of surfaces are protected", score_pct >= 85, f"Got {score_pct}%")
    check("No 'not_protected' surfaces exceed 3", not_protected <= 3, f"Got {not_protected}")
    check("Total surfaces documented", total >= 30, f"Got {total}")

def main():
    global _all_passed
    print("=" * 74)
    print("  ANTI-DETECT SURFACE COVERAGE AUDIT (STATIC ANALYSIS)")
    print("=" * 74)

    with open(BM_PATH, "r") as f:
        bm_source = f.read()

    print("\n--- JS Pattern Coverage (anti_detect_script + spoofing_script) ---")
    test_js_patterns_in_source(bm_source, REQUIRED_JS_PATTERNS, "browser_manager.py")

    print("\n--- CDP / Python Infrastructure ---")
    test_js_patterns_in_source(bm_source, REQUIRED_PYTHON_CDP_PATTERNS, "browser_manager.py")

    print("\n--- Surface Registry ---")
    test_surface_registry_consistency()

    print("\n--- Configuration Checks ---")
    test_measuretext_on_by_default()
    test_permissions_variation()
    test_function_tostring_integrity()
    test_surface_registry_protection_scores()

    print("\n" + "=" * 74)
    print(f"  RESULT: {'ALL PASSED' if _all_passed else f'{len(_failures)} FAILURES:'}")
    if _failures:
        for f in _failures:
            print(f"    - {f}")
    print("=" * 74)
    return _all_passed

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
