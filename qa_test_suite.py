"""
GhostBrowser QA Test Suite — T07-T10
Strictly Isolated, High-Assurance, Offline, and Deterministic
"""
import asyncio
import sys
import os
import shutil
import tempfile
import socket
import json
import psutil
from unittest.mock import patch

# 1. Block network access except to loopback (local Playwright)
_orig_connect = socket.socket.connect

def guarded_connect(self, address):
    host = address[0]
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(f"Forbidden network access attempt detected in QA test suite: {host}")
    return _orig_connect(self, address)

socket.socket.connect = guarded_connect

# 2. Setup isolated temp directory environment
temp_test_dir = tempfile.TemporaryDirectory()
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_test_dir.name
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

# Inject a mock cloudflare_manager to prevent loading real accounts during tests
from unittest.mock import MagicMock
mock_cf_module = MagicMock()
mock_cf_manager = MagicMock()
mock_cf_manager.accounts = []
mock_cf_manager.accounts_file = "mock_file.txt"
mock_cf_manager.load_accounts = lambda *args, **kwargs: None
mock_cf_module.cloudflare_manager = mock_cf_manager
mock_cf_module.CloudflareManager = MagicMock
sys.modules["backend.cloudflare_manager"] = mock_cf_module

# Import backend modules after setting up the environment
sys.path.append(os.path.abspath(os.getcwd()))

from backend.config import get_data_dir
from backend.profile_manager import ProfileManager
test_manager = ProfileManager()

# Apply the global override
import backend.profile_manager
import backend.profile_creator
import backend.browser_manager
backend.profile_manager.profile_manager = test_manager
backend.profile_creator.profile_manager = test_manager
backend.browser_manager.profile_manager = test_manager

from backend.profile_creator import profile_creator
from backend.browser_manager import (
    launch_profile,
    close_profile,
    active_browsers,
    find_profile_processes,
    kill_process_tree,
    profile_cdp_tasks,
)
from backend.cookie_robot import cookie_robot

# Snapshot production metadata
real_profiles_dir = os.path.realpath(get_data_dir("profiles_data"))
real_meta_path = os.path.join(real_profiles_dir, "profiles_meta.json")

def read_real_metadata():
    if os.path.exists(real_meta_path):
        try:
            with open(real_meta_path, "rb") as f:
                return f.read()
        except Exception:
            return None
    return None

PRODUCTION_METADATA_SNAPSHOT = read_real_metadata()

# Assert safety of the path
assert not os.path.realpath(test_manager.PROFILES_DIR).startswith(real_profiles_dir + os.sep) and os.path.realpath(test_manager.PROFILES_DIR) != real_profiles_dir, \
    "SECURITY FAULT: Test dir resolved inside production directory!"

# Result tracking
checks_total = 0
checks_passed = 0
checks_failed = 0
checks_skipped = 0
failed_check_names = []

def record_check(name, success, is_skipped=False):
    global checks_total, checks_passed, checks_failed, checks_skipped
    checks_total += 1
    if is_skipped:
        checks_skipped += 1
        print(f"  [SKIPPED] {name}")
    elif success:
        checks_passed += 1
        print(f"  [PASS] {name}")
    else:
        checks_failed += 1
        failed_check_names.append(name)
        print(f"  [FAIL] {name}")

def _is_test_owned_chromium(proc):
    """Return whether a Chromium process is descended from this QA process."""
    try:
        if (proc.info.get("name") or "").lower() not in {"chrome.exe", "chromium.exe"}:
            return False
        return any(parent.pid == os.getpid() for parent in proc.parents())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


