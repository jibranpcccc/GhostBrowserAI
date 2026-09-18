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

    def test_metrics_renders_credential_store_only_for_supported_windows_contract(self):
        script = r"""
const fs = require('fs');
const source = fs.readFileSync('frontend/app.js', 'utf8');
const start = source.indexOf('async function fetchMetrics');
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

const osSelect = {};
const credentialStatus = {};
const health = {
    classList: { add() {}, remove() {} },
    querySelector(selector) {
        return selector === '.health-dot' ? {} : { textContent: '' };
    },
};
const elements = {
    'stat-active': {}, 'stat-quarantine': {}, 'stat-ram': {}, 'topbar-ram': {},
    'new-profile-os': osSelect, 'setting-credential-store': credentialStatus,
    'system-health': health,
};
global.document = { getElementById: (id) => elements[id] || null };
global.API = '';
global.escHtml = (value) => value;
global.escAttr = (value) => value;
global.SUPPORTED_HOST_OSES = new Set(['Windows', 'Mac', 'Linux']);
let metrics;
global.requestJson = async () => metrics;
eval(source.slice(start, end));

(async () => {
    metrics = {
        active_profiles: 1, total_profiles: 2, quarantined_profiles: 0,
        memory_usage_percent: 20, host_os: 'Windows',
        credential_store: { configured: true, count: 2 },
    };
    await fetchMetrics();
    if (credentialStatus.value !== 'Windows DPAPI protected — 2 accounts') {
        throw new Error(`Windows status was incorrect: ${credentialStatus.value}`);
    }

    metrics.host_os = 'Linux';
    await fetchMetrics();
    if (credentialStatus.value !== 'Credential store unavailable — Windows DPAPI requires Windows') {
        throw new Error(`Non-Windows status was incorrect: ${credentialStatus.value}`);
    }

    metrics.host_os = '<unsupported>';
    metrics.credential_store = { configured: true, count: 'secret' };
    await fetchMetrics();
    if (credentialStatus.value !== 'Credential store status unavailable') {
        throw new Error(`Unsupported host status was incorrect: ${credentialStatus.value}`);
    }
    if (osSelect.innerHTML && osSelect.innerHTML.includes('<unsupported>')) {
        throw new Error('Unsupported host OS was rendered into the selector');
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

    def test_credential_store_hint_states_windows_only_support(self):
        self.assertIn("Credential storage is supported on Windows only", INDEX_HTML)

    def test_bulk_create_rejects_out_of_range_count(self):
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-contract-token"
        client, headers = self._client()
        for count in (0, 51, -1):
            with self.subTest(count=count):
                for limiter in RATE_LIMITERS.values():
                    limiter.reset()
                response = client.post(
                    "/api/profiles/generate/bulk",
                    json={"base_name": "bulk", "count": count},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 422)

    def test_bulk_create_model_forwards_pin_field(self):
        from backend.main import BulkCreateProfileModel
        model = BulkCreateProfileModel(base_name="locked", count=2, pin="1234")
        self.assertEqual(model.pin, "1234")
        self.assertEqual(model.count, 2)

    def test_create_modal_markup_has_dialog_semantics_and_field_errors(self):
        self.assertIn('role="dialog"', INDEX_HTML)
        self.assertIn('id="create-profile-submit-btn"', INDEX_HTML)
        self.assertIn('id="view-created-profiles-btn"', INDEX_HTML)
        self.assertIn('id="new-profile-name-error"', INDEX_HTML)
        self.assertIn('id="new-profile-count-error"', INDEX_HTML)
        self.assertIn('data-action="setCreateQuantity"', INDEX_HTML)

    def test_polling_pauses_when_document_hidden(self):
        self.assertIn("if (document.hidden) return;", APP_JS)

    def test_polling_is_consolidated_to_single_interval(self):
        intervals = re.findall(r"setInterval\([^)]+\)", APP_JS)
        self.assertLessEqual(len(intervals), 1, "Polling must use a single consolidated setInterval")

    def test_mutation_api_calls_use_requestJson(self):
        required_patterns = [
            ("clone profile", r"requestJson\(.*?/clone"),
            ("save cookies", r"requestJson\(.*?/cookies"),
            ("proxy health test", r"requestJson\(.*?/proxies/test"),
            ("scrape proxies", r"requestJson\(.*?/proxies/scrape"),
            ("bulk macro", r"requestJson\(.*?/macros/run/bulk"),
            ("create macro", r"requestJson\(.*?/api/macros"),
        ]
        for label, pattern in required_patterns:
            with self.subTest(endpoint=label):
                self.assertRegex(
                    APP_JS, pattern,
                    f"expected requestJson call for {label}"
                )

    def test_csp_remains_strict(self):
        os.environ.setdefault("GHOSTBROWSER_ADMIN_TOKEN", "test-contract-token")
        from fastapi.testclient import TestClient
        from backend.main import app

        csp = TestClient(app).get("/api/system/health").headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("style-src 'self'", csp)
        self.assertNotIn("'unsafe" + "-inline'", csp)

    def test_admin_token_gate_prompts_once_then_attaches_token(self):
        script = r"""
