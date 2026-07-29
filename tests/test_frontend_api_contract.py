"""Contracts between frontend API calls, generated markup, and FastAPI routes."""
from __future__ import annotations

import os
import re
from pathlib import Path
from unittest import TestCase, mock

from fastapi.routing import APIRoute


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


class FrontendApiContractTests(TestCase):
    def test_every_app_api_url_has_a_matching_route_and_method(self):
        os.environ.setdefault("GHOSTBROWSER_ADMIN_TOKEN", "test-contract-token")
        from backend.main import app

        expected = {
            ("GET", "/api/system/csrf-token"), ("GET", "/api/metrics"),
            ("GET", "/api/cloudflare/status"), ("GET", "/api/profiles"),
            ("POST", "/api/profiles"), ("POST", "/api/profiles/generate"),
            ("POST", "/api/profiles/generate/bulk"), ("PUT", "/api/profiles/{profile_id}"),
            ("DELETE", "/api/profiles/{profile_id}"), ("POST", "/api/profiles/{profile_id}/clone"),
            ("POST", "/api/profiles/{profile_id}/launch"), ("POST", "/api/profiles/{profile_id}/close"),
            ("GET", "/api/profiles/{profile_id}/scan"), ("GET", "/api/profiles/{profile_id}/cookies"),
            ("POST", "/api/profiles/{profile_id}/cookies"), ("PATCH", "/api/profiles/{profile_id}/metadata"),
            ("PATCH", "/api/profiles/{profile_id}/proxy"), ("PATCH", "/api/profiles/{profile_id}/tags"),
            ("PATCH", "/api/profiles/{profile_id}/privacy-mode"), ("POST", "/api/profiles/{profile_id}/pin/verify"),
            ("POST", "/api/profiles/{profile_id}/pin/set"), ("DELETE", "/api/profiles/{profile_id}/pin"),
            ("GET", "/api/profiles/{profile_id}/detection-risk"), ("GET", "/api/proxies"),
            ("POST", "/api/proxies"), ("GET", "/api/proxies/titan"), ("POST", "/api/proxies/test"),
            ("POST", "/api/proxies/test-connection"), ("POST", "/api/proxies/scrape"),
            ("POST", "/api/rotator/start"), ("GET", "/api/macros"), ("POST", "/api/macros"),
            ("DELETE", "/api/macros/{macro_id}"), ("POST", "/api/macros/run/bulk"),
            ("GET", "/api/macros/schedule"), ("POST", "/api/macros/schedule"),
            ("DELETE", "/api/macros/schedule/{job_id}"), ("POST", "/api/cookie-robot/start"),
            ("GET", "/api/cookie-robot/status"), ("POST", "/api/sync/start"), ("POST", "/api/sync/stop"),
            ("GET", "/api/anti-detect/surfaces"), ("GET", "/api/sites/access-log"),
            ("DELETE", "/api/sites/access-log"),
        }
        route_objects = list(app.routes)
        # This project uses FastAPI's deferred router inclusion, which stores
        # included routers as _IncludedRouter instances until dispatch.
        route_objects.extend(
            route for included in app.routes
            for route in getattr(getattr(included, "original_router", None), "routes", ())
        )
        routes = {
            (method, route.path)
            for route in route_objects if isinstance(route, APIRoute)
            for method in route.methods
        }
        self.assertSetEqual(expected - routes, set())

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        csrf = client.get("/api/system/csrf-token").json()["token"]
        return client, {"X-Admin-Token": "test-contract-token", "X-XSRF-Token": csrf}

    @mock.patch("backend.main.profile_creator.create_zero_leak_profile", new_callable=mock.AsyncMock)
    def test_create_forwards_pin_to_profile_creator(self, create):
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-contract-token"
        create.return_value = {"status": "success", "profile": {"id": "p1", "name": "locked"}}
        client, headers = self._client()
        response = client.post("/api/profiles", json={"name": "locked", "pin": "1234"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.await_args.kwargs["pin"], "1234")

    @mock.patch("backend.main.profile_creator.create_zero_leak_profile", new_callable=mock.AsyncMock)
    def test_bulk_create_retains_canonical_proxy_string(self, create):
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-contract-token"
        create.return_value = {"status": "success", "profile": {"id": "p1"}}
        client, headers = self._client()
        response = client.post(
            "/api/profiles/generate/bulk",
            json={"base_name": "bulk", "count": 1, "proxy_string": "socks5://127.0.0.1:1080:user:pass"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        proxy = create.await_args.kwargs["proxy"]
        self.assertEqual(proxy["server"], "socks5://127.0.0.1:1080")
        self.assertEqual(proxy["username"], "user")
        self.assertEqual(proxy["password"], "pass")

    def test_generated_markup_has_no_inline_event_handlers(self):
        generated_sections = re.findall(r"(?:innerHTML\s*=|return\s+)`([\s\S]*?)`", APP_JS)
        self.assertNotRegex("\n".join(generated_sections), r"\son(?:click|change|input)\s*=")

    def test_csp_remains_strict(self):
        os.environ.setdefault("GHOSTBROWSER_ADMIN_TOKEN", "test-contract-token")
        from fastapi.testclient import TestClient
        from backend.main import app

        csp = TestClient(app).get("/api/system/health").headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("style-src", 1)[0])
