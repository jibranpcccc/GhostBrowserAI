import os
import sys
import tempfile
import asyncio

def main():

    # Initialize variables safely for finally block
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

        # ------------------------------------------------------------------ helpers

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
            """Full getImageData, toDataURL(png), toBlob(png) bytes."""
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

        async def run_test():
            global playwright_instance, browser_instance
            all_passed = True

            from playwright.async_api import async_playwright
            playwright_instance = await async_playwright().start()
            try:
                browser_instance = await playwright_instance.chromium.launch(headless=True)
                try:
                    # Build configs
                    native_cfg = {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "native"),
                        "advanced": {"os": "Windows", "screen_resolution": "1920x1080", "canvas_noise": False}
                    }
                    no_noise_cfg = {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "no_noise"),
                        "advanced": {"os": "Windows", "screen_resolution": "1920x1080", "canvas_noise": False}
                    }
                    p1_cfg = {
                        "id": "00000001-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "p1"),
                        "advanced": {"os": "Windows", "screen_resolution": "1920x1080", "canvas_noise": True}
                    }
                    p2_cfg = {
                        "id": "00000002-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "p2"),
                        "advanced": {"os": "Windows", "screen_resolution": "1920x1080", "canvas_noise": True}
                    }

                    native_built   = await bm.build_browser_launch_config(native_cfg)
                    no_noise_built = await bm.build_browser_launch_config(no_noise_cfg)
                    p1_built       = await bm.build_browser_launch_config(p1_cfg)
                    p2_built       = await bm.build_browser_launch_config(p2_cfg)

                    # ---- CHECK 1 — PARTIAL-REGION CONSISTENCY ----
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

                            // Extract matching region from full (row-major, RGBA)
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
                        if partial_check["match"]:
                            print("[PASS] CHECK 1 — partial-region consistency")
                        else:
                            print(f"[FAIL] CHECK 1 — partial-region consistency (partialLen={partial_check['partialLen']}, extractedLen={partial_check['extractedLen']})")
                            all_passed = False

                        # ---- CHECK 2 — PARTIAL-REGION STABILITY ----
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
                        if stability_check:
                            print("[PASS] CHECK 2 — partial-region stability")
                        else:
                            print("[FAIL] CHECK 2 — partial-region stability")
                            all_passed = False
                    finally:
                        await pg_p1.close()
                        await ctx_p1.close()

                    # ---- CHECK 3 — PROPERTY DESCRIPTORS ----
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

                        desc_ok = True
                        for method in ["getImageData", "toDataURL", "toBlob"]:
                            n = desc_result.get(method)
                            p = desc_protected.get(method)
                            if n is None or p is None:
                                print(f"[FAIL] CHECK 3 — descriptor for {method} is None (native={n}, protected={p})")
                                desc_ok = False
                            elif n != p:
                                print(f"[FAIL] CHECK 3 — descriptor mismatch for {method}: native={n} protected={p}")
                                desc_ok = False
                        if desc_ok:
                            print("[PASS] CHECK 3 — property descriptors unchanged")
                        else:
                            all_passed = False

                        # ---- CHECK 4 — FUNCTION NAME AND LENGTH ----
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

                        name_len_ok = True
                        for method in ["getImageData", "toDataURL", "toBlob"]:
                            n = name_len_native.get(method, {})
                            p = name_len_protected.get(method, {})
                            if n.get("name") != p.get("name") or n.get("length") != p.get("length"):
                                print(f"[FAIL] CHECK 4 — name/length mismatch for {method}: native={n} protected={p}")
                                name_len_ok = False
                        if name_len_ok:
                            print("[PASS] CHECK 4 — function name and length unchanged")
                        else:
                            all_passed = False

                        # ---- CHECK 5 — NATIVE-LIKE TOSTRING ----
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

                        tostring_ok = True
                        for method, r in tostring_result.items():
                            if not r["hasNativeCode"]:
                                print(f"[FAIL] CHECK 5 — toString for {method} does not contain '[native code]'")
                                tostring_ok = False
                            if r["exposesWrapper"]:
                                print(f"[FAIL] CHECK 5 — toString for {method} exposes wrapper implementation")
                                tostring_ok = False
                            if not r["hasName"]:
                                print(f"[FAIL] CHECK 5 — toString for {method} does not contain function name")
                                tostring_ok = False
                        if tostring_ok:
                            print("[PASS] CHECK 5 — native-like toString")
                        else:
                            all_passed = False

                    finally:
                        await pg_native.close()
                        await ctx_native.close()
                        await pg_p1b.close()
                        await ctx_p1b.close()

                    # ---- CHECK 6 — CANVAS NOISE DISABLED ----
                    ctx_nat2, pg_nat2 = await make_context(browser_instance)
                    ctx_nn, pg_nn = await make_context(browser_instance, no_noise_built["spoofing_script"])
                    try:
                        nat_out = await get_all_outputs(pg_nat2)
                        nn_out  = await get_all_outputs(pg_nn)
                        if (nat_out["imgData"] == nn_out["imgData"] and
                                nat_out["dataURL"] == nn_out["dataURL"] and
                                nat_out["blobBytes"] == nn_out["blobBytes"]):
                            print("[PASS] CHECK 6 — canvas_noise=False is identical to native")
                        else:
                            changed = []
                            if nat_out["imgData"]   != nn_out["imgData"]:   changed.append("imgData")
                            if nat_out["dataURL"]   != nn_out["dataURL"]:   changed.append("dataURL")
                            if nat_out["blobBytes"] != nn_out["blobBytes"]: changed.append("blobBytes")
                            print(f"[FAIL] CHECK 6 — canvas_noise=False differs from native in: {changed}")
                            all_passed = False
                    finally:
                        await pg_nat2.close()
                        await ctx_nat2.close()
                        await pg_nn.close()
                        await ctx_nn.close()

                    # ---- CHECK 7 — MIME TYPE AND QUALITY PRESERVATION ----
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

                        q_ok = True
                        if not q_result["pngURLOk"]:
                            print("[FAIL] CHECK 7 — PNG toDataURL does not start with data:image/png"); q_ok = False
                        if not q_result["jpegURLOk"]:
                            print("[FAIL] CHECK 7 — JPEG toDataURL does not start with data:image/jpeg"); q_ok = False
                        if q_result["pngBlobType"] != "image/png":
                            print(f"[FAIL] CHECK 7 — PNG toBlob type={q_result['pngBlobType']}"); q_ok = False
                        if q_result["jpegBlobType"] != "image/jpeg":
                            print(f"[FAIL] CHECK 7 — JPEG toBlob type={q_result['jpegBlobType']}"); q_ok = False
                        if not q_result["qualityDiffers"]:
                            print("[FAIL] CHECK 7 — JPEG quality 0.20 same as 0.90"); q_ok = False
                        if not q_result["qualityStable"]:
                            print("[FAIL] CHECK 7 — JPEG quality 0.90 not stable across two calls"); q_ok = False
                        if q_ok:
                            print("[PASS] CHECK 7 — MIME type and quality preservation")
                        else:
                            all_passed = False
                    finally:
                        await pg_q.close()
                        await ctx_q.close()

                    # ---- CHECK 8 — SAME-ORIGIN IFRAME ----
                    ctx_if1a, pg_if1a = await make_context(browser_instance, p1_built["spoofing_script"])
                    ctx_if1b, pg_if1b = await make_context(browser_instance, p1_built["spoofing_script"])
                    ctx_if2,  pg_if2  = await make_context(browser_instance, p2_built["spoofing_script"])
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
                        iframe_p2  = await pg_if2.evaluate(iframe_js)

                        iframe_ok = True
                        p1_iframe_stable = (
                            iframe_p1a["imgData"]   == iframe_p1b["imgData"] and
                            iframe_p1a["dataURL"]   == iframe_p1b["dataURL"] and
                            iframe_p1a["blobBytes"] == iframe_p1b["blobBytes"]
                        )
                        if not p1_iframe_stable:
                            print("[FAIL] CHECK 8 — iframe: Profile 1 not stable across two runs"); iframe_ok = False

                        for api in ["imgData", "dataURL", "blobBytes"]:
                            if iframe_p1a[api] == iframe_p2[api]:
                                print(f"[FAIL] CHECK 8 — iframe: Profile 1 and Profile 2 identical for {api}"); iframe_ok = False

                        if iframe_ok:
                            print("[PASS] CHECK 8 — same-origin iframe coverage")
                        else:
                            all_passed = False
                    finally:
                        await pg_if1a.close(); await ctx_if1a.close()
                        await pg_if1b.close(); await ctx_if1b.close()
                        await pg_if2.close();  await ctx_if2.close()

                    # ---- CHECK 9 — MAIN-THREAD OFFSCREENCANVAS ----
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

                        if not oc_p1.get("supported"):
                            print("[FAIL] CHECK 9 — main-thread OffscreenCanvas: SKIP — coverage not established")
                            all_passed = False
                        else:
                            oc_ok = True
                            if oc_p1["imgData"] == oc_p2["imgData"]:
                                print("[FAIL] CHECK 9 — OffscreenCanvas getImageData identical across profiles"); oc_ok = False
                            if oc_p1["blobBytes"] == oc_p2["blobBytes"]:
                                print("[FAIL] CHECK 9 — OffscreenCanvas convertToBlob identical across profiles"); oc_ok = False
                            if oc_ok:
                                print("[PASS] CHECK 9 — main-thread OffscreenCanvas profile separation")
                            else:
                                all_passed = False
                    finally:
                        await pg_oc1.close(); await ctx_oc1.close()
                        await pg_oc2.close(); await ctx_oc2.close()

                    # ---- CHECK 10 — WORKER OFFSCREENCANVAS ----
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
                        # Inject worker source as a JS variable
                        worker_src_escaped = worker_js_src.replace('`', '\\`').replace('\\', '\\\\').replace('`', '\\`')
                        launch_js_w1 = worker_fetch_js.replace("WORKER_SRC", f"`{worker_js_src}`")
                        launch_js_w2 = worker_fetch_js.replace("WORKER_SRC", f"`{worker_js_src}`")

                        wk_p1 = await pg_w1.evaluate(launch_js_w1)
                        wk_p2 = await pg_w2.evaluate(launch_js_w2)

                        if not wk_p1.get("supported"):
                            err = wk_p1.get("error", "unsupported")
                            print(f"[FAIL] CHECK 10 — worker OffscreenCanvas not available: {err}")
                            all_passed = False
                        else:
                            wk_ok = True
                            if wk_p1["imgData"] == wk_p2["imgData"]:
                                print("[FAIL] CHECK 10 — worker OffscreenCanvas getImageData identical across profiles"); wk_ok = False
                            if wk_p1["blobBytes"] == wk_p2["blobBytes"]:
                                print("[FAIL] CHECK 10 — worker OffscreenCanvas convertToBlob identical across profiles"); wk_ok = False
                            if wk_ok:
                                print("[PASS] CHECK 10 — worker OffscreenCanvas profile separation")
                            else:
                                all_passed = False
                    finally:
                        await pg_w1.close(); await ctx_w1.close()
                        await pg_w2.close(); await ctx_w2.close()

                    # ---- CHECK 11 — ILLEGAL INVOCATION ----
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
                        ii_native = await pg_ii.evaluate("""
                        (() => {
                            // We expect TypeError for illegal invocation on native methods.
                            // The protected methods should throw the same type.
                            return {expected: 'TypeError'};
                        })()
                        """)

                        ii_ok = True
                        for method, thrown in ii_result.items():
                            if thrown == "no-throw":
                                print(f"[FAIL] CHECK 11 — {method} did not throw on invalid receiver")
                                ii_ok = False
                            elif thrown != "TypeError":
                                print(f"[FAIL] CHECK 11 — {method} threw {thrown} instead of TypeError")
                                ii_ok = False
                        if ii_ok:
                            print("[PASS] CHECK 11 — illegal invocation throws TypeError")
                        else:
                            all_passed = False
                    finally:
                        await pg_ii.close()
                        await ctx_ii.close()

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
        print(f"[FAIL] Test execution error: {e}")
        import traceback
        traceback.print_exc()
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

        if test_success and cleanup_success:
            return True

        else:
            return False


if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
