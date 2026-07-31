"""Tests for bulk operation hardening (A1):

- close/delete are throttled by semaphores (no unbounded concurrency)
- errors are sanitized to stable codes, never leaking exception strings
- delete is fail-closed: a running profile that fails to close is NOT deleted
"""
import asyncio
import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend import bulk_operations


class FakeProfileManager:
    """Minimal profile manager stub for delete behavior."""

    def __init__(self):
        self.deleted = []

    def delete_profile(self, pid):
        self.deleted.append(pid)
        return True


class FakeLaunchResult:
    def __init__(self, status="success", message="ok"):
        self.status = status
        self.message = message


class TestBulkSemaphores(unittest.TestCase):
    def test_close_and_delete_semaphores_exist(self):
        self.assertIsInstance(bulk_operations._CLOSE_SEM, asyncio.Semaphore)
        self.assertIsInstance(bulk_operations._DELETE_SEM, asyncio.Semaphore)
        self.assertIsInstance(bulk_operations._LAUNCH_SEM, asyncio.Semaphore)
        self.assertIsInstance(bulk_operations._CREATE_SEM, asyncio.Semaphore)


class TestBulkLaunchSanitizedErrors(unittest.IsolatedAsyncioTestCase):
    async def test_launch_exception_returns_stable_code(self):
        orig_launch = bulk_operations.launch_profile

        async def boom(pid):
            raise RuntimeError("secret internal launch detail")

        bulk_operations.launch_profile = boom
        try:
            result = await bulk_operations.bulk_launch_profiles(
                bulk_operations.BulkProfileIdsRequest(profile_ids=["abc"])
            )
        finally:
            bulk_operations.launch_profile = orig_launch

        item = result["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["code"], "LAUNCH_FAILED")
        self.assertNotIn("secret internal launch detail", item["message"])


class TestBulkCloseSanitizedErrors(unittest.IsolatedAsyncioTestCase):
    async def test_close_exception_returns_stable_code(self):
        orig_close = bulk_operations.close_profile

        async def boom(pid):
            raise RuntimeError("secret internal close detail")

        bulk_operations.close_profile = boom
        try:
            result = await bulk_operations.bulk_close_profiles(
                bulk_operations.BulkProfileIdsRequest(profile_ids=["abc"])
            )
        finally:
            bulk_operations.close_profile = orig_close

        item = result["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["code"], "CLOSE_FAILED")
        self.assertNotIn("secret internal close detail", item["message"])


class TestBulkDeleteFailClosed(unittest.IsolatedAsyncioTestCase):
    async def test_delete_aborts_when_close_fails(self):
        fake_mgr = FakeProfileManager()
        orig_mgr = bulk_operations.profile_manager
        orig_close = bulk_operations.close_profile
        orig_running = bulk_operations.is_profile_running

        async def close_fails(pid):
            return {"status": "error", "message": "Close failed"}

        bulk_operations.profile_manager = fake_mgr
        bulk_operations.close_profile = close_fails
        bulk_operations.is_profile_running = lambda pid: True
        try:
            result = await bulk_operations.bulk_delete_profiles(
                bulk_operations.BulkProfileIdsRequest(profile_ids=["abc"])
            )
        finally:
            bulk_operations.profile_manager = orig_mgr
            bulk_operations.close_profile = orig_close
            bulk_operations.is_profile_running = orig_running

        item = result["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["code"], "CLOSE_FAILED")
        self.assertEqual(fake_mgr.deleted, [], "Profile must not be deleted after a failed close")

    async def test_delete_succeeds_when_close_ok(self):
        fake_mgr = FakeProfileManager()
        orig_mgr = bulk_operations.profile_manager
        orig_close = bulk_operations.close_profile
        orig_running = bulk_operations.is_profile_running

        async def close_ok(pid):
            return {"status": "success", "message": "closed"}

        bulk_operations.profile_manager = fake_mgr
        bulk_operations.close_profile = close_ok
        bulk_operations.is_profile_running = lambda pid: True
        try:
            result = await bulk_operations.bulk_delete_profiles(
                bulk_operations.BulkProfileIdsRequest(profile_ids=["abc"])
            )
        finally:
            bulk_operations.profile_manager = orig_mgr
            bulk_operations.close_profile = orig_close
            bulk_operations.is_profile_running = orig_running

        item = result["results"][0]
        self.assertEqual(item["status"], "success")
        self.assertEqual(fake_mgr.deleted, ["abc"])

    async def test_delete_error_returns_stable_code(self):
        class BoomManager:
            def delete_profile(self, pid):
                raise RuntimeError("secret internal delete detail")

        orig_mgr = bulk_operations.profile_manager
        orig_running = bulk_operations.is_profile_running
        bulk_operations.profile_manager = BoomManager()
        bulk_operations.is_profile_running = lambda pid: False
        try:
            result = await bulk_operations.bulk_delete_profiles(
                bulk_operations.BulkProfileIdsRequest(profile_ids=["abc"])
            )
        finally:
            bulk_operations.profile_manager = orig_mgr
            bulk_operations.is_profile_running = orig_running

        item = result["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["code"], "DELETE_FAILED")
        self.assertNotIn("secret internal delete detail", item["message"])


class TestNormalizeOpResult(unittest.TestCase):
    def test_success_passthrough(self):
        res = bulk_operations._normalize_op_result(
            "p1", {"status": "success", "message": "ok"}, "LAUNCH_FAILED", "Launch failed"
        )
        self.assertEqual(res, {"profile_id": "p1", "status": "success", "message": "ok"})

    def test_unknown_code_collapsed_to_default(self):
        res = bulk_operations._normalize_op_result(
            "p1",
            {"status": "error", "code": "SECRET_INTERNAL", "message": "hijack"},
            "LAUNCH_FAILED",
            "Launch failed",
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["code"], "LAUNCH_FAILED")
        self.assertEqual(res["message"], "Launch failed")

    def test_known_code_passes_through_whitelist(self):
        for code in bulk_operations._PUBLIC_CODE_MESSAGES:
            expected_message = bulk_operations._PUBLIC_CODE_MESSAGES[code]
            with self.subTest(code=code):
                res = bulk_operations._normalize_op_result(
                    "p1",
                    {"status": "error", "code": code, "message": "arbitrary internal text"},
                    "CLOSE_FAILED",
                    "Close failed",
                )
                self.assertEqual(res["code"], code)
                self.assertEqual(res["message"], expected_message)

    def test_non_dict_result_collapsed_to_default(self):
        res = bulk_operations._normalize_op_result("p1", None, "CLOSE_FAILED", "Close failed")
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["code"], "CLOSE_FAILED")
        self.assertEqual(res["message"], "Close failed")


class TestBulkConcurrencyBounded(unittest.IsolatedAsyncioTestCase):
    async def test_close_concurrency_never_exceeds_semaphore(self):
        orig_close = bulk_operations.close_profile
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def slow_close(pid):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
            return {"status": "success", "message": "closed"}

        bulk_operations.close_profile = slow_close
        try:
            ids = [f"p{i}" for i in range(30)]
            await bulk_operations.bulk_close_profiles(
                bulk_operations.BulkProfileIdsRequest(profile_ids=ids)
            )
        finally:
            bulk_operations.close_profile = orig_close

        self.assertLessEqual(peak, 8, "close concurrency must never exceed _CLOSE_SEM")
        self.assertGreater(peak, 1, "test must observe real concurrency")

    async def test_delete_concurrency_never_exceeds_semaphore(self):
        orig_running = bulk_operations.is_profile_running
        orig_close = bulk_operations.close_profile
        orig_delete = bulk_operations.profile_manager.delete_profile
        active = 0
        peak = 0
        deleted = []
        lock = asyncio.Lock()

        async def slow_close(pid):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
            return {"status": "success", "message": "closed"}

        def track_delete(pid):
            deleted.append(pid)
            return True

        bulk_operations.is_profile_running = lambda pid: True
        bulk_operations.close_profile = slow_close
        bulk_operations.profile_manager.delete_profile = track_delete
        try:
            ids = [f"p{i}" for i in range(30)]
            await bulk_operations.bulk_delete_profiles(
                bulk_operations.BulkProfileIdsRequest(profile_ids=ids)
            )
        finally:
            bulk_operations.is_profile_running = orig_running
            bulk_operations.close_profile = orig_close
            bulk_operations.profile_manager.delete_profile = orig_delete

        self.assertLessEqual(peak, 8, "delete concurrency must never exceed _DELETE_SEM")
        self.assertGreater(peak, 1, "test must observe real concurrency")
        self.assertEqual(len(deleted), 30, "all profiles must be deleted after successful close")


class TestBulkCreateSanitizedErrors(unittest.IsolatedAsyncioTestCase):
    async def test_create_exception_returns_stable_code(self):
        orig_creator = bulk_operations.profile_creator.create_zero_leak_profile

        async def boom(*args, **kwargs):
            raise RuntimeError("secret internal create detail")

        bulk_operations.profile_creator.create_zero_leak_profile = boom
        try:
            result = await bulk_operations.bulk_create_profiles(
                bulk_operations.BulkCreateRequest(base_name="bulk", count=2)
            )
        finally:
            bulk_operations.profile_creator.create_zero_leak_profile = orig_creator

        self.assertEqual(result["status"], "error")
        for item in result["results"]:
            self.assertEqual(item["status"], "error")
            self.assertEqual(item["code"], "CREATE_FAILED")
            self.assertNotIn("secret internal create detail", item["message"])

    async def test_create_success_returns_profile_id(self):
        orig_creator = bulk_operations.profile_creator.create_zero_leak_profile

        async def ok_create(name, **kwargs):
            return {"status": "success", "profile": {"id": f"id-{name}"}}

        bulk_operations.profile_creator.create_zero_leak_profile = ok_create
        try:
            result = await bulk_operations.bulk_create_profiles(
                bulk_operations.BulkCreateRequest(base_name="ok", count=2)
            )
        finally:
            bulk_operations.profile_creator.create_zero_leak_profile = orig_creator

        self.assertEqual(result["status"], "success")
        for item in result["results"]:
            self.assertEqual(item["status"], "success")
            self.assertEqual(item["profile_id"], f"id-{item['name']}")

    async def test_create_non_success_keeps_known_code(self):
        orig_creator = bulk_operations.profile_creator.create_zero_leak_profile

        async def fail_create(name, **kwargs):
            return {"status": "error", "code": "KIMI_UNAVAILABLE", "message": "strictly unavailable"}

        bulk_operations.profile_creator.create_zero_leak_profile = fail_create
        try:
            result = await bulk_operations.bulk_create_profiles(
                bulk_operations.BulkCreateRequest(base_name="fail", count=1)
            )
        finally:
            bulk_operations.profile_creator.create_zero_leak_profile = orig_creator

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["results"][0]["code"], "KIMI_UNAVAILABLE")


class TestBulkCreateApiContract(unittest.TestCase):
    """A2: the legacy /api/profiles/generate/bulk route delegates to the
    hardened bulk_operations implementation (no raw exception strings, same
    sanitized contract as /api/profiles/bulk/create)."""

    def setUp(self):
        self.orig_token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "bulk-contract-token"

    def tearDown(self):
        if self.orig_token is None:
            os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        else:
            os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = self.orig_token

    def _post(self, path):
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        csrf = client.get("/api/system/csrf-token").json()["token"]
        return client.post(
            path,
            json={"base_name": "bulk", "count": 1},
            headers={"X-Admin-Token": "bulk-contract-token", "X-XSRF-Token": csrf},
        )

    def test_legacy_alias_does_not_leak_exception_details(self):
        async def boom(*args, **kwargs):
            raise RuntimeError("secret internal detail ABC")

        orig = bulk_operations.profile_creator.create_zero_leak_profile
        bulk_operations.profile_creator.create_zero_leak_profile = boom
        try:
            resp = self._post("/api/profiles/generate/bulk")
        finally:
            bulk_operations.profile_creator.create_zero_leak_profile = orig

        self.assertEqual(resp.status_code, 200)
        item = resp.json()["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["code"], "CREATE_FAILED")
        self.assertNotIn("secret internal detail ABC", resp.text)

    def test_legacy_and_new_routes_share_sanitized_contract(self):
        async def fail_create(name, **kwargs):
            return {"status": "error", "code": "KIMI_UNAVAILABLE", "message": "strictly unavailable"}

        orig = bulk_operations.profile_creator.create_zero_leak_profile
        bulk_operations.profile_creator.create_zero_leak_profile = fail_create
        try:
            legacy = self._post("/api/profiles/generate/bulk").json()
            canonical = self._post("/api/profiles/bulk/create").json()
        finally:
            bulk_operations.profile_creator.create_zero_leak_profile = orig

        self.assertEqual(legacy["status"], canonical["status"])
        self.assertEqual(legacy["total"], canonical["total"])
        self.assertEqual(legacy["succeeded"], canonical["succeeded"])
        self.assertEqual(legacy["failed"], canonical["failed"])
        self.assertEqual(legacy["results"][0]["code"], "KIMI_UNAVAILABLE")
        self.assertEqual(legacy["results"][0]["code"], canonical["results"][0]["code"])


class TestBulkCreateIntegrationN10(unittest.IsolatedAsyncioTestCase):
    """A7: N=10 integration through the real bulk_create_profiles orchestration
    (asyncio.gather + _CREATE_SEM). Verifies full result contract, unique naming,
    real (bounded) concurrency, and proxy/pin/advanced passthrough."""

    async def test_bulk_create_10_succeeds_with_bounded_concurrency(self):
        orig_creator = bulk_operations.profile_creator.create_zero_leak_profile

        calls = []
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def ok_create(name, **kwargs):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.01)
            calls.append((name, kwargs))
            async with lock:
                active -= 1
            return {"status": "success", "profile": {"id": f"id-{name}"}}

        bulk_operations.profile_creator.create_zero_leak_profile = ok_create
        try:
            result = await bulk_operations.bulk_create_profiles(
                bulk_operations.BulkCreateRequest(
                    base_name="bulk",
                    count=10,
                    proxy_string="socks5://user:pass@127.0.0.1:1234",
                    pin="4321",
                    timezone="Europe/London",
                    locale="en-GB",
                    advanced={"os": "Mac", "screen_resolution": "2560x1440"},
                )
            )
        finally:
            bulk_operations.profile_creator.create_zero_leak_profile = orig_creator

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total"], 10)
        self.assertEqual(result["succeeded"], 10)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(len(result["results"]), 10)
        for item in result["results"]:
            self.assertEqual(item["status"], "success")
            self.assertIn("profile_id", item)

        names = [item["name"] for item in result["results"]]
        self.assertEqual(names, [f"bulk_{i}" for i in range(1, 11)])
        self.assertEqual(len(set(names)), 10, "profile names must be unique")

        self.assertLessEqual(peak, 8, "create concurrency must never exceed _CREATE_SEM")
        self.assertGreater(peak, 1, "test must observe real concurrency")

        self.assertEqual(len(calls), 10)
        for name, kwargs in calls:
            self.assertEqual(kwargs["pin"], "4321")
            self.assertEqual(kwargs["skip_warming"], True)
            self.assertEqual(kwargs["proxy"]["host"], "127.0.0.1")
            self.assertEqual(kwargs["proxy"]["port"], 1234)
            self.assertEqual(kwargs["proxy"]["scheme"], "socks5")
            self.assertNotIn("password", str(kwargs["proxy"]) if isinstance(kwargs["proxy"], str) else "")
            self.assertEqual(kwargs["advanced_ui"]["timezone"], "Europe/London")
            self.assertEqual(kwargs["advanced_ui"]["locale"], "en-GB")
            self.assertEqual(kwargs["advanced_ui"]["os"], "Mac")

    async def test_bulk_create_10_partial_failure_contract(self):
        orig_creator = bulk_operations.profile_creator.create_zero_leak_profile

        async def half_fail(name, **kwargs):
            if name.endswith(("_2", "_4", "_6", "_8", "_10")):
                return {"status": "error", "code": "KIMI_UNAVAILABLE", "message": "strictly unavailable"}
            return {"status": "success", "profile": {"id": f"id-{name}"}}

        bulk_operations.profile_creator.create_zero_leak_profile = half_fail
        try:
            result = await bulk_operations.bulk_create_profiles(
                bulk_operations.BulkCreateRequest(base_name="partial", count=10)
            )
        finally:
            bulk_operations.profile_creator.create_zero_leak_profile = orig_creator

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total"], 10)
        self.assertEqual(result["succeeded"], 5)
        self.assertEqual(result["failed"], 5)
        for item in result["results"]:
            if item["status"] == "success":
                self.assertIn("profile_id", item)
            else:
                self.assertEqual(item["code"], "KIMI_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