async def _stop_test_background_work(profile_ids):
    """Cancel and await work that could otherwise race profile deletion."""
    for profile_id in profile_ids:
        if profile_id is not None:
            await cookie_robot.stop_warming(profile_id)

    for profile_id in profile_ids:
        tasks = list(profile_cdp_tasks.pop(profile_id, set()))
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def cleanup_browsers(profile_ids):
    """Close only QA-owned browser processes after awaiting background work."""
    await _stop_test_background_work(profile_ids)

    # Close any profiles still tracked in active_browsers
    for pid in list(active_browsers.keys()):
        try:
            await close_profile(pid)
        except Exception as e:
            print(f"[WARNING] close_profile({pid}) failed during cleanup: {e}")

    # Do not use `taskkill /IM chrome.exe`: it terminates unrelated user
    # sessions.  Profile-dir matching covers failed launches; ancestry covers
    # Chromium descendants launched by Playwright from this test process.
    leftovers = []
    for profile_id in profile_ids:
        profile = test_manager.get_profile(profile_id) if profile_id is not None else None
        if profile and profile.get("path"):
            leftovers.extend(find_profile_processes(profile["path"]))
    leftovers.extend(
        proc
        for proc in psutil.process_iter(["pid", "name"])
        if _is_test_owned_chromium(proc)
    )
    for proc in {proc.pid: proc for proc in leftovers}.values():
        try:
            kill_process_tree(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"[WARNING] Could not terminate QA Chromium PID {proc.pid}: {e}")

# Deterministic finger-print fixture
from backend.config import get_installed_chromium_major_version, get_installed_chromium_version
inst_maj = get_installed_chromium_major_version()
inst_full = get_installed_chromium_version()

TEST_FP_FIXTURE = {
    "os": "Windows",
    "platform": "Win32",
    "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{inst_maj}.0.0.0 Safari/537.36",
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
    "sec_ch_ua": f'"Not(A:Brand";v="99", "Chromium";v="{inst_maj}"',
    "sec_ch_ua_platform": '"Windows"',
    "client_hints": {
        "architecture": "x86",
        "bitness": "64",
        "model": "",
        "platformVersion": "10.0.0",
        "uaFullVersion": inst_full
    },
    "notification_permission": "default",
    "battery_level": 0.98,
    "battery_charging": False,
    "battery_discharging": 46800,
    "behavior": {}
}

