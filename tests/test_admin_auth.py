"""Zero-auth contract tests for protected API routes.

Product decision: GhostBrowser ships with authentication fully disabled.
Every credential is bundled by default and no user is ever asked for a
token or API key. These tests lock in that contract:

* Protected routes succeed without any ``X-Admin-Token`` header
* The same holds for remote (non-loopback) clients
* Responses never leak 401/403-auth/503-auth to users
* CSRF protection (a separate, browser-automatic mechanism) stays active

All real browser/network/file side effects are mocked out.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest import TestCase, mock

from fastapi.testclient import TestClient

TEST_PROFILE_ID = "test-profile-1"
DUMMY_PROFILE = {"id": TEST_PROFILE_ID, "name": "Test Profile"}


class ZeroAuthContractTests(TestCase):
    def setUp(self):
        # No token configured at all: the default shipping state.
        self.orig_token = os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)

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
        if self.orig_token is not None:
            os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = self.orig_token

    @contextmanager
    def _client(self, remote: bool = False, csrf: bool = True):
        from backend.main import app

        kwargs = {}
        if remote:
            kwargs["client"] = ("203.0.113.50", 50000)
        with TestClient(app, **kwargs) as client:
            if csrf:
                resp = client.get("/api/system/csrf-token")
                if resp.status_code == 200:
                    client.headers["X-XSRF-Token"] = resp.json()["token"]
            yield client

    # ----------------------------------------------------------------------
    # Core contract: protected routes are open without any credentials
    # ----------------------------------------------------------------------
    def test_get_profiles_open_without_token(self):
        with self._client() as client:
            resp = client.get("/api/profiles")
            self.assertEqual(resp.status_code, 200)

    def test_get_profiles_open_without_token_from_remote_host(self):
        with self._client(remote=True) as client:
            resp = client.get("/api/profiles")
            self.assertEqual(resp.status_code, 200)

    def test_get_profiles_open_with_garbage_token(self):
        with self._client() as client:
            resp = client.get("/api/profiles", headers={"X-Admin-Token": "garbage-###"})
            self.assertEqual(resp.status_code, 200)

    def test_delete_profile_open_without_token(self):
        with mock.patch("backend.main.profile_manager.delete_profile", return_value=True), \
             mock.patch("backend.main.close_profile", new=mock.AsyncMock(return_value={"status": "success"})):
            with self._client() as client:
                resp = client.delete(f"/api/profiles/{TEST_PROFILE_ID}")
                self.assertNotIn(resp.status_code, (401, 403))

    def test_launch_profile_open_without_token(self):
        with mock.patch(
            "backend.main.launch_profile",
            new=mock.AsyncMock(return_value={"status": "success"}),
        ):
            with self._client() as client:
                resp = client.post(f"/api/profiles/{TEST_PROFILE_ID}/launch")
                self.assertNotIn(resp.status_code, (401, 403))

    def test_close_profile_open_without_token(self):
        with mock.patch(
            "backend.main.close_profile",
            new=mock.AsyncMock(return_value={"status": "success"}),
        ):
            with self._client() as client:
                resp = client.post(f"/api/profiles/{TEST_PROFILE_ID}/close")
                self.assertNotIn(resp.status_code, (401, 403))

    def test_macros_open_without_token(self):
        with mock.patch(
            "backend.macro_manager.macro_manager.create_macro",
            return_value={"status": "success", "macro": {}},
        ):
            with self._client() as client:
                resp = client.post("/api/macros", json={"name": "m", "steps": []})
                self.assertNotIn(resp.status_code, (401, 403))

    def test_proxies_open_without_token(self):
        with mock.patch(
            "backend.proxy_manager.proxy_manager.add_proxies",
            return_value=[{"server": "http://127.0.0.1:8080"}],
        ):
            with self._client() as client:
                resp = client.post("/api/proxies", json={"proxy_string": "http://127.0.0.1:8080"})
                self.assertNotIn(resp.status_code, (401, 403))



    # ----------------------------------------------------------------------
    # Public endpoints stay public
    # ----------------------------------------------------------------------
    def test_system_health_is_public(self):
        with self._client() as client:
            resp = client.get("/api/system/health")
            self.assertEqual(resp.status_code, 200)

    def test_csrf_token_is_public(self):
        with self._client() as client:
            resp = client.get("/api/system/csrf-token")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("token", resp.json())

    # ----------------------------------------------------------------------
    # CSRF stays active (browser handles it automatically; not user-facing)
    # ----------------------------------------------------------------------
    def test_csrf_post_without_csrf_token_returns_403(self):
        with self._client(csrf=False) as client:
            resp = client.post("/api/profiles", json={"name": "x"})
            self.assertEqual(resp.status_code, 403)

    def test_csrf_get_does_not_require_csrf(self):
        with self._client(csrf=False) as client:
            resp = client.get("/api/profiles")
            self.assertEqual(resp.status_code, 200)

    # ----------------------------------------------------------------------
    # Read-only flows with and without headers behave identically
    # ----------------------------------------------------------------------
    def test_read_only_get_profiles_with_admin_token_returns_data(self):
        with mock.patch(
            "backend.main.profile_manager.list_profiles",
            return_value=[DUMMY_PROFILE],
        ), mock.patch(
            "backend.main.is_profile_running",
            return_value=False,
        ):
            with self._client() as client:
                resp = client.get("/api/profiles", headers={"X-Admin-Token": "anything"})
                self.assertEqual(resp.status_code, 200)

    def test_read_only_get_profiles_without_admin_token_returns_data(self):
        with mock.patch(
            "backend.main.profile_manager.list_profiles",
            return_value=[DUMMY_PROFILE],
        ), mock.patch(
            "backend.main.is_profile_running",
            return_value=False,
        ):
            with self._client() as client:
                resp = client.get("/api/profiles")
                self.assertEqual(resp.status_code, 200)
                self.assertIn("Test Profile", resp.text)

    # ----------------------------------------------------------------------
    # CORS preflight unchanged
    # ----------------------------------------------------------------------
    def test_cors_preflight_returns_appropriate_headers(self):
        with self._client() as client:
            resp = client.options(
                "/api/profiles",
                headers={"Origin": "http://127.0.0.1:8000", "Access-Control-Request-Method": "GET"},
            )
            self.assertIn("access-control-allow-origin", {k.lower() for k in resp.headers})
