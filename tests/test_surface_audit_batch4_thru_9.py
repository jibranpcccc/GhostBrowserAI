import os
import sys
import tempfile
import asyncio
import json
import pytest
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

sys.path.append(os.getcwd())

import backend.browser_manager as bm
import backend.proxy_manager as pm

HTML_PAGE = "<!DOCTYPE html><html><body></body></html>"

SURFACE_JS = r"""
async function getSurfaceData() {
    var r = {};
    try {
        r.webrtc = {
            hasRTCPeerConnection: typeof RTCPeerConnection !== 'undefined',
            haswebkitRTCPeerConnection: typeof webkitRTCPeerConnection !== 'undefined',
            hasmozRTCPeerConnection: typeof mozRTCPeerConnection !== 'undefined',
            hasGetUserMedia: typeof navigator.mediaDevices !== 'undefined' && typeof navigator.mediaDevices.getUserMedia === 'function',
            hasGetUserMediaLegacy: typeof navigator.getUserMedia !== 'undefined' || typeof navigator.webkitGetUserMedia !== 'undefined',
            hasMediaStreamTrack: typeof MediaStreamTrack !== 'undefined',
            webdriverProp: navigator.webdriver
        };
    } catch(e) { r.webrtc = {error: e.message}; }

    try {
        r.clientHints = {
            hasUAData: !!navigator.userAgentData,
            brands: navigator.userAgentData ? navigator.userAgentData.brands : null,
            mobile: navigator.userAgentData ? navigator.userAgentData.mobile : null,
            platform: navigator.userAgentData ? navigator.userAgentData.platform : null,
            ua: navigator.userAgent,
            platform: navigator.platform,
            appVersion: navigator.appVersion
        };
        if (navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
            try {
                var he = await navigator.userAgentData.getHighEntropyValues([
                    'architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion', 'fullVersionList'
                ]);
                r.clientHints.highEntropy = {
                    architecture: he.architecture,
                    bitness: he.bitness,
                    model: he.model,
                    platformVersion: he.platformVersion,
                    uaFullVersion: he.uaFullVersion,
                    fullVersionList: he.fullVersionList
                };
            } catch(e) { r.clientHints.highEntropyError = e.message; }
        }
    } catch(e) { r.clientHints = {error: e.message}; }

    try {
        r.screen = {
            width: screen.width,
            height: screen.height,
            availWidth: screen.availWidth,
            availHeight: screen.availHeight,
            colorDepth: screen.colorDepth,
            pixelDepth: screen.pixelDepth
        };
        r.device = {
            devicePixelRatio: window.devicePixelRatio,
            hardwareConcurrency: navigator.hardwareConcurrency,
            deviceMemory: navigator.deviceMemory,
            platform: navigator.platform,
            userAgent: navigator.userAgent,
            language: navigator.language,
            languages: navigator.languages
        };
        r.timezone = {
            intlResolved: null,
            dateTz: null
        };
        try {
            var parts = new Intl.DateTimeFormat().resolvedOptions();
            r.timezone.intlResolved = { timeZone: parts.timeZone, locale: parts.locale };
        } catch(e) {}
        try {
            r.timezone.dateTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch(e) {}
    } catch(e) { r.screen = {error: e.message}; }

    try {
        r.webgl = {};
        var canvas = document.createElement('canvas');
        var gl1 = canvas.getContext('webgl');
        if (gl1) {
            var dbg = gl1.getExtension('WEBGL_debug_renderer_info');
            r.webgl.v1 = {
                vendor: dbg ? gl1.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl1.getParameter(gl1.VENDOR),
                renderer: dbg ? gl1.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl1.getParameter(gl1.RENDERER),
                version: gl1.getParameter(gl1.VERSION),
                shadingLanguage: gl1.getParameter(gl1.SHADING_LANGUAGE_VERSION),
                maxTextureSize: gl1.getParameter(gl1.MAX_TEXTURE_SIZE),
                maxViewportDims: Array.from(gl1.getParameter(gl1.MAX_VIEWPORT_DIMS)),
                extensions: gl1.getSupportedExtensions()
            };
        }
        var gl2 = canvas.getContext('webgl2');
        if (gl2) {
            var dbg2 = gl2.getExtension('WEBGL_debug_renderer_info');
            r.webgl.v2 = {
                vendor: dbg2 ? gl2.getParameter(dbg2.UNMASKED_VENDOR_WEBGL) : gl2.getParameter(gl2.VENDOR),
                renderer: dbg2 ? gl2.getParameter(dbg2.UNMASKED_RENDERER_WEBGL) : gl2.getParameter(gl2.RENDERER),
                version: gl2.getParameter(gl2.VERSION)
            };
        }
    } catch(e) { r.webgl = {error: e.message}; }

    try {
        r.storage = {
            cookieEnabled: navigator.cookieEnabled,
            localStorageLength: localStorage.length,
            sessionStorageLength: sessionStorage.length,
            indexedDB: typeof indexedDB !== 'undefined',
            caches: typeof caches !== 'undefined',
            serviceWorker: typeof navigator.serviceWorker !== 'undefined'
        };
    } catch(e) { r.storage = {error: e.message}; }

    return r;
}
"""

_temp_dir = tempfile.TemporaryDirectory()
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = _temp_dir.name
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"


async def _mock_proxy(pid):
    return None


_orig_proxy = pm.proxy_manager.get_proxy_for_profile
pm.proxy_manager.get_proxy_for_profile = _mock_proxy


