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
        self.assertEqual(result["message"], "Validation failed: cannot determine installed Chromium version")
        self.assertNotIn("secret version detail 99", str(result))


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


if __name__ == "__main__":
    unittest.main()
