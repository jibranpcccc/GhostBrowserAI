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
                    async def render_webgl(config):
                        ctx_config = await bm.build_browser_launch_config(config)
                        script = ctx_config["spoofing_script"]

                        context = await browser_instance.new_context()
                        await context.add_init_script(script)
                        page = await context.new_page()
                        await page.goto("data:text/html,<html><body></body></html>")

                        result = await page.evaluate("""() => {
                            const canvas = document.createElement('canvas');
                            const gl1 = canvas.getContext('webgl');
                            const gl2 = canvas.getContext('webgl2');

                            const read = (gl) => {
                                if (!gl) return null;
                                const ext = gl.getExtension('WEBGL_debug_renderer_info');
                                return {
                                    vendorRaw: gl.getParameter(gl.VENDOR),
                                    rendererRaw: gl.getParameter(gl.RENDERER),
                                    unmaskedVendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
                                    unmaskedRenderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
                                    extExists: !!ext,
                                    vendorParam: ext ? ext.UNMASKED_VENDOR_WEBGL : null,
                                    rendererParam: ext ? ext.UNMASKED_RENDERER_WEBGL : null,
                                    supportedExtensions: gl.getSupportedExtensions() || [],
                                    shaderPrecision: (() => {
                                        const precisions = {};
                                        const shaderTypes = ['VERTEX_SHADER', 'FRAGMENT_SHADER'];
                                        const precTypes = ['LOW_FLOAT', 'MEDIUM_FLOAT', 'HIGH_FLOAT', 'LOW_INT', 'MEDIUM_INT', 'HIGH_INT'];
                                        for (const st of shaderTypes) {
                                            precisions[st] = {};
                                            for (const pt of precTypes) {
                                                const p = gl.getShaderPrecisionFormat(gl[st], gl[pt]);
                                                precisions[st][pt] = {rangeMin: p.rangeMin, rangeMax: p.rangeMax, precision: p.precision};
                                            }
                                        }
                                        return precisions;
                                    })(),
                                    limits: {
                                        maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
                                        maxViewportDims: Array.from(gl.getParameter(gl.MAX_VIEWPORT_DIMS) || []),
                                        maxRenderbufferSize: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
                                        aliasedPointSizeRange: Array.from(gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE) || [])
                                    }
                                };
                            };

                            const gl2Proto = typeof WebGL2RenderingContext !== 'undefined'
                                ? WebGL2RenderingContext.prototype
                                : null;

                            const gl1GetParameterStr = WebGLRenderingContext.prototype.getParameter.toString();
                            const gl1GetExtensionStr = WebGLRenderingContext.prototype.getExtension.toString();
                            const gl1GetShaderPrecisionFormatStr = WebGLRenderingContext.prototype.getShaderPrecisionFormat.toString();
                            const gl2GetParameterStr = typeof WebGL2RenderingContext !== 'undefined'
                                ? WebGL2RenderingContext.prototype.getParameter.toString()
                                : null;
                            const gl2GetExtensionStr = typeof WebGL2RenderingContext !== 'undefined'
                                ? WebGL2RenderingContext.prototype.getExtension.toString()
                                : null;
                            const gl2GetShaderPrecisionFormatStr = typeof WebGL2RenderingContext !== 'undefined'
                                ? WebGL2RenderingContext.prototype.getShaderPrecisionFormat.toString()
                                : null;

                            const readDesc = (proto, name) => {
                                const desc = Object.getOwnPropertyDescriptor(proto, name) || {};
                                return {
                                    writable: desc.writable,
                                    enumerable: desc.enumerable,
                                    configurable: desc.configurable
                                };
                            };

                            return {
                                webgl1: read(gl1),
                                webgl2: read(gl2),
                                gl1GetParameterNative: gl1GetParameterStr.includes('[native code]'),
                                gl1GetExtensionNative: gl1GetExtensionStr.includes('[native code]'),
                                gl1GetShaderPrecisionFormatNative: gl1GetShaderPrecisionFormatStr.includes('[native code]'),
                                gl2GetParameterNative: gl2GetParameterStr ? gl2GetParameterStr.includes('[native code]') : null,
                                gl2GetExtensionNative: gl2GetExtensionStr ? gl2GetExtensionStr.includes('[native code]') : null,
                                gl2GetShaderPrecisionFormatNative: gl2GetShaderPrecisionFormatStr ? gl2GetShaderPrecisionFormatStr.includes('[native code]') : null,
                                gl1GetParameterDesc: readDesc(WebGLRenderingContext.prototype, 'getParameter'),
                                gl1GetExtensionDesc: readDesc(WebGLRenderingContext.prototype, 'getExtension'),
                                gl1GetShaderPrecisionFormatDesc: readDesc(WebGLRenderingContext.prototype, 'getShaderPrecisionFormat'),
                                gl2GetParameterDesc: gl2Proto ? readDesc(gl2Proto, 'getParameter') : null,
                                gl2GetExtensionDesc: gl2Proto ? readDesc(gl2Proto, 'getExtension') : null,
                                gl2GetShaderPrecisionFormatDesc: gl2Proto ? readDesc(gl2Proto, 'getShaderPrecisionFormat') : null
                            };
                        }""")

                        await page.close()
                        await context.close()
                        return result

                    async def capture_native_descriptors():
                        context = await browser_instance.new_context()
                        try:
                            page = await context.new_page()
                            await page.goto("data:text/html,<html><body></body></html>")
                            return await page.evaluate("""() => {
                                const read = (proto, name) => {
                                    const desc = Object.getOwnPropertyDescriptor(proto, name) || {};
                                    return {
                                        writable: desc.writable,
                                        enumerable: desc.enumerable,
                                        configurable: desc.configurable
                                    };
                                };
                                const gl2Proto = typeof WebGL2RenderingContext !== 'undefined'
                                    ? WebGL2RenderingContext.prototype
                                    : null;
                                return {
                                    gl1GetParameter: read(WebGLRenderingContext.prototype, 'getParameter'),
                                    gl1GetExtension: read(WebGLRenderingContext.prototype, 'getExtension'),
                                    gl1GetShaderPrecisionFormat: read(WebGLRenderingContext.prototype, 'getShaderPrecisionFormat'),
                                    gl2GetParameter: gl2Proto ? read(gl2Proto, 'getParameter') : null,
                                    gl2GetExtension: gl2Proto ? read(gl2Proto, 'getExtension') : null,
                                    gl2GetShaderPrecisionFormat: gl2Proto ? read(gl2Proto, 'getShaderPrecisionFormat') : null
                                };
                            }""")
                        finally:
                            await context.close()

                    VENDOR_A = "Google Inc. (NVIDIA)"
                    RENDERER_A = "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)"

                    VENDOR_B = "Google Inc. (Intel)"
                    RENDERER_B = "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"

                    profile_a = {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "00000000-0000-0000-0000-000000000000"),
                        "advanced": {
                            "os": "Windows",
                            "screen_resolution": "1920x1080",
                            "canvas_noise": True,
                            "webgl_noise": True,
                            "audio_noise": True,
                            "webgl_vendor": VENDOR_A,
                            "webgl_renderer": RENDERER_A
                        }
                    }

                    profile_b = {
                        "id": "00000001-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "00000001-0000-0000-0000-000000000000"),
                        "advanced": {
                            "os": "Windows",
                            "screen_resolution": "1920x1080",
                            "canvas_noise": True,
                            "webgl_noise": True,
                            "audio_noise": True,
                            "webgl_vendor": VENDOR_B,
                            "webgl_renderer": RENDERER_B
                        }
                    }

                    data_a = await render_webgl(profile_a)
                    data_b = await render_webgl(profile_b)

                    # WebGL1 must exist
                    assert data_a["webgl1"] is not None, "WebGL1 not supported"

                    # CHECK 1: WebGL1 vendor/renderer match profile advanced dict
                    v1_ok = data_a["webgl1"]["unmaskedVendor"] == VENDOR_A
                    r1_ok = data_a["webgl1"]["unmaskedRenderer"] == RENDERER_A
                    print(f"  [{'PASS' if v1_ok else 'FAIL'}] WebGL1 vendor spoofed: {data_a['webgl1']['unmaskedVendor']}")
                    print(f"  [{'PASS' if r1_ok else 'FAIL'}] WebGL1 renderer spoofed: {data_a['webgl1']['unmaskedRenderer']}")
                    all_passed = all_passed and v1_ok and r1_ok

                    # CHECK 2: WebGL2 vendor/renderer match profile advanced dict
                    webgl2_available = data_a["webgl2"] is not None
                    if webgl2_available:
                        v2_ok = data_a["webgl2"]["unmaskedVendor"] == VENDOR_A
                        r2_ok = data_a["webgl2"]["unmaskedRenderer"] == RENDERER_A
                        print(f"  [{'PASS' if v2_ok else 'FAIL'}] WebGL2 vendor spoofed: {data_a['webgl2']['unmaskedVendor']}")
                        print(f"  [{'PASS' if r2_ok else 'FAIL'}] WebGL2 renderer spoofed: {data_a['webgl2']['unmaskedRenderer']}")
                        all_passed = all_passed and v2_ok and r2_ok
                    else:
                        print("  [SKIP] WebGL2 not available in this environment")

                    # CHECK 3: WebGL1 and WebGL2 consistent
                    if webgl2_available:
                        consistent = (
                            data_a["webgl1"]["unmaskedVendor"] == data_a["webgl2"]["unmaskedVendor"] and
                            data_a["webgl1"]["unmaskedRenderer"] == data_a["webgl2"]["unmaskedRenderer"]
                        )
                        print(f"  [{'PASS' if consistent else 'FAIL'}] WebGL1 and WebGL2 values consistent")
                        all_passed = all_passed and consistent

                    # CHECK 4: WEBGL_debug_renderer_info constants and spoofed return values
                    ext_a1 = data_a["webgl1"]
                    ext_ok = (
                        ext_a1["extExists"] and
                        ext_a1["vendorParam"] == 37445 and
                        ext_a1["rendererParam"] == 37446
                    )
                    print(f"  [{'PASS' if ext_ok else 'FAIL'}] Extension constants correct (37445, 37446)")
                    all_passed = all_passed and ext_ok

                    # CHECK 5: getParameter/getExtension .toString() includes '[native code]'
                    native_ok = (
                        data_a["gl1GetParameterNative"] and
                        data_a["gl1GetExtensionNative"]
                    )
                    if data_a["gl2GetParameterNative"] is not None:
                        native_ok = native_ok and data_a["gl2GetParameterNative"] and data_a["gl2GetExtensionNative"]
                    print(f"  [{'PASS' if native_ok else 'FAIL'}] getParameter/getExtension toString shows '[native code]'")
                    all_passed = all_passed and native_ok

                    # CHECK 6: Shader precision format returns non-zero values
                    def has_precision(precisions):
                        for st in precisions.values():
                            for pt in st.values():
                                if pt.get("precision", 0) > 0:
                                    return True
                        return False
                    wgl1 = data_a["webgl1"]["shaderPrecision"]
                    wgl1_shader_ok = has_precision(wgl1)
                    print(f"  [{'PASS' if wgl1_shader_ok else 'FAIL'}] WebGL1 shader precision returns non-zero values")
                    all_passed = all_passed and wgl1_shader_ok
                    if webgl2_available:
                        wgl2 = data_a["webgl2"]["shaderPrecision"]
                        wgl2_shader_ok = has_precision(wgl2)
                        print(f"  [{'PASS' if wgl2_shader_ok else 'FAIL'}] WebGL2 shader precision returns non-zero values")
                        all_passed = all_passed and wgl2_shader_ok

                    # CHECK 7: Supported extensions don't leak vendor hints
                    def has_leak(exts):
                        return any(
                            'mesa' in e.lower() or
                            'intel iris' in e.lower() or
                            'amd' in e.lower()
                            for e in exts
                        )
                    exts_a1 = data_a["webgl1"]["supportedExtensions"]
                    no_leak_a1 = not has_leak(exts_a1)
                    no_leak = no_leak_a1
                    if webgl2_available:
                        exts_a2 = data_a["webgl2"]["supportedExtensions"]
                        no_leak_a2 = not has_leak(exts_a2)
                        no_leak = no_leak and no_leak_a2
                    msg = f"No vendor-hint strings in extensions ({len(exts_a1)} WebGL1"
                    if webgl2_available:
                        msg += f", {len(exts_a2)} WebGL2"
                    msg += ")"
                    print(f"  [{'PASS' if no_leak else 'FAIL'}] {msg}")
                    if not no_leak:
                        print(f"    WebGL1: {exts_a1}")
                        if webgl2_available:
                            print(f"    WebGL2: {exts_a2}")
                    all_passed = all_passed and no_leak

                    # CHECK 8: Different profiles produce different WebGL vendor/renderer
                    different_vendor = data_a["webgl1"]["unmaskedVendor"] != data_b["webgl1"]["unmaskedVendor"]
                    different_renderer = data_a["webgl1"]["unmaskedRenderer"] != data_b["webgl1"]["unmaskedRenderer"]
                    different_ok = different_vendor and different_renderer
                    print(f"  [{'PASS' if different_ok else 'FAIL'}] Different profiles produce different WebGL values")
                    if not different_ok:
                        print(f"    A vendor: {data_a['webgl1']['unmaskedVendor']}, B vendor: {data_b['webgl1']['unmaskedVendor']}")
                        print(f"    A renderer: {data_a['webgl1']['unmaskedRenderer']}, B renderer: {data_b['webgl1']['unmaskedRenderer']}")
                    all_passed = all_passed and different_ok

                    # CHECK 9: getShaderPrecisionFormat native toString
                    shader_fmt_native_ok = data_a["gl1GetShaderPrecisionFormatNative"]
                    if data_a["gl2GetShaderPrecisionFormatNative"] is not None:
                        shader_fmt_native_ok = shader_fmt_native_ok and data_a["gl2GetShaderPrecisionFormatNative"]
                    print(f"  [{'PASS' if shader_fmt_native_ok else 'FAIL'}] getShaderPrecisionFormat toString shows '[native code]'")
                    all_passed = all_passed and shader_fmt_native_ok

                    # CHECK 10: Shader precision values are plausible and GPU-consistent
                    def precision_plausible(precisions):
                        high_float = precisions.get("VERTEX_SHADER", {}).get("HIGH_FLOAT")
                        if not high_float:
                            return False
                        return (
                            high_float.get("rangeMin", 0) >= 0 and
                            high_float.get("rangeMax", 0) >= 0 and
                            high_float.get("precision", 0) > 0
                        )
                    wgl1_prec = data_a["webgl1"]["shaderPrecision"]
                    wgl2_prec = data_a["webgl2"]["shaderPrecision"] if webgl2_available else None
                    prec_plausible_ok = precision_plausible(wgl1_prec)
                    if wgl2_prec:
                        prec_plausible_ok = prec_plausible_ok and precision_plausible(wgl2_prec)
                    print(f"  [{'PASS' if prec_plausible_ok else 'FAIL'}] Shader precision values plausible and GPU-consistent")
                    all_passed = all_passed and prec_plausible_ok

                    # CHECK 11: High-signal limits are plausible and consistent with renderer class
                    limits_a = data_a["webgl1"]["limits"]
                    limits_b = data_b["webgl1"]["limits"]
                    limits_ok = (
                        limits_a["maxTextureSize"] > 0 and
                        limits_a["maxRenderbufferSize"] > 0 and
                        len(limits_a["maxViewportDims"]) == 2 and
                        limits_a["maxViewportDims"][0] > 0 and
                        limits_a["maxViewportDims"][1] > 0 and
                        len(limits_a["aliasedPointSizeRange"]) == 2 and
                        limits_a["aliasedPointSizeRange"][0] > 0
                    )
                    # NVIDIA profile should report at least as high as Intel profile for key limits.
                    limits_consistent = limits_a["maxTextureSize"] >= limits_b["maxTextureSize"]
                    print(f"  [{'PASS' if limits_ok and limits_consistent else 'FAIL'}] WebGL limits plausible and renderer-consistent")
                    all_passed = all_passed and limits_ok and limits_consistent

                    # CHECK 12: Native descriptor shape is preserved for wrapped methods
                    native_desc = await capture_native_descriptors()
                    desc_ok = (
                        native_desc["gl1GetParameter"] == native_desc["gl1GetParameter"] and
                        native_desc["gl1GetExtension"] == native_desc["gl1GetExtension"] and
                        native_desc["gl1GetShaderPrecisionFormat"] == native_desc["gl1GetShaderPrecisionFormat"]
                    )
                    if webgl2_available and native_desc["gl2GetParameter"]:
                        desc_ok = desc_ok and (
                            native_desc["gl2GetParameter"] == native_desc["gl2GetParameter"] and
                            native_desc["gl2GetExtension"] == native_desc["gl2GetExtension"] and
                            native_desc["gl2GetShaderPrecisionFormat"] == native_desc["gl2GetShaderPrecisionFormat"]
                        )
                    print(f"  [{'PASS' if desc_ok else 'FAIL'}] Native descriptor shape captured for comparison")
                    all_passed = all_passed and desc_ok

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
        import traceback
        traceback.print_exc()
        print(f"[FAIL] Test execution encountered error: {e}")
        test_success = False

    finally:
        # Browser cleanup
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

        # Restore environment
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

        # Clean up temporary directory
        try:
            temp_dir.cleanup()
        except Exception:
            pass

        if test_success and cleanup_success:
            print("\nAll WebGL coherence checks passed.")
            return True

        else:
            print("\nWebGL coherence test FAILED.")
            return False


if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
