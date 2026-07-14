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
    async def test_function_tostring_proxy():
        global playwright_instance, browser_instance
        all_passed = True

        from playwright.async_api import async_playwright

        playwright_instance = await async_playwright().start()
        try:
            browser_instance = await playwright_instance.chromium.launch(headless=True)
            try:
                async def get_tostring_info(config):
                    ctx_config = await bm.build_browser_launch_config(config)
                    script = ctx_config["spoofing_script"]

                    context = await browser_instance.new_context()
                    await context.add_init_script(script)
                    page = await context.new_page()

                    await page.goto("data:text/html,<html><body></body></html>")

                    js_code = """
                    (async () => {
                        const createElStr = document.createElement.toString();
                        const createElName = document.createElement.name;
                        const createElLen = document.createElement.length;

                        const getImageDataStr = CanvasRenderingContext2D.prototype.getImageData.toString();
                        const getImageDataName = CanvasRenderingContext2D.prototype.getImageData.name;
                        const getImageDataLen = CanvasRenderingContext2D.prototype.getImageData.length;

                        const startRenderingStr = OfflineAudioContext.prototype.startRendering.toString();
                        const startRenderingName = OfflineAudioContext.prototype.startRendering.name;
                        const startRenderingLen = OfflineAudioContext.prototype.startRendering.length;

                        return {
                            createElStr: createElStr,
                            createElName: createElName,
                            createElLen: createElLen,
                            getImageDataStr: getImageDataStr,
                            getImageDataName: getImageDataName,
                            getImageDataLen: getImageDataLen,
                            startRenderingStr: startRenderingStr,
                            startRenderingName: startRenderingName,
                            startRenderingLen: startRenderingLen
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
                        "audio_noise": True
                    }
                }

                info = await get_tostring_info(p1_cfg)

                if "[native code]" in info["createElStr"]:
                    print("[PASS] document.createElement.toString() contains [native code]")
                else:
                    print(f"[FAIL] document.createElement.toString() = {info['createElStr']}")
                    all_passed = False

                if info["createElName"] == "createElement":
                    print("[PASS] document.createElement.name == 'createElement'")
                else:
                    print(f"[FAIL] document.createElement.name == '{info['createElName']}', expected 'createElement'")
                    all_passed = False

                if info["createElLen"] == 1:
                    print("[PASS] document.createElement.length == 1")
                else:
                    print(f"[FAIL] document.createElement.length == {info['createElLen']}, expected 1")
                    all_passed = False

                if "[native code]" in info["getImageDataStr"]:
                    print("[PASS] getImageData.toString() contains [native code]")
                else:
                    print(f"[FAIL] getImageData.toString() = {info['getImageDataStr']}")
                    all_passed = False

                if info["getImageDataName"] == "getImageData":
                    print("[PASS] getImageData.name == 'getImageData'")
                else:
                    print(f"[FAIL] getImageData.name == '{info['getImageDataName']}'")
                    all_passed = False

                if info["getImageDataLen"] == 4:
                    print("[PASS] getImageData.length == 4")
                else:
                    print(f"[FAIL] getImageData.length == {info['getImageDataLen']}, expected 4")
                    all_passed = False

                if "[native code]" in info["startRenderingStr"]:
                    print("[PASS] startRendering.toString() contains [native code]")
                else:
                    print(f"[FAIL] startRendering.toString() = {info['startRenderingStr']}")
                    all_passed = False

                if info["startRenderingName"] == "startRendering":
                    print("[PASS] startRendering.name == 'startRendering'")
                else:
                    print(f"[FAIL] startRendering.name == '{info['startRenderingName']}'")
                    all_passed = False

                if info["startRenderingLen"] == 0:
                    print("[PASS] startRendering.length == 0")
                else:
                    print(f"[FAIL] startRendering.length == {info['startRenderingLen']}, expected 0")
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
        return await test_function_tostring_proxy()

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