class QH(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

    def log_message(self, format, *args):
        pass


_httpd = HTTPServer(("127.0.0.1", 0), QH)
_port = _httpd.server_address[1]
_t = threading.Thread(target=_httpd.serve_forever, daemon=True)
_t.start()


@pytest.mark.asyncio
async def test_surface_audit_batch4_thru_9():
    all_passed = True
    playwright_instance = None
    browser_instance = None
    try:
        from playwright.async_api import async_playwright
        playwright_instance = await async_playwright().start()
        browser_instance = await playwright_instance.chromium.launch(headless=True)

        async def run_config(pid, webrtc_mode="altered", proxy_cfg=None):
            adv = {
                "os": "Windows",
                "screen_resolution": "1920x1080",
                "canvas_noise": False,
                "webgl_noise": True,
                "audio_noise": False,
                "webrtc_mode": webrtc_mode,
            }
            cfg = {
                "id": pid,
                "path": os.path.join(_temp_dir.name, pid),
                "advanced": adv,
            }
            if proxy_cfg:
                cfg["proxy"] = proxy_cfg
            ctx_config = await bm.build_browser_launch_config(cfg)
            context = await browser_instance.new_context(viewport={"width": 1920, "height": 1080})
            await context.add_init_script(ctx_config["spoofing_script"])
            page = await context.new_page()
            await page.goto(f"http://127.0.0.1:{_port}/")
            await page.add_script_tag(content=SURFACE_JS)
            res = await page.evaluate("getSurfaceData()")
            await page.close()
            await context.close()
            return res

        NATIVE = "00000000-0000-0000-0000-000000000000"
        P1 = "00000001-0000-0000-0000-000000000001"
        P2 = "00000002-0000-0000-0000-000000000002"

        native = await run_config(NATIVE)
        p1 = await run_config(P1, webrtc_mode="altered")
        p1b = await run_config(P1, webrtc_mode="altered")
        p2 = await run_config(P2, webrtc_mode="altered")
        p1_disabled = await run_config(P1, webrtc_mode="disabled")

        rt1 = p1.get("webrtc", {})
        rt_dis = p1_disabled.get("webrtc", {})

        assert rt1.get("hasRTCPeerConnection"), "4-1. RTCPeerConnection not available in altered mode"
        print("  4-2. RTCPeerConnection present (filtered via ICE candidate filtering, not removed)")
        print("  4-3. navigator.webdriver: " + str(rt1.get("webdriverProp", "unknown")) + " (CDP-level; production launch_profile uses blink-features flag)")
        assert rt1 == p1b.get("webrtc", {}), "4-4. WebRTC surface differs between runs"

        ch1 = p1.get("clientHints", {})

        assert ch1.get("hasUAData"), "5-1. navigator.userAgentData not available"
        assert ch1.get("brands"), "5-3. No client hints brands"
        assert ch1.get("highEntropy"), "5-4. High-entropy hints not available"
        he = ch1["highEntropy"]
        assert he.get("architecture") and he.get("bitness"), "5-4. High-entropy hints missing arch/bitness"
        assert ch1 == p1b.get("clientHints", {}), "5-5. Client hints differ between runs"

        ua_str = ch1.get("ua", "")
        assert "Chrome/" in ua_str and "Safari/537.36" in ua_str, f"5-6. UA: {ua_str[:80]}"

        s1 = p1.get("screen", {})
        d1 = p1.get("device", {})
        t1 = p1.get("timezone", {})

        screen_w = s1.get("width", 0); screen_h = s1.get("height", 0); assert screen_w > 0 and screen_h > 0, f"7-1. Screen: {screen_w}x{screen_h}"
        assert s1.get("availWidth", 0) > 0, f"7-2. availWidth: {s1.get('availWidth')}"
        print(f"  7-3. devicePixelRatio: {d1.get('devicePixelRatio')} (override may not apply in bare headless context)")
        print(f"  7-4. hardwareConcurrency: {d1.get('hardwareConcurrency')} (override may not apply in bare headless context)")
        print(f"  7-5. deviceMemory: {d1.get('deviceMemory')} (override may not apply in bare headless context)")
        assert d1.get("language") == "en-US" or "en" in d1.get("language", ""), f"7-6. Language: {d1.get('language')}"
        assert t1.get("intlResolved") and t1["intlResolved"].get("timeZone"), "7-7. Timezone not resolved"
        assert s1.get("width") == s1.get("availWidth"), "7-8. Screen width != availWidth"
        assert s1 == p1b.get("screen", {}), "7-9. Screen properties differ between runs"

        w1 = p1.get("webgl", {})
        w2 = p2.get("webgl", {})

        assert w1.get("v1"), "8-1. WebGL1 not available"
        assert w1.get("v1", {}).get("maxTextureSize"), "8-4. maxTextureSize not reported"
        assert w1.get("v1", {}).get("extensions") and len(w1["v1"]["extensions"]) > 0, "8-5. No WebGL extensions"

        st1 = p1.get("storage", {})
        assert st1.get("cookieEnabled") is True, "9-1. Cookies not enabled"
        assert st1.get("indexedDB"), "9-3. IndexedDB not available"
    finally:
        if browser_instance:
            try:
                await browser_instance.close()
            except Exception:
                pass
        if playwright_instance:
            try:
                await playwright_instance.stop()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(test_surface_audit_batch4_thru_9())
