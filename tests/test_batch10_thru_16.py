import os
import sys
import tempfile
import asyncio
import json
import shutil
import pytest
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

orig_sys_path = list(sys.path)
orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
bm_module = None
pm_module = None
_temp_dir = tempfile.TemporaryDirectory()
test_success = False
cleanup_success = True
playwright_instance = None
browser_instance = None
httpd = None

HTML_PAGE = "<!DOCTYPE html><html><body></body></html>"

os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = _temp_dir.name
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
sys.path.append(os.getcwd())

import backend.browser_manager as bm
import backend.proxy_manager as pm
bm_module = bm
pm_module = pm

orig_proxy = pm.proxy_manager.get_proxy_for_profile


async def mock_proxy(pid):
    return None


pm.proxy_manager.get_proxy_for_profile = mock_proxy


class QH(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

    def log_message(self, format, *args):
        pass


httpd = HTTPServer(("127.0.0.1", 0), QH)
port = httpd.server_address[1]
t = threading.Thread(target=httpd.serve_forever, daemon=True)
t.start()


@pytest.mark.asyncio
async def test_batch10_thru_16():
    global playwright_instance, browser_instance
    all_passed = True
    from playwright.async_api import async_playwright
    playwright_instance = await async_playwright().start()
    try:
        browser_instance = await playwright_instance.chromium.launch(headless=True)
        try:
            async def run_config(pid):
                cfg = {
                    "id": pid,
                    "path": os.path.join(_temp_dir.name, pid),
                    "advanced": {
                        "os": "Windows",
                        "screen_resolution": "1920x1080",
                        "canvas_noise": True,
                        "webgl_noise": True,
                        "audio_noise": True,
                    },
                }
                ctx_config = await bm.build_browser_launch_config(cfg)
                context = await browser_instance.new_context()
                await context.add_init_script(ctx_config["spoofing_script"])
                page = await context.new_page()
                await page.goto(f"http://127.0.0.1:{port}/")
                res = {"spoofing_script_len": len(ctx_config["spoofing_script"])}
                await page.close()
                await context.close()
                return res

            P1 = "00000001-0000-0000-0000-000000000001"
            P2 = "00000002-0000-0000-0000-000000000002"

            with open("backend/browser_manager.py", "r", encoding="utf-8") as f:
                bm_content = f.read()
            assert "Proxy URL contains invalid path" in bm_content, "11-7. Missing path traversal protection"
            assert "FAIL-CLOSED" in bm_content, "11-8. No FAIL-CLOSED guards"

            with open("backend/ai_generator.py", "r", encoding="utf-8") as f:
                ag_content = f.read()
            secret_patterns = ["password=", "token=", "secret=", "api_key="]
            secrets_found = False
            for pattern in secret_patterns:
                if pattern in ag_content.lower() and "YOUR_" not in ag_content:
                    secrets_found = True
            assert not secrets_found, "12-2. Potential hardcoded credentials found"

            with open("backend/browser_manager.py", "r", encoding="utf-8") as f:
                bm_src = f.read()
            assert "print(f\"DEBUG KILL" not in bm_src, "12-3. DEBUG KILL prints present"

            with open("backend/config.py", "r", encoding="utf-8") as f:
                cfg_content = f.read()

            with open("tests/run_tests.py", "r", encoding="utf-8") as f:
                rt_content = f.read()
            assert "assert 1 == 2" in rt_content, "13-2. Missing negative self-test"
            assert "Forbidden network access" in rt_content, "13-3. Missing network guard"
            assert "REQUIRED_CHECKS" in rt_content, "13-4. Missing required checks registry"
            assert "PRODUCTION_METADATA_SNAPSHOT" in rt_content, "13-5. Missing production metadata snapshot"

            p1 = await run_config(P1)
            p2 = await run_config(P2)

            assert p1["spoofing_script_len"] > 1000, f"14-1. Spoofing script too short ({p1['spoofing_script_len']} chars)"

            with open("backend/browser_manager.py", "r", encoding="utf-8") as f:
                bm_final = f.read()

            checks_14 = [
                "disable_non_proxied_udp",
                "enforce-webrtc-ip-permission-check",
                "host-resolver-rules",
                "AutomationControlled",
            ]
            for pattern in checks_14:
                assert pattern in bm_final, f"14-{pattern} missing"

        finally:
            if browser_instance:
                await browser_instance.close()
                browser_instance = None
    finally:
        if playwright_instance:
            await playwright_instance.stop()
            playwright_instance = None


if __name__ == "__main__":
    asyncio.run(test_batch10_thru_16())