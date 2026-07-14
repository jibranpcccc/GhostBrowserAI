import os
import sys
import tempfile
import asyncio

orig_sys_path = list(sys.path)
orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")

orig_probe_native_metadata = None
orig_get_proxy_for_profile = None

bm_module = None
pm_module = None

temp_dir = tempfile.TemporaryDirectory()
test_success = False
cleanup_success = True

playwright_instance = None
browser_instance = None

try:
    os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir.name
    os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

    import backend.browser_manager as bm
    import backend.proxy_manager as pm

    bm_module = bm
    pm_module = pm

    orig_probe_native_metadata = bm.probe_native_metadata
    orig_get_proxy_for_profile = pm.proxy_manager.get_proxy_for_profile

    async def mock_probe_native_metadata(force_headless=True):
        return {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "uadata": {
                "brands": [
                    {"brand": "Chromium", "version": "149"},
                    {"brand": "Not)A;Brand", "version": "24"}
                ],
                "mobile": False,
                "platform": "Windows",
                "architecture": "x86",
                "bitness": "64",
                "model": "",
                "platformVersion": "19.0.0",
                "uaFullVersion": "149.0.0.0",
                "fullVersionList": [
                    {"brand": "Chromium", "version": "149.0.0.0"},
                    {"brand": "Not)A;Brand", "version": "24.0.0.0"}
                ]
            }
        }

    bm.probe_native_metadata = mock_probe_native_metadata

    async def mock_get_proxy_for_profile(profile_id):
        return None

    pm.proxy_manager.get_proxy_for_profile = mock_get_proxy_for_profile

    async def run_test():
        global playwright_instance, browser_instance
        all_passed = True

        from playwright.async_api import async_playwright

        playwright_instance = await async_playwright().start()
        try:
            browser_instance = await playwright_instance.chromium.launch(headless=True)
            try:
                config = {
                    "id": "00000001-0000-0000-0000-000000000000",
                    "path": os.path.join(temp_dir.name, "00000001-0000-0000-0000-000000000000"),
                    "advanced": {
                        "os": "Windows",
                        "screen_resolution": "1920x1080",
                        "canvas_noise": True,
                        "cpu_cores": 8,
                        "memory_gb": 16,
                        "device_pixel_ratio": 1.5
                    }
                }

                ctx_config = await bm.build_browser_launch_config(config)
                script = ctx_config["spoofing_script"]

                context = await browser_instance.new_context()
                await context.add_init_script(script)
                page = await context.new_page()

                await page.goto("data:text/html,<html><body></body></html>")

                conn_exists = await page.evaluate("typeof navigator.connection !== 'undefined' && navigator.connection !== null")
                if conn_exists:
                    print("[PASS] navigator.connection exists")
                else:
                    print("[FAIL] navigator.connection does not exist")
                    all_passed = False

                downlink = await page.evaluate("navigator.connection ? navigator.connection.downlink : null")
                if isinstance(downlink, (int, float)) and downlink > 0:
                    print(f"[PASS] navigator.connection.downlink is a number > 0: {downlink}")
                else:
                    print(f"[FAIL] navigator.connection.downlink is invalid: {downlink}")
                    all_passed = False

                rtt = await page.evaluate("navigator.connection ? navigator.connection.rtt : null")
                if isinstance(rtt, (int, float)) and rtt >= 0:
                    print(f"[PASS] navigator.connection.rtt is a number >= 0: {rtt}")
                else:
                    print(f"[FAIL] navigator.connection.rtt is invalid: {rtt}")
                    all_passed = False

                effective_type = await page.evaluate("navigator.connection ? navigator.connection.effectiveType : null")
                if isinstance(effective_type, str):
                    print(f"[PASS] navigator.connection.effectiveType is a string: '{effective_type}'")
                else:
                    print(f"[FAIL] navigator.connection.effectiveType is not a string: {effective_type}")
                    all_passed = False

                save_data = await page.evaluate("navigator.connection ? navigator.connection.saveData : null")
                if save_data is False:
                    print("[PASS] navigator.connection.saveData is false")
                else:
                    print(f"[FAIL] navigator.connection.saveData is not false: {save_data}")
                    all_passed = False

                await page.reload()

                downlink2 = await page.evaluate("navigator.connection ? navigator.connection.downlink : null")
                rtt2 = await page.evaluate("navigator.connection ? navigator.connection.rtt : null")
                et2 = await page.evaluate("navigator.connection ? navigator.connection.effectiveType : null")

                if downlink == downlink2:
                    print(f"[PASS] connection.downlink stable across reload: {downlink} == {downlink2}")
                else:
                    print(f"[FAIL] connection.downlink changed: {downlink} -> {downlink2}")
                    all_passed = False

                if rtt == rtt2:
                    print(f"[PASS] connection.rtt stable across reload: {rtt} == {rtt2}")
                else:
                    print(f"[FAIL] connection.rtt changed: {rtt} -> {rtt2}")
                    all_passed = False

                if effective_type == et2:
                    print(f"[PASS] connection.effectiveType stable across reload: '{effective_type}' == '{et2}'")
                else:
                    print(f"[FAIL] connection.effectiveType changed: '{effective_type}' -> '{et2}'")
                    all_passed = False

                await page.close()
                await context.close()
                return all_passed
            finally:
                if browser_instance:
                    await browser_instance.close()
                    browser_instance = None
        finally:
            if playwright_instance:
                await playwright_instance.stop()
                playwright_instance = None
except Exception as e:
    print(f"[FAIL] Test execution threw exception: {e}")
    test_success = False
finally:
    browser_cleanup_ok = (playwright_instance is None and browser_instance is None)
    if browser_cleanup_ok:
        print("[PASS] browser cleanup")
    else:
        print("[FAIL] browser cleanup")
        cleanup_success = False

    monkeypatch_ok = True
    if bm_module is not None and orig_probe_native_metadata is not None:
        bm_module.probe_native_metadata = orig_probe_native_metadata
        if bm_module.probe_native_metadata is not orig_probe_native_metadata:
            monkeypatch_ok = False
    else:
        monkeypatch_ok = False

    if pm_module is not None and orig_get_proxy_for_profile is not None:
        pm_module.proxy_manager.get_proxy_for_profile = orig_get_proxy_for_profile
        if pm_module.proxy_manager.get_proxy_for_profile is not orig_get_proxy_for_profile:
            monkeypatch_ok = False
    else:
        monkeypatch_ok = False

    if monkeypatch_ok:
        print("[PASS] monkeypatch restoration")
    else:
        print("[FAIL] monkeypatch restoration")
        cleanup_success = False

    env_ok = True
    sys.path = list(orig_sys_path)
    if sys.path != orig_sys_path:
        env_ok = False

    if orig_dir is not None:
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = orig_dir
    else:
        os.environ.pop("GHOSTBROWSER_TEST_PROFILES_DIR", None)

    if orig_env is not None:
        os.environ["GHOSTBROWSER_TEST_ENV"] = orig_env
    else:
        os.environ.pop("GHOSTBROWSER_TEST_ENV", None)

    if os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR") != orig_dir or os.environ.get("GHOSTBROWSER_TEST_ENV") != orig_env:
        env_ok = False

    if env_ok:
        print("[PASS] environment restoration")
    else:
        print("[FAIL] environment restoration")
        cleanup_success = False

    try:
        temp_dir.cleanup()
    except Exception:
        pass

if __name__ == "__main__":
    if test_success and cleanup_success:
        sys.exit(0)
    else:
        sys.exit(1)
