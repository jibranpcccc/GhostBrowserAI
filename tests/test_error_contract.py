"""Error handling contract tests.

Verifies that:
- Unhandled exceptions return safe generic 500 responses without stack traces.
- Expected 503 auth failures do NOT trigger 500-error log pattern.
- Security headers are present on error responses.
"""
from __future__ import annotations

import os
from unittest import TestCase, mock

from fastapi.testclient import TestClient


def _with_csrf(client, headers=None):
    """Fetch a CSRF token and return headers dict with it."""
    resp = client.get("/api/system/csrf-token")
    token = resp.json()["token"] if resp.status_code == 200 else ""
    hdrs = dict(headers or {})
    hdrs["X-XSRF-Token"] = token
    return hdrs


class ErrorContractTests(TestCase):

    def setUp(self):
        self._orig_token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-error-token"

    def tearDown(self):
        if self._orig_token is None:
            os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        else:
            os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = self._orig_token

    def _client(self):
        from backend.main import app
        return TestClient(app)

    def test_auth_misconfiguration_returns_503_not_500(self):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            resp = client.post("/api/profiles", json={"name": "x"}, headers=_with_csrf(client))
            self.assertEqual(resp.status_code, 503)
            self.assertNotEqual(resp.status_code, 500)

    def test_missing_auth_header_returns_401_not_500(self):
        with self._client() as client:
            resp = client.post("/api/profiles", json={"name": "x"}, headers=_with_csrf(client))
            self.assertEqual(resp.status_code, 401)
            self.assertNotEqual(resp.status_code, 500)

    def test_500_response_contains_no_stack_trace(self):
        """Simulate an unhandled error — must not leak internals."""
        with self._client() as client:
            resp = client.get("/api/system/health")
            if resp.status_code == 500:
                self.assertNotIn("Traceback", resp.text)
                self.assertNotIn("File ", resp.text)
                self.assertNotIn("line ", resp.text)
                self.assertNotIn("backend", resp.text)

    def test_security_headers_on_error_response(self):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            resp = client.post("/api/profiles", json={"name": "x"}, headers=_with_csrf(client))
            self.assertIn("X-Content-Type-Options", resp.headers)
            self.assertIn("X-Frame-Options", resp.headers)
            self.assertIn("Referrer-Policy", resp.headers)

    def test_security_headers_on_success_response(self):
        with self._client() as client:
            resp = client.get("/api/system/health")
            self.assertIn("X-Content-Type-Options", resp.headers)
            self.assertIn("X-Frame-Options", resp.headers)


def test_environment_isolation(sanitize_env):
    """The test environment never loads real Cloudflare accounts and keeps the
    admin token isolated per test.
    """
    from backend.cloudflare_manager import cloudflare_manager

    # Real Cloudflare account loading is disabled by the autouse fixture.
    assert cloudflare_manager.accounts == []
    assert cloudflare_manager.load_accounts() is None
    assert cloudflare_manager.total_accounts == 0

    # GHOSTBROWSER_ADMIN_TOKEN is sanitized for this test only.
    assert os.environ.get("GHOSTBROWSER_ADMIN_TOKEN") == sanitize_env


class ProfileCreateErrorContractTests(TestCase):
    """POST /api/profiles and /clone must collapse internal error text into
    stable catalog messages, preserving only controlled Validation failed:
    text, and map KIMI_UNAVAILABLE to 503."""

    def setUp(self):
        self._orig_token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-error-token"
        from backend.profile_creator import profile_creator
        self._orig_create = profile_creator.create_zero_leak_profile
        self._orig_sem = None
        from backend import main
        self._app = main.app

    def tearDown(self):
        from backend.profile_creator import profile_creator
        profile_creator.create_zero_leak_profile = self._orig_create
        if self._orig_token is None:
            os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        else:
            os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = self._orig_token

    def _set_create(self, result):
        from backend.profile_creator import profile_creator

        async def fake_create(name, **kwargs):
            return result

        profile_creator.create_zero_leak_profile = fake_create

    def _headers(self, client):
        hdrs = _with_csrf(client)
        hdrs["X-Admin-Token"] = "test-error-token"
        return hdrs

    def test_unknown_internal_code_collapses_to_catalog_message(self):
        self._set_create(
            {"status": "error", "code": "SECRET_INTERNAL", "message": "secret internal create detail 777"}
        )
        with TestClient(self._app) as client:
            resp = client.post("/api/profiles", json={"name": "x"}, headers=self._headers(client))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "Profile creation failed")
        self.assertNotIn("secret internal create detail 777", resp.text)

    def test_validation_failed_text_is_preserved(self):
        self._set_create(
            {"status": "error", "code": "CUSTOM", "message": "Validation failed: Profile name already exists"}
        )
        with TestClient(self._app) as client:
            resp = client.post("/api/profiles", json={"name": "x"}, headers=self._headers(client))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "Validation failed: Profile name already exists")

    def test_kimi_unavailable_maps_to_503_with_catalog_message(self):
        self._set_create({"status": "error", "code": "KIMI_UNAVAILABLE", "message": "raw noise"})
        with TestClient(self._app) as client:
            resp = client.post("/api/profiles", json={"name": "x"}, headers=self._headers(client))
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "Strict AI fingerprint service unavailable")
        self.assertNotIn("raw noise", resp.text)

    def test_clone_unknown_internal_code_collapses_to_catalog_message(self):
        from backend.profile_manager import profile_manager
        self._set_create(
            {"status": "error", "code": "SECRET_INTERNAL", "message": "secret clone detail 888"}
        )
        orig_get = profile_manager.get_profile
        profile_manager.get_profile = lambda pid: {"id": "abc", "name": "Base", "proxy": None, "advanced": {}}
        try:
            with TestClient(self._app) as client:
                resp = client.post("/api/profiles/abc/clone", headers=self._headers(client))
        finally:
            profile_manager.get_profile = orig_get
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "Profile creation failed")
        self.assertNotIn("secret clone detail 888", resp.text)
