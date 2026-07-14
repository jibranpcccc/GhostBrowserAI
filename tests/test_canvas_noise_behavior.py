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
    async def test_canvas_noise_behavior():
        global playwright_instance, browser_instance
        all_passed = True

        from playwright.async_api import async_playwright

        playwright_instance = await async_playwright().start()
        try:
            browser_instance = await playwright_instance.chromium.launch(headless=True)
            try:
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

                baseline_data = await render_canvas(baseline_cfg)
                p1_data_1 = await render_canvas(p1_cfg)
                p1_data_2 = await render_canvas(p1_cfg)
                p2_data = await render_canvas(p2_cfg)
                p1_trans_data = await render_canvas(p1_cfg, width=64, height=64, transparent=True)

                same_profile_stable = (p1_data_1 == p1_data_2)
                if same_profile_stable:
                    print("[PASS] same-profile stability")
                else:
                    print("[FAIL] same-profile stability")
                    all_passed = False

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

                if at_least_one_rgb_changed:
                    print("[PASS] nonzero modification")
                else:
                    print("[FAIL] nonzero modification")
                    all_passed = False

                if all_rgb_changes_are_one:
                    print("[PASS] one-level maximum delta")
                else:
                    print("[FAIL] one-level maximum delta")
                    all_passed = False

                if alpha_unchanged:
                    print("[PASS] alpha unchanged")
                else:
                    print("[FAIL] alpha unchanged")
                    all_passed = False

                limit = int(total_pixels * 0.03)
                if changed_rgb_count <= limit:
                    print("[PASS] sparse modification limit")
                else:
                    print(f"[FAIL] sparse modification limit (changed: {changed_rgb_count} > {limit})")
                    all_passed = False

                if len(p1_data_1) == len(baseline_data):
                    print("[PASS] array length unchanged")
                else:
                    print("[FAIL] array length unchanged")
                    all_passed = False

                if p2_data != p1_data_1:
                    print("[PASS] different-profile separation")
                else:
                    print("[FAIL] different-profile separation")
                    all_passed = False

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

    async def run_test():
        return await test_canvas_noise_behavior()

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
