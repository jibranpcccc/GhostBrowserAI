"""Isolation regression guard for test discovery.

``unittest discover`` imports test modules in sorted order, so this module is
imported first and applies the shared isolation guard (see ``isolation_guard``)
before any test module can load production singletons. It also pins the
behavior down with assertions so a future regression fails loudly instead of
silently leaking real Cloudflare credentials into the test run.
"""

from __future__ import annotations

import os
import unittest

import isolation_guard  # noqa: F401  (applies import-time sanitization)


class TestDiscoveryIsolation(unittest.TestCase):
    def test_cloudflare_credentials_are_not_loaded(self):
        from backend.cloudflare_manager import cloudflare_manager

        self.assertEqual(cloudflare_manager.accounts, [])
        self.assertFalse(cloudflare_manager.allow_plaintext)

    def test_secure_store_is_redirected_away_from_production(self):
        from pathlib import Path

        from backend import credential_store

        production_store = (
            isolation_guard.WORKSPACE_ROOT / "api_data" / "cloudflare_accounts.secure.json"
        )
        self.assertNotEqual(
            Path(credential_store.DEFAULT_STORE_PATH).resolve(),
            production_store.resolve(),
        )

    def test_environment_is_sanitized(self):
        self.assertEqual(os.getenv("GHOSTBROWSER_ALLOW_PLAINTEXT_CREDENTIALS"), "0")
        self.assertEqual(os.getenv("GHOSTBROWSER_CF_ACCOUNTS_JSON"), "")
        self.assertEqual(os.getenv("GHOSTBROWSER_ENABLE_COOKIE_WARMER"), "0")

    def test_fresh_manager_loads_no_accounts(self):
        from backend.cloudflare_manager import CloudflareManager

        manager = CloudflareManager()
        self.assertEqual(manager.accounts, [])

    def test_plaintext_account_file_is_not_the_production_file(self):
        import backend.cloudflare_manager as cfm

        production_file = isolation_guard.WORKSPACE_ROOT / "cloudflare_accounts.txt"
        self.assertNotEqual(cfm.ACCOUNTS_FILE, str(production_file))


if __name__ == "__main__":
    unittest.main()
