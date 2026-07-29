"""Contracts between frontend API calls, generated markup, and FastAPI routes."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from unittest import TestCase, mock

from fastapi.routing import APIRoute
from backend.auth import RATE_LIMITERS


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


class FrontendApiContractTests(TestCase):
    def test_request_json_normalizes_header_init_without_mutating_callers(self):
        script = r"""
const fs = require('fs');
const source = fs.readFileSync('frontend/app.js', 'utf8');
const prefix = source.slice(0, source.indexOf('// --- Transparently add admin token'));

global.document = { cookie: '' };
const requests = [];
global.window = {
    XSRF_TOKEN: 'xsrf-token',
    fetch: async (_url, options) => {
        requests.push(options);
        return { ok: true, json: async () => ({}) };
    },
};
eval(prefix);
window.XSRF_TOKEN = 'xsrf-token';
global.fetch = window.fetch;

const start = source.indexOf('async function requestJson');
const firstBrace = source.indexOf('{', source.indexOf(')', start));
let depth = 0;
let end = firstBrace;
for (; end < source.length; end += 1) {
    if (source[end] === '{') depth += 1;
    if (source[end] === '}' && --depth === 0) {
        end += 1;
        break;
    }
}
eval(source.slice(start, end));

const cases = [
    { name: 'object', headers: { 'Content-Type': 'application/json', 'X-Caller': 'object' } },
    { name: 'headers', headers: new Headers([['Content-Type', 'application/json'], ['X-Caller', 'headers']]) },
    { name: 'tuples', headers: [['Content-Type', 'application/json'], ['X-Caller', 'tuples']] },
];

(async () => {
    for (const testCase of cases) {
        const before = Array.from(new Headers(testCase.headers).entries());
        const options = { method: 'POST', headers: testCase.headers };
        await requestJson('/api/test', options);
        const sent = requests.pop().headers;
        if (!(sent instanceof Headers)) throw new Error(`${testCase.name}: headers were not normalized`);
        if (sent.get('content-type') !== 'application/json') throw new Error(`${testCase.name}: content type was lost`);
        if (sent.get('x-caller') !== testCase.name) throw new Error(`${testCase.name}: caller header was lost`);
        if (sent.get('x-xsrf-token') !== 'xsrf-token') throw new Error(`${testCase.name}: XSRF token was not appended`);
        const after = Array.from(new Headers(testCase.headers).entries());
        if (JSON.stringify(after) !== JSON.stringify(before)) throw new Error(`${testCase.name}: caller headers were mutated`);
        if (options.headers !== testCase.headers) throw new Error(`${testCase.name}: options headers were replaced`);
    }
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
    def test_pin_policy_accepts_boundary_values_for_create_and_bulk(self, create):
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-contract-token"
        create.return_value = {"status": "success", "profile": {"id": "p1", "name": "locked"}}
        client, headers = self._client()
        with mock.patch("backend.main.profile_manager.set_profile_pin", return_value=True):
            for pin in ("1234", "123456"):
                with self.subTest(pin=pin):
                    self.assertEqual(client.post("/api/profiles", json={"name": "locked", "pin": pin}, headers=headers).status_code, 200)
                    self.assertEqual(client.post("/api/profiles/generate/bulk", json={"base_name": "locked", "count": 1, "pin": pin}, headers=headers).status_code, 200)
                    self.assertEqual(client.post("/api/profiles/p1/pin/set", json={"pin": pin}, headers=headers).status_code, 200)

    def test_new_pin_endpoints_reject_invalid_policy_values(self):
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-contract-token"
        client, headers = self._client()

        def post(url, payload):
            for limiter in RATE_LIMITERS.values():
                limiter.reset()
            return client.post(url, json=payload, headers=headers)

        for pin in ("123", "1234567", "12a4", "１２３４"):
            with self.subTest(pin=pin):
                responses = (
                    post("/api/profiles", {"name": "locked", "pin": pin}),
                    post("/api/profiles/generate/bulk", {"base_name": "locked", "pin": pin}),
                    post("/api/profiles/p1/pin/set", {"pin": pin}),
                )
                for response in responses:
                    self.assertEqual(response.status_code, 422)
                    self.assertIn("PIN must be exactly 4-6 ASCII digits", response.text)

    def test_verify_accepts_legacy_pin_values(self):
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-contract-token"
        client, headers = self._client()
        with mock.patch("backend.main.profile_manager.get_profile", return_value={"id": "p1"}), mock.patch(
            "backend.main.profile_manager.verify_profile_pin", return_value=True
        ) as verify:
            response = client.post("/api/profiles/p1/pin/verify", json={"pin": "legacy PIN"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["verified"])
        verify.assert_called_once_with("p1", "legacy PIN")

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

    def test_static_markup_has_no_inline_styles_or_event_handlers(self):
        self.assertNotRegex(INDEX_HTML, r"\sstyle\s*=")
        self.assertNotRegex(INDEX_HTML, r"\son[a-zA-Z]+\s*=")

    def test_app_does_not_emit_inline_styles_or_mutate_cssom(self):
        self.assertNotRegex(APP_JS, r"\sstyle\s*=")
        self.assertNotRegex(APP_JS, r"\.style\.|\.style\s*=|cssText|setProperty\(")

    def test_csp_remains_strict(self):
        os.environ.setdefault("GHOSTBROWSER_ADMIN_TOKEN", "test-contract-token")
        from fastapi.testclient import TestClient
        from backend.main import app

        csp = TestClient(app).get("/api/system/health").headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("style-src 'self'", csp)
        self.assertNotIn("'unsafe" + "-inline'", csp)
