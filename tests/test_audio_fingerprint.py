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
    async def test_audio_fingerprint():
        global playwright_instance, browser_instance
        all_passed = True

        from playwright.async_api import async_playwright

        playwright_instance = await async_playwright().start()
        try:
            browser_instance = await playwright_instance.chromium.launch(headless=True)
            try:
                async def get_audio_hash(config):
                    ctx_config = await bm.build_browser_launch_config(config)
                    script = ctx_config["spoofing_script"]

                    context = await browser_instance.new_context()
                    await context.add_init_script(script)
                    page = await context.new_page()

                    await page.goto("data:text/html,<html><body></body></html>")

                    js_code = """
                    (async () => {
                        const sampleRate = 44100;
                        const length = 44100;
                        const ctx = new OfflineAudioContext(1, length, sampleRate);
                        const osc = ctx.createOscillator();
                        osc.type = "sine";
                        osc.frequency.value = 440;
                        osc.connect(ctx.destination);
                        osc.start(0);
                        const renderedBuffer = await ctx.startRendering();
                        const data = renderedBuffer.getChannelData(0);
                        let hash = 0;
                        for (let i = 0; i < data.length; i++) {
                            const bits = Math.round(data[i] * 1000000);
                            hash = Math.imul(hash ^ bits, 0x5bd1e995);
                            hash ^= hash >>> 15;
                        }
                        return hash | 0;
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

                p2_cfg = {
                    "id": "00000002-0000-0000-0000-000000000000",
                    "path": os.path.join(temp_dir.name, "00000002-0000-0000-0000-000000000000"),
                    "advanced": {
                        "os": "Windows",
                        "screen_resolution": "1920x1080",
                        "canvas_noise": True,
                        "audio_noise": True
                    }
                }

                p1_hash_1 = await get_audio_hash(p1_cfg)
                p1_hash_2 = await get_audio_hash(p1_cfg)
                p2_hash_1 = await get_audio_hash(p2_cfg)
                p2_hash_2 = await get_audio_hash(p2_cfg)

                if p1_hash_1 == p1_hash_2:
                    print(f"[PASS] Same profile produces same audio hash: {p1_hash_1}")
                else:
                    print(f"[FAIL] Same profile produced different audio hashes: {p1_hash_1} vs {p1_hash_2}")
                    all_passed = False

                if p2_hash_1 == p2_hash_2:
                    print(f"[PASS] Second profile is deterministic: {p2_hash_1}")
                else:
                    print(f"[FAIL] Second profile not deterministic: {p2_hash_1} vs {p2_hash_2}")
                    all_passed = False

                if p1_hash_1 != p2_hash_1:
                    print(f"[PASS] Different profiles produce different audio hashes: {p1_hash_1} vs {p2_hash_1}")
                else:
                    print(f"[FAIL] Different profiles produced same audio hash: {p1_hash_1}")
                    all_passed = False

                ctx_config = await bm.build_browser_launch_config(p1_cfg)
                script = ctx_config["spoofing_script"]
                context = await browser_instance.new_context()
                await context.add_init_script(script)
                page = await context.new_page()
                await page.goto("data:text/html,<html><body></body></html>")

                bounded_js = """
                (async () => {
                    const sampleRate = 44100;
                    const length = 4410;
                    const ctx = new OfflineAudioContext(1, length, sampleRate);
                    const osc = ctx.createOscillator();
                    osc.type = "sine";
                    osc.frequency.value = 440;
                    osc.connect(ctx.destination);
                    osc.start(0);
                    const renderedBuffer = await ctx.startRendering();
                    const data = renderedBuffer.getChannelData(0);
                    let maxAbs = 0;
                    for (let i = 0; i < data.length; i++) {
                        const abs = Math.abs(data[i]);
                        if (abs > maxAbs) maxAbs = abs;
                    }
                    return maxAbs;
                })()
                """

                max_val = await page.evaluate(bounded_js)
                await page.close()
                await context.close()

                if max_val <= 1.0:
                    print(f"[PASS] Audio output is bounded (max absolute value: {max_val})")
                else:
                    print(f"[FAIL] Audio output exceeded bounds (max absolute value: {max_val})")
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
        return await test_audio_fingerprint()

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
