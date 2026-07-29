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
                    # Helper to render canvas
                    async def render_canvas(config, width=256, height=128, transparent=False):
                        ctx_config = await bm.build_browser_launch_config(config)
                        script = ctx_config["spoofing_script"]

                        context = await browser_instance.new_context()
                        await context.add_init_script(script)
                        page = await context.new_page()

                        await page.goto("data:text/html,<html><body></body></html>")

                        if transparent:
                            js_code = f"""
                            (() => {{
                                const canvas = document.createElement('canvas');
                                canvas.width = {width};
                                canvas.height = {height};
                                const ctx = canvas.getContext('2d');
                                return Array.from(ctx.getImageData(0, 0, {width}, {height}).data);
                            }})()
                            """
                        else:
                            js_code = f"""
                            (() => {{
                                const canvas = document.createElement('canvas');
                                canvas.width = {width};
                                canvas.height = {height};
                                const ctx = canvas.getContext('2d');
                                ctx.fillStyle = 'rgb(100,150,200)';
                                ctx.fillRect(0, 0, {width}, {height});
                                return Array.from(ctx.getImageData(0, 0, {width}, {height}).data);
                            }})()
                            """

                        data = await page.evaluate(js_code)
                        await page.close()
                        await context.close()
                        return data

                    # Define configurations
                    baseline_cfg = {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "baseline"),
                        "advanced": {
                            "os": "Windows",
                            "screen_resolution": "1920x1080",
                            "canvas_noise": False
                        }
                    }

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

                    # Run Baseline
                    baseline_data = await render_canvas(baseline_cfg)

                    # Run Profile 1 (twice)
                    p1_data_1 = await render_canvas(p1_cfg)
                    p1_data_2 = await render_canvas(p1_cfg)

                    # Run Profile 2
                    p2_data = await render_canvas(p2_cfg)

                    # Run Profile 1 transparent
                    p1_trans_data = await render_canvas(p1_cfg, width=64, height=64, transparent=True)

                    # CHECK A: same-profile stability
                    same_profile_stable = (p1_data_1 == p1_data_2)
                    if same_profile_stable:
                        print("[PASS] same-profile stability")
                    else:
                        print("[FAIL] same-profile stability")
                        all_passed = False

                    # CHECK B: sparse and bounded modification
                    total_pixels = 256 * 128
                    changed_rgb_count = 0
                    all_rgb_changes_are_one = True
                    alpha_unchanged = True
                    at_least_one_rgb_changed = False

                    for i in range(len(baseline_data)):
                        channel_idx = i % 4
                        old_val = baseline_data[i]
                        new_val = p1_data_1[i]
                        if channel_idx == 3:
                            if old_val != new_val:
                                alpha_unchanged = False
                        else:
                            if old_val != new_val:
                                at_least_one_rgb_changed = True
                                changed_rgb_count += 1
                                if abs(new_val - old_val) != 1:
                                    all_rgb_changes_are_one = False

                    # nonzero modification
                    if at_least_one_rgb_changed:
                        print("[PASS] nonzero modification")
                    else:
                        print("[FAIL] nonzero modification")
                        all_passed = False

                    # one-level maximum delta
                    if all_rgb_changes_are_one:
                        print("[PASS] one-level maximum delta")
                    else:
                        print("[FAIL] one-level maximum delta")
                        all_passed = False

                    # alpha unchanged
                    if alpha_unchanged:
                        print("[PASS] alpha unchanged")
                    else:
                        print("[FAIL] alpha unchanged")
                        all_passed = False

                    # sparse modification limit
                    limit = int(total_pixels * 0.03)
                    if changed_rgb_count <= limit:
                        print("[PASS] sparse modification limit")
                    else:
                        print(f"[FAIL] sparse modification limit (changed: {changed_rgb_count} > {limit})")
                        all_passed = False

                    # array length unchanged
                    if len(p1_data_1) == len(baseline_data):
                        print("[PASS] array length unchanged")
                    else:
                        print("[FAIL] array length unchanged")
                        all_passed = False

                    # CHECK C: Profile separation and transparency
                    # different-profile separation
                    if p2_data != p1_data_1:
                        print("[PASS] different-profile separation")
                    else:
                        print("[FAIL] different-profile separation")
                        all_passed = False

                    # transparent pixels unchanged
                    transparent_pixels_ok = all(v == 0 for v in p1_trans_data)
                    if transparent_pixels_ok:
                        print("[PASS] transparent pixels unchanged")
                    else:
                        print("[FAIL] transparent pixels unchanged")
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
        # Restore sys.path
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
