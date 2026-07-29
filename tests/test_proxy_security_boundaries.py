import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.browser_manager as browser_manager
import backend.db as proxy_db
import backend.proxy_manager as proxy_module
from backend.profile_manager import ProfileManager


FAKE_NATIVE_METADATA = {
    "ua": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "uadata": {
        "brands": [
            {"brand": "Chromium", "version": "149"},
            {"brand": "Not)A;Brand", "version": "24"},
        ],
        "mobile": False,
        "platform": "Windows",
        "architecture": "x86",
        "bitness": "64",
        "model": "",
        "platformVersion": "19.0.0",
        "uaFullVersion": "149.0.0.0",
        "fullVersionList": [
            {"brand": "Chromium", "version": "149.0.0.0"},
            {"brand": "Not)A;Brand", "version": "24.0.0.0"},
        ],
    },
}


class EnvironmentMixin:
    def setUp(self):
        self.original_environment = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_environment)


class ProxyLaunchBoundaryTests(EnvironmentMixin, unittest.IsolatedAsyncioTestCase):
    def _profile(self, **updates):
        profile = {
            "id": "11111111-2222-3333-4444-555555555555",
            "advanced": {"webrtc_mode": "protected"},
            "fingerprint": {"_source": "direct_cloudflare"},
            "ai_provenance": {
                "verified": True,
                "requested_model": "@cf/moonshotai/kimi-k2.7-code",
            },
            "verification_status": "verified",
        }
        profile.update(updates)
        return profile

    def _config_patches(self):
        return (
            patch(
                "backend.security_hardening.validate_extensions",
                return_value=[],
            ),
            patch(
                "backend.config.get_installed_chromium_version",
                return_value="149.0.0.0",
            ),
            patch(
                "backend.config.get_installed_chromium_major_version",
                return_value=149,
            ),
            patch.object(
                browser_manager,
                "probe_native_metadata",
                new=AsyncMock(return_value=FAKE_NATIVE_METADATA),
            ),
        )

    async def test_no_proxy_uses_direct_connection_without_pool(self):
        os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
        pool = AsyncMock(side_effect=AssertionError("pool must not run"))
        patches = self._config_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            proxy_module.proxy_manager, "get_proxy_for_profile", new=pool
        ):
            config = await browser_manager.build_browser_launch_config(self._profile())
        pool.assert_not_awaited()
        self.assertIsNone(config["proxy"])

    async def test_explicit_profile_proxy_wins_and_is_health_checked(self):
        os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
        explicit = {
            "server": "http://127.0.0.1:19001",
            "username": "audit-user",
            "password": "audit-password",
        }
        pool = AsyncMock(side_effect=AssertionError("pool must not run"))
        health = AsyncMock(return_value=True)
        patches = self._config_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            proxy_module.proxy_manager, "get_proxy_for_profile", new=pool
        ), patch.object(
            proxy_module.proxy_manager, "check_proxy_health", new=health
        ):
            config = await browser_manager.build_browser_launch_config(
                self._profile(proxy=explicit)
            )
        pool.assert_not_awaited()
        health.assert_awaited_once_with(explicit)
        self.assertEqual(config["proxy"], explicit)
        self.assertIn("--disable-quic", config["args"])
        self.assertIn(
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            config["args"],
        )

    async def test_proxy_pin_has_priority_over_profile_proxy(self):
        os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
        health = AsyncMock(return_value=True)
        pool = AsyncMock(side_effect=AssertionError("pool must not run"))
        patches = self._config_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            proxy_module.proxy_manager, "get_proxy_for_profile", new=pool
        ), patch.object(
            proxy_module.proxy_manager, "check_proxy_health", new=health
        ):
            config = await browser_manager.build_browser_launch_config(
                self._profile(
                    proxy={"server": "http://127.0.0.1:19001"},
                    proxy_pin="127.0.0.2:19002:pin-user:pin-password",
                )
            )
        pool.assert_not_awaited()
        checked = health.await_args.args[0]
        self.assertEqual(checked["server"], "http://127.0.0.2:19002")
        self.assertEqual(config["proxy"]["username"], "pin-user")
        self.assertEqual(config["proxy"]["password"], "pin-password")

    async def test_dead_explicit_proxy_aborts_without_pool_or_probe(self):
        os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
        health = AsyncMock(return_value=False)
        pool = AsyncMock(side_effect=AssertionError("pool must not run"))
        probe = AsyncMock(side_effect=AssertionError("probe must not run"))
        with patch.object(
            proxy_module.proxy_manager, "get_proxy_for_profile", new=pool
        ), patch.object(
            proxy_module.proxy_manager, "check_proxy_health", new=health
        ), patch.object(browser_manager, "probe_native_metadata", new=probe), patch(
            "backend.security_hardening.validate_extensions",
            return_value=[],
        ):
            with self.assertRaisesRegex(RuntimeError, "configured proxy is unavailable"):
                await browser_manager.build_browser_launch_config(
                    self._profile(proxy={"server": "http://127.0.0.1:19001"})
                )
        pool.assert_not_awaited()
        probe.assert_not_awaited()

    async def test_unverified_profile_is_blocked_before_playwright(self):
        os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
        with tempfile.TemporaryDirectory() as directory:
            profile = self._profile(
                path=directory,
                fingerprint={},
                ai_provenance={"verified": False},
                verification_status="unverified",
            )
            with patch.object(
                browser_manager.profile_manager, "get_profile", return_value=profile
            ), patch.object(
                browser_manager.profile_manager, "PROFILES_DIR", directory
            ), patch.object(
                browser_manager, "async_playwright"
            ) as playwright_factory:
                result = await browser_manager.launch_profile(profile["id"])
        self.assertEqual(result["status"], "error")
        self.assertIn("provenance", result["message"])
        playwright_factory.assert_not_called()

    async def test_test_environment_explicitly_allows_no_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
            os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = directory
            pool = AsyncMock(return_value=None)
            patches = self._config_patches()
            with patches[0], patches[1], patches[2], patches[3], patch.object(
                browser_manager.profile_manager, "PROFILES_DIR", directory
            ), patch.object(
                proxy_module.proxy_manager, "get_proxy_for_profile", new=pool
            ):
                config = await browser_manager.build_browser_launch_config(
                    self._profile()
                )
            self.assertIsNone(config["proxy"])

    async def test_test_marker_alone_still_uses_direct_connection(self):
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        os.environ.pop("GHOSTBROWSER_TEST_PROFILES_DIR", None)
        pool = AsyncMock(return_value=None)
        patches = self._config_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            proxy_module.proxy_manager, "get_proxy_for_profile", new=pool
        ):
            config = await browser_manager.build_browser_launch_config(self._profile())
        pool.assert_not_awaited()
        self.assertIsNone(config["proxy"])

    async def test_mismatched_test_directory_does_not_change_direct_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
            os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = directory
            pool = AsyncMock(return_value=None)
            patches = self._config_patches()
            with patches[0], patches[1], patches[2], patches[3], patch.object(
                proxy_module.proxy_manager, "get_proxy_for_profile", new=pool
            ):
                config = await browser_manager.build_browser_launch_config(self._profile())
            pool.assert_not_awaited()
            self.assertIsNone(config["proxy"])


