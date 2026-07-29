import os
import shutil
import sys
import tempfile
import unittest
import uuid

from playwright.async_api import async_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.browser_manager import (
    _origin_allows_client_hints,
    _persist_accept_ch_opt_in,
    build_browser_launch_config,
    profile_opted_origins,
)
from backend.config import get_installed_chromium_major_version


class ClientHintsBatch5Tests(unittest.IsolatedAsyncioTestCase):
    def test_secure_origin_allowed_for_client_hints(self):
        self.assertTrue(_origin_allows_client_hints("https://example.com"))
        self.assertTrue(_origin_allows_client_hints("https://example.com:8443/path"))

    def test_localhost_origin_allowed_for_client_hints(self):
        self.assertTrue(_origin_allows_client_hints("http://localhost:8080"))
        self.assertTrue(_origin_allows_client_hints("http://127.0.0.1:8080"))

    def test_insecure_non_local_origin_denied_for_client_hints(self):
        self.assertFalse(_origin_allows_client_hints("http://example.com"))
        self.assertFalse(_origin_allows_client_hints("http://192.168.1.1"))
        self.assertFalse(_origin_allows_client_hints("ftp://example.com"))

    def test_secure_accept_ch_is_persisted(self):
        profile_id = str(uuid.uuid4())
        profile_dir = tempfile.mkdtemp(prefix="ghost_accept_ch_secure_")
        try:
            profile_opted_origins.pop(profile_id, None)
            _persist_accept_ch_opt_in(
                profile_id,
                profile_dir,
                "https://secure.example.test/page",
                "sec-ch-ua-platform, sec-ch-ua-arch",
            )
            self.assertIn("https://secure.example.test", profile_opted_origins.get(profile_id, {}))
        finally:
            profile_opted_origins.pop(profile_id, None)
            shutil.rmtree(profile_dir, ignore_errors=True)

    def test_insecure_non_local_accept_ch_is_not_persisted(self):
        profile_id = str(uuid.uuid4())
        profile_dir = tempfile.mkdtemp(prefix="ghost_accept_ch_insecure_")
        try:
            profile_opted_origins.pop(profile_id, None)
            _persist_accept_ch_opt_in(
                profile_id,
                profile_dir,
                "http://insecure.example.test/page",
                "sec-ch-ua-platform, sec-ch-ua-arch",
            )
            self.assertNotIn("http://insecure.example.test", profile_opted_origins.get(profile_id, {}))
        finally:
            profile_opted_origins.pop(profile_id, None)
            shutil.rmtree(profile_dir, ignore_errors=True)

    async def test_stored_ua_reconciled_to_installed_chromium_version(self):
        installed_major = str(get_installed_chromium_major_version())
        profile_dir = tempfile.mkdtemp(prefix="ghost_ua_reconcile_")
        old_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        try:
            profile = {
                "id": "12345678-1234-1234-1234-123456789abc",
                "name": "ua-reconcile-test",
                "path": profile_dir,
                "proxy": None,
                "timezone": "UTC",
                "locale": "en-US",
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"
                ),
                "advanced": {
                    "os": "Windows",
                    "screen_resolution": "1920x1080",
                    "device_scale_factor": 1.0,
                    "cpu_cores": 4,
                    "memory_gb": 8,
                    "webrtc_mode": "protected",
                },
            }
            config = await build_browser_launch_config(profile, force_headless=True)

            ua_match = __import__("re").search(r"Chrome/(\d+)", config["user_agent"])
            self.assertIsNotNone(ua_match)
            self.assertEqual(ua_match.group(1), installed_major)

            brands = config.get("userAgentMetadata", {}).get("brands", [])
            chromium_brand = next((b for b in brands if "Chromium" in b.get("brand", "")), None)
            if chromium_brand:
                self.assertEqual(chromium_brand["version"], installed_major)
        finally:
            if old_test_env is None:
                os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
            else:
                os.environ["GHOSTBROWSER_TEST_ENV"] = old_test_env
            shutil.rmtree(profile_dir, ignore_errors=True)

    async def test_launched_ua_matches_user_agent_data(self):
        installed_major = str(get_installed_chromium_major_version())
        profile_dir = tempfile.mkdtemp(prefix="ghost_ua_runtime_")
        old_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        context = None
        try:
            profile = {
                "id": "22345678-1234-1234-1234-123456789abc",
                "name": "ua-runtime-test",
                "path": profile_dir,
                "proxy": None,
                "timezone": "America/New_York",
                "locale": "en-US",
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"
                ),
                "advanced": {
                    "os": "Windows",
                    "screen_resolution": "1920x1080",
                    "device_scale_factor": 1.0,
                    "cpu_cores": 4,
                    "memory_gb": 8,
                },
            }

            config = await build_browser_launch_config(profile, force_headless=True)
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=config["headless"],
                    args=config["args"],
                    user_agent=config["user_agent"],
                    viewport=config["viewport"],
                    device_scale_factor=config["device_scale_factor"],
                    locale=config["locale"],
                    timezone_id=config["timezone_id"],
                )
                await context.add_init_script(config["spoofing_script"])
                page = context.pages[0] if context.pages else await context.new_page()

                observed = await page.evaluate("""
                    async () => {
                        let brands = [];
                        if (navigator.userAgentData && navigator.userAgentData.brands) {
                            brands = navigator.userAgentData.brands;
                        }
                        let fullList = [];
                        try {
                            const hev = await navigator.userAgentData.getHighEntropyValues(['fullVersionList']);
                            fullList = hev.fullVersionList || [];
                        } catch (e) {}
                        return {
                            userAgent: navigator.userAgent,
                            brands: brands,
                            fullVersionList: fullList
                        };
                    }
                """)

                ua_match = __import__("re").search(r"Chrome/(\d+)", observed["userAgent"])
                self.assertIsNotNone(ua_match)
                self.assertEqual(ua_match.group(1), installed_major)

                # navigator.userAgentData may be unavailable in some CI/headless environments.
                # When present, its Chromium brand/version must match the installed binary.
                if observed["brands"]:
                    chromium_brand = next(
                        (b for b in observed["brands"] if b.get("brand") == "Chromium"), None
                    )
                    if chromium_brand:
                        self.assertEqual(str(chromium_brand["version"]), installed_major)

                chromium_full = next(
                    (b for b in observed["fullVersionList"] if b.get("brand") == "Chromium"), None
                )
                if chromium_full:
                    self.assertTrue(str(chromium_full["version"]).startswith(installed_major + "."))
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if old_test_env is None:
                os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
            else:
                os.environ["GHOSTBROWSER_TEST_ENV"] = old_test_env
            shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
