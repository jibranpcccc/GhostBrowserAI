"""CDP test-mode contract tests (reviewer P0).

Covers the ``DevToolsActivePort`` polling helper (``read_devtools_active_port``)
and the ``GET /api/profiles/{profile_id}/cdp`` endpoint:

- the helper parses the Chromium-written port + WebSocket path
- it ignores incomplete/invalid files and times out cleanly instead of hanging
- the endpoint requires admin auth
- the endpoint reports profiles that are not running or lack CDP
- the endpoint returns ``cdp_url`` + ``cdp_ws_url`` built from the stored WS path
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

ADMIN_TOKEN = "cdp-test-token-12345"
PROFILE_ID = "cdp-profile-1"


class ReadDevToolsActivePortTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_valid_file(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "DevToolsActivePort").write_text(
                "43210\n/devtools/browser/abc123\n", encoding="utf-8"
            )
            from backend.browser_manager import read_devtools_active_port

            port, ws_path = await read_devtools_active_port(td, timeout=1.0, poll_interval=0.01)
            self.assertEqual((port, ws_path), (43210, "/devtools/browser/abc123"))

    async def test_waits_for_incomplete_file_to_become_valid(self):
        with tempfile.TemporaryDirectory() as td:
            port_file = Path(td) / "DevToolsActivePort"
            port_file.write_text("43210\n", encoding="utf-8")

            from backend.browser_manager import read_devtools_active_port

            async def populate_later():
                await asyncio.sleep(0.05)
                port_file.write_text("43210\n/devtools/browser/later\n", encoding="utf-8")

            populate = asyncio.create_task(populate_later())
            try:
                port, ws_path = await read_devtools_active_port(td, timeout=2.0, poll_interval=0.01)
            finally:
                populate.cancel()
            self.assertEqual((port, ws_path), (43210, "/devtools/browser/later"))

    async def test_rejects_zero_port_and_bad_ws_path(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "DevToolsActivePort").write_text(
                "0\n/devtools/browser/nope\n", encoding="utf-8"
            )
            from backend.browser_manager import read_devtools_active_port

            port, ws_path = await read_devtools_active_port(td, timeout=0.15, poll_interval=0.01)
            self.assertIsNone(port)
            self.assertIsNone(ws_path)

    async def test_malformed_content_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "DevToolsActivePort").write_text(
                "not-a-number\n/devtools/browser/x\n", encoding="utf-8"
            )
            from backend.browser_manager import read_devtools_active_port

            port, ws_path = await read_devtools_active_port(td, timeout=0.15, poll_interval=0.01)
            self.assertIsNone(port)
            self.assertIsNone(ws_path)

    async def test_times_out_when_file_never_appears(self):
        with tempfile.TemporaryDirectory() as td:
            from backend.browser_manager import read_devtools_active_port

            started = asyncio.get_running_loop().time()
            port, ws_path = await read_devtools_active_port(td, timeout=0.15, poll_interval=0.01)
            elapsed = asyncio.get_running_loop().time() - started
            self.assertIsNone(port)
            self.assertIsNone(ws_path)
            self.assertGreaterEqual(elapsed, 0.12)


class ProfileCdpEndpointTests(unittest.TestCase):
    def setUp(self):
        self.orig_token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
        self.orig_cdp = os.environ.get("GHOSTBROWSER_CDP_TEST")
        self.orig_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = ADMIN_TOKEN
        os.environ["GHOSTBROWSER_CDP_TEST"] = "1"
        # Self-contained: this class runs under unittest discover too, where
        # conftest.py is not loaded, so TEST_ENV must be set here explicitly.
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        self._patchers = [
            mock.patch("backend.system_monitor.system_monitor.start", new=mock.AsyncMock()),
            mock.patch("backend.system_monitor.system_monitor.stop", new=mock.Mock()),
            mock.patch("backend.main.scheduler_manager.start", new=mock.Mock()),
            mock.patch("backend.main.scheduler_manager.stop", new=mock.Mock()),
            mock.patch("backend.ai_generator._shared_client.aclose", new=mock.AsyncMock()),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self._patchers):
            patcher.stop()
        for name, orig in (
            ("GHOSTBROWSER_ADMIN_TOKEN", self.orig_token),
            ("GHOSTBROWSER_CDP_TEST", self.orig_cdp),
            ("GHOSTBROWSER_TEST_ENV", self.orig_test_env),
        ):
            if orig is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = orig

    def _get_cdp(self, client, token=None, profile_id=PROFILE_ID):
        headers = {}
        if token is not None:
            headers["X-Admin-Token"] = token
        return client.get(f"/api/profiles/{profile_id}/cdp", headers=headers)

    def _running_browser(self, cdp_port=None, cdp_ws_path=None):
        return {
            "playwright": None,
            "context": None,
            "page": None,
            "pid": 1,
            "proxy": None,
            "args": [],
            "cdp_port": cdp_port,
            "cdp_ws_path": cdp_ws_path,
        }

    def test_requires_admin_token(self):
        from backend.main import app

        with TestClient(app) as client:
            resp = self._get_cdp(client, token=None)
            self.assertEqual(resp.status_code, 401)

    def test_profile_not_running_returns_400(self):
        from backend.main import app

        with TestClient(app) as client, mock.patch.dict(
            "backend.browser_manager.active_browsers", {}, clear=True
        ):
            resp = self._get_cdp(client, token=ADMIN_TOKEN)
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["detail"], "Profile not running")

    def test_cdp_disabled_returns_403(self):
        from backend.main import app

        with mock.patch.dict(os.environ, {}, clear=False) as env:
            env.pop("GHOSTBROWSER_CDP_TEST", None)
            with TestClient(app) as client, mock.patch.dict(
                "backend.browser_manager.active_browsers",
                {PROFILE_ID: self._running_browser(cdp_port=43210)},
                clear=True,
            ):
                resp = self._get_cdp(client, token=ADMIN_TOKEN)
                self.assertEqual(resp.status_code, 403)
                self.assertIn("CDP test mode is disabled", resp.json()["detail"])

    def test_returns_cdp_urls_with_stored_ws_path(self):
        from backend.main import app

        with TestClient(app) as client, mock.patch.dict(
            "backend.browser_manager.active_browsers",
            {PROFILE_ID: self._running_browser(cdp_port=43210, cdp_ws_path="/devtools/browser/uuid123")},
            clear=True,
        ):
            resp = self._get_cdp(client, token=ADMIN_TOKEN)
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["profile_id"], PROFILE_ID)
            self.assertEqual(body["cdp_url"], "http://127.0.0.1:43210")
            self.assertEqual(body["cdp_ws_url"], "ws://127.0.0.1:43210/devtools/browser/uuid123")

    def test_missing_ws_path_fails_closed(self):
        from backend.main import app

        with TestClient(app) as client, mock.patch.dict(
            "backend.browser_manager.active_browsers",
            {PROFILE_ID: self._running_browser(cdp_port=43211)},
            clear=True,
        ):
            resp = self._get_cdp(client, token=ADMIN_TOKEN)
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["detail"], "CDP endpoint unavailable for this profile")


class CdpTestModeGuardTests(unittest.IsolatedAsyncioTestCase):
    """A07/A08: CDP test mode must never run in production, and the
    allow-origins default is loopback-only unless explicitly overridden."""

    def setUp(self):
        self.orig_prod = os.environ.get("GHOSTBROWSER_PROD")
        self.orig_cdp = os.environ.get("GHOSTBROWSER_CDP_TEST")
        self.orig_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV")
        self.orig_origins = os.environ.get("GHOSTBROWSER_CDP_ALLOW_ORIGINS")
        os.environ["GHOSTBROWSER_CDP_TEST"] = "1"

    def tearDown(self):
        for name, orig in (
            ("GHOSTBROWSER_PROD", self.orig_prod),
            ("GHOSTBROWSER_CDP_TEST", self.orig_cdp),
            ("GHOSTBROWSER_TEST_ENV", self.orig_test_env),
            ("GHOSTBROWSER_CDP_ALLOW_ORIGINS", self.orig_origins),
        ):
            if orig is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = orig

    def test_test_env_is_snapshotted_and_restored(self):
        # Regression: test_cdp_enabled_in_test_env sets TEST_ENV=1; it must be
        # restored/removed by tearDown so it cannot leak into later tests.
        # Deterministic (no reliance on execution order): run tearDown directly.
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        self.orig_test_env = None
        self.tearDown()
        self.assertNotIn("GHOSTBROWSER_TEST_ENV", os.environ)
        self.setUp()

    async def test_cdp_disabled_when_prod(self):
        from backend.browser_manager import _cdp_test_mode_enabled, build_browser_launch_config

        os.environ["GHOSTBROWSER_PROD"] = "1"
        self.assertFalse(_cdp_test_mode_enabled())

        # Deterministic unit test: mock the native metadata probe so this test
        # only exercises the CDP flag behavior (no real browser/threads/spawns).
        fake_meta = {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/139.0.0.0 Safari/537.36",
            "uadata": {
                "brands": [{"brand": "Chromium", "version": "139"}, {"brand": "Not A(Brand", "version": "99"}],
                "platform": "Windows",
                "platformVersion": "10.0.0",
                "architecture": "x86",
                "bitness": "64",
                "uaFullVersion": "139.0.0.0",
            },
        }
        with mock.patch(
            "backend.browser_manager.probe_native_metadata", new=mock.AsyncMock(return_value=fake_meta)
        ):
            config = await build_browser_launch_config(
                {"id": "abcd1234", "path": ".", "advanced": {}}, force_headless=True
            )
        self.assertNotIn("--remote-debugging-port=0", config["args"])

    async def test_prod_wins_even_with_both_test_flags(self):
        # Production always denies, even if CDP_TEST and TEST_ENV are both set:
        # a deployed instance misconfigured with test flags is still closed.
        from backend.browser_manager import _cdp_test_mode_enabled

        os.environ["GHOSTBROWSER_PROD"] = "1"
        os.environ["GHOSTBROWSER_CDP_TEST"] = "1"
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        self.assertFalse(_cdp_test_mode_enabled())

    async def test_truthy_values_are_normalized(self):
        from backend.browser_manager import _cdp_test_mode_enabled, _env_flag

        os.environ.pop("GHOSTBROWSER_PROD", None)
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        for truthy in ("1", "TRUE", "Yes", " yes ", "true"):
            os.environ["GHOSTBROWSER_CDP_TEST"] = truthy
            self.assertTrue(_env_flag("GHOSTBROWSER_CDP_TEST"), truthy)
            self.assertTrue(_cdp_test_mode_enabled(), truthy)
        for falsy in ("", "0", "false", "no", "banana", "2"):
            os.environ["GHOSTBROWSER_CDP_TEST"] = falsy
            self.assertFalse(_env_flag("GHOSTBROWSER_CDP_TEST"), falsy)
            self.assertFalse(_cdp_test_mode_enabled(), falsy)

    async def test_cdp_enabled_in_test_env(self):
        from backend.browser_manager import _cdp_test_mode_enabled

        os.environ.pop("GHOSTBROWSER_PROD", None)
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        self.assertTrue(_cdp_test_mode_enabled())

    async def test_cdp_disabled_without_test_env(self):
        # A deployed instance that only has the flag (no explicit test/dev
        # context, no prod flag) is still closed: fail-closed by default.
        from backend.browser_manager import _cdp_test_mode_enabled

        os.environ.pop("GHOSTBROWSER_PROD", None)
        os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
        self.assertFalse(_cdp_test_mode_enabled())

    async def test_allow_origins_defaults_to_loopback_only(self):
        from backend.browser_manager import _cdp_allow_origins

        os.environ.pop("GHOSTBROWSER_CDP_ALLOW_ORIGINS", None)
        arg = _cdp_allow_origins()
        self.assertNotIn("*", arg)
        self.assertIn("http://localhost", arg)
        self.assertIn("http://127.0.0.1", arg)

    async def test_allow_origins_broad_only_by_explicit_override(self):
        from backend.browser_manager import _cdp_allow_origins

        os.environ["GHOSTBROWSER_CDP_ALLOW_ORIGINS"] = "*"
        self.assertIn("--remote-allow-origins=*", _cdp_allow_origins())


if __name__ == "__main__":
    unittest.main()
