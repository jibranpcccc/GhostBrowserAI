from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend import ai_generator
from backend.cloudflare_manager import CloudflareManager


class CloudflarePriorityManagerTests(unittest.TestCase):
    def _manager(self, directory: str) -> CloudflareManager:
        root = Path(directory)
        (root / "priority.txt").write_text(
            "p1,token-p1\np2,token-p2\np3,token-p3\n",
            encoding="utf-8",
        )
        (root / "standard.txt").write_text(
            "p2,duplicate-standard-token\ns1,token-s1\ns2,token-s2\n",
            encoding="utf-8",
        )
        return CloudflareManager(
            accounts_file=str(root / "standard.txt"),
            priority_accounts_file=str(root / "priority.txt"),
            cooldowns_file=str(root / "cooldowns.json"),
            use_secure_store=False,
            allow_plaintext=True,
        )

    def test_priority_file_wins_duplicates_and_status_redacts_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)

            self.assertEqual(manager.total_accounts, 5)
            self.assertEqual(manager.priority_count, 3)
            duplicate = next(account for account in manager.accounts if account["account_id"] == "p2")
            self.assertTrue(duplicate["priority"])
            self.assertEqual(duplicate["token"], "token-p2")
            self.assertTrue(all("token" not in item for item in manager.get_all_status()))

    def test_priority_batches_rotate_and_cooldowns_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)

            first = manager.get_account_candidates(True, max_accounts=2, rotate_by=2)
            second = manager.get_account_candidates(True, max_accounts=2, rotate_by=2)
            self.assertEqual([account["account_id"] for account in first], ["p1", "p2"])
            self.assertEqual([account["account_id"] for account in second], ["p3", "p1"])

            manager.cooldowns["p1"] = time.time() + 60
            healthy_ids = {
                account["account_id"] for account in manager.get_healthy_accounts(priority=True)
            }
            self.assertEqual(healthy_ids, {"p2", "p3"})

    def test_single_account_selection_prefers_priority_then_standard(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)

            self.assertTrue(manager.get_account()["priority"])
            expiry = time.time() + 60
            for account in manager.accounts:
                if account["priority"]:
                    manager.cooldowns[account["account_id"]] = expiry
            self.assertFalse(manager.get_account()["priority"])


class CloudflarePriorityGeneratorTests(unittest.IsolatedAsyncioTestCase):
    def test_json_extraction_accepts_fences_reasoning_and_content_blocks(self):
        expected = {"os": "Windows", "cpu_cores": 8}
        reasoned = f"<think>checking consistency</think>\n```json\n{expected!r}\n```".replace("'", '"')
        self.assertIn(expected, ai_generator._extract_json_objects(reasoned))
        self.assertIn(
            expected,
            ai_generator._extract_json_objects([{"type": "text", "text": reasoned}]),
        )

    async def test_generation_order_is_priority_then_hermes_then_standard(self):
        priority_result = {"source": "priority"}
        with patch.object(
            ai_generator, "_call_direct_cloudflare", new=AsyncMock(return_value=priority_result)
        ) as direct, patch.object(
            ai_generator, "_call_via_racing_proxy", new=AsyncMock()
        ) as racing, patch.object(
            ai_generator, "sanitize_native_surface_fields", side_effect=lambda value: value
        ):
            result = await ai_generator.generate_fingerprint_ai("Windows", "Chrome", 139)

        self.assertEqual(result, priority_result)
        self.assertEqual(direct.await_count, 1)
        self.assertTrue(direct.await_args.kwargs["priority"])
        racing.assert_not_awaited()

        with patch.object(
            ai_generator, "_call_direct_cloudflare", new=AsyncMock(side_effect=[None, {"source": "standard"}])
        ) as direct, patch.object(
            ai_generator, "_call_via_racing_proxy", new=AsyncMock(return_value=None)
        ) as racing, patch.object(
            ai_generator, "sanitize_native_surface_fields", side_effect=lambda value: value
        ):
            result = await ai_generator.generate_fingerprint_ai("Windows", "Chrome", 139)

        self.assertEqual(result, {"source": "standard"})
        priorities = [call.kwargs["priority"] for call in direct.await_args_list]
        self.assertEqual(priorities, [True, False])
        racing.assert_awaited_once()

    async def test_direct_priority_wave_is_bounded_and_rotating(self):
        requested_tiers = []
        accounts = [
            {"account_id": f"p{index}", "token": f"token-{index}", "priority": True}
            for index in range(10)
        ]

        def get_candidates(priority, max_accounts, rotate_by):
            requested_tiers.append((priority, max_accounts, rotate_by))
            return accounts[:max_accounts]

        fake_manager = SimpleNamespace(
            accounts=accounts,
            cooldowns={},
            load_accounts=lambda: None,
            get_account_candidates=get_candidates,
            report_failure=lambda *_args, **_kwargs: None,
        )
        fake_response = SimpleNamespace(status_code=503)
        fake_client = SimpleNamespace(post=AsyncMock(return_value=fake_response))

        with patch.object(ai_generator, "cloudflare_manager", fake_manager), patch.object(
            ai_generator, "_shared_client", fake_client
        ):
            result = await ai_generator._call_direct_cloudflare(
                "Windows", "Chrome", 139, priority=True
            )

        self.assertIsNone(result)
        self.assertEqual(
            requested_tiers,
            [(True, ai_generator.DIRECT_PRIORITY_RACE_SIZE, ai_generator.DIRECT_PRIORITY_RACE_SIZE)],
        )
        self.assertEqual(fake_client.post.await_count, ai_generator.DIRECT_PRIORITY_RACE_SIZE)


if __name__ == "__main__":
    unittest.main()
