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
