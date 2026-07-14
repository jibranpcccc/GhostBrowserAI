import os
import sys
import tempfile
import asyncio
import re
import pytest

orig_sys_path = list(sys.path)
orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")

bm_module = None

temp_dir = tempfile.TemporaryDirectory()
test_success = False
cleanup_success = True

playwright_instance = None
browser_instance = None

try:
    os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir.name
    os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

    sys.path.append(os.getcwd())

    import backend.browser_manager as bm

    bm_module = bm

    @pytest.mark.asyncio
    async def test_canvas_api_consistency():
        global playwright_instance, browser_instance
        all_passed = True

        from playwright.async_api import async_playwright

        playwright_instance = await async_playwright().start()
        try:
            browser_instance = await playwright_instance.chromium.launch(headless=True)
            try:
                async def get_canvas_outputs(config):
                    ctx_config = await bm.build_browser_launch_config(config)
                    script = ctx_config["spoofing_script"]

                    context = await browser_instance.new_context()
                    await context.add_init_script(script)
                    page = await context.new_page()

                    await page.goto("data:text/html,<html><body></body></html>")

                    js_code = """
                    (async () => {
                        const canvas = document.createElement('canvas');
                        canvas.width = 256;
                        canvas.height = 128;
                        const ctx = canvas.getContext('2d');
                        ctx.fillStyle = 'rgb(100,150,200)';
                        ctx.fillRect(0, 0, 256, 128);

                        ctx.fillStyle = 'rgb(255, 0, 0)';
                        ctx.fillRect(10, 10, 50, 50);
                        ctx.font = '20px Arial';
                        ctx.fillText('Test Text', 100, 50);

                        const imgData = Array.from(ctx.getImageData(0, 0, 256, 128).data);
                        const dataURL = canvas.toDataURL("image/png");

                        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
                        const buffer = await blob.arrayBuffer();
                        const blobBytes = Array.from(new Uint8Array(buffer));

                        return { imgData, dataURL, blobBytes };
                    })()
                    """

                    data = await page.evaluate(js_code)
                    await page.close()
                    await context.close()
                    return data

                async def test_zero_width(config):
                    ctx_config = await bm.build_browser_launch_config(config)
                    script = ctx_config["spoofing_script"]

                    context = await browser_instance.new_context()
                    await context.add_init_script(script)
                    page = await context.new_page()

                    await page.goto("data:text/html,<html><body></body></html>")

                    js_code = """
                    (async () => {
                        const canvas = document.createElement('canvas');
                        canvas.width = 0;
                        canvas.height = 128;

                        const dataURL = canvas.toDataURL("image/png");

                        const blobResult = await new Promise(resolve => {
                            try {
                                canvas.toBlob((b) => {
                                    resolve(b === null ? "IS_NULL" : "NOT_NULL");
                                }, "image/png");
                            } catch (e) {
                                resolve("THREW_ERROR: " + e.message);
                            }
                        });

                        return { dataURL, blobResult };
                    })()
                    """
                    res = await page.evaluate(js_code)
                    await page.close()
                    await context.close()
                    return res

                p1_cfg = {
                    "id": "00000001-0000-0000-0000-000000000000",
                    "path": os.path.join(temp_dir.name, "00000001-0000-0000-0000-000000000000"),
                    "advanced": {
                        "os": "Windows",
                        "screen_resolution": "1920x1080",
                        "canvas_noise": True
                    }
                }

                p2_cfg = {
                    "id": "00000002-0000-0000-0000-000000000000",
                    "path": os.path.join(temp_dir.name, "00000002-0000-0000-0000-000000000000"),
                    "advanced": {
                        "os": "Windows",
                        "screen_resolution": "1920x1080",
                        "canvas_noise": True
                    }
                }

                p1_run_1 = await get_canvas_outputs(p1_cfg)
                p1_run_2 = await get_canvas_outputs(p1_cfg)
                p2_run = await get_canvas_outputs(p2_cfg)
                zero_res = await test_zero_width(p1_cfg)
                zero_dataurl_ok = (zero_res["dataURL"] == "data:,")
                zero_toblob_ok = (zero_res["blobResult"] == "IS_NULL")

                if p1_run_1["imgData"] != p2_run["imgData"]:
                    print("[PASS] Profile 1 and Profile 2 have different getImageData arrays.")
                else:
                    print("[FAIL] Profile 1 and Profile 2 have different getImageData arrays.")
                    all_passed = False

                if p1_run_1["dataURL"] != p2_run["dataURL"]:
                    print("[PASS] Profile 1 and Profile 2 have different toDataURL strings.")
                else:
                    print("[FAIL] Profile 1 and Profile 2 have different toDataURL strings.")
                    all_passed = False

                if p1_run_1["blobBytes"] != p2_run["blobBytes"]:
                    print("[PASS] Profile 1 and Profile 2 have different toBlob byte arrays.")
                else:
                    print("[FAIL] Profile 1 and Profile 2 have different toBlob byte arrays.")
                    all_passed = False

                p1_identical = (
                    p1_run_1["imgData"] == p1_run_2["imgData"] and
                    p1_run_1["dataURL"] == p1_run_2["dataURL"] and
                    p1_run_1["blobBytes"] == p1_run_2["blobBytes"]
                )
                if p1_identical:
                    print("[PASS] Two Profile 1 runs remain identical across all three APIs.")
                else:
                    print("[FAIL] Two Profile 1 runs remain identical across all three APIs.")
                    all_passed = False

                if zero_dataurl_ok:
                    print("[PASS] A zero-width canvas returns exactly `data:,` from toDataURL.")
                else:
                    print(f"[FAIL] A zero-width canvas returns exactly `data:,` from toDataURL (got: {zero_res['dataURL']}).")
                    all_passed = False

                if zero_toblob_ok:
                    print("[PASS] A zero-width canvas invokes the toBlob callback with null.")
                else:
                    print(f"[FAIL] A zero-width canvas invokes the toBlob callback with null (got: {zero_res['blobResult']}).")
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

    async def run_test():
        return await test_canvas_api_consistency()

    test_success = asyncio.run(run_test())
except Exception as e:
    print(f"[FAIL] Test execution encountered error: {e}")
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

if __name__ == '__main__':
    if test_success and cleanup_success:
        sys.exit(0)
    else:
        sys.exit(1)
