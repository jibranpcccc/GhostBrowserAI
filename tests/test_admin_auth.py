"""Admin token authorization tests for protected API routes.

These tests verify the fail-closed behavior enforced by
``backend.auth.require_admin_token``:

* 503 when the server has no admin token configured
* 401 when the request omits the ``X-Admin-Token`` header
* 403 when the supplied token does not match
* 200 on protected routes when the correct token is supplied

Public endpoints (health, csrf-token) are confirmed to work without auth.
All real browser/network/file side effects are mocked out.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest import TestCase, mock

from fastapi.testclient import TestClient

ADMIN_TOKEN = "test-admin-token-12345"
WRONG_TOKEN = "wrong-token"
TEST_PROFILE_ID = "test-profile-1"
DUMMY_PROFILE = {"id": TEST_PROFILE_ID, "name": "Test Profile"}


class AdminAuthTests(TestCase):
    def setUp(self):
        self.orig_token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = ADMIN_TOKEN

        # Patch lifespan and global side effects so importing/running the app
        # does not touch real browsers, networks, scheduler threads, or files.
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

    @contextmanager
    def _client(self, csrf: bool = True):
        from backend.main import app

        with TestClient(app) as client:
            # Seed a valid CSRF token by default so state-changing requests in
            # non-CSRF-specific tests pass the double-submit middleware.
            if csrf:
                resp = client.get("/api/system/csrf-token")
                if resp.status_code == 200:
                    client.headers["X-XSRF-Token"] = resp.json()["token"]
            yield client

    def _admin_headers(self, token: str = ADMIN_TOKEN) -> dict:
        return {"X-Admin-Token": token}

    def _assert_public_endpoint(self, client: TestClient, method: str, path: str):
        response = client.request(method, path)
        self.assertEqual(response.status_code, 200)

    # ----------------------------------------------------------------------
    # Public endpoints
    # ----------------------------------------------------------------------
    def test_system_health_is_public(self):
        with self._client() as client:
            self._assert_public_endpoint(client, "GET", "/api/system/health")

    def test_csrf_token_is_public(self):
        with self._client() as client:
            response = client.get("/api/system/csrf-token")
            self.assertEqual(response.status_code, 200)
            self.assertIn("token", response.json())

    # ----------------------------------------------------------------------
    # POST /api/profiles
    # ----------------------------------------------------------------------
    @mock.patch("backend.main.profile_creator.create_zero_leak_profile")
    def test_post_profiles_no_token_env_returns_503(self, _mock_create):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            response = client.post("/api/profiles", json={"name": "Test"})
            self.assertEqual(response.status_code, 503)

    def test_post_profiles_missing_header_returns_401(self):
        with self._client() as client:
            response = client.post("/api/profiles", json={"name": "Test"})
            self.assertEqual(response.status_code, 401)

    def test_post_profiles_wrong_token_returns_403(self):
        with self._client() as client:
            response = client.post(
                "/api/profiles",
                json={"name": "Test"},
                headers=self._admin_headers(WRONG_TOKEN),
            )
            self.assertEqual(response.status_code, 403)

    @mock.patch("backend.main.profile_creator.create_zero_leak_profile")
    def test_post_profiles_correct_token_returns_200(self, mock_create):
        mock_create.return_value = {
            "status": "success",
            "profile": DUMMY_PROFILE,
        }
        with self._client() as client:
            response = client.post(
                "/api/profiles",
                json={"name": "Test Profile"},
                headers=self._admin_headers(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], TEST_PROFILE_ID)
            mock_create.assert_called_once()

    # ----------------------------------------------------------------------
    # DELETE /api/profiles/{profile_id}
    # ----------------------------------------------------------------------
    @mock.patch("backend.main.profile_manager.delete_profile", return_value=True)
    @mock.patch("backend.main.close_profile")
    def test_delete_profile_no_token_env_returns_503(self, _mock_close, _mock_delete):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            response = client.delete(f"/api/profiles/{TEST_PROFILE_ID}")
            self.assertEqual(response.status_code, 503)

    @mock.patch("backend.main.profile_manager.delete_profile", return_value=True)
    @mock.patch("backend.main.close_profile")
    def test_delete_profile_missing_header_returns_401(self, _mock_close, _mock_delete):
        with self._client() as client:
            response = client.delete(f"/api/profiles/{TEST_PROFILE_ID}")
            self.assertEqual(response.status_code, 401)

    @mock.patch("backend.main.profile_manager.delete_profile", return_value=True)
    @mock.patch("backend.main.close_profile")
    def test_delete_profile_wrong_token_returns_403(self, _mock_close, _mock_delete):
        with self._client() as client:
            response = client.delete(
                f"/api/profiles/{TEST_PROFILE_ID}",
                headers=self._admin_headers(WRONG_TOKEN),
            )
            self.assertEqual(response.status_code, 403)

    @mock.patch("backend.main.profile_manager.delete_profile", return_value=True)
    @mock.patch("backend.main.close_profile")
    def test_delete_profile_correct_token_returns_200(self, mock_close, mock_delete):
        with self._client() as client:
            response = client.delete(
                f"/api/profiles/{TEST_PROFILE_ID}",
                headers=self._admin_headers(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            mock_close.assert_called_once_with(TEST_PROFILE_ID)
            mock_delete.assert_called_once_with(TEST_PROFILE_ID)

    # ----------------------------------------------------------------------
    # POST /api/profiles/{profile_id}/launch
    # ----------------------------------------------------------------------
    @mock.patch("backend.main.launch_profile")
    def test_launch_profile_no_token_env_returns_503(self, _mock_launch):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            response = client.post(f"/api/profiles/{TEST_PROFILE_ID}/launch")
            self.assertEqual(response.status_code, 503)

    @mock.patch("backend.main.launch_profile")
    def test_launch_profile_missing_header_returns_401(self, _mock_launch):
        with self._client() as client:
            response = client.post(f"/api/profiles/{TEST_PROFILE_ID}/launch")
            self.assertEqual(response.status_code, 401)

    @mock.patch("backend.main.launch_profile")
    def test_launch_profile_wrong_token_returns_403(self, _mock_launch):
        with self._client() as client:
            response = client.post(
                f"/api/profiles/{TEST_PROFILE_ID}/launch",
                headers=self._admin_headers(WRONG_TOKEN),
            )
            self.assertEqual(response.status_code, 403)

    @mock.patch("backend.main.launch_profile")
    def test_launch_profile_correct_token_returns_200(self, mock_launch):
        mock_launch.return_value = {"status": "success", "profile_id": TEST_PROFILE_ID}
        with self._client() as client:
            response = client.post(
                f"/api/profiles/{TEST_PROFILE_ID}/launch",
                headers=self._admin_headers(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            mock_launch.assert_called_once_with(TEST_PROFILE_ID)

    # ----------------------------------------------------------------------
    # POST /api/profiles/{profile_id}/close
    # ----------------------------------------------------------------------
    @mock.patch("backend.main.close_profile")
    def test_close_profile_no_token_env_returns_503(self, _mock_close):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            response = client.post(f"/api/profiles/{TEST_PROFILE_ID}/close")
            self.assertEqual(response.status_code, 503)

    @mock.patch("backend.main.close_profile")
    def test_close_profile_missing_header_returns_401(self, _mock_close):
        with self._client() as client:
            response = client.post(f"/api/profiles/{TEST_PROFILE_ID}/close")
            self.assertEqual(response.status_code, 401)

    @mock.patch("backend.main.close_profile")
    def test_close_profile_wrong_token_returns_403(self, _mock_close):
        with self._client() as client:
            response = client.post(
                f"/api/profiles/{TEST_PROFILE_ID}/close",
                headers=self._admin_headers(WRONG_TOKEN),
            )
            self.assertEqual(response.status_code, 403)

    @mock.patch("backend.main.close_profile")
    def test_close_profile_correct_token_returns_200(self, mock_close):
        mock_close.return_value = {"status": "success"}
        with self._client() as client:
            response = client.post(
                f"/api/profiles/{TEST_PROFILE_ID}/close",
                headers=self._admin_headers(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            mock_close.assert_called_once_with(TEST_PROFILE_ID)

    # ----------------------------------------------------------------------
    # POST /api/macros
    # ----------------------------------------------------------------------
    @mock.patch("backend.main.macro_manager.create_macro")
    def test_post_macros_no_token_env_returns_503(self, _mock_create):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            response = client.post(
                "/api/macros",
                json={"name": "Test Macro", "steps": [{"action": "navigate"}]},
            )
            self.assertEqual(response.status_code, 503)

    @mock.patch("backend.main.macro_manager.create_macro")
    def test_post_macros_missing_header_returns_401(self, _mock_create):
        with self._client() as client:
            response = client.post(
                "/api/macros",
                json={"name": "Test Macro", "steps": [{"action": "navigate"}]},
            )
            self.assertEqual(response.status_code, 401)

    @mock.patch("backend.main.macro_manager.create_macro")
    def test_post_macros_wrong_token_returns_403(self, _mock_create):
        with self._client() as client:
            response = client.post(
                "/api/macros",
                json={"name": "Test Macro", "steps": [{"action": "navigate"}]},
                headers=self._admin_headers(WRONG_TOKEN),
            )
            self.assertEqual(response.status_code, 403)

    @mock.patch("backend.main.macro_manager.create_macro")
    def test_post_macros_correct_token_returns_200(self, mock_create):
        mock_create.return_value = {"id": "macro-1", "name": "Test Macro"}
        with self._client() as client:
            response = client.post(
                "/api/macros",
                json={"name": "Test Macro", "steps": [{"action": "navigate"}]},
                headers=self._admin_headers(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], "macro-1")
            mock_create.assert_called_once()

    # ----------------------------------------------------------------------
    # POST /api/rotator/start
    # ----------------------------------------------------------------------
    @mock.patch("backend.main.rotator.start", new=mock.AsyncMock())
    def test_rotator_start_no_token_env_returns_503(self):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            response = client.post("/api/rotator/start", json={})
            self.assertEqual(response.status_code, 503)

    @mock.patch("backend.main.rotator.start", new=mock.AsyncMock())
    def test_rotator_start_missing_header_returns_401(self):
        with self._client() as client:
            response = client.post("/api/rotator/start", json={})
            self.assertEqual(response.status_code, 401)

    @mock.patch("backend.main.rotator.start", new=mock.AsyncMock())
    def test_rotator_start_wrong_token_returns_403(self):
        with self._client() as client:
            response = client.post(
                "/api/rotator/start",
                json={},
                headers=self._admin_headers(WRONG_TOKEN),
            )
            self.assertEqual(response.status_code, 403)

    @mock.patch("backend.main.rotator.start", new=mock.AsyncMock())
    def test_rotator_start_correct_token_returns_200(self):
        with self._client() as client:
            response = client.post(
                "/api/rotator/start",
                json={},
                headers=self._admin_headers(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")

    # ----------------------------------------------------------------------
    # POST /api/proxies
    # ----------------------------------------------------------------------
    @mock.patch("backend.main.proxy_manager.add_proxies")
    def test_post_proxies_no_token_env_returns_503(self, _mock_add):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            response = client.post(
                "/api/proxies",
                json={"proxies": [{"server": "http://localhost:8080"}]},
            )
            self.assertEqual(response.status_code, 503)

    @mock.patch("backend.main.proxy_manager.add_proxies")
    def test_post_proxies_missing_header_returns_401(self, _mock_add):
        with self._client() as client:
            response = client.post(
                "/api/proxies",
                json={"proxies": [{"server": "http://localhost:8080"}]},
            )
            self.assertEqual(response.status_code, 401)

    @mock.patch("backend.main.proxy_manager.add_proxies")
    def test_post_proxies_wrong_token_returns_403(self, _mock_add):
        with self._client() as client:
            response = client.post(
                "/api/proxies",
                json={"proxies": [{"server": "http://localhost:8080"}]},
                headers=self._admin_headers(WRONG_TOKEN),
            )
            self.assertEqual(response.status_code, 403)

    @mock.patch("backend.main.proxy_manager.add_proxies")
    def test_post_proxies_correct_token_returns_200(self, mock_add):
        mock_add.return_value = 1
        with self._client() as client:
            response = client.post(
                "/api/proxies",
                json={"proxies": [{"server": "http://localhost:8080"}]},
                headers=self._admin_headers(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            self.assertEqual(response.json()["added"], 1)
            mock_add.assert_called_once()

    # ----------------------------------------------------------------------
    # CSRF tests
    # ----------------------------------------------------------------------
    def test_csrf_post_without_token_returns_403(self):
        with self._client(csrf=False) as client:
            response = client.post(
                "/api/profiles",
                json={"name": "Test"},
                headers=self._admin_headers(),
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"], "CSRF token missing or invalid")

    def test_csrf_post_with_mismatched_token_returns_403(self):
        with self._client(csrf=False) as client:
            csrf_resp = client.get("/api/system/csrf-token")
            self.assertEqual(csrf_resp.status_code, 200)
            response = client.post(
                "/api/profiles",
                json={"name": "Test"},
                headers={
                    **self._admin_headers(),
                    "X-XSRF-Token": "mismatched-token",
                },
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"], "CSRF token missing or invalid")

    @mock.patch("backend.main.profile_creator.create_zero_leak_profile")
    def test_csrf_post_with_valid_token_and_valid_admin_token_succeeds(self, mock_create):
        mock_create.return_value = {
            "status": "success",
            "profile": DUMMY_PROFILE,
        }
        with self._client() as client:
            csrf_resp = client.get("/api/system/csrf-token")
            self.assertEqual(csrf_resp.status_code, 200)
            token = csrf_resp.json()["token"]
            response = client.post(
                "/api/profiles",
                json={"name": "Test Profile"},
                headers={
                    **self._admin_headers(),
                    "X-XSRF-Token": token,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], TEST_PROFILE_ID)
            mock_create.assert_called_once()

    @mock.patch("backend.main.profile_manager.list_profiles", return_value=[{"id": TEST_PROFILE_ID, "name": "Test Profile"}])
    @mock.patch("backend.main.is_profile_running", return_value=False)
    def test_csrf_get_does_not_require_csrf(self, _mock_running, _mock_list):
        with self._client() as client:
            response = client.get("/api/profiles", headers=self._admin_headers())
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], TEST_PROFILE_ID)
            _mock_list.assert_called_once()

    # ----------------------------------------------------------------------
    # Read-only vs write boundary
    # ----------------------------------------------------------------------
    @mock.patch("backend.main.profile_manager.list_profiles", return_value=[{"id": TEST_PROFILE_ID, "name": "Test Profile"}])
    @mock.patch("backend.main.is_profile_running", return_value=False)
    def test_read_only_get_profiles_with_admin_token_returns_data(self, _mock_running, _mock_list):
        with self._client() as client:
            response = client.get("/api/profiles", headers=self._admin_headers())
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], TEST_PROFILE_ID)
            self.assertEqual(data[0]["status"], "Stopped")
            _mock_list.assert_called_once()

    def test_read_only_get_profiles_without_admin_token_returns_401(self):
        with self._client() as client:
            response = client.get("/api/profiles")
            self.assertEqual(response.status_code, 401)

    # ----------------------------------------------------------------------
    # Token edge cases
    # ----------------------------------------------------------------------
    def test_empty_admin_token_header_whitespace_returns_401(self):
        with self._client() as client:
            response = client.get(
                "/api/profiles",
                headers={"X-Admin-Token": "   \t\n"},
            )
            self.assertEqual(response.status_code, 401)

    def test_very_long_admin_token_returns_403(self):
        long_token = "x" * 4096
        with self._client() as client:
            response = client.get(
                "/api/profiles",
                headers={"X-Admin-Token": long_token},
            )
            self.assertEqual(response.status_code, 403)

    @mock.patch("backend.main.profile_manager.list_profiles", return_value=[{"id": TEST_PROFILE_ID, "name": "Test Profile"}])
    @mock.patch("backend.main.is_profile_running", return_value=False)
    def test_admin_token_with_special_characters_works(self, _mock_running, _mock_list):
        special = '''token-!@#$%^&*()_+-=[]{}|;':",./<>?`~\\\\ '''
        original = os.environ["GHOSTBROWSER_ADMIN_TOKEN"]
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = special
        try:
            with self._client() as client:
                response = client.get(
                    "/api/profiles",
                    headers={"X-Admin-Token": special},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()[0]["id"], TEST_PROFILE_ID)
        finally:
            os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = original

    # ----------------------------------------------------------------------
    # CORS headers
    # ----------------------------------------------------------------------
    def test_cors_preflight_returns_appropriate_headers(self):
        with self._client() as client:
            response = client.options(
                "/api/profiles",
                headers={
                    "Origin": "http://127.0.0.1:8000",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "X-Admin-Token",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers.get("access-control-allow-origin"),
                "http://127.0.0.1:8000",
            )
            self.assertIn("GET", response.headers.get("access-control-allow-methods", ""))
            self.assertIn("X-Admin-Token", response.headers.get("access-control-allow-headers", ""))

    def test_cors_unauthorized_origin_rejected(self):
        with self._client() as client:
            response = client.options(
                "/api/profiles",
                headers={
                    "Origin": "https://evil.com",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "X-Admin-Token",
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertIsNone(response.headers.get("access-control-allow-origin"))

    # ----------------------------------------------------------------------
    # Rate limiting / concurrency
    # ----------------------------------------------------------------------
    def test_profile_creation_semaphore_does_not_block_auth_checks(self):
        import asyncio
        blocker = asyncio.Event()
        with mock.patch(
            "backend.main._profile_create_sem.acquire",
            new=mock.AsyncMock(side_effect=blocker.wait),
        ) as mock_acquire:
            with self._client() as client:
                csrf_resp = client.get("/api/system/csrf-token")
                self.assertEqual(csrf_resp.status_code, 200)
                token = csrf_resp.json()["token"]

                missing = client.post(
                    "/api/profiles",
                    json={"name": "Test"},
                    headers={"X-XSRF-Token": token},
                )
                self.assertEqual(missing.status_code, 401)

                wrong = client.post(
                    "/api/profiles",
                    json={"name": "Test"},
                    headers={
                        **self._admin_headers(WRONG_TOKEN),
                        "X-XSRF-Token": token,
                    },
                )
                self.assertEqual(wrong.status_code, 403)
                mock_acquire.assert_not_called()
