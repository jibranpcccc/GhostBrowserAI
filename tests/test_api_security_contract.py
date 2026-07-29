"""Route-inventory security matrix test.

Every non-public /api route must declare require_admin_token in its
dependencies.  Public routes must exactly match the documented allowlist.
"""
from __future__ import annotations

import os
import json
from unittest.mock import patch
from unittest import TestCase

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute


# Routes intentionally left public
PUBLIC_API_ROUTES = frozenset({
    ("GET", "/api/system/health"),
    ("GET", "/api/system/csrf-token"),
})


def _route_has_admin_dep(route: APIRoute) -> bool:
    """Check whether the route has a dependency on require_admin_token."""
    for dep in getattr(route.dependant, "dependencies", ()):
        if dep.name == "_auth":
            return True
    return False


class ApiSecurityContractTests(TestCase):
    """Ensure every API route abides by the documented auth contract."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("GHOSTBROWSER_ADMIN_TOKEN", "test-contract-token")

    def setUp(self):
        from backend.main import app
        self.app = app
        self.client = TestClient(app)

    def test_all_api_routes_have_auth_or_are_in_allowlist(self):
        missing = []
        for route in self.app.routes:
            if not isinstance(route, APIRoute):
                continue
            path = route.path
            if not path.startswith("/api/"):
                continue
            key = (list(route.methods)[0], path) if route.methods else ("UNKNOWN", path)
            if key in PUBLIC_API_ROUTES:
                continue
            if _route_has_admin_dep(route):
                continue
            missing.append(key)
        self.assertListEqual(missing, [],
                             f"Routes missing admin auth: {missing}")

    def test_public_routes_work_without_auth(self):
        for method, path in PUBLIC_API_ROUTES:
            with self.subTest(method=method, path=path):
                resp = self.client.request(method, path)
                self.assertIn(resp.status_code, (200, 307),
                              f"{method} {path} should be public")

    def test_protected_route_returns_401_without_token(self):
        resp = self.client.get("/api/profiles")
        self.assertEqual(resp.status_code, 401)

    def test_health_check_contains_security_headers(self):
        resp = self.client.get("/api/system/health")
        self.assertIn("X-Content-Type-Options", resp.headers)
        self.assertIn("X-Frame-Options", resp.headers)
        self.assertIn("Referrer-Policy", resp.headers)

    def _fresh_client(self):
        from backend.main import app
        return TestClient(app)

    def test_csrf_missing_returns_403_for_unsafe_methods(self):
        client = self._fresh_client()
        client.cookies.set("XSRF-TOKEN", "dummy-token")
        resp = client.post(
            "/api/proxies/test",
            headers={"X-Admin-Token": "test-contract-token"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"], "CSRF token missing or invalid")

    def test_csrf_mismatched_returns_403(self):
        client = self._fresh_client()
        client.cookies.set("XSRF-TOKEN", "cookie-token")
        resp = client.post(
            "/api/proxies/test",
            headers={
                "X-Admin-Token": "test-contract-token",
                "X-XSRF-Token": "header-token",
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_csrf_valid_allows_request_through(self):
        client = self._fresh_client()
        csrf_resp = client.get("/api/system/csrf-token")
        token = csrf_resp.json()["token"]
        resp = client.post(
            "/api/proxies/test",
            headers={
                "X-Admin-Token": "test-contract-token",
                "X-XSRF-Token": token,
            },
        )
        self.assertNotEqual(resp.status_code, 403)
        # the route itself may succeed or have business-logic errors, but CSRF passed
        self.assertIn(resp.status_code, (200, 422, 404))

    def test_get_requests_do_not_require_csrf(self):
        resp = self.client.get(
            "/api/proxies",
            headers={"X-Admin-Token": "test-contract-token"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_metrics_exposes_only_sanitized_credential_store_status(self):
        headers = {"X-Admin-Token": "test-contract-token"}
        raw_status = {
            "configured": True,
            "count": 2,
            "provider": "windows-dpapi-user",
            "path": "C:/private/cloudflare_accounts.secure.json",
            "account_id": "account-id-secret",
            "token": "token-secret",
            "payload": "encrypted-payload-secret",
        }
        with patch("backend.main.store_status", return_value=raw_status):
            response = self.client.get("/api/metrics", headers=headers)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsInstance(body["host_os"], str)
        self.assertIn(body["host_os"], {"Windows", "Mac", "Linux"})
        self.assertEqual(body["credential_store"], {"configured": True, "count": 2})
        self.assertIsInstance(body["credential_store"]["configured"], bool)
        self.assertIsInstance(body["credential_store"]["count"], int)
        serialized = json.dumps(body)
        for credential_material in (
            "C:/private/cloudflare_accounts.secure.json",
            "account-id-secret",
            "token-secret",
            "encrypted-payload-secret",
        ):
            self.assertNotIn(credential_material, serialized)

    def test_metrics_handles_unreadable_credential_store(self):
        headers = {"X-Admin-Token": "test-contract-token"}
        with patch("backend.main.store_status", side_effect=OSError("unreadable")):
            response = self.client.get("/api/metrics", headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["credential_store"],
            {"configured": False, "count": 0},
        )

    def test_proxy_credentials_never_appear_in_profile_or_proxy_responses(self):
        secret_proxy = {
            "server": "http://url-user-secret:url-password-secret@proxy.example:8080",
            "username": "proxy-user-secret",
            "password": "proxy-password-secret",
        }
        profile = {"id": "profile-1", "proxy": secret_proxy, "proxy_pin": "1234"}
        headers = {"X-Admin-Token": "test-contract-token"}
        with patch("backend.main.profile_manager.list_profiles", return_value=[profile]), \
             patch("backend.main.proxy_manager._get_active_proxies", return_value=[secret_proxy]), \
             patch("os.path.exists", return_value=False):
            responses = [
                self.client.get("/api/profiles", headers=headers),
                self.client.get("/api/proxies", headers=headers),
            ]
        for response in responses:
            self.assertEqual(response.status_code, 200)
            body = response.text
            self.assertNotIn("proxy-user-secret", body)
            self.assertNotIn("proxy-password-secret", body)
            self.assertNotIn("url-user-secret", body)
            self.assertNotIn("url-password-secret", body)
            self.assertNotIn('"proxy_pin"', body)
