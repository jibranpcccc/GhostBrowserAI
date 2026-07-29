r"""
Batch 12 — Secrets Hygiene Audit
==================================
Offline (no browser, no network) test scaffold that verifies GhostBrowser
backend modules do not leak API keys, Cloudflare tokens, proxy credentials, or
canary secrets into stdout, stderr, application logs, API responses, or
generated report files (.json, .md, .txt).

Run with:
    venv\Scripts\python.exe tests/test_secrets_hygiene.py
"""

import asyncio
import io
import json
import logging
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Make the project root importable as `backend.*`
sys.path.append(os.getcwd())

# Force the global profile manager onto an isolated temporary directory before
# any backend module loads its singleton.
_TEST_PROFILE_DIR = tempfile.mkdtemp(prefix="ghostbrowser_secrets_hygiene_")
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = _TEST_PROFILE_DIR
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

from backend import ai_generator
from backend import cloudflare_manager
from backend import logging_config
from backend import profile_creator
from backend import proxy_manager


# ---------------------------------------------------------------------------
# Canary secrets — these tokens must never appear in logs, stdout, stderr, API
# responses, or report files.
# ---------------------------------------------------------------------------
FAKE_API_KEY = "gb_hygiene_secret_apikey_9f3e2d1a7c6b4e5f"
FAKE_PASSWORD = "gb_hygiene_secret_password_8h2j5k9l0m1n3o4p"
FAKE_ACCOUNT_ID = "hygiene-account-id"


# ---------------------------------------------------------------------------
# Output capture helper
# ---------------------------------------------------------------------------
class SecretCapture:
    """Capture stdout, stderr, and backend log records for leak inspection."""

    def __init__(self):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.log_stream = io.StringIO()
        self.handler = logging.StreamHandler(self.log_stream)
        self.handler.setLevel(logging.DEBUG)
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._orig_handlers = None
        self._orig_level = None

    def __enter__(self):
        self._orig_handlers = logging_config.logger.handlers[:]
        self._orig_level = logging_config.logger.level
        logging_config.logger.handlers = [self.handler]
        logging_config.logger.setLevel(logging.DEBUG)
        self._stdout_ctx = redirect_stdout(self.stdout)
        self._stderr_ctx = redirect_stderr(self.stderr)
        self._stdout_ctx.__enter__()
        self._stderr_ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stderr_ctx.__exit__(exc_type, exc, tb)
        self._stdout_ctx.__exit__(exc_type, exc, tb)
        logging_config.logger.handlers = self._orig_handlers
        logging_config.logger.setLevel(self._orig_level)
        self.handler.flush()
        return False

    def combined(self) -> str:
        return self.stdout.getvalue() + self.stderr.getvalue() + self.log_stream.getvalue()

    def assert_no_canaries(self, label: str = "capture"):
        combined = self.combined()
        assert FAKE_API_KEY not in combined, f"{label}: fake API key leaked to stdout/stderr/logs"
        assert FAKE_PASSWORD not in combined, f"{label}: fake proxy password leaked to stdout/stderr/logs"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
