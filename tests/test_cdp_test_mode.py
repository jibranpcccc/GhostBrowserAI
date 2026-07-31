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
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = ADMIN_TOKEN
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
        if self.orig_token is None:
            os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        else:
            os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = self.orig_token

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

    def test_cdp_disabled_returns_400(self):
        from backend.main import app

        with TestClient(app) as client, mock.patch.dict(
            "backend.browser_manager.active_browsers",
            {PROFILE_ID: self._running_browser(cdp_port=None)},
            clear=True,
        ):
            resp = self._get_cdp(client, token=ADMIN_TOKEN)
            self.assertEqual(resp.status_code, 400)
            self.assertIn("CDP not enabled", resp.json()["detail"])

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

    def test_defaults_ws_path_when_missing(self):
        from backend.main import app

        with TestClient(app) as client, mock.patch.dict(
            "backend.browser_manager.active_browsers",
            {PROFILE_ID: self._running_browser(cdp_port=43211)},
            clear=True,
        ):
            resp = self._get_cdp(client, token=ADMIN_TOKEN)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["cdp_ws_url"], "ws://127.0.0.1:43211/devtools/browser/")


if __name__ == "__main__":
    unittest.main()
