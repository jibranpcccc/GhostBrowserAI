import asyncio
import copy
import os
import sys
import tempfile
from types import SimpleNamespace

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend import ai_generator
from backend import bulk_operations
from backend import profile_transfer as transfer_module


MAJOR = 139


def valid_fingerprint():
    return {
        "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{MAJOR}.0.0.0 Safari/537.36",
        "platform": "Win32",
        "os": "Windows",
        "hardwareConcurrency": 8,
        "deviceMemory": 16,
        "cpu_cores": 8,
        "memory_gb": 16,
        "screen_resolution": "1920x1080",
        "screen_color_depth": 24,
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "timezone": "America/New_York",
        "locale": "en-US",
        "languages": ["en-US", "en"],
        "sec_ch_ua": f'"Not)A;Brand";v="8", "Chromium";v="{MAJOR}"',
        "sec_ch_ua_platform": '"Windows"',
        "client_hints": {
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "platformVersion": "10.0.0",
            "uaFullVersion": f"{MAJOR}.0.0.0",
        },
        "canvas_noise": True,
        "webgl_noise": True,
        "audio_noise": True,
    }


def check_schema_and_attribution():
    fp = valid_fingerprint()
    result = ai_generator.validate_fingerprint_schema(fp, MAJOR)
    assert result["passed"], result

    malformed = copy.deepcopy(fp)
    malformed["cpu_cores"] = True
    malformed["client_hints"].pop("bitness")
    result = ai_generator.validate_fingerprint_schema(malformed, MAJOR)
    assert not result["passed"]
    assert any("cpu_cores" in issue for issue in result["issues"])
    assert any("bitness" in issue for issue in result["issues"])

    wrong_model = ai_generator._finalize_ai_result(
        copy.deepcopy(fp), {"model": "not-kimi"}, "racing_proxy", MAJOR
    )
    assert wrong_model is None

    no_attestation = ai_generator._finalize_ai_result(
        copy.deepcopy(fp), {}, "racing_proxy", MAJOR
    )
    assert no_attestation["_provenance"]["requested_model"] == ai_generator.KIMI_MODEL
    assert no_attestation["_provenance"]["reported_model"] is None
    assert no_attestation["_provenance"]["reported_model_verified"] is False


async def check_direct_rotation():
    calls = []

    class FakeResponse:
        status_code = 503

    class FakeClient:
        async def post(self, url, **kwargs):
            calls.append(url)
            return FakeResponse()

    failures = []
    fake_manager = SimpleNamespace(
        accounts=[
            {"account_id": "a" * 32, "token": "one"},
            {"account_id": "b" * 32, "token": "two"},
            {"account_id": "c" * 32, "token": "three"},
        ],
        cooldowns={},
        load_accounts=lambda: None,
        report_failure=lambda account_id, cooldown_minutes=5: failures.append(account_id),
    )

    original_manager = ai_generator.cloudflare_manager
    original_client = ai_generator._shared_client
    original_sleep = ai_generator.asyncio.sleep

    async def no_sleep(_seconds):
        return None

    try:
        ai_generator.cloudflare_manager = fake_manager
        ai_generator._shared_client = FakeClient()
        ai_generator.asyncio.sleep = no_sleep
        result = await ai_generator._call_direct_cloudflare("Windows", "Chrome", MAJOR)
        assert result is None
        assert len(calls) == 3
        assert len(set(calls)) == 3
        assert len(failures) == 3
    finally:
        ai_generator.cloudflare_manager = original_manager
        ai_generator._shared_client = original_client
        ai_generator.asyncio.sleep = original_sleep


async def check_bulk_uses_strict_orchestrator():
    created = []

    class FakeCreator:
        async def create_zero_leak_profile(self, **kwargs):
            created.append(kwargs)
            return {"status": "success", "profile": {"id": f"profile-{len(created)}"}}

    original_creator = bulk_operations.profile_creator
    try:
        bulk_operations.profile_creator = FakeCreator()
        request = bulk_operations.BulkCreateRequest(
            base_name="Strict",
            count=3,
            timezone="America/New_York",
            locale="en-US",
            advanced={"os": "Windows"},
        )
        result = await bulk_operations.bulk_create_profiles(request)
        assert result["status"] == "success"
        assert result["succeeded"] == 3
        assert len(created) == 3
        assert all(item["advanced_ui"]["timezone"] == "America/New_York" for item in created)
    finally:
        bulk_operations.profile_creator = original_creator


def check_import_is_unverified():
    class FakeManager:
        def __init__(self):
            self.profiles = {}
            self.saved = 0

        def _save_metadata(self):
            self.saved += 1

    fake_manager = FakeManager()
    original_manager = transfer_module.profile_manager
    original_profiles_dir = transfer_module.PROFILES_DIR

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            transfer_module.profile_manager = fake_manager
            transfer_module.PROFILES_DIR = temp_dir
            transfer = transfer_module.ProfileTransfer()
            payload = valid_fingerprint()
            payload["_source"] = "racing_proxy"
            payload["_provenance"] = {"verified": True}
            assert transfer._import_single({"name": "Imported", "fingerprint": payload}, False)
            profile = next(iter(fake_manager.profiles.values()))
            assert profile["verification_status"] == "unverified"
            assert profile["ai_provenance"]["verified"] is False
            assert "_source" not in profile["fingerprint"]
            assert "_provenance" not in profile["fingerprint"]
            assert profile["fingerprint"]["_is_fallback"] is True
            assert fake_manager.saved == 1
        finally:
            transfer_module.profile_manager = original_manager
            transfer_module.PROFILES_DIR = original_profiles_dir


async def main():
    check_schema_and_attribution()
    await check_direct_rotation()
    await check_bulk_uses_strict_orchestrator()
    check_import_is_unverified()
    print("[PASS] strict fingerprint schema and honest model attribution")
    print("[PASS] direct account rotation exhausts each eligible account once")
    print("[PASS] bulk creation uses strict Kimi orchestrator")
    print("[PASS] imported profiles cannot retain trusted AI provenance")


if __name__ == "__main__":
    asyncio.run(main())