class AtRestCredentialTests(EnvironmentMixin, unittest.TestCase):
    def test_profile_proxy_pin_plaintext_is_migrated_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "profiles_meta.json"
            secret_pin = "127.0.0.1:19100:user-secret:password-secret"
            metadata.write_text(
                json.dumps(
                    {
                        "profile-1": {
                            "id": "profile-1",
                            "path": str(Path(directory) / "profile-1"),
                            "proxy_pin": secret_pin,
                            "proxy": {
                                "server": "http://127.0.0.1:19100",
                                "username": "user-secret",
                                "password": "password-secret",
                            },
                            "advanced": {"webrtc_mode": "protected"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            manager = ProfileManager(override_dir=directory)
            self.assertEqual(manager.get_profile("profile-1")["proxy_pin"], secret_pin)
            raw = metadata.read_text(encoding="utf-8")
            self.assertNotIn("user-secret", raw)
            self.assertNotIn("password-secret", raw)
            stored = json.loads(raw)["profile-1"]
            self.assertTrue(stored["proxy_pin"].startswith("enc:"))
            self.assertTrue(stored["proxy"].startswith("enc:"))
            reloaded = ProfileManager(override_dir=directory)
            self.assertEqual(reloaded.get_profile("profile-1")["proxy_pin"], secret_pin)

    def test_authenticated_proxy_db_persists_ciphertext_and_recovers_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            original_db_path = proxy_db.DB_PATH
            original_key_path = proxy_db.PROXY_KEY_PATH
            try:
                proxy_db.DB_PATH = str(Path(directory) / "proxies.db")
                proxy_db.PROXY_KEY_PATH = str(Path(directory) / ".proxy.key")
                proxy_db.init_db()
                manager = proxy_module.ProxyManager()
                added = manager.add_proxies(
                    [
                        {
                            "server": "http://127.0.0.1:19200",
                            "username": "db-user-secret",
                            "password": "db-password-secret",
                        }
                    ]
                )
                self.assertEqual(added, 1)
                raw_bytes = Path(proxy_db.DB_PATH).read_bytes()
                self.assertNotIn(b"db-user-secret", raw_bytes)
                self.assertNotIn(b"db-password-secret", raw_bytes)
                connection = sqlite3.connect(proxy_db.DB_PATH)
                try:
                    encrypted = connection.execute(
                        "SELECT credentials_enc FROM proxies"
                    ).fetchone()[0]
                finally:
                    connection.close()
                self.assertTrue(encrypted.startswith("enc:"))
                loaded = proxy_db.get_best_proxies(limit=1)[0]
                self.assertEqual(loaded["username"], "db-user-secret")
                self.assertEqual(loaded["password"], "db-password-secret")
            finally:
                proxy_db.DB_PATH = original_db_path
                proxy_db.PROXY_KEY_PATH = original_key_path


class ApiAndAuditBoundaryTests(EnvironmentMixin, unittest.TestCase):
    def test_api_redaction_removes_every_proxy_secret(self):
        os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "unit-test-token"
        from backend.main import _redact_sensitive_api_data

        payload = {
            "id": "profile-1",
            "proxy_pin": "host:1:user:password",
            "proxy": {
                "server": "http://host.example:8080",
                "username": "api-user-secret",
                "password": "api-password-secret",
            },
            "nested": {"password": "another-secret", "safe": True},
        }
        redacted = _redact_sensitive_api_data(payload)
        serialized = json.dumps(redacted)
        self.assertNotIn("proxy_pin", serialized)
        self.assertNotIn("username", serialized)
        self.assertNotIn("password", serialized)
        self.assertNotIn("api-user-secret", serialized)
        self.assertNotIn("api-password-secret", serialized)
        self.assertEqual(redacted["proxy"]["server"], "http://host.example:8080")
        self.assertTrue(redacted["proxy"]["authenticated"])

    def test_live_audit_guard_runs_before_secret_access_and_restores_parent(self):
        environment = os.environ.copy()
        environment.pop("GHOSTBROWSER_RUN_PROXY_WEBRTC_TEST", None)
        completed = subprocess.run(
            [
                sys.executable,
                "-u",
                str(ROOT / "tests" / "test_webrtc_proxy_leak_audit.py"),
            ],
            cwd=str(ROOT),
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "PROXY WEBRTC LEAK TEST: NOT RUN")
        self.assertEqual(completed.stderr.strip(), "")
        self.assertEqual(os.environ, self.original_environment)

        source = (ROOT / "tests" / "test_webrtc_proxy_leak_audit.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("TEST_PROXY_", source)
        self.assertNotIn("traceback", source)
        self.assertIn("proxy_audit_secrets.json", source)
        self.assertIn("temp_root.cleanup()", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
