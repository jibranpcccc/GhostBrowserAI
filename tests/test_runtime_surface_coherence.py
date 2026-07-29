import os
import shutil
import sys
import tempfile
import unittest

from playwright.async_api import async_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.browser_manager import build_browser_launch_config
from backend.config import get_installed_chromium_version


class RuntimeSurfaceCoherenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_dpr_network_media_and_webgpu_shapes(self):
        profile_dir = tempfile.mkdtemp(prefix="ghost_surface_")
        old_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        version = get_installed_chromium_version()
        profile = {
            "id": "12345678-1234-1234-1234-123456789abc",
            "name": "runtime-surface-test",
            "path": profile_dir,
            "proxy": None,
            "timezone": "America/New_York",
            "locale": "en-US",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"
            ),
            "advanced": {
                "os": "Windows",
                "screen_resolution": "1536x864",
                "device_scale_factor": 1.25,
                "cpu_cores": 8,
                "memory_gb": 16,
                "webrtc_mode": "protected",
                "block_service_workers": False,
            },
            "fingerprint": {"client_hints": {}},
        }

        context = None
        try:
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
                    service_workers="allow",
                )
                await context.add_init_script(config["spoofing_script"])
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("data:text/html,<title>surface</title>")
                observed = await page.evaluate("""
                    () => {
                        const nativeLike = value => typeof value === 'function' &&
                            Function.prototype.toString.call(value).includes('[native code]');
                        return {
                            dpr: devicePixelRatio,
                            resolution: matchMedia(`(resolution: ${devicePixelRatio}dppx)`).matches,
                            screen: [screen.width, screen.height],
                            connection: !navigator.connection || (
                                Object.getPrototypeOf(navigator.connection) !== Object.prototype &&
                                nativeLike(navigator.connection.addEventListener)
                            ),
                            media: !navigator.mediaCapabilities || nativeLike(navigator.mediaCapabilities.decodingInfo),
                            webgpu: !navigator.gpu || nativeLike(navigator.gpu.requestAdapter)
                        };
                    }
                """)
                self.assertEqual(observed["dpr"], 1.25)
                self.assertTrue(observed["resolution"])
                self.assertEqual(observed["screen"], [1536, 864])
                self.assertTrue(observed["connection"])
                self.assertTrue(observed["media"])
                self.assertTrue(observed["webgpu"])
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    # The enclosing Playwright manager may already have closed
                    # the persistent context after an assertion or launch exit.
                    pass
            if old_test_env is None:
                os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
            else:
                os.environ["GHOSTBROWSER_TEST_ENV"] = old_test_env
            shutil.rmtree(profile_dir, ignore_errors=True)


    async def test_max_touch_points_coherent_with_desktop(self):
        profile_dir = tempfile.mkdtemp(prefix="ghost_mtp_")
        old_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        version = get_installed_chromium_version()
        profile = {
            "id": "12345678-1234-1234-1234-123456789abc",
            "name": "mtp-test",
            "path": profile_dir,
            "proxy": None,
            "timezone": "America/New_York",
            "locale": "en-US",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"
            ),
            "advanced": {
                "os": "Windows",
                "screen_resolution": "1536x864",
                "device_scale_factor": 1.25,
                "cpu_cores": 8,
                "memory_gb": 16,
            },
            "fingerprint": {"client_hints": {}},
        }

        context = None
        try:
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
                await page.goto("data:text/html,<title>mtp</title>")
                mtp = await page.evaluate("() => navigator.maxTouchPoints")
                self.assertIn(mtp, [0, 1])
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

    async def test_navigator_and_screen_descriptor_metadata_is_preserved(self):
        profile_dir = tempfile.mkdtemp(prefix="ghost_descriptor_")
        old_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        version = get_installed_chromium_version()
        profile = {
            "id": "abcdef12-1234-1234-1234-123456789abc",
            "name": "descriptor-test",
            "path": profile_dir,
            "proxy": None,
            "timezone": "Europe/London",
            "locale": "en-GB",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"
            ),
            "advanced": {
                "os": "Windows",
                "screen_resolution": "1536x864",
                "device_scale_factor": 1.25,
                "cpu_cores": 8,
                "memory_gb": 16,
            },
            "fingerprint": {"client_hints": {}},
        }

        context = None
        baseline_browser = None
        try:
            async with async_playwright() as playwright:
                baseline_browser = await playwright.chromium.launch(headless=True)
                baseline_page = await baseline_browser.new_page()
                baseline = await baseline_page.evaluate("""
                    () => {
                        const read = (obj, prop) => {
                            let target = obj;
                            let desc = undefined;
                            while (target) {
                                desc = Object.getOwnPropertyDescriptor(target, prop);
                                if (desc) break;
                                target = Object.getPrototypeOf(target);
                            }
                            return {
                                configurable: desc ? desc.configurable : null,
                                enumerable: desc ? desc.enumerable : null,
                                hasGet: typeof desc?.get === 'function',
                                getToString: typeof desc?.get === 'function'
                                    ? Function.prototype.toString.call(desc.get)
                                    : null
                            };
                        };
                        return {
                            hardwareConcurrency: read(navigator, 'hardwareConcurrency'),
                            deviceMemory: read(navigator, 'deviceMemory'),
                            userAgent: read(navigator, 'userAgent'),
                            language: read(navigator, 'language'),
                            languages: read(navigator, 'languages'),
                            maxTouchPoints: read(navigator, 'maxTouchPoints'),
                            screenWidth: read(window.screen, 'width'),
                            screenHeight: read(window.screen, 'height'),
                            screenAvailWidth: read(window.screen, 'availWidth'),
                            screenAvailHeight: read(window.screen, 'availHeight'),
                        };
                    }
                """)
                await baseline_page.close()
                await baseline_browser.close()
                baseline_browser = None

                config = await build_browser_launch_config(profile, force_headless=True)
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
                await page.goto("data:text/html,<title>desc</title>")
                spoofed = await page.evaluate("""
                    () => {
                        const read = (obj, prop) => {
                            let target = obj;
                            let desc = undefined;
                            while (target) {
                                desc = Object.getOwnPropertyDescriptor(target, prop);
                                if (desc) break;
                                target = Object.getPrototypeOf(target);
                            }
                            return {
                                configurable: desc ? desc.configurable : null,
                                enumerable: desc ? desc.enumerable : null,
                                hasGet: typeof desc?.get === 'function',
                                getToString: typeof desc?.get === 'function'
                                    ? Function.prototype.toString.call(desc.get)
                                    : null
                            };
                        };
                        return {
                            hardwareConcurrency: read(navigator, 'hardwareConcurrency'),
                            deviceMemory: read(navigator, 'deviceMemory'),
                            userAgent: read(navigator, 'userAgent'),
                            language: read(navigator, 'language'),
                            languages: read(navigator, 'languages'),
                            maxTouchPoints: read(navigator, 'maxTouchPoints'),
                            screenWidth: read(window.screen, 'width'),
                            screenHeight: read(window.screen, 'height'),
                            screenAvailWidth: read(window.screen, 'availWidth'),
                            screenAvailHeight: read(window.screen, 'availHeight'),
                        };
                    }
                """)

                for prop in (
                    "hardwareConcurrency",
                    "deviceMemory",
                    "userAgent",
                    "language",
                    "languages",
                    "maxTouchPoints",
                ):
                    # Only compare descriptor shape when the browser natively exposes the property.
                    if baseline[prop]["configurable"] is not None:
                        self.assertEqual(
                            spoofed[prop]["configurable"],
                            baseline[prop]["configurable"],
                            f"navigator.{prop} configurable mismatch",
                        )
                        self.assertEqual(
                            spoofed[prop]["enumerable"],
                            baseline[prop]["enumerable"],
                            f"navigator.{prop} enumerable mismatch",
                        )
                    self.assertTrue(spoofed[prop]["hasGet"], f"navigator.{prop} getter missing")
                    self.assertIn("[native code]", spoofed[prop]["getToString"])

                for prop in ("screenWidth", "screenHeight", "screenAvailWidth", "screenAvailHeight"):
                    if baseline[prop]["configurable"] is not None:
                        self.assertEqual(
                            spoofed[prop]["configurable"],
                            baseline[prop]["configurable"],
                            f"{prop} configurable mismatch",
                        )
                        self.assertEqual(
                            spoofed[prop]["enumerable"],
                            baseline[prop]["enumerable"],
                            f"{prop} enumerable mismatch",
                        )
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if baseline_browser:
                try:
                    await baseline_browser.close()
                except Exception:
                    pass
            if old_test_env is None:
                os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
            else:
                os.environ["GHOSTBROWSER_TEST_ENV"] = old_test_env
            shutil.rmtree(profile_dir, ignore_errors=True)

    async def test_timezone_language_and_ua_are_consistent(self):
        profile_dir = tempfile.mkdtemp(prefix="ghost_locale_")
        old_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        version = get_installed_chromium_version()
        profile = {
            "id": "fedcba98-1234-1234-1234-123456789abc",
            "name": "locale-consistency-test",
            "path": profile_dir,
            "proxy": None,
            "timezone": "Asia/Tokyo",
            "locale": "ja-JP",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"
            ),
            "advanced": {
                "os": "Windows",
                "screen_resolution": "1536x864",
                "device_scale_factor": 1.25,
                "cpu_cores": 4,
                "memory_gb": 8,
            },
            "fingerprint": {"client_hints": {}},
        }

        context = None
        try:
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
                await page.goto("data:text/html,<title>locale</title>")
                observed = await page.evaluate("""
                    () => {
                        return {
                            timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                            language: navigator.language,
                            languages: navigator.languages,
                            userAgent: navigator.userAgent,
                        };
                    }
                """)
                self.assertEqual(observed["timeZone"], "Asia/Tokyo")
                self.assertTrue(observed["language"].startswith("ja"))
                self.assertTrue(observed["languages"][0].startswith("ja"))
                self.assertIn("Windows NT 10.0", observed["userAgent"])
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
