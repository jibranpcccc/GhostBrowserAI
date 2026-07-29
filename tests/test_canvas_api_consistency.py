import os
import sys
import tempfile
import asyncio
import re

def main():

    # 1. Initialize variables safely for finally block
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

        sys.path.append(os.getcwd())

        # Import modules
        import backend.browser_manager as bm
        import backend.proxy_manager as pm

        bm_module = bm
        pm_module = pm

        # Store originals
        orig_probe_native_metadata = bm.probe_native_metadata
        orig_get_proxy_for_profile = pm.proxy_manager.get_proxy_for_profile

        # Monkeypatch
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
                    # Helper to render canvas and return data
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

                            // Deterministic drawing
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

                    # Zero-width canvas test
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

                    # Define configurations
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

                    # Render Profile 1 (twice)
                    p1_run_1 = await get_canvas_outputs(p1_cfg)
                    p1_run_2 = await get_canvas_outputs(p1_cfg)

                    # Render Profile 2 (once)
                    p2_run = await get_canvas_outputs(p2_cfg)

                    # Render zero-width canvas
                    zero_res = await test_zero_width(p1_cfg)
                    zero_dataurl_ok = (zero_res["dataURL"] == "data:,")
                    zero_toblob_ok = (zero_res["blobResult"] == "IS_NULL")

                    # Profile 1 and Profile 2 have different getImageData arrays
                    if p1_run_1["imgData"] != p2_run["imgData"]:
                        print("[PASS] Profile 1 and Profile 2 have different getImageData arrays.")
                    else:
                        print("[FAIL] Profile 1 and Profile 2 have different getImageData arrays.")
                        all_passed = False

                    # Profile 1 and Profile 2 have different toDataURL strings
                    if p1_run_1["dataURL"] != p2_run["dataURL"]:
                        print("[PASS] Profile 1 and Profile 2 have different toDataURL strings.")
                    else:
                        print("[FAIL] Profile 1 and Profile 2 have different toDataURL strings.")
                        all_passed = False

                    # Profile 1 and Profile 2 have different toBlob byte arrays
                    if p1_run_1["blobBytes"] != p2_run["blobBytes"]:
                        print("[PASS] Profile 1 and Profile 2 have different toBlob byte arrays.")
                    else:
                        print("[FAIL] Profile 1 and Profile 2 have different toBlob byte arrays.")
                        all_passed = False

                    # Two Profile 1 runs remain identical across all three APIs
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

                    # A zero-width canvas returns exactly data:, from toDataURL
                    if zero_dataurl_ok:
                        print("[PASS] A zero-width canvas returns exactly `data:,` from toDataURL.")
                    else:
                        print(f"[FAIL] A zero-width canvas returns exactly `data:,` from toDataURL (got: {zero_res['dataURL']}).")
                        all_passed = False

                    # A zero-width canvas invokes the toBlob callback with null
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

        test_success = asyncio.run(run_test())
    except Exception as e:
        print(f"[FAIL] Test execution encountered error: {e}")
        test_success = False

    finally:
        # browser cleanup
        browser_cleanup_ok = (playwright_instance is None and browser_instance is None)
        if browser_cleanup_ok:
            print("[PASS] browser cleanup")
        else:
            print("[FAIL] browser cleanup")
            cleanup_success = False

        # Restore monkeypatches
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

        # Restore environment variables
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

        # Clean up TemporaryDirectory
        try:
            temp_dir.cleanup()
        except Exception:
            pass

        if test_success and cleanup_success:
            return True

        else:
            return False


if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
