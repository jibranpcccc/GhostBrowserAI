import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.baseline_lab import compare_baseline_to_cohorts
from backend.credential_store import load_accounts, save_accounts, store_status
from backend.launch_policy import build_surface_launch_policy
from scripts.release_audit import audit_distribution


class CredentialStoreTests(unittest.TestCase):
    def test_dpapi_round_trip_without_plaintext_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.secure.json"
            accounts = [{"account_id": "account-a", "token": "secret-value-a", "priority": True}]
            self.assertEqual(save_accounts(accounts, path), 1)
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-value-a", raw)
            self.assertEqual(load_accounts(path), accounts)
            self.assertEqual(store_status(path)["count"], 1)


class LaunchPolicyTests(unittest.TestCase):
    def test_direct_and_proxy_service_worker_policies(self):
        direct = build_surface_launch_policy({"screen_resolution": "1536x864", "device_scale_factor": 1.25}, False)
        proxied = build_surface_launch_policy({"screen_resolution": "1920x1080"}, True)
        self.assertFalse(direct.block_service_workers)
        self.assertEqual(direct.service_worker_policy, "native_direct")
        self.assertTrue(proxied.block_service_workers)
        self.assertEqual(proxied.service_worker_policy, "blocked_for_proxy")

    def test_invalid_values_fall_back_safely(self):
        policy = build_surface_launch_policy({"screen_resolution": "bad", "device_scale_factor": 9}, False)
        self.assertEqual((policy.viewport_width, policy.viewport_height), (1920, 1080))
        self.assertEqual(policy.device_scale_factor, 1.0)


class BaselineLabTests(unittest.TestCase):
    def test_known_windows_baseline_matches_mainstream_cohort(self):
        report = compare_baseline_to_cohorts({
            "host_os": "Windows",
            "chromium_version": "149.0.0.0",
            "surfaces": {
                "hardwareConcurrency": 8,
                "deviceMemory": 16,
                "screen_resolution": "1920x1080",
                "device_scale_factor": 1.0,
            },
        })
        self.assertEqual(report["best_match"]["cohort_id"], "win-mainstream-1080p")
        self.assertEqual(report["best_match"]["mismatch_count"], 0)


class ReleaseAuditTests(unittest.TestCase):
    def test_clean_distribution_generates_no_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "playwright-browsers/chrome-win64").mkdir(parents=True)
            (root / "GhostBrowser.exe").write_bytes(b"safe executable placeholder")
            (root / "playwright-browsers/chrome-win64/chrome.exe").write_bytes(b"safe chrome placeholder")
            self.assertTrue(audit_distribution(root)["passed"])

    def test_secret_material_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "playwright-browsers/chrome-win64").mkdir(parents=True)
            (root / "GhostBrowser.exe").write_bytes(b"safe")
            (root / "playwright-browsers/chrome-win64/chrome.exe").write_bytes(b"safe")
            (root / "config.txt").write_bytes(b"cfut_abcdefghijklmnopqrstuvwxyz123456")
            self.assertFalse(audit_distribution(root)["passed"])


if __name__ == "__main__":
    unittest.main()
