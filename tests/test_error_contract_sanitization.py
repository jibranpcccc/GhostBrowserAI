"""P1: error-contract sanitization regression tests.

Any exception detail that originates server-side must be collapsed into a
stable public code + message before it can reach an API client. These tests
pin the fix at every leak site found by the audit:

- bulk create non-success path (raw ``result["message"]`` passthrough)
- profile_creator ``CHROMIUM_VERSION_MISSING`` / ``KIMI_UNAVAILABLE`` (``str(e)``)
- close_profile single-API failure (``Close failed: {str(e)}``)
- synchronizer ``broadcast_action`` per-profile errors (``str(exc)``)
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend import bulk_operations
from backend import synchronizer


class TestBulkCreateMessageSanitized(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_internal_code_collapsed_to_create_failed(self):
        orig_creator = bulk_operations.profile_creator.create_zero_leak_profile

        async def leaky_create(name, **kwargs):
            return {"status": "error", "code": "SECRET_INTERNAL", "message": "hijack internal detail 12345"}

        bulk_operations.profile_creator.create_zero_leak_profile = leaky_create
        try:
            result = await bulk_operations.bulk_create_profiles(
                bulk_operations.BulkCreateRequest(base_name="leak", count=1)
            )
        finally:
            bulk_operations.profile_creator.create_zero_leak_profile = orig_creator

        item = result["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["code"], "CREATE_FAILED")
        self.assertEqual(item["message"], "Profile creation failed")
        self.assertNotIn("hijack internal detail 12345", item["message"])

    async def test_known_public_codes_surface_canonical_messages(self):
        orig_creator = bulk_operations.profile_creator.create_zero_leak_profile
        cases = [
            ("KIMI_TIMEOUT", "AI fingerprint generation timed out"),
            ("KIMI_UNAVAILABLE", "Strict AI fingerprint service unavailable"),
            ("CHROMIUM_VERSION_MISSING", "Cannot determine installed Chromium version"),
        ]
        for code, expected_message in cases:
            with self.subTest(code=code):
                async def fail_create(name, _code=code, **kwargs):
                    return {"status": "error", "code": _code, "message": "raw internal noise"}

                bulk_operations.profile_creator.create_zero_leak_profile = fail_create
                try:
                    result = await bulk_operations.bulk_create_profiles(
                        bulk_operations.BulkCreateRequest(base_name="known", count=1)
                    )
                finally:
                    bulk_operations.profile_creator.create_zero_leak_profile = orig_creator

                item = result["results"][0]
                self.assertEqual(item["status"], "error")
                self.assertEqual(item["code"], code)
                self.assertEqual(item["message"], expected_message)
                self.assertNotIn("raw internal noise", item["message"])

    def test_public_catalog_contains_new_codes(self):
        for code in ("KIMI_TIMEOUT", "CHROMIUM_VERSION_MISSING", "CREATE_FAILED",
                     "LAUNCH_FAILED", "CLOSE_FAILED", "DELETE_FAILED"):
            self.assertIn(code, bulk_operations._PUBLIC_CODE_MESSAGES)


class TestProfileCreatorSanitized(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orig_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

    def tearDown(self):
        if self.orig_test_env is None:
            os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
        else:
            os.environ["GHOSTBROWSER_TEST_ENV"] = self.orig_test_env

    async def test_kimi_unavailable_drops_exception_detail(self):
        from backend import profile_creator

        async def boom(*args, **kwargs):
            raise RuntimeError("secret kimi detail 42")

        with patch.object(profile_creator, "generate_fingerprint_ai", boom):
            result = await profile_creator.create_zero_leak_profile(name="KimiSanitized")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "KIMI_UNAVAILABLE")
        self.assertEqual(result["message"], "Strict AI fingerprint service unavailable")
        self.assertNotIn("secret kimi detail 42", str(result))

    async def test_chromium_version_missing_drops_exception_detail(self):
        from backend import profile_creator

        def boom():
            raise RuntimeError("secret version detail 99")

        with patch.object(profile_creator, "get_installed_chromium_major_version", boom):
            result = await profile_creator.create_zero_leak_profile(name="VerSanitized")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "CHROMIUM_VERSION_MISSING")
        self.assertEqual(result["message"], "Cannot determine installed Chromium version")
        self.assertNotIn("secret version detail 99", str(result))

    async def test_ai_os_mismatch_uses_catalog_code_not_raw_os(self):
        from backend import profile_creator

        async def bad_fp(*args, **kwargs):
            return {"os": "FreeBSD-raw-value", "screen_resolution": "1920x1080",
                    "cpu_cores": 8, "memory_gb": 16, "timezone": "UTC",
                    "locale": "en-US", "languages": ["en"], "userAgent": "ua",
                    "webgl_vendor": "v", "webgl_renderer": "r", "canvas": "c"}

        with patch.object(profile_creator, "get_installed_chromium_major_version", return_value=140), \
             patch.object(profile_creator, "generate_fingerprint_ai", bad_fp):
            result = await profile_creator.create_zero_leak_profile(name="FpSanitized")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "FINGERPRINT_MISMATCH")
        self.assertEqual(result["message"], "Generated fingerprint failed environment validation")
        self.assertNotIn("FreeBSD-raw-value", str(result))


class TestCloseProfileSanitized(unittest.IsolatedAsyncioTestCase):
    async def test_close_failure_drops_exception_detail(self):
        from backend import browser_manager
        from backend import lock_manager

        def boom(*args, **kwargs):
            raise RuntimeError("secret close detail 7")

        # get_profile is called inside _do_close_profile's try block (lock
        # acquire happens before it, so raising there would escape uncaught).
        with patch.object(browser_manager.profile_manager, "get_profile", boom):
            result = await browser_manager.close_profile("p-does-not-exist")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "CLOSE_FAILED")
        self.assertEqual(result["message"], "Close failed")
        self.assertNotIn("secret close detail 7", str(result))


class TestSynchronizerSanitized(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_action_drops_exception_detail(self):
        from backend import browser_manager

        orig_state = synchronizer._sync_state
        orig_active = browser_manager.active_browsers

        async def boom(*args, **kwargs):
            raise RuntimeError("secret sync detail 77")

        synchronizer._sync_state = {"profile_ids": ["p1"], "actions_sent": 0}
        browser_manager.active_browsers["p1"] = {"page": object()}
        with patch.object(synchronizer, "_execute_action", boom):
            try:
                result = await synchronizer.broadcast_action(
                    synchronizer.SyncActionRequest(action_type="reload")
                )
            finally:
                synchronizer._sync_state = orig_state
                browser_manager.active_browsers.pop("p1", None)
                if orig_active is not browser_manager.active_browsers:
                    browser_manager.active_browsers = orig_active

        self.assertEqual(result["status"], "success")
        item = result["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["code"], "ACTION_FAILED")
        self.assertEqual(item["message"], "Action failed")
        self.assertNotIn("secret sync detail 77", str(item))


class TestLaunchFailClosedSanitized(unittest.IsolatedAsyncioTestCase):
    """A FAIL-CLOSED launch abort must surface the stable LAUNCH_FAILED message,
    never the raw RuntimeError text (which used to embed exception details)."""

    async def test_fail_closed_launch_error_drops_internal_detail(self):
        from backend import browser_manager as bm
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            profile = {
                "id": "lp1",
                "path": td,
                "proxy": None,
                "fingerprint": {"os": "Windows"},
            }

            # Production calls scan_profile_before_launch synchronously; keep
            # the mock sync so the scan step is really exercised (an async mock
            # would trigger an unintended TypeError/unawaited-coroutine path).
            scan_calls = []

            def fake_scan(profile):
                scan_calls.append(profile)
                return {"status": "clean"}

            def boom(profile, force_headless=False, forced_proxy=None):
                raise RuntimeError("FAIL-CLOSED: secret internal launch detail 555")

            async def noop_acquire(pid):
                pass

            with patch.object(bm.profile_manager, "get_profile", return_value=profile), \
                 patch.object(bm.profile_manager, "PROFILES_DIR", Path(td).parent), \
                 patch.object(bm, "_profile_has_verified_provenance", return_value=True), \
                 patch.object(bm.ai_scanner, "scan_profile_before_launch", fake_scan), \
                 patch("backend.lock_manager.lock_manager.acquire", noop_acquire), \
                 patch.object(bm, "build_browser_launch_config", boom):
                result = await bm.launch_profile("lp1")

        self.assertEqual(scan_calls, [profile], "the sync pre-launch scan must be reached")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "Launch failed")
        self.assertNotIn("secret internal launch detail 555", str(result))

    async def test_wrapper_fallback_drops_exception_detail(self):
        from backend import browser_manager as bm

        async def boom(pid, force_headless=False, pin=None):
            raise RuntimeError("secret wrapper detail 999")

        with patch.object(bm, "_do_launch_profile", boom):
            result = await bm.launch_profile("p-x", force_headless=True)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "Launch failed")
        self.assertNotIn("secret wrapper detail 999", str(result))

    async def test_early_return_traversal_collapses(self):
        from backend import browser_manager as bm

        profile = {"id": "lp2", "path": "C:/outside/profiles/x", "proxy": None}
        with patch.object(bm.profile_manager, "get_profile", return_value=profile), \
             patch.object(bm.profile_manager, "PROFILES_DIR", "C:/profiles"):
            result = await bm._do_launch_profile("lp2")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "Launch failed")
        self.assertEqual(result.get("code"), "LAUNCH_FAILED")
        self.assertNotIn("FAIL-CLOSED", str(result))
        self.assertNotIn("Directory traversal", str(result))

    async def test_early_return_missing_dir_collapses(self):
        from backend import browser_manager as bm
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            profile = {"id": "lp5", "path": str(Path(td) / "nonexistent"), "proxy": None}
            with patch.object(bm.profile_manager, "get_profile", return_value=profile), \
                 patch.object(bm.profile_manager, "PROFILES_DIR", Path(td).parent):
                result = await bm._do_launch_profile("lp5")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "Launch failed")
        self.assertEqual(result.get("code"), "LAUNCH_FAILED")
        self.assertNotIn("missing", str(result))

    async def test_early_return_provenance_collapses(self):
        from backend import browser_manager as bm
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            profile = {"id": "lp3", "path": td, "proxy": None}
            with patch.object(bm.profile_manager, "get_profile", return_value=profile), \
                 patch.object(bm.profile_manager, "PROFILES_DIR", Path(td).parent), \
                 patch.object(bm, "_profile_has_verified_provenance", return_value=False):
                result = await bm._do_launch_profile("lp3")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "Launch failed")
        self.assertEqual(result.get("code"), "LAUNCH_FAILED")
        self.assertNotIn("provenance", str(result))

    async def test_early_return_invalid_pinned_proxy_collapses(self):
        from backend import browser_manager as bm
        import tempfile
        from pathlib import Path

        def clean_scan(profile):
            return {"status": "clean"}

        with tempfile.TemporaryDirectory() as td:
            profile = {"id": "lp6", "path": td, "proxy": None, "proxy_pin": "ftp://bad:8080"}
            with patch.object(bm.profile_manager, "get_profile", return_value=profile), \
                 patch.object(bm.profile_manager, "PROFILES_DIR", Path(td).parent), \
                 patch.object(bm, "_profile_has_verified_provenance", return_value=True), \
                 patch.object(bm.ai_scanner, "scan_profile_before_launch", clean_scan):
                result = await bm._do_launch_profile("lp6")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "Launch failed")
        self.assertEqual(result.get("code"), "LAUNCH_FAILED")
        self.assertNotIn("pinned proxy", str(result))

    async def test_scanner_block_collapses(self):
        from backend import browser_manager as bm
        import tempfile
        from pathlib import Path

        def blocking_scan(profile):
            return {"status": "blocked", "message": "secret scanner detail 123"}

        with tempfile.TemporaryDirectory() as td:
            profile = {"id": "lp4", "path": td, "proxy": None}
            with patch.object(bm.profile_manager, "get_profile", return_value=profile), \
                 patch.object(bm.profile_manager, "PROFILES_DIR", Path(td).parent), \
                 patch.object(bm, "_profile_has_verified_provenance", return_value=True), \
                 patch.object(bm.ai_scanner, "scan_profile_before_launch", blocking_scan):
                result = await bm._do_launch_profile("lp4")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "Launch failed")
        self.assertNotIn("secret scanner detail 123", str(result))


if __name__ == "__main__":
    unittest.main()
