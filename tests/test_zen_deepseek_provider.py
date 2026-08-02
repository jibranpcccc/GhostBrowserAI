"""OpenCode Zen (DeepSeek) fingerprint-provider tests.

The zen provider is the PRIMARY attempt in the fingerprint cascade and uses
the free DeepSeek model by default (deepseek-v4-flash-free). These tests pin
the key discovery, the request shape, provenance tagging, and the cascade
ordering without making live network calls.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend import ai_generator


def _valid_ai_response(extra: dict | None = None) -> dict:
    fingerprint = {
        "os": "Windows",
        "platform": "Win32",
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11)",
        "cpu_cores": 16,
        "hardwareConcurrency": 16,
        "memory_gb": 32,
        "deviceMemory": 32,
        "timezone": "America/New_York",
        "locale": "en-US",
        "languages": ["en-US", "en"],
        "screen_resolution": "2560x1440",
        "screen_color_depth": 24,
        "canvas_noise": True,
        "webgl_noise": True,
        "audio_noise": True,
        "sec_ch_ua": '"Not A(Brand";v="99", "Chromium";v="139"',
        "sec_ch_ua_platform": '"Windows"',
        "client_hints": {
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "platformVersion": "10.0.0",
            "uaFullVersion": "139.0.0.0",
        },
        "notification_permission": "default",
        "battery_level": 0.98,
        "battery_charging": False,
        "battery_discharging": 46800,
        "behavior": {},
    }
    fingerprint.update(extra or {})
    return fingerprint


class ZenKeyDiscoveryTests(unittest.TestCase):
    def tearDown(self):
        for key in list(os.environ):
            if key.startswith("ZEN_API_KEY"):
                os.environ.pop(key, None)

    def test_plain_and_numbered_keys_are_collected(self):
        os.environ["ZEN_API_KEY"] = " key-0 "
        os.environ["ZEN_API_KEY_1"] = "key-1"
        os.environ["ZEN_API_KEY_2"] = "key-2"
        self.assertEqual(ai_generator._get_zen_api_keys(), ["key-0", "key-1", "key-2"])

    def test_duplicates_and_blank_keys_are_dropped(self):
        os.environ["ZEN_API_KEY"] = "same"
        os.environ["ZEN_API_KEY_1"] = "same"
        os.environ["ZEN_API_KEY_2"] = "   "
        self.assertEqual(ai_generator._get_zen_api_keys(), ["same"])

    def test_no_keys_returns_empty(self):
        self.assertEqual(ai_generator._get_zen_api_keys(), [])


class ZenCallTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        for key in list(os.environ):
            if key.startswith("ZEN_API_KEY"):
                os.environ.pop(key, None)
        for key in list(os.environ):
            if key.startswith("ZEN_"):
                os.environ.pop(key, None)

    async def test_malformed_numeric_config_falls_back_to_defaults(self):
        # Regression: invalid/zero numeric settings must not raise — the caller
        # falls through to the next provider instead of failing the whole chain.
        os.environ["ZEN_API_KEY"] = "zen-test-key"
        os.environ["ZEN_RACE_SIZE"] = "bogus"
        os.environ["ZEN_REQUEST_TIMEOUT"] = ""
        os.environ["ZEN_INTER_BATCH_DELAY"] = "NaN"

        self.assertEqual(ai_generator._safe_int_env("ZEN_RACE_SIZE", 3, minimum=1), 3)
        self.assertEqual(
            ai_generator._safe_float_env("ZEN_REQUEST_TIMEOUT", 60.0, minimum=1.0), 60.0
        )
        self.assertEqual(
            ai_generator._safe_float_env("ZEN_INTER_BATCH_DELAY", 1.0, minimum=0.0), 1.0
        )

        # And the real call path still works (uses defaults).
        response_body = {
            "choices": [{"message": {"content": __import__("json").dumps(_valid_ai_response())}}]
        }
        fake_response = Mock()
        fake_response.status_code = 200
        fake_response.json.return_value = response_body
        post = AsyncMock(return_value=fake_response)
        with patch.object(ai_generator, "_shared_client") as client:
            client.post = post
            result = await ai_generator._call_zen_deepseek_api("Windows", "Chrome", 139)
        self.assertIsNotNone(result)
        self.assertEqual(post.await_args.kwargs["timeout"], 60.0)

    async def test_zero_and_negative_values_are_clamped(self):
        self.assertEqual(ai_generator._safe_int_env("ZEN_RACE_SIZE", 3, minimum=1), 3)
        os.environ["ZEN_RACE_SIZE"] = "0"
        self.assertEqual(ai_generator._safe_int_env("ZEN_RACE_SIZE", 3, minimum=1), 1)
        os.environ["ZEN_RACE_SIZE"] = "-5"
        self.assertEqual(ai_generator._safe_int_env("ZEN_RACE_SIZE", 3, minimum=1), 1)

        os.environ["ZEN_REQUEST_TIMEOUT"] = "0.0"
        self.assertEqual(
            ai_generator._safe_float_env("ZEN_REQUEST_TIMEOUT", 60.0, minimum=1.0), 1.0
        )
        os.environ["ZEN_INTER_BATCH_DELAY"] = "-3.0"
        self.assertEqual(
            ai_generator._safe_float_env("ZEN_INTER_BATCH_DELAY", 1.0, minimum=0.0), 0.0
        )

    async def test_valid_values_are_used(self):
        os.environ["ZEN_RACE_SIZE"] = "7"
        os.environ["ZEN_REQUEST_TIMEOUT"] = "12.5"
        self.assertEqual(ai_generator._safe_int_env("ZEN_RACE_SIZE", 3, minimum=1), 7)
        self.assertEqual(
            ai_generator._safe_float_env("ZEN_REQUEST_TIMEOUT", 60.0, minimum=1.0), 12.5
        )

    async def test_no_keys_returns_none_without_network(self):
        result = await ai_generator._call_zen_deepseek_api("Windows", "Chrome", 139)
        self.assertIsNone(result)

    async def test_success_uses_free_default_model_and_zen_provenance(self):
        os.environ["ZEN_API_KEY"] = "zen-test-key"
        response_body = {
            "choices": [{"message": {"content": __import__("json").dumps(_valid_ai_response())}}]
        }
        fake_response = Mock()
        fake_response.status_code = 200
        fake_response.json.return_value = response_body
        post = AsyncMock(return_value=fake_response)

        with patch.object(ai_generator, "_shared_client") as client, \
             patch.object(ai_generator, "sanitize_native_surface_fields", side_effect=lambda v: v):
            client.post = post
            result = await ai_generator._call_zen_deepseek_api("Windows", "Chrome", 139)

        self.assertIsNotNone(result)
        request_json = post.await_args.kwargs["json"]
        self.assertEqual(request_json["model"], ai_generator.ZEN_MODEL)
        self.assertEqual(request_json["model"], "deepseek-v4-flash-free")
        headers = post.await_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer zen-test-key")
        self.assertEqual(post.await_args.args[0], ai_generator.ZEN_API_URL)
        self.assertEqual(result["_provenance"]["source"], "zen_deepseek")
        self.assertEqual(result["_provenance"]["requested_model"], "deepseek-v4-flash-free")

    async def test_zend_model_env_override_wins(self):
        os.environ["ZEN_API_KEY"] = "zen-test-key"
        os.environ["ZEN_MODEL"] = "deepseek-v4-pro"
        response_body = {
            "choices": [{"message": {"content": __import__("json").dumps(_valid_ai_response())}}]
        }
        fake_response = Mock()
        fake_response.status_code = 200
        fake_response.json.return_value = response_body
        post = AsyncMock(return_value=fake_response)

        with patch.object(ai_generator, "_shared_client") as client:
            client.post = post
            result = await ai_generator._call_zen_deepseek_api("Windows", "Chrome", 139)

        self.assertIsNotNone(result)
        self.assertEqual(post.await_args.kwargs["json"]["model"], "deepseek-v4-pro")

    async def test_auth_failure_returns_none(self):
        os.environ["ZEN_API_KEY"] = "zen-test-key"
        fake_response = Mock()
        fake_response.status_code = 401
        post = AsyncMock(return_value=fake_response)

        with patch.object(ai_generator, "_shared_client") as client:
            client.post = post
            result = await ai_generator._call_zen_deepseek_api("Windows", "Chrome", 139)

        self.assertIsNone(result)

    async def test_invalid_json_in_response_returns_none(self):
        os.environ["ZEN_API_KEY"] = "zen-test-key"
        fake_response = Mock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
        post = AsyncMock(return_value=fake_response)

        with patch.object(ai_generator, "_shared_client") as client:
            client.post = post
            result = await ai_generator._call_zen_deepseek_api("Windows", "Chrome", 139)

        self.assertIsNone(result)


class ZenCascadeOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_zen_is_attempted_first_and_wins(self):
        zen_result = {"source": "zen_deepseek"}
        with patch.object(
            ai_generator, "_call_zen_deepseek_api", new=AsyncMock(return_value=zen_result)
        ) as zen, patch.object(
            ai_generator, "_call_mistral_api", new=AsyncMock()
        ) as mistral, patch.object(
            ai_generator, "_call_direct_cloudflare", new=AsyncMock()
        ) as direct, patch.object(
            ai_generator, "_call_via_racing_proxy", new=AsyncMock()
        ) as racing, patch.object(
            ai_generator, "sanitize_native_surface_fields", side_effect=lambda value: value
        ):
            result = await ai_generator.generate_fingerprint_ai("Windows", "Chrome", 139)

        self.assertEqual(result, zen_result)
        zen.assert_awaited_once()
        mistral.assert_not_awaited()
        direct.assert_not_awaited()
        racing.assert_not_awaited()

    async def test_zen_failure_falls_through_to_mistral(self):
        mistral_result = {"source": "mistral_api"}
        with patch.object(
            ai_generator, "_call_zen_deepseek_api", new=AsyncMock(return_value=None)
        ) as zen, patch.object(
            ai_generator, "_call_mistral_api", new=AsyncMock(return_value=mistral_result)
        ) as mistral, patch.object(
            ai_generator, "_call_direct_cloudflare", new=AsyncMock()
        ) as direct, patch.object(
            ai_generator, "_call_via_racing_proxy", new=AsyncMock()
        ) as racing, patch.object(
            ai_generator, "sanitize_native_surface_fields", side_effect=lambda value: value
        ):
            result = await ai_generator.generate_fingerprint_ai("Windows", "Chrome", 139)

        self.assertEqual(result, mistral_result)
        zen.assert_awaited_once()
        mistral.assert_awaited_once()
        direct.assert_not_awaited()
        racing.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
