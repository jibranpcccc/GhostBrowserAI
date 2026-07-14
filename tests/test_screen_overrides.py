import os
import sys
import tempfile
import asyncio
import pytest

orig_sys_path = list(sys.path)
orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")

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

    @pytest.mark.asyncio
    async def test_screen_overrides():
        global playwright_instance, browser_instance
        all_passed = True

        from playwright.async_api import async_playwright

        playwright_instance = await async_playwright().start()
        try:
            browser_instance = await playwright_instance.chromium.launch(headless=True)
            try:
                async def get_overrides(config):
                    ctx_config = await bm.build_browser_launch_config(config)
                    script = ctx_config["spoofing_script"]

                    context = await browser_instance.new_context()
                    await context.add_init_script(script)
                    page = await context.new_page()

                    await page.goto("data:text/html,<html><body></body></html>")

                    js_code = """
                    (async () => {
                        return {
                            colorDepth: window.screen.colorDepth,
                            pixelDepth: window.screen.pixelDepth,
                            hardwareConcurrency: navigator.hardwareConcurrency,
                            deviceMemory: navigator.deviceMemory,
                            devicePixelRatio: window.devicePixelRatio,
                            screenWidth: window.screen.width,
                            screenHeight: window.screen.height
                        };
                    })()
                    """

                    result = await page.evaluate(js_code)
                    await page.close()
                    await context.close()
                    return result

                p1_cfg = {
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

                overrides = await get_overrides(p1_cfg)

                if overrides["colorDepth"] == 24:
                    print(f"[PASS] screen.colorDepth == 24")
                else:
                    print(f"[FAIL] screen.colorDepth == {overrides['colorDepth']}, expected 24")
                    all_passed = False

                if overrides["pixelDepth"] == 24:
                    print(f"[PASS] screen.pixelDepth == 24")
                else:
                    print(f"[FAIL] screen.pixelDepth == {overrides['pixelDepth']}, expected 24")
                    all_passed = False

                if isinstance(overrides["hardwareConcurrency"], (int, float)):
                    print(f"[PASS] navigator.hardwareConcurrency is a number: {overrides['hardwareConcurrency']}")
                else:
                    print(f"[FAIL] navigator.hardwareConcurrency is not a number: {overrides['hardwareConcurrency']}")
                    all_passed = False

                if isinstance(overrides["deviceMemory"], (int, float)):
                    print(f"[PASS] navigator.deviceMemory is a number: {overrides['deviceMemory']}")
                else:
                    print(f"[FAIL] navigator.deviceMemory is not a number: {overrides['deviceMemory']}")
                    all_passed = False

                if overrides["devicePixelRatio"] == 1.5:
                    print(f"[PASS] devicePixelRatio == 1.5")
                else:
                    print(f"[FAIL] devicePixelRatio == {overrides['devicePixelRatio']}, expected 1.5")
                    all_passed = False

                return all_passed
            finally:
                if browser_instance:
                    await browser_instance.close()
                    browser_instance = None
        finally:
            if playwright_instance:
                await playwright_instance.stop()
                playwright_instance = None

    async def standalone():
        return await test_screen_overrides()

    test_success = asyncio.run(standalone())
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

    sys.path = list(orig_sys_path)
    if orig_dir is not None:
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = orig_dir
    else:
        os.environ.pop("GHOSTBROWSER_TEST_PROFILES_DIR", None)
    if orig_env is not None:
        os.environ["GHOSTBROWSER_TEST_ENV"] = orig_env
    if os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR") != orig_dir or os.environ.get("GHOSTBROWSER_TEST_ENV") != orig_env:
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
