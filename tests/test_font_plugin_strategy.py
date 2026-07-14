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

FONT_PLUGIN_JS = r"""
async function getFontPluginData() {
    var results = { fonts: {}, plugins: {} };
    try {
        var fontProbes = ['Segoe UI', 'Arial', 'Times New Roman', 'Courier New', 'Calibri', 'Consolas', 'Tahoma', 'Verdana', 'Georgia', 'GhostProfileOneFont', 'FakeNonexistentFont123'];
        results.fonts.check = {};
        results.fonts.metrics = {};
        var cvs = document.createElement('canvas');
        var ctx2d = cvs.getContext('2d');
        for (var fi=0; fi<fontProbes.length; fi++) {
            var f = fontProbes[fi];
            var fontStr = '16px "' + f + '"';
            var chk = false;
            try { chk = document.fonts.check(fontStr); } catch(e){}
            results.fonts.check[f] = chk;
            ctx2d.font = fontStr + ', "fallback-font-xyz"';
            var m = ctx2d.measureText("Test Metrics 123");
            results.fonts.metrics[f] = { width: m.width, asc: m.actualBoundingBoxAscent, desc: m.actualBoundingBoxDescent };
        }
        results.fonts.fontFaceSetSize = document.fonts.size;
        results.fonts.readyIsFunction = typeof document.fonts.ready.then === 'function';
    } catch(e) { results.fonts.error = e.message; }
    try {
        results.plugins.list = [];
        for(var i=0; i<navigator.plugins.length; i++) {
            var p = navigator.plugins[i];
            var mt = [];
            for(var j=0; j<p.length; j++) { mt.push(p[j].type); }
            results.plugins.list.push({ name: p.name, filename: p.filename, description: p.description, length: p.length, mimeTypes: mt });
        }
        results.plugins.mimeTypes = [];
        for(var i=0; i<navigator.mimeTypes.length; i++) {
            var m = navigator.mimeTypes[i];
            results.plugins.mimeTypes.push({ type: m.type, suffixes: m.suffixes, description: m.description });
        }
        results.plugins.pluginCount = navigator.plugins.length;
        results.plugins.mimeTypeCount = navigator.mimeTypes.length;
    } catch(e) { results.plugins.error = e.message; }
    return results;
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
async def test_font_plugin_strategy():
    all_passed = True
    playwright_instance = None
    browser_instance = None
    try:
        from playwright.async_api import async_playwright
        playwright_instance = await async_playwright().start()
        browser_instance = await playwright_instance.chromium.launch(headless=True)

        async def run_config(profile_data):
            config = {
                "id": profile_data["id"],
                "path": os.path.join(_temp_dir.name, profile_data["id"]),
                "advanced": {
                    "os": "Windows",
                    "screen_resolution": "1920x1080",
                    "canvas_noise": False,
                    "webgl_noise": False,
                    "audio_noise": False,
                },
            }
            ctx_config = await bm.build_browser_launch_config(config)
            context = await browser_instance.new_context()
            await context.add_init_script(ctx_config["spoofing_script"])
            page = await context.new_page()
            await page.goto(f"http://127.0.0.1:{_port}/")
            await page.add_script_tag(content=FONT_PLUGIN_JS)
            res = await page.evaluate("getFontPluginData()")
            await page.close()
            await context.close()
            return res

        NATIVE = {"id": "00000000-0000-0000-0000-000000000000"}
        P1 = {"id": "00000001-0000-0000-0000-000000000001"}
        P2 = {"id": "00000002-0000-0000-0000-000000000002"}

        native = await run_config(NATIVE)
        p1a = await run_config(P1)
        p1b = await run_config(P1)
        p2 = await run_config(P2)

        def _f(r):
            return json.dumps(
                {
                    "check": r.get("fonts", {}).get("check"),
                    "metrics": r.get("fonts", {}).get("metrics"),
                },
                sort_keys=True,
            )

        def _pl(r):
            return json.dumps(
                {
                    "plugins": r.get("plugins", {}).get("list"),
                    "mimes": r.get("plugins", {}).get("mimeTypes"),
                },
                sort_keys=True,
            )

        assert _f(p1a) == _f(p1b), "3C-1. Profile 1 font results stable across runs"
        assert _f(native) == _f(p1a), "3C-2. Profile 1 fonts match native (correct: do not apply AI fonts)"

        real_fonts = [
            "Segoe UI", "Arial", "Times New Roman", "Courier New",
            "Calibri", "Consolas",
        ]
        fake_fonts = ["GhostProfileOneFont", "FakeNonexistentFont123"]
        fake_results_native = [native["fonts"]["check"].get(f) for f in fake_fonts]
        fake_results_p1 = [p1a["fonts"]["check"].get(f) for f in fake_fonts]
        assert fake_results_native == fake_results_p1, "3C-4. Spoofing must not alter fake font detection behavior"
        assert p1a["fonts"]["readyIsFunction"], "3C-5. document.fonts.ready is not a function"
        assert _f(native) == _f(p2), "3C-6. Profile 2 fonts differ from native"

        assert _pl(p1a) == _pl(p1b), "3D-1. Profile 1 plugins stable across runs"
        assert _pl(native) == _pl(p1a), "3D-2. Profile 1 plugins differ from native"
        assert p1a["plugins"]["pluginCount"] >= 0, "3D-3. Plugin count issue"
        assert _pl(native) == _pl(p2), "3D-4. Profile 2 plugins differ from native"
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
    asyncio.run(test_font_plugin_strategy())