def _build_fake_cloudflare_manager(tmp: str) -> cloudflare_manager.CloudflareManager:
    accounts_file = os.path.join(tmp, "cf_accounts.txt")
    priority_file = os.path.join(tmp, "cf_priority.txt")
    cooldowns_file = os.path.join(tmp, "cf_cooldowns.json")
    with open(accounts_file, "w", encoding="utf-8") as f:
        f.write(f"{FAKE_ACCOUNT_ID},{FAKE_API_KEY}\n")
    open(priority_file, "w", encoding="utf-8").close()
    return cloudflare_manager.CloudflareManager(
        accounts_file=accounts_file,
        priority_accounts_file=priority_file,
        cooldowns_file=cooldowns_file,
        use_secure_store=False,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
async def test_cloudflare_manager_load_and_status_hides_token():
    print("\n--- Cloudflare manager load/status hides bearer token ---")
    with tempfile.TemporaryDirectory() as tmp:
        with SecretCapture() as cap:
            cm = _build_fake_cloudflare_manager(tmp)
            cm.load_accounts()
            status = cm.get_all_status()
            cm.report_failure(FAKE_ACCOUNT_ID, cooldown_minutes=5)
            cm._save_cooldowns()

    cap.assert_no_canaries("cloudflare_manager load/status")
    serialized = json.dumps(status)
    assert FAKE_API_KEY not in serialized, "API key appeared in status payload"
    assert FAKE_ACCOUNT_ID not in serialized, "Full account ID appeared in status payload"
    assert all("..." in entry["account_id"] for entry in status), "Account ID not masked"
    print(" -> PASS")


async def test_ai_generator_direct_cloudflare_hides_bearer_token():
    print("\n--- AI generator direct Cloudflare path hides bearer token ---")
    original_manager = ai_generator.cloudflare_manager
    original_client = ai_generator._shared_client
    original_sleep = ai_generator.asyncio.sleep

    async def no_sleep(_seconds):
        return None

    fake_manager = SimpleNamespace(
        accounts=[{"account_id": FAKE_ACCOUNT_ID, "token": FAKE_API_KEY, "priority": False}],
        cooldowns={},
        load_accounts=lambda: None,
        report_failure=lambda account_id, cooldown_minutes=5: None,
    )

    class FakeResponse:
        status_code = 503

    class FakeClient:
        async def post(self, url, **kwargs):
            # The real full account_id belongs in the request URL; it must never
            # be printed or logged by the caller under test.
            assert FAKE_ACCOUNT_ID in url
            return FakeResponse()

    try:
        ai_generator.cloudflare_manager = fake_manager
        ai_generator._shared_client = FakeClient()
        ai_generator.asyncio.sleep = no_sleep

        with SecretCapture() as cap:
            result = await ai_generator._call_direct_cloudflare(
                "Windows", "Chrome", 149, priority=False
            )

        assert result is None, "Expected all accounts to fail"
        cap.assert_no_canaries("ai_generator direct Cloudflare")
    finally:
        ai_generator.cloudflare_manager = original_manager
        ai_generator._shared_client = original_client
        ai_generator.asyncio.sleep = original_sleep
    print(" -> PASS")


async def test_proxy_parse_exception_does_not_echo_password():
    print("\n--- Proxy parser exception does not echo password ---")
    record = {
        "server": "unsupported://host.example:1080",
        "username": "proxy-user",
        "password": FAKE_PASSWORD,
    }
    with SecretCapture() as cap:
        try:
            proxy_manager._parse_proxy_record(record)
            raise AssertionError("Expected ValueError for unsupported scheme")
        except ValueError as exc:
            error_text = str(exc)

    assert FAKE_PASSWORD not in error_text, "Password leaked in exception message"
    cap.assert_no_canaries("proxy_manager parse exception")
    print(" -> PASS")


async def test_proxy_redaction_strips_credentials():
    print("\n--- Proxy redaction strips credentials from API metadata ---")
    record = {
        "server": "http://proxy.example:8080",
        "username": "proxy-user",
        "password": FAKE_PASSWORD,
    }
    redacted = proxy_manager.redact_proxy_record(record)
    serialized = json.dumps(redacted)
    assert FAKE_PASSWORD not in serialized, "Password leaked in redacted record"
    assert "username" not in serialized, "Username leaked in redacted record"
    assert "password" not in serialized, "Password key leaked in redacted record"
    assert redacted["server"] == "http://proxy.example:8080"
    assert redacted["authenticated"] is True
    print(" -> PASS")


async def test_proxy_health_check_failure_hides_password():
    print("\n--- Proxy health-check failure hides password ---")
    fake_client = AsyncMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(side_effect=Exception("simulated network failure"))

    with patch("httpx.AsyncClient", return_value=fake_client):
        with SecretCapture() as cap:
            result = await proxy_manager.proxy_manager.check_proxy_health(
                {"server": "http://proxy.example:8080", "username": "u", "password": FAKE_PASSWORD},
                record_failure=False,
            )

    assert result is False, "Expected health check to return False"
    cap.assert_no_canaries("proxy_manager health check")
    print(" -> PASS")


async def test_proxy_unhealthy_pool_log_hides_password():
    print("\n--- Proxy pool exhaustion log hides password ---")
    with patch.object(proxy_manager.proxy_manager, "_get_active_proxies", return_value=[]):
        with SecretCapture() as cap:
            result = await proxy_manager.proxy_manager.get_proxy_for_profile("profile-123")

    assert result is None, "Expected None when pool is empty"
    cap.assert_no_canaries("proxy_manager get_proxy_for_profile")
    print(" -> PASS")


async def test_profile_creator_error_hides_proxy_credentials():
    print("\n--- Profile creator validation error hides proxy credentials ---")
    leaked_proxy = {
        "server": "http://proxy.example:8080",
        "username": "profile-user",
        "password": FAKE_PASSWORD,
    }
    with patch.object(profile_creator, "profile_manager", MagicMock(list_profiles=MagicMock(return_value=[]))):
        with SecretCapture() as cap:
            result = await profile_creator.profile_creator.create_zero_leak_profile(
                name="SecretsHygieneProfile",
                proxy=leaked_proxy,
                advanced_ui={"timezone": "Invalid/FakeTZ"},
            )

    assert result["status"] == "error", "Expected validation error"
    assert "Validation failed" in result["message"], "Unexpected error message"
    assert FAKE_PASSWORD not in result["message"], "Password leaked in error response"
    cap.assert_no_canaries("profile_creator validation error")
    print(" -> PASS")


async def test_generated_report_files_do_not_contain_canary_secrets():
    print("\n--- Generated report files do not contain canary secrets ---")
    with tempfile.TemporaryDirectory() as tmp:
        generated_files = []

        # Cloudflare cooldown report
        cm = _build_fake_cloudflare_manager(tmp)
        cm.report_failure(FAKE_ACCOUNT_ID, cooldown_minutes=5)
        cm._save_cooldowns()
        generated_files.append(cm.cooldowns_file)

        # Quarantine report
        quarantine_dir = os.path.join(tmp, "quarantine")
        profile_src = os.path.join(tmp, "profile_src")
        os.makedirs(quarantine_dir, exist_ok=True)
        os.makedirs(profile_src, exist_ok=True)

        old_quarantine_dir = profile_creator.QUARANTINE_DIR
        old_quarantine_meta = profile_creator.QUARANTINE_META
        try:
            profile_creator.QUARANTINE_DIR = quarantine_dir
            profile_creator.QUARANTINE_META = os.path.join(quarantine_dir, "quarantine_meta.json")

            test_profile = {
                "id": "secrets-hygiene-profile",
                "name": "SecretsHygieneProfile",
                "path": profile_src,
                "proxy": {
                    "server": "http://proxy.example:8080",
                    "username": "profile-user",
                    "password": FAKE_PASSWORD,
                },
            }
            profile_creator.profile_creator._quarantine_profile(
                test_profile, ["simulated hygiene failure"]
            )
            generated_files.append(profile_creator.QUARANTINE_META)
        finally:
            profile_creator.QUARANTINE_DIR = old_quarantine_dir
            profile_creator.QUARANTINE_META = old_quarantine_meta

        for path in generated_files:
            assert os.path.exists(path), f"Expected generated report file missing: {path}"
            with open(path, "rb") as f:
                data = f.read()
            assert FAKE_API_KEY.encode() not in data, f"Fake API key leaked into {path}"
            assert FAKE_PASSWORD.encode() not in data, f"Fake proxy password leaked into {path}"
    print(" -> PASS")


# ---------------------------------------------------------------------------
# Entry point matching the other `tests/test_*.py` files.
# ---------------------------------------------------------------------------
async def main():
    tests = [
        test_cloudflare_manager_load_and_status_hides_token,
        test_ai_generator_direct_cloudflare_hides_bearer_token,
        test_proxy_parse_exception_does_not_echo_password,
        test_proxy_redaction_strips_credentials,
        test_proxy_health_check_failure_hides_password,
        test_proxy_unhealthy_pool_log_hides_password,
        test_profile_creator_error_hides_proxy_credentials,
        test_generated_report_files_do_not_contain_canary_secrets,
    ]
    try:
        for test in tests:
            await test()
        print("\nALL SECRETS-HYGIENE TESTS PASSED SUCCESSFULLY!")
    except AssertionError as exc:
        print(f"\nTEST FAILURE: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\nUNEXPECTED EXCEPTION: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
