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
    async def test_navigator_webdriver():
        global playwright_instance, browser_instance
        all_passed = True

        from playwright.async_api import async_playwright

        playwright_instance = await async_playwright().start()
        try:
            browser_instance = await playwright_instance.chromium.launch(headless=True)
            try:
                async def get_webdriver_info(config):
                    ctx_config = await bm.build_browser_launch_config(config)
                    script = ctx_config["spoofing_script"]

                    context = await browser_instance.new_context()
                    await context.add_init_script(script)
                    page = await context.new_page()

                    await page.goto("data:text/html,<html><body></body></html>")

                    js_code = """
                    (async () => {
                        const webdriverVal = navigator.webdriver;
                        const ownProps = Object.getOwnPropertyNames(navigator);
                        const hasWebdriverProp = ownProps.includes("webdriver");
                        return {
                            webdriver: webdriverVal,
                            hasWebdriverProp: hasWebdriverProp,
                            webdriverType: typeof webdriverVal
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
                        "canvas_noise": True
                    }
                }

                info = await get_webdriver_info(p1_cfg)

                if info["webdriver"] is False:
                    print("[PASS] navigator.webdriver === false")
                else:
                    print(f"[FAIL] navigator.webdriver === {info['webdriver']} (type: {info['webdriverType']}), expected false")
                    all_passed = False

                if not info["hasWebdriverProp"]:
                    print("[PASS] 'webdriver' not in Object.getOwnPropertyNames(navigator)")
                else:
                    print("[FAIL] 'webdriver' found in Object.getOwnPropertyNames(navigator)")
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
        return await test_navigator_webdriver()

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
