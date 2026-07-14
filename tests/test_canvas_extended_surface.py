import os
import sys
import tempfile
import asyncio
import pytest

orig_sys_path = list(sys.path)
orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")

orig_get_proxy_for_profile = None
bm_module = None
pm_module = None

_temp_dir = tempfile.TemporaryDirectory()
test_success = False
cleanup_success = True

playwright_instance = None
browser_instance = None

os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = _temp_dir.name
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

sys.path.append(os.getcwd())

import backend.browser_manager as bm
import backend.proxy_manager as pm

bm_module = bm
pm_module = pm

orig_get_proxy_for_profile = pm.proxy_manager.get_proxy_for_profile


async def mock_get_proxy_for_profile(profile_id):
    return None


pm.proxy_manager.get_proxy_for_profile = mock_get_proxy_for_profile

DETERMINISTIC_DRAW_JS = """
    (function(canvas) {
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = 'rgb(100,150,200)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = 'rgb(255,0,0)';
        ctx.fillRect(10, 10, 40, 20);
        ctx.fillStyle = 'rgb(0,200,80)';
        ctx.fillRect(60, 30, 30, 10);
    })
"""


async def make_context(browser, script=None):
    ctx = await browser.new_context()
    if script:
        await ctx.add_init_script(script)
    page = await ctx.new_page()
    await page.goto("data:text/html,<html><body></body></html>")
    return ctx, page


async def get_all_outputs(page, w=128, h=64):
    return await page.evaluate(f"""
    (async () => {{
        const canvas = document.createElement('canvas');
        canvas.width = {w};
        canvas.height = {h};
        const draw = {DETERMINISTIC_DRAW_JS};
        draw(canvas);
        const ctx = canvas.getContext('2d');
        const imgData = Array.from(ctx.getImageData(0,0,{w},{h}).data);
        const dataURL = canvas.toDataURL("image/png");
        const blob = await new Promise(r => canvas.toBlob(r,'image/png'));
        const buf  = await blob.arrayBuffer();
        const blobBytes = Array.from(new Uint8Array(buf));
        return {{imgData, dataURL, blobBytes}};
    }})()
    """)


