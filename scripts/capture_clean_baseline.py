import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import json
import hashlib
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from backend.engine_resolver import get_chromium_executable_path
from backend.browser_version import BrowserVersion

async def capture():
    from backend.engine_resolver import get_chromium_executable_path_async
    exe_path = await get_chromium_executable_path_async()
    with open(exe_path, "rb") as f:
        exe_sha256 = hashlib.sha256(f.read()).hexdigest()

    bv = BrowserVersion.from_installed_engine()
    version = bv.full_version
    execution_mode = "headless"
    capture_timestamp = datetime.now(timezone.utc).isoformat()

    print(f"Capturing clean native baseline for:")
    print(f"  Executable: {exe_path}")
    print(f"  SHA-256:    {exe_sha256}")
    print(f"  Version:    {version} (Major: {bv.major})")
    print(f"  Mode:       {execution_mode}")
    print(f"  Timestamp:  {capture_timestamp}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=exe_path,
            headless=True
        )
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("data:text/html,<html><head><title>Clean Baseline</title></head><body><h1>Clean Baseline</h1></body></html>")

        telemetry = await page.evaluate("""async () => {
            const getDesc = (obj, prop) => {
                const d = Object.getOwnPropertyDescriptor(obj, prop);
                if (!d) return null;
                return {
                    enumerable: d.enumerable,
                    configurable: d.configurable,
                    writable: d.writable,
                    hasGetter: typeof d.get === 'function',
                    hasSetter: typeof d.set === 'function',
                    valueType: typeof d.value
                };
            };

            const navProps = {};
            const navProtoProps = {};
            const checkedProps = [
                'userAgent', 'platform', 'languages', 'language',
                'hardwareConcurrency', 'deviceMemory', 'webdriver',
                'maxTouchPoints', 'cookieEnabled', 'pdfViewerEnabled',
                'vendor', 'product', 'productSub'
            ];

            for (const p of checkedProps) {
                navProps[p] = {
                    isOwn: Object.prototype.hasOwnProperty.call(navigator, p),
                    value: (typeof navigator[p] === 'function') ? 'function' : navigator[p],
                    descriptor: getDesc(navigator, p)
                };
                navProtoProps[p] = {
                    isOwn: Object.prototype.hasOwnProperty.call(Navigator.prototype, p),
                    descriptor: getDesc(Navigator.prototype, p)
                };
            }

            let uadata = null;
            if (navigator.userAgentData) {
                let high = {};
                try {
                    high = await navigator.userAgentData.getHighEntropyValues([
                        'architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion', 'fullVersionList'
                    ]);
                } catch(e) {
                    high = { error: e.message };
                }
                uadata = {
                    brands: navigator.userAgentData.brands,
                    mobile: navigator.userAgentData.mobile,
                    platform: navigator.userAgentData.platform,
                    highEntropy: high
                };
            }

            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl');
            let webgl1 = null;
            if (gl) {
                const dbg = gl.getExtension('WEBGL_debug_renderer_info');
                webgl1 = {
                    vendor: gl.getParameter(gl.VENDOR),
                    renderer: gl.getParameter(gl.RENDERER),
                    version: gl.getParameter(gl.VERSION),
                    shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
                    unmaskedVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
                    unmaskedRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
                    maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
                    extensionsCount: gl.getSupportedExtensions() ? gl.getSupportedExtensions().length : 0
                };
            }

            const gl2 = canvas.getContext('webgl2');
            let webgl2 = null;
            if (gl2) {
                const dbg2 = gl2.getExtension('WEBGL_debug_renderer_info');
                webgl2 = {
                    vendor: gl2.getParameter(gl2.VENDOR),
                    renderer: gl2.getParameter(gl2.RENDERER),
                    version: gl2.getParameter(gl2.VERSION),
                    shadingLanguageVersion: gl2.getParameter(gl2.SHADING_LANGUAGE_VERSION),
                    unmaskedVendor: dbg2 ? gl2.getParameter(dbg2.UNMASKED_VENDOR_WEBGL) : null,
                    unmaskedRenderer: dbg2 ? gl2.getParameter(dbg2.UNMASKED_RENDERER_WEBGL) : null,
                    maxTextureSize: gl2.getParameter(gl2.MAX_TEXTURE_SIZE)
                };
            }

            let webgpu = { supported: !!navigator.gpu };
            if (navigator.gpu) {
                try {
                    const adapter = await navigator.gpu.requestAdapter();
                    if (adapter) {
                        const info = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
                        webgpu.adapterAvailable = true;
                        webgpu.info = info ? {
                            vendor: info.vendor,
                            architecture: info.architecture,
                            device: info.device,
                            description: info.description
                        } : null;
                    }
                } catch(e) {
                    webgpu.error = e.message;
                }
            }

            return {
                navProps,
                navProtoProps,
                uadata,
                webgl1,
                webgl2,
                webgpu,
                pluginsLength: navigator.plugins ? navigator.plugins.length : 0,
                mimeTypesLength: navigator.mimeTypes ? navigator.mimeTypes.length : 0
            };
        }""")

        await browser.close()

        baseline = {
            "exact_executable_path": exe_path,
            "executable_sha256": exe_sha256,
            "exact_version": version,
            "browser_major": bv.major,
            "execution_mode": execution_mode,
            "capture_timestamp": capture_timestamp,
            "telemetry": telemetry
        }

        out_dir = Path("baselines")
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "clean_chromium_baseline.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(baseline, f, indent=2)
        print(f"Clean native baseline successfully saved to: {out_path}")

if __name__ == "__main__":
    asyncio.run(capture())