async def run_qa_tests():
    # Mock AI output deterministically
    async def mock_ai_gen(*args, **kwargs):
        return TEST_FP_FIXTURE.copy()

    # Mock Kimi analysis to avoid network/timeouts
    async def mock_kimi_analysis(profile_id, fingerprint, technical_result):
        return {
            "ai_score": 85,
            "detected_issues": [],
            "recommendations": [],
            "overall_verdict": "Acceptable",
            "reasoning": "Mocked validation analysis",
            "_is_fallback": False
        }
    from backend.ai_auto_validator import auto_validator
    auto_validator._get_kimi_analysis = mock_kimi_analysis

    p1_id = None
    p2_id = None

    with patch("backend.profile_creator.generate_fingerprint_ai", mock_ai_gen), \
         patch("backend.proxy_manager.proxy_manager.resolve_proxy_geo", return_value={"timezone": "America/New_York", "locale": "en-US"}), \
         patch("backend.proxy_manager.proxy_manager.check_proxy_health", return_value=True):

        try:
            # Create disposable profile
            # Warming intentionally launches an independent background browser.
            # T08-T10 own their browser lifecycle, so disable it for fixtures.
            p1 = await profile_creator.create_zero_leak_profile("QA-Profile-1", skip_warming=True)
            record_check("Create QA-Profile-1", p1["status"] == "success")
            p1_id = p1["profile"]["id"]

            p2 = await profile_creator.create_zero_leak_profile("QA-Profile-2", skip_warming=True)
            record_check("Create QA-Profile-2", p2["status"] == "success")
            p2_id = p2["profile"]["id"]

            # ============= T07: CONSISTENCY =============
            print("\n" + "=" * 60)
            print("T07: PROFILE CONSISTENCY")
            print("=" * 60)

            prof1 = test_manager.get_profile(p1_id)
            adv = prof1.get("advanced", {})
            ua = prof1.get("user_agent", "")
            tz = prof1.get("timezone", "")
            locale = prof1.get("locale", "")

            record_check("OS is Windows", adv.get("os") == "Windows")
            record_check("User-Agent contains Windows NT", "Windows NT" in ua)
            record_check("Timezone format is valid IANA", "/" in tz)
            record_check("Locale is valid format", "-" in locale)

            screen = adv.get("screen_resolution", "")
            record_check("Screen resolution format is valid", "x" in screen and int(screen.split("x")[0]) > 0)
            record_check("CPU cores range check", 1 <= int(adv.get("cpu_cores", 0)) <= 32)
            record_check("Memory range check", 1 <= int(adv.get("memory_gb", 0)) <= 64)
            record_check("WebGL vendor exists", adv.get("webgl_vendor") is not None)
            record_check("sec_ch_ua contains Chromium", "Chromium" in adv.get("sec_ch_ua", ""))

            # ============= T08: PROFILE LAUNCH =============
            print("\n" + "=" * 60)
            print("T08: PROFILE LAUNCH (headless)")
            print("=" * 60)

            try:
                launch_res = await launch_profile(p1_id, force_headless=True)
            except Exception as e:
                print(f"[ERROR] launch_profile({p1_id}) raised exception: {e}")
                record_check("Launch persistent context", False)
                record_check("Tracked in active_browsers", False)
                record_check("Browser-dependent T08-T10 checks skipped due to launch failure", True, is_skipped=True)
                return

            if p1_id not in active_browsers:
                print(f"[ERROR] launch_profile({p1_id}) returned but profile is not tracked in active_browsers")
                print(f"        launch result: {launch_res}")
                record_check("Launch persistent context", False)
                record_check("Tracked in active_browsers", False)
                record_check("Browser-dependent T08-T10 checks skipped due to launch failure", True, is_skipped=True)
                return

            record_check("Launch persistent context", launch_res["status"] == "success")
            record_check("Tracked in active_browsers", p1_id in active_browsers)

            bd = active_browsers[p1_id]
            page = bd.get("page")
            record_check("Page object exists", page is not None)
            record_check("Context object exists", bd.get("context") is not None)
            record_check("Playwright object exists", bd.get("playwright") is not None)

            # ============= T09: ISOLATION =============
            print("\n" + "=" * 60)
            print("T09: PROFILE ISOLATION")
            print("=" * 60)

            # Setup offline mock route for profile 1
            await bd["context"].route("https://example.com", lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<html><body>Test Page</body></html>"
            ))
            await page.goto("https://example.com", wait_until="domcontentloaded")

            await page.evaluate("document.cookie = 'qa_test_isolation=profile1; path=/;'")
            await page.evaluate("localStorage.setItem('qa_isolation', 'profile1_value')")

            cookie1 = await page.evaluate("document.cookie")
            ls1 = await page.evaluate("localStorage.getItem('qa_isolation')")

            record_check("Cookie set in profile 1", "qa_test_isolation=profile1" in cookie1)
            record_check("localStorage set in profile 1", ls1 == "profile1_value")

            # Launch profile 2
            try:
                launch_res2 = await launch_profile(p2_id, force_headless=True)
            except Exception as e:
                print(f"[ERROR] launch_profile({p2_id}) raised exception: {e}")
                record_check("Launch profile 2", False)
                record_check("Profile 2 isolation checks skipped due to launch failure", True, is_skipped=True)
                launch_res2 = None

            if launch_res2 is not None and p2_id not in active_browsers:
                print(f"[ERROR] launch_profile({p2_id}) returned but profile is not tracked in active_browsers")
                print(f"        launch result: {launch_res2}")
                record_check("Launch profile 2", False)
                record_check("Profile 2 isolation checks skipped due to launch failure", True, is_skipped=True)
            elif launch_res2 is not None:
                record_check("Launch profile 2", launch_res2["status"] == "success")

                bd2 = active_browsers[p2_id]
                page2 = bd2["page"]

                # Setup offline mock route for profile 2
                await bd2["context"].route("https://example.com", lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><body>Test Page 2</body></html>"
                ))
                await page2.goto("https://example.com", wait_until="domcontentloaded")

                cookie2 = await page2.evaluate("document.cookie")
                ls2 = await page2.evaluate("localStorage.getItem('qa_isolation')")

                record_check("Cookie isolation check (no leak)", "qa_test_isolation" not in (cookie2 or ""))
                record_check("localStorage isolation check (no leak)", ls2 != "profile1_value")

                # Close profile 2
                await close_profile(p2_id)

            # Close profile 1
            await close_profile(p1_id)

            # ============= T10: FINGERPRINT SPOOFING =============
            print("\n" + "=" * 60)
            print("T10: FINGERPRINT SPOOFING")
            print("=" * 60)

            # Relaunch profile 1
            try:
                launch_res3 = await launch_profile(p1_id, force_headless=True)
            except Exception as e:
                print(f"[ERROR] launch_profile({p1_id}) raised exception during T10 relaunch: {e}")
                record_check("Relaunch profile 1 for T10", False)
                record_check("T10 fingerprint checks skipped due to launch failure", True, is_skipped=True)
                return

            if p1_id not in active_browsers:
                print(f"[ERROR] launch_profile({p1_id}) returned for T10 but profile is not tracked in active_browsers")
                print(f"        launch result: {launch_res3}")
                record_check("Relaunch profile 1 for T10", False)
                record_check("T10 fingerprint checks skipped due to launch failure", True, is_skipped=True)
                return

            page = active_browsers[p1_id]["page"]

            webdriver = await page.evaluate("navigator.webdriver")
            record_check("navigator.webdriver spoofed to False", webdriver == False)

            cores_spoof = await page.evaluate("navigator.hardwareConcurrency")
            record_check("navigator.hardwareConcurrency spoofed correctly", cores_spoof == TEST_FP_FIXTURE["cpu_cores"])

            mem_spoof = await page.evaluate("navigator.deviceMemory")
            record_check("navigator.deviceMemory spoofed correctly", mem_spoof == TEST_FP_FIXTURE["memory_gb"])

            tz_spoof = await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
            record_check("Timezone matches configured value", tz_spoof == TEST_FP_FIXTURE["timezone"])

            lang_spoof = await page.evaluate("navigator.language")
            record_check("Locale matches configured value", lang_spoof == TEST_FP_FIXTURE["locale"])

            await close_profile(p1_id)

        finally:
            print("\n[CLEANUP] Closing QA browser profiles and background work...")
            await cleanup_browsers((p1_id, p2_id))

            for pid in (p1_id, p2_id):
                if pid is not None:
                    try:
                        test_manager.delete_profile(pid)
                    except Exception as e:
                        print(f"[WARNING] Failed to delete profile {pid}: {e}")

    # Cleanup temp directory
    temp_test_dir.cleanup()

def finalize_qa_suite():
    print("\n" + "=" * 60)
    print("QA TEST SUMMARY")
    print("=" * 60)
    print(f"Total checks: {checks_total}")
    print(f"Passed:       {checks_passed}")
    print(f"Failed:       {checks_failed}")
    print(f"Skipped:      {checks_skipped}")

    # Assert real metadata unchanged
    post_meta = read_real_metadata()
    if PRODUCTION_METADATA_SNAPSHOT != post_meta:
        print("❌ CRITICAL SAFETY GUARD: Production metadata was modified during the QA suite execution!")
        sys.exit(1)

    if checks_failed > 0 or checks_skipped > 0:
        print("\nQA STATUS: FAILED (Some checks failed or skipped)")
        sys.exit(1)
    else:
        print("\nQA STATUS: PASSED")
        sys.exit(0)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--force-fail":
        print("Forced QA failure triggered!")
        assert 1 == 2, "Deliberate forced QA failure"
        sys.exit(1)

    asyncio.run(run_qa_tests())
    finalize_qa_suite()