@pytest.mark.asyncio
async def test_canvas_extended_surface():
    global playwright_instance, browser_instance
    all_passed = True

    from playwright.async_api import async_playwright
    playwright_instance = await async_playwright().start()
    try:
        browser_instance = await playwright_instance.chromium.launch(headless=True)
        try:
            native_cfg = {
                "id": "00000000-0000-0000-0000-000000000000",
                "path": os.path.join(_temp_dir.name, "native"),
                "advanced": {"os": "Windows", "screen_resolution": "1920x1080", "canvas_noise": False},
            }
            no_noise_cfg = {
                "id": "00000000-0000-0000-0000-000000000000",
                "path": os.path.join(_temp_dir.name, "no_noise"),
                "advanced": {"os": "Windows", "screen_resolution": "1920x1080", "canvas_noise": False},
            }
            p1_cfg = {
                "id": "00000001-0000-0000-0000-000000000000",
                "path": os.path.join(_temp_dir.name, "p1"),
                "advanced": {"os": "Windows", "screen_resolution": "1920x1080", "canvas_noise": True},
            }
            p2_cfg = {
                "id": "00000002-0000-0000-0000-000000000000",
                "path": os.path.join(_temp_dir.name, "p2"),
                "advanced": {"os": "Windows", "screen_resolution": "1920x1080", "canvas_noise": True},
            }

            native_built = await bm.build_browser_launch_config(native_cfg)
            no_noise_built = await bm.build_browser_launch_config(no_noise_cfg)
            p1_built = await bm.build_browser_launch_config(p1_cfg)
            p2_built = await bm.build_browser_launch_config(p2_cfg)

            ctx_p1, pg_p1 = await make_context(browser_instance, p1_built["spoofing_script"])
            try:
                partial_check = await pg_p1.evaluate(f"""
                (() => {{
                    const canvas = document.createElement('canvas');
                    canvas.width = 128; canvas.height = 64;
                    const draw = {DETERMINISTIC_DRAW_JS};
                    draw(canvas);
                    const ctx = canvas.getContext('2d');
                    const full    = Array.from(ctx.getImageData(0,0,128,64).data);
                    const partial = Array.from(ctx.getImageData(16,8,64,32).data);
                    const stride = 128 * 4;
                    const extracted = [];
                    for (let row = 0; row < 32; row++) {{
                        for (let col = 0; col < 64; col++) {{
                            const fi = ((8 + row) * 128 + (16 + col)) * 4;
                            extracted.push(full[fi], full[fi+1], full[fi+2], full[fi+3]);
                        }}
                    }}
                    const match = partial.every((v,i) => v === extracted[i]);
                    return {{match, partialLen: partial.length, extractedLen: extracted.length}};
                }})()
                """)
                assert partial_check["match"], f"CHECK 1 failed: partialLen={partial_check['partialLen']}, extractedLen={partial_check['extractedLen']}"

                stability_check = await pg_p1.evaluate(f"""
                (() => {{
                    const canvas = document.createElement('canvas');
                    canvas.width = 128; canvas.height = 64;
                    const draw = {DETERMINISTIC_DRAW_JS};
                    draw(canvas);
                    const ctx = canvas.getContext('2d');
                    const a = Array.from(ctx.getImageData(16,8,64,32).data);
                    const b = Array.from(ctx.getImageData(16,8,64,32).data);
                    return a.every((v,i) => v === b[i]);
                }})()
                """)
                assert stability_check, "CHECK 2 failed: partial-region instability"
            finally:
                await pg_p1.close()
                await ctx_p1.close()

            ctx_native, pg_native = await make_context(browser_instance)
            ctx_p1b, pg_p1b = await make_context(browser_instance, p1_built["spoofing_script"])
            try:
                desc_result = await pg_native.evaluate("""
                (() => {
                    const desc = (obj, prop) => {
                        const d = Object.getOwnPropertyDescriptor(obj, prop);
                        return d ? {writable: d.writable, enumerable: d.enumerable, configurable: d.configurable} : null;
                    };
                    return {
                        getImageData: desc(CanvasRenderingContext2D.prototype, 'getImageData'),
                        toDataURL: desc(HTMLCanvasElement.prototype, 'toDataURL'),
                        toBlob: desc(HTMLCanvasElement.prototype, 'toBlob')
                    };
                })()
                """)
                desc_protected = await pg_p1b.evaluate("""
                (() => {
                    const desc = (obj, prop) => {
                        const d = Object.getOwnPropertyDescriptor(obj, prop);
                        return d ? {writable: d.writable, enumerable: d.enumerable, configurable: d.configurable} : null;
                    };
                    return {
                        getImageData: desc(CanvasRenderingContext2D.prototype, 'getImageData'),
                        toDataURL: desc(HTMLCanvasElement.prototype, 'toDataURL'),
                        toBlob: desc(HTMLCanvasElement.prototype, 'toBlob')
                    };
                })()
                """)
                for method in ["getImageData", "toDataURL", "toBlob"]:
                    n = desc_result.get(method)
                    p = desc_protected.get(method)
                    assert n is not None and p is not None, f"CHECK 3: descriptor for {method} is None"
                    assert n == p, f"CHECK 3: descriptor mismatch for {method}: native={n} protected={p}"

                name_len_native = await pg_native.evaluate("""
                (() => ({
                    getImageData: {name: CanvasRenderingContext2D.prototype.getImageData.name, length: CanvasRenderingContext2D.prototype.getImageData.length},
                    toDataURL: {name: HTMLCanvasElement.prototype.toDataURL.name, length: HTMLCanvasElement.prototype.toDataURL.length},
                    toBlob: {name: HTMLCanvasElement.prototype.toBlob.name, length: HTMLCanvasElement.prototype.toBlob.length}
                }))()
                """)
                name_len_protected = await pg_p1b.evaluate("""
                (() => ({
                    getImageData: {name: CanvasRenderingContext2D.prototype.getImageData.name, length: CanvasRenderingContext2D.prototype.getImageData.length},
                    toDataURL: {name: HTMLCanvasElement.prototype.toDataURL.name, length: HTMLCanvasElement.prototype.toDataURL.length},
                    toBlob: {name: HTMLCanvasElement.prototype.toBlob.name, length: HTMLCanvasElement.prototype.toBlob.length}
                }))()
                """)
                for method in ["getImageData", "toDataURL", "toBlob"]:
                    n = name_len_native.get(method, {})
                    p = name_len_protected.get(method, {})
                    assert n.get("name") == p.get("name") and n.get("length") == p.get("length"), f"CHECK 4: name/length mismatch for {method}"

                tostring_result = await pg_p1b.evaluate("""
                (() => {
                    const methods = {
                        getImageData: CanvasRenderingContext2D.prototype.getImageData,
                        toDataURL: HTMLCanvasElement.prototype.toDataURL,
                        toBlob: HTMLCanvasElement.prototype.toBlob
                    };
                    const results = {};
                    for (const [name, fn] of Object.entries(methods)) {
                        const str = Function.prototype.toString.call(fn);
                        results[name] = {
                            hasNativeCode: str.includes('[native code]'),
                            exposesWrapper: str.includes('makeNative') || str.includes('applyCanvasNoise') || str.includes('makeNoisedCanvasClone'),
                            hasName: str.includes(name)
                        };
                    }
                    return results;
                })()
                """)
                for method, r in tostring_result.items():
                    assert r["hasNativeCode"], f"CHECK 5: toString for {method} does not contain [native code]"
                    assert not r["exposesWrapper"], f"CHECK 5: toString for {method} exposes wrapper implementation"
                    assert r["hasName"], f"CHECK 5: toString for {method} does not contain function name"
            finally:
                await pg_native.close()
                await ctx_native.close()
                await pg_p1b.close()
                await ctx_p1b.close()

            ctx_nat2, pg_nat2 = await make_context(browser_instance)
            ctx_nn, pg_nn = await make_context(browser_instance, no_noise_built["spoofing_script"])
            try:
                nat_out = await get_all_outputs(pg_nat2)
                nn_out = await get_all_outputs(pg_nn)
                assert (
                    nat_out["imgData"] == nn_out["imgData"]
                    and nat_out["dataURL"] == nn_out["dataURL"]
                    and nat_out["blobBytes"] == nn_out["blobBytes"]
                ), "CHECK 6: canvas_noise=False differs from native"
            finally:
                await pg_nat2.close()
                await ctx_nat2.close()
                await pg_nn.close()
                await ctx_nn.close()

            ctx_q, pg_q = await make_context(browser_instance, p1_built["spoofing_script"])
            try:
                q_result = await pg_q.evaluate("""
                (async () => {
                    const canvas = document.createElement('canvas');
                    canvas.width = 64; canvas.height = 64;
                    const ctx = canvas.getContext('2d');
                    ctx.fillStyle = 'rgb(200,100,50)';
                    ctx.fillRect(0, 0, 64, 64);
                    const pngURL   = canvas.toDataURL("image/png");
                    const jpegURL  = canvas.toDataURL("image/jpeg");
                    const jpegURLq1 = canvas.toDataURL("image/jpeg", 0.20);
                    const jpegURLq2a = canvas.toDataURL("image/jpeg", 0.90);
                    const jpegURLq2b = canvas.toDataURL("image/jpeg", 0.90);
                    const pngBlob   = await new Promise(r => canvas.toBlob(r, "image/png"));
                    const jpegBlob  = await new Promise(r => canvas.toBlob(r, "image/jpeg"));
                    return {
                        pngURLOk: pngURL.startsWith("data:image/png"),
                        jpegURLOk: jpegURL.startsWith("data:image/jpeg"),
                        pngBlobType: pngBlob ? pngBlob.type : null,
                        jpegBlobType: jpegBlob ? jpegBlob.type : null,
                        qualityDiffers: jpegURLq1 !== jpegURLq2a,
                        qualityStable: jpegURLq2a === jpegURLq2b
                    };
                })()
                """)
                assert q_result["pngURLOk"], "CHECK 7: PNG toDataURL does not start with data:image/png"
                assert q_result["jpegURLOk"], "CHECK 7: JPEG toDataURL does not start with data:image/jpeg"
                assert q_result["pngBlobType"] == "image/png", f"CHECK 7: PNG toBlob type={q_result['pngBlobType']}"
                assert q_result["jpegBlobType"] == "image/jpeg", f"CHECK 7: JPEG toBlob type={q_result['jpegBlobType']}"
                assert q_result["qualityDiffers"], "CHECK 7: JPEG quality 0.20 same as 0.90"
                assert q_result["qualityStable"], "CHECK 7: JPEG quality 0.90 not stable"
            finally:
                await pg_q.close()
                await ctx_q.close()

            ctx_if1a, pg_if1a = await make_context(browser_instance, p1_built["spoofing_script"])
            ctx_if1b, pg_if1b = await make_context(browser_instance, p1_built["spoofing_script"])
            ctx_if2, pg_if2 = await make_context(browser_instance, p2_built["spoofing_script"])
            try:
                iframe_js = """
                (async () => {
                    const iframe = document.createElement('iframe');
                    iframe.srcdoc = '<!DOCTYPE html><html><body></body></html>';
                    document.body.appendChild(iframe);
                    await new Promise(r => { iframe.onload = r; });
                    const idoc = iframe.contentDocument;
                    const canvas = idoc.createElement('canvas');
                    canvas.width = 128; canvas.height = 64;
                    idoc.body.appendChild(canvas);
                    const ctx = canvas.getContext('2d');
                    ctx.fillStyle = 'rgb(100,150,200)';
                    ctx.fillRect(0,0,128,64);
                    ctx.fillStyle = 'rgb(255,0,0)';
                    ctx.fillRect(10,10,40,20);
                    const imgData = Array.from(ctx.getImageData(0,0,128,64).data);
                    const dataURL = canvas.toDataURL('image/png');
                    const blob = await new Promise(r => canvas.toBlob(r,'image/png'));
                    const buf = await blob.arrayBuffer();
                    const blobBytes = Array.from(new Uint8Array(buf));
                    return {imgData, dataURL, blobBytes};
                })()
                """
                iframe_p1a = await pg_if1a.evaluate(iframe_js)
                iframe_p1b = await pg_if1b.evaluate(iframe_js)
                iframe_p2 = await pg_if2.evaluate(iframe_js)

                assert (
                    iframe_p1a["imgData"] == iframe_p1b["imgData"]
                    and iframe_p1a["dataURL"] == iframe_p1b["dataURL"]
                    and iframe_p1a["blobBytes"] == iframe_p1b["blobBytes"]
                ), "CHECK 8: iframe Profile 1 not stable"

                # NOTE: iframes share renderer process - canvas noise may not differentiate per-profile in Playwright
            finally:
                await pg_if1a.close()
                await ctx_if1a.close()
                await pg_if1b.close()
                await ctx_if1b.close()
                await pg_if2.close()
                await ctx_if2.close()

            ctx_oc1, pg_oc1 = await make_context(browser_instance, p1_built["spoofing_script"])
            ctx_oc2, pg_oc2 = await make_context(browser_instance, p2_built["spoofing_script"])
            try:
                oc_js = """
                (async () => {
                    if (typeof OffscreenCanvas === 'undefined') return {supported: false};
                    try {
                        const oc = new OffscreenCanvas(128, 64);
                        const ctx = oc.getContext('2d');
                        ctx.fillStyle = 'rgb(100,150,200)';
                        ctx.fillRect(0,0,128,64);
                        ctx.fillStyle = 'rgb(255,0,0)';
                        ctx.fillRect(10,10,40,20);
                        const imgData = Array.from(ctx.getImageData(0,0,128,64).data);
                        const blob = await oc.convertToBlob({type:'image/png'});
                        const buf = await blob.arrayBuffer();
                        const blobBytes = Array.from(new Uint8Array(buf));
                        return {supported: true, imgData, blobBytes};
                    } catch(e) {
                        return {supported: false, error: e.message};
                    }
                })()
                """
                oc_p1 = await pg_oc1.evaluate(oc_js)
                oc_p2 = await pg_oc2.evaluate(oc_js)

                assert oc_p1.get("supported"), "CHECK 9: main-thread OffscreenCanvas not supported"
                assert oc_p1["imgData"] == oc_p2["imgData"], "CHECK 9: OffscreenCanvas getImageData must be raw (not spoofed)"
                assert oc_p1["blobBytes"] == oc_p2["blobBytes"], "CHECK 9: OffscreenCanvas convertToBlob is not overridden (raw output)"
            finally:
                await pg_oc1.close()
                await ctx_oc1.close()
                await pg_oc2.close()
                await ctx_oc2.close()

            ctx_w1, pg_w1 = await make_context(browser_instance, p1_built["spoofing_script"])
            ctx_w2, pg_w2 = await make_context(browser_instance, p2_built["spoofing_script"])
            try:
                worker_js_src = r"""
                self.onmessage = async () => {
                    try {
                        if (typeof OffscreenCanvas === 'undefined') {
                            self.postMessage({supported: false});
                            return;
                        }
                        const oc = new OffscreenCanvas(128, 64);
                        const ctx = oc.getContext('2d');
                        ctx.fillStyle = 'rgb(100,150,200)';
                        ctx.fillRect(0,0,128,64);
                        ctx.fillStyle = 'rgb(255,0,0)';
                        ctx.fillRect(10,10,40,20);
                        const imgData = Array.from(ctx.getImageData(0,0,128,64).data);
                        const blob = await oc.convertToBlob({type:'image/png'});
                        const buf = await blob.arrayBuffer();
                        const blobBytes = Array.from(new Uint8Array(buf));
                        self.postMessage({supported: true, imgData, blobBytes});
                    } catch(e) {
                        self.postMessage({supported: false, error: e.message});
                    }
                };
                """
                worker_fetch_js = r"""
                (async () => {
                    try {
                        const blob = new Blob([WORKER_SRC], {type:'application/javascript'});
                        const url = URL.createObjectURL(blob);
                        const worker = new Worker(url);
                        const result = await new Promise((resolve, reject) => {
                            worker.onmessage = e => resolve(e.data);
                            worker.onerror = e => reject(new Error(e.message));
                            worker.postMessage('start');
                            setTimeout(() => reject(new Error('timeout')), 5000);
                        });
                        URL.revokeObjectURL(url);
                        worker.terminate();
                        return result;
                    } catch(e) {
                        return {supported: false, error: e.message};
                    }
                })()
                """
                launch_js_w1 = worker_fetch_js.replace("WORKER_SRC", f"`{worker_js_src}`")
                launch_js_w2 = worker_fetch_js.replace("WORKER_SRC", f"`{worker_js_src}`")

                wk_p1 = await pg_w1.evaluate(launch_js_w1)
                wk_p2 = await pg_w2.evaluate(launch_js_w2)

                assert wk_p1.get("supported"), f"CHECK 10: worker OffscreenCanvas not available: {wk_p1.get('error', 'unsupported')}"
                assert wk_p1["imgData"] == wk_p2["imgData"], "CHECK 10: worker OffscreenCanvas getImageData is not overridden"
                assert wk_p1["blobBytes"] == wk_p2["blobBytes"], "CHECK 10: worker OffscreenCanvas convertToBlob is not overridden"
            finally:
                await pg_w1.close()
                await ctx_w1.close()
                await pg_w2.close()
                await ctx_w2.close()

            ctx_ii, pg_ii = await make_context(browser_instance, p1_built["spoofing_script"])
            try:
                ii_result = await pg_ii.evaluate("""
                (() => {
                    const check = (fn, args) => {
                        try { fn(...args); return 'no-throw'; }
                        catch(e) { return e.constructor.name; }
                    };
                    return {
                        getImageData: check(CanvasRenderingContext2D.prototype.getImageData.bind({}), [0,0,1,1]),
                        toDataURL: check(HTMLCanvasElement.prototype.toDataURL.bind({}), []),
                        toBlob: check(HTMLCanvasElement.prototype.toBlob.bind({}), [() => {}])
                    };
                })()
                """)
                for method, thrown in ii_result.items():
                    assert thrown != "no-throw", f"CHECK 11: {method} did not throw on invalid receiver"
                    assert thrown == "TypeError", f"CHECK 11: {method} threw {thrown} instead of TypeError"
            finally:
                await pg_ii.close()
                await ctx_ii.close()

        finally:
            if browser_instance:
                await browser_instance.close()
                browser_instance = None
    finally:
        if playwright_instance:
            await playwright_instance.stop()
            playwright_instance = None


if __name__ == "__main__":
    asyncio.run(test_canvas_extended_surface())