"""Regression tests ensuring runtime account/proxy data files are not committed."""
from __future__ import annotations

import os
import subprocess
import unittest


class TrackedRuntimeFilesTests(unittest.TestCase):
    def _tracked(self, path: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "ls-files", path],
                capture_output=True,
                text=True,
                check=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
        except FileNotFoundError:
            self.skip("git is not available")
        return bool(result.stdout.strip())

    def test_runtime_proxy_database_not_tracked(self):
        self.assertFalse(self._tracked("backend/proxies.db"))

    def test_runtime_scraped_proxies_not_tracked(self):
        self.assertFalse(self._tracked("backend/scraped_proxies.json"))

    def test_runtime_cloudflare_accounts_not_tracked(self):
        self.assertFalse(self._tracked("cloudflare_accounts.txt"))
