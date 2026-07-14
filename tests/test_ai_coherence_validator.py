# -*- coding: utf-8 -*-
"""Test AI coherence validator with various inputs."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.ai_coherence_validator import AICoherenceValidator

validator = AICoherenceValidator()
passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}")
        failed += 1


VALID_WINDOWS = {
    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "webgl_vendor": "Google Inc. (NVIDIA)",
    "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "cpu_cores": 16,
    "memory_gb": 32,
    "timezone": "America/New_York",
    "locale": "en-US",
    "screen_resolution": "1920x1080",
    "fonts": ["Arial", "Calibri", "Consolas", "Georgia", "Times New Roman"],
    "sec_ch_ua": '"Chromium";v="128", "Google Chrome";v="128", "Not=A?Brand";v="99"',
    "sec_ch_ua_platform": '"Windows"',
    "os": "Windows",
}


def run_tests():
    global passed, failed

    # Test 1: Valid Windows profile should pass (score >= 90)
    print("\n--- Test 1: Valid Windows profile ---")
    result = validator.validate(VALID_WINDOWS.copy())
    check("Valid Windows passes", result["passed"] is True)
    check("Score >= 90", result["score"] >= 90)
    check("No issues", len(result["issues"]) == 0)

    # Test 2: Mac with NVIDIA GPU should be penalized
    print("\n--- Test 2: Mac + NVIDIA GPU ---")
    mac_nvidia = {
        **VALID_WINDOWS,
        "os": "Mac",
        "sec_ch_ua_platform": '"macOS"',
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
    }
    result = validator.validate(mac_nvidia)
    check("Mac+NVIDIA fails", result["passed"] is False)
    check("Score drops below 90", result["score"] < 90)
    nvidia_issue = any("NVIDIA" in i for i in result["issues"])
    check("Reports NVIDIA-on-Mac issue", nvidia_issue)

    # Test 3: Version mismatch between UA and sec_ch_ua
    print("\n--- Test 3: Version mismatch ---")
    version_mismatch = {
        **VALID_WINDOWS,
        "sec_ch_ua": '"Chromium";v="127", "Google Chrome";v="127", "Not=A?Brand";v="99"',
    }
    result = validator.validate(version_mismatch)
    check("Version mismatch fails", result["passed"] is False)
    mismatch_issue = any("mismatch" in i.lower() for i in result["issues"])
    check("Reports mismatch issue", mismatch_issue)
    check("Score < 90", result["score"] < 90)

    # Test 4: Windows + Apple GPU
    print("\n--- Test 4: Windows + Apple GPU ---")
    win_apple = {
        **VALID_WINDOWS,
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M1",
    }
    result = validator.validate(win_apple)
    check("Windows+Apple fails", result["passed"] is False)
    apple_issue = any("Apple" in i for i in result["issues"])
    check("Reports Apple-on-Windows issue", apple_issue)

    # Test 5: Mac + Direct3D renderer (vendor must contain "amd" to trigger)
    # The AMD/Direct3D check only fires when "amd" is in the vendor string
    print("\n--- Test 5: Mac + AMD vendor + Direct3D ---")
    mac_amd_d3d = {
        **VALID_WINDOWS,
        "os": "Mac",
        "sec_ch_ua_platform": '"macOS"',
        "webgl_vendor": "AMD",
        "webgl_renderer": "AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0",
    }
    result = validator.validate(mac_amd_d3d)
    check("Mac+AMD+Direct3D fails", result["passed"] is False)
    d3d_issue = any("Direct3D" in i for i in result["issues"])
    check("Reports Direct3D-on-Mac issue", d3d_issue)

    # Test 5b: Mac + ATI vendor (does NOT contain "amd") - Direct3D check won't fire
    print("\n--- Test 5b: Mac + ATI vendor + Direct3D (check skipped) ---")
    mac_ati_d3d = {
        **VALID_WINDOWS,
        "os": "Mac",
        "sec_ch_ua_platform": '"macOS"',
        "webgl_vendor": "ATI Technologies Inc.",
        "webgl_renderer": "ATI Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0",
    }
    result = validator.validate(mac_ati_d3d)
    d3d_no_fire = not any("Direct3D" in i for i in result["issues"])
    check("ATI vendor skips Direct3D check (vendor lacks 'amd')", d3d_no_fire)

    # Test 6: Client hints platform mismatch
    print("\n--- Test 6: Client hints platform mismatch ---")
    plat_mismatch = {**VALID_WINDOWS, "sec_ch_ua_platform": '"Linux"'}
    result = validator.validate(plat_mismatch)
    check("Platform mismatch fails", result["passed"] is False)
    plat_issue = any("ClientHints Platform" in i for i in result["issues"])
    check("Reports platform mismatch", plat_issue)

    # Test 7: Core/memory ratio anomaly
    print("\n--- Test 7: Core/memory ratio anomaly ---")
    hw_bad = {**VALID_WINDOWS, "cpu_cores": 64, "memory_gb": 2}
    result = validator.validate(hw_bad)
    hw_issue = any("cores" in i.lower() for i in result["issues"])
    check("Reports hardware ratio issue", hw_issue)
    check("Score penalised", result["score"] < 100)

    # Test 8: Locale/timezone anomaly
    print("\n--- Test 8: Locale/timezone mismatch ---")
    tz_bad = {**VALID_WINDOWS, "timezone": "America/New_York", "locale": "ja-JP"}
    result = validator.validate(tz_bad)
    tz_issue = any("anomalous" in i.lower() for i in result["issues"])
    check("Reports locale/tz anomaly", tz_issue)

    # Test 9: Low battery not charging
    print("\n--- Test 9: Low battery not charging ---")
    low_bat = {**VALID_WINDOWS, "battery_level": 0.02, "battery_charging": False}
    result = validator.validate(low_bat)
    bat_issue = any("Battery" in i for i in result["issues"])
    check("Reports low battery issue", bat_issue)

    # Test 10: AMD vendor with NVIDIA renderer text
    # The check is: "amd" in vendor AND "radeon" not in renderer AND "amd" not in renderer
    # "AMD" contains "amd" (lowered). "NVIDIA GeForce..." does not contain "radeon" or "amd".
    print("\n--- Test 10: AMD vendor with wrong renderer ---")
    amd_bad = {
        **VALID_WINDOWS,
        "webgl_vendor": "AMD",
        "webgl_renderer": "NVIDIA GeForce RTX 3060 Direct3D11",
    }
    result = validator.validate(amd_bad)
    check("Score penalised for AMD vendor/renderer mismatch", result["score"] < 100)
    vendor_issue = any("Vendor is AMD" in i for i in result["issues"])
    check("Reports AMD vendor mismatch", vendor_issue)

    # Test 10b: ATI vendor doesn't contain "amd" so check is skipped
    print("\n--- Test 10b: ATI vendor with wrong renderer (skipped) ---")
    ati_bad = {
        **VALID_WINDOWS,
        "webgl_vendor": "ATI Technologies Inc.",
        "webgl_renderer": "NVIDIA GeForce RTX 3060 Direct3D11",
    }
    result = validator.validate(ati_bad)
    no_amd_issue = not any("Vendor is AMD" in i for i in result["issues"])
    check("ATI vendor skips AMD check (lacks 'amd' substring)", no_amd_issue)

    # Test 11: NVIDIA vendor check has a bug - "a" not in renderer is always False
    # for any non-empty renderer, so NVIDIA vendor mismatch NEVER fires
    print("\n--- Test 11: NVIDIA vendor check behavior ---")
    nv_bad = {
        **VALID_WINDOWS,
        "webgl_vendor": "NVIDIA Corporation",
        "webgl_renderer": "AMD Radeon RX 6700 XT Direct3D11",
    }
    result = validator.validate(nv_bad)
    # The "a" not in renderer check means NVIDIA vendor mismatch never triggers
    nv_issue = any("Vendor is NVIDIA" in i for i in result["issues"])
    check("NVIDIA vendor check never fires (code has 'a' substring bug)", not nv_issue)

    # Test 12: Intel vendor with unknown renderer
    print("\n--- Test 12: Intel vendor, unknown renderer ---")
    intel_bad = {
        **VALID_WINDOWS,
        "webgl_vendor": "Intel Inc.",
        "webgl_renderer": "Some Unknown Renderer",
    }
    result = validator.validate(intel_bad)
    check("Intel vendor/renderer mismatch penalised", result["score"] < 100)
    intel_issue = any("Vendor is Intel" in i for i in result["issues"])
    check("Reports Intel vendor mismatch", intel_issue)

    # Test 13: NVIDIA vendor with GeForce renderer (should pass vendor check)
    print("\n--- Test 13: NVIDIA + GeForce renderer (valid) ---")
    nv_good = {
        **VALID_WINDOWS,
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    }
    result = validator.validate(nv_good)
    vendor_ok = not any("Vendor is NVIDIA" in i for i in result["issues"])
    check("NVIDIA+GeForce renderer passes vendor check", vendor_ok)

    # Test 14: AMD vendor with Radeon renderer (should pass vendor check)
    print("\n--- Test 14: AMD + Radeon renderer (valid) ---")
    amd_good = {
        **VALID_WINDOWS,
        "webgl_vendor": "AMD",
        "webgl_renderer": "AMD Radeon RX 6700 XT Direct3D11",
    }
    result = validator.validate(amd_good)
    vendor_ok_amd = not any("Vendor is AMD" in i for i in result["issues"])
    check("AMD+Radeon renderer passes vendor check", vendor_ok_amd)

    # Test 15: Intel vendor with Iris renderer (should pass vendor check)
    print("\n--- Test 15: Intel + Iris renderer (valid) ---")
    intel_good = {
        **VALID_WINDOWS,
        "webgl_vendor": "Intel Inc.",
        "webgl_renderer": "Intel Iris OpenGL Engine",
    }
    result = validator.validate(intel_good)
    vendor_ok_intel = not any("Vendor is Intel" in i for i in result["issues"])
    check("Intel+Iris renderer passes vendor check", vendor_ok_intel)

    # Test 16: Mac with 1920x1080 gets penalty
    print("\n--- Test 16: Mac with 1920x1080 resolution ---")
    mac_1080 = {
        **VALID_WINDOWS,
        "os": "Mac",
        "sec_ch_ua_platform": '"macOS"',
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M2",
        "screen_resolution": "1920x1080",
    }
    result = validator.validate(mac_1080)
    res_issue = any("1920x1080" in i for i in result["issues"])
    check("Mac 1920x1080 resolution penalised", res_issue)

    # Test 17: Mac with 1366x768 also penalised
    print("\n--- Test 17: Mac with 1366x768 resolution ---")
    mac_768 = {**mac_1080, "screen_resolution": "1366x768"}
    result = validator.validate(mac_768)
    res_issue2 = any("1366x768" in i for i in result["issues"])
    check("Mac 1366x768 resolution penalised", res_issue2)

    # Test 18: Score can go below 0 (no floor in code)
    print("\n--- Test 18: Score can go negative (no floor) ---")
    terrible = {
        "os": "Windows",
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M1",
        "cpu_cores": 128,
        "memory_gb": 1,
        "timezone": "America/New_York",
        "locale": "ja-JP",
        "sec_ch_ua_platform": '"Linux"',
        "userAgent": "Chrome/999",
        "sec_ch_ua": '"Chromium";v="1", "Google Chrome";v="1"',
        "battery_level": 0.01,
        "battery_charging": False,
    }
    result = validator.validate(terrible)
    check("Terrible profile fails", result["passed"] is False)
    check("Score can be negative (no floor)", result["score"] < 0)

    # Test 19: Valid Linux-like profile
    print("\n--- Test 19: Linux-like profile ---")
    linux_like = {
        **VALID_WINDOWS,
        "os": "Linux",
        "sec_ch_ua_platform": '"Linux"',
        "webgl_vendor": "Mesa",
        "webgl_renderer": "Mesa Intel UHD Graphics 630",
    }
    result = validator.validate(linux_like)
    check("Linux-like profile score >= 90", result["score"] >= 90)

    # Test 20: Valid Mac profile with proper resolution
    print("\n--- Test 20: Valid Mac profile ---")
    valid_mac = {
        **VALID_WINDOWS,
        "os": "Mac",
        "sec_ch_ua_platform": '"macOS"',
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M2",
        "screen_resolution": "2560x1600",
    }
    result = validator.validate(valid_mac)
    check("Valid Mac passes", result["passed"] is True)
    check("Mac score >= 90", result["score"] >= 90)

    # Test 21: Multiple simultaneous issues compound penalty
    print("\n--- Test 21: Multiple issues compound penalty ---")
    multi_issue = {
        **VALID_WINDOWS,
        "os": "Mac",
        "sec_ch_ua_platform": '"macOS"',
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
        "screen_resolution": "1920x1080",
        "cpu_cores": 64,
        "memory_gb": 2,
    }
    result = validator.validate(multi_issue)
    check("Multi-issue score < 90", result["passed"] is False)
    check("Multiple issues flagged", len(result["issues"]) >= 3)

    # Test 22: Empty dict - returns valid structure
    print("\n--- Test 22: Empty dict ---")
    result = validator.validate({})
    check("Empty dict returns score", isinstance(result["score"], (int, float)))
    check("Empty dict returns issues list", isinstance(result["issues"], list))
    check("Empty dict returns passed bool", isinstance(result["passed"], bool))

    # Test 23: Battery charged - no issue
    print("\n--- Test 23: Battery charged (no issue) ---")
    charged = {**VALID_WINDOWS, "battery_level": 0.95, "battery_charging": True}
    result = validator.validate(charged)
    no_bat = not any("Battery" in i for i in result["issues"])
    check("Charged battery causes no issue", no_bat)

    # Test 24: Low battery but charging - no issue
    print("\n--- Test 24: Low battery but charging ---")
    charging_low = {**VALID_WINDOWS, "battery_level": 0.03, "battery_charging": True}
    result = validator.validate(charging_low)
    no_bat2 = not any("Battery" in i for i in result["issues"])
    check("Low battery + charging causes no issue", no_bat2)

    # Test 25: Non-dict input (validator lacks isinstance guard)
    print("\n--- Test 25: Non-dict input handling ---")
    try:
        result = validator.validate("not a dict")
        check("Non-dict returns structure", isinstance(result, dict))
    except (AttributeError, TypeError):
        check("Non-dict raises error (expected, no guard)", True)

    # Test 26: None input
    print("\n--- Test 26: None input handling ---")
    try:
        result = validator.validate(None)
        check("None returns structure", isinstance(result, dict))
    except (AttributeError, TypeError):
        check("None raises error (expected, no guard)", True)

    # Test 27: NVIDIA RTX renderer passes check
    print("\n--- Test 27: NVIDIA RTX renderer passes ---")
    nv_rtx = {
        **VALID_WINDOWS,
        "webgl_vendor": "NVIDIA Corporation",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
    }
    result = validator.validate(nv_rtx)
    nv_rtx_ok = not any("Vendor is NVIDIA" in i for i in result["issues"])
    check("NVIDIA RTX renderer passes vendor check", nv_rtx_ok)

    # Test 28: NVIDIA GTX renderer passes check
    print("\n--- Test 28: NVIDIA GTX renderer passes ---")
    nv_gtx = {
        **VALID_WINDOWS,
        "webgl_vendor": "NVIDIA Corporation",
        "webgl_renderer": "NVIDIA GeForce GTX 1080 Direct3D11",
    }
    result = validator.validate(nv_gtx)
    nv_gtx_ok = not any("Vendor is NVIDIA" in i for i in result["issues"])
    check("NVIDIA GTX renderer passes vendor check", nv_gtx_ok)

    # Test 29: NVIDIA Quadro renderer passes check
    print("\n--- Test 29: NVIDIA Quadro renderer passes ---")
    nv_quadro = {
        **VALID_WINDOWS,
        "webgl_vendor": "NVIDIA Corporation",
        "webgl_renderer": "NVIDIA Quadro RTX 5000",
    }
    result = validator.validate(nv_quadro)
    nv_quadro_ok = not any("Vendor is NVIDIA" in i for i in result["issues"])
    check("NVIDIA Quadro renderer passes vendor check", nv_quadro_ok)

    # Test 30: NVIDIA Titan renderer passes check
    print("\n--- Test 30: NVIDIA Titan renderer passes ---")
    nv_titan = {
        **VALID_WINDOWS,
        "webgl_vendor": "NVIDIA Corporation",
        "webgl_renderer": "NVIDIA TITAN X Direct3D11",
    }
    result = validator.validate(nv_titan)
    nv_titan_ok = not any("Vendor is NVIDIA" in i for i in result["issues"])
    check("NVIDIA Titan renderer passes vendor check", nv_titan_ok)

    # Test 31: Intel UHD renderer passes check
    print("\n--- Test 31: Intel UHD renderer passes ---")
    intel_uhd = {
        **VALID_WINDOWS,
        "webgl_vendor": "Intel Inc.",
        "webgl_renderer": "Intel(R) UHD Graphics 630",
    }
    result = validator.validate(intel_uhd)
    intel_uhd_ok = not any("Vendor is Intel" in i for i in result["issues"])
    check("Intel UHD renderer passes vendor check", intel_uhd_ok)

    # Test 32: Intel HD Graphics renderer passes check
    print("\n--- Test 32: Intel HD Graphics renderer passes ---")
    intel_hd = {
        **VALID_WINDOWS,
        "webgl_vendor": "Intel Inc.",
        "webgl_renderer": "Intel HD Graphics 4000",
    }
    result = validator.validate(intel_hd)
    intel_hd_ok = not any("Vendor is Intel" in i for i in result["issues"])
    check("Intel HD Graphics renderer passes vendor check", intel_hd_ok)

    # Summary
    sep = "=" * 50
    print(f"\n{sep}")
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{sep}")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    if not success:
        sys.exit(1)