const fs = require('fs');
const source = fs.readFileSync('frontend/app.js', 'utf8');

// Eval everything up to (but excluding) ensureXsrfToken: the admin-token
// gate (let _adminToken, withAdminToken, transparent fetch/XHR interceptors,
// showAdminTokenPrompt, verifyAdminToken, ensureAdminToken).
const slice = source.slice(0, source.indexOf('async function ensureXsrfToken'));

global.document = { cookie: '' };

const calls = [];
let seenFirstContact = false;
const fetchImpl = async (url, init) => {
    const headers = new Headers((init && init.headers) || undefined);
    calls.push({ url, init });
    if (headers.get('x-admin-token') === 'canned-token') return { ok: true, status: 200 };
    if (url === '/api/system/admin-token-hint') return { ok: false, status: 403 };
    if (!seenFirstContact) { seenFirstContact = true; return { ok: false, status: 401 }; }
    return { ok: false, status: 401 };
};
global.window = { XSRF_TOKEN: '', fetch: fetchImpl };
global.fetch = fetchImpl;
global.escHtml = (s) => String(s);

global.XMLHttpRequest = class {
    open(method, url) { this._url = url; }
    setRequestHeader() {}
    send() {}
};

eval(slice);

// Replace the DOM prompt with a canned token; the auth flow itself is under test.
showAdminTokenPrompt = async () => 'canned-token';

(async () => {
    if (!await ensureAdminToken()) throw new Error('ensureAdminToken returned false with a valid token');

    // The loopback hint is consulted first and must not carry an admin token.
    if (calls[0].url !== '/api/system/admin-token-hint') throw new Error('unexpected first contact URL: ' + calls[0].url);
    if (calls[0].init && calls[0].init.headers && new Headers(calls[0].init.headers).has('x-admin-token')) {
        throw new Error('first contact leaked admin token');
    }

    // Because the hint returned 403, the gate falls back to the profiles probe
    // and then to the manual prompt; that probe must not leak a token either.
    const probe = calls.find((c) => c.url === '/api/profiles' && !(c.init && c.init.method));
    if (!probe) throw new Error('profiles probe was not performed after hint 403');
    if (probe.init && probe.init.headers && new Headers(probe.init.headers).has('x-admin-token')) {
        throw new Error('profiles probe leaked admin token');
    }

    if (!await verifyAdminToken('canned-token')) throw new Error('verifyAdminToken rejected a valid token');
    if (await verifyAdminToken('wrong-token')) throw new Error('verifyAdminToken accepted an invalid token');

    // After auth the transparent interceptor must attach the token to API calls.
    await window.fetch('/api/profiles', { method: 'POST', body: 'x' });
    const last = calls[calls.length - 1];
    const lastHeaders = new Headers(last.init ? last.init.headers : undefined);
    if (lastHeaders.get('x-admin-token') !== 'canned-token') {
        throw new Error('transparent interceptor did not attach admin token');
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
