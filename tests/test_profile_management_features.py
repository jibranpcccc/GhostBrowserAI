from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

import backend.main as main_module
from backend.main import EditProfileModel, ProfileProxyUpdateRequest
from backend.profile_manager import ProfileManager
from backend.proxy_manager import proxy_manager


class ProfileDisplayMetadataTests(unittest.TestCase):
    def test_colors_are_unique_and_pin_order_persists(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "backend.config.get_installed_chromium_version", return_value="131.0.0.0"
        ):
            manager = ProfileManager(override_dir=directory)
            profiles = [manager.create_profile(f"Profile {index}") for index in range(32)]

            colors = [profile["color"] for profile in profiles]
            self.assertEqual(len(colors), len(set(colors)))
            self.assertTrue(all(re.fullmatch(r"#[0-9A-F]{6}", color) for color in colors))
            self.assertTrue(all(profile["pinned"] is False for profile in profiles))

            selected_id = profiles[17]["id"]
            self.assertTrue(manager.update_profile(selected_id, {"pinned": True}))
            self.assertEqual(manager.list_profiles()[0]["id"], selected_id)

            reloaded = ProfileManager(override_dir=directory)
            self.assertEqual(reloaded.list_profiles()[0]["id"], selected_id)
            reloaded_colors = [profile["color"] for profile in reloaded.list_profiles()]
            self.assertEqual(len(reloaded_colors), len(set(reloaded_colors)))


class ProfileProxyUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {
            "id": "profile-1",
            "name": "Original",
            "proxy": {"server": "http://old.example:8080"},
            "advanced": {
                "os": "Mac",
                "cpu_cores": 12,
                "memory_gb": 32,
                "screen_resolution": "2560x1440",
                "webrtc_mode": "protected",
            },
            "verification_status": "verified",
        }

    async def test_successful_proxy_test_saves_exact_tested_proxy(self):
        with patch.object(
            main_module.profile_manager, "get_profile", return_value=self.profile
        ), patch.object(
            main_module.profile_manager, "update_profile", return_value=True
        ) as update, patch.object(
            main_module, "is_profile_running", return_value=False
        ), patch.object(
            proxy_manager, "check_proxy_health", new=AsyncMock(return_value=True)
        ) as health:
            result = await main_module.update_profile_proxy(
                "profile-1",
                ProfileProxyUpdateRequest(proxy_string="127.0.0.1:8080:user:pass"),
            )

        tested_proxy = health.await_args.args[0]
        self.assertEqual(tested_proxy["server"], "http://127.0.0.1:8080")
        self.assertEqual(tested_proxy["username"], "user")
        self.assertEqual(health.await_args.kwargs, {"record_failure": False})
        update.assert_called_once_with("profile-1", {"proxy": tested_proxy})
        self.assertEqual(result["status"], "success")

    async def test_failed_proxy_test_never_changes_saved_profile(self):
        with patch.object(
            main_module.profile_manager, "get_profile", return_value=self.profile
        ), patch.object(
            main_module.profile_manager, "update_profile", return_value=True
        ) as update, patch.object(
            main_module, "is_profile_running", return_value=False
        ), patch.object(
            proxy_manager, "check_proxy_health", new=AsyncMock(return_value=False)
        ):
            with self.assertRaises(HTTPException) as raised:
                await main_module.update_profile_proxy(
                    "profile-1",
                    ProfileProxyUpdateRequest(proxy_string="127.0.0.1:9"),
                )

        self.assertEqual(raised.exception.status_code, 502)
        update.assert_not_called()

    async def test_invalid_proxy_never_reports_success(self):
        with patch.object(
            main_module.profile_manager, "get_profile", return_value=self.profile
        ), patch.object(
            main_module.profile_manager, "update_profile", return_value=True
        ) as update, patch.object(main_module, "is_profile_running", return_value=False):
            with self.assertRaises(HTTPException) as raised:
                await main_module.update_profile_proxy(
                    "profile-1", ProfileProxyUpdateRequest(proxy_string="not a proxy")
                )

        self.assertEqual(raised.exception.status_code, 422)
        update.assert_not_called()

    async def test_clear_proxy_restores_direct_mode_without_health_probe(self):
        with patch.object(
            main_module.profile_manager, "get_profile", return_value=self.profile
        ), patch.object(
            main_module.profile_manager, "update_profile", return_value=True
        ) as update, patch.object(
            main_module, "is_profile_running", return_value=False
        ), patch.object(
            proxy_manager, "check_proxy_health", new=AsyncMock()
        ) as health:
            result = await main_module.update_profile_proxy(
                "profile-1", ProfileProxyUpdateRequest(clear_proxy=True)
            )

        update.assert_called_once_with("profile-1", {"proxy": None})
        health.assert_not_awaited()
        self.assertIsNone(result["proxy"])

    async def test_name_only_edit_preserves_advanced_and_verification(self):
        with patch.object(
            main_module.profile_manager, "get_profile", return_value=self.profile
        ), patch.object(
            main_module.profile_manager, "update_profile", return_value=True
        ) as update:
            await main_module.edit_profile(
                "profile-1", EditProfileModel(name="Renamed")
            )

        saved_updates = update.call_args.args[1]
        self.assertEqual(saved_updates, {"name": "Renamed"})
        self.assertNotIn("advanced", saved_updates)
        self.assertNotIn("verification_status", saved_updates)


if __name__ == "__main__":
    unittest.main()
