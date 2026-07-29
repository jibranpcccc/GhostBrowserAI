import asyncio
import copy
import hashlib
import os
import sys
import tempfile
import types


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRODUCTION_FILES = (
    "cloudflare_accounts.txt",
    "cloudflare_accounts.priority.txt",
    "profiles_data/profiles_meta.json",
    "quarantined_profiles/quarantine_meta.json",
)


def production_hashes():
    result = {}
    for relative_path in PRODUCTION_FILES:
        path = os.path.join(PROJECT_ROOT, relative_path)
        if os.path.exists(path):
            with open(path, "rb") as handle:
                result[relative_path] = hashlib.sha256(handle.read()).hexdigest()
        else:
            result[relative_path] = None
    return result


async def main():
    original_sys_path = list(sys.path)
    original_environ = dict(os.environ)
    original_cf_module = sys.modules.get("backend.cloudflare_manager")
    hashes_before = production_hashes()
    generated_calls = []

    with tempfile.TemporaryDirectory() as temp_root:
        profiles_dir = os.path.join(temp_root, "profiles")
        quarantine_dir = os.path.join(temp_root, "quarantine")
        os.makedirs(profiles_dir)
        os.makedirs(quarantine_dir)

        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = profiles_dir
        sys.path.insert(0, PROJECT_ROOT)

        fake_cf_module = types.ModuleType("backend.cloudflare_manager")
        fake_cf_module.cloudflare_manager = types.SimpleNamespace(
            accounts=[],
            load_accounts=lambda: None,
            get_account=lambda: None,
            report_failure=lambda *_args, **_kwargs: None,
        )
        sys.modules["backend.cloudflare_manager"] = fake_cf_module

        import backend.profile_creator as creator_module
        import backend.profile_manager as manager_module
        from backend.ai_auto_validator import auto_validator
        from backend.config import (
            get_installed_chromium_major_version,
            get_installed_chromium_version,
        )

        test_manager = manager_module.ProfileManager()
        original_creator_manager = creator_module.profile_manager
        original_manager_singleton = manager_module.profile_manager
        original_generate = creator_module.generate_fingerprint_ai
        original_validate = auto_validator.validate_profile
        original_quarantine_dir = creator_module.QUARANTINE_DIR
        original_quarantine_meta = creator_module.QUARANTINE_META

        manager_module.profile_manager = test_manager
        creator_module.profile_manager = test_manager
        creator_module.QUARANTINE_DIR = quarantine_dir
        creator_module.QUARANTINE_META = os.path.join(quarantine_dir, "quarantine_meta.json")

        chromium_major = get_installed_chromium_major_version()
        chromium_full = get_installed_chromium_version()
        valid_fingerprint = {
            "os": "Windows",
            "userAgent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chromium_full} Safari/537.36"
            ),
            "platform": "Win32",
            "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11)",
            "cpu_cores": 16,
            "memory_gb": 32,
            "hardwareConcurrency": 16,
            "deviceMemory": 32,
            "timezone": "America/New_York",
            "locale": "en-US",
            "languages": ["en-US", "en"],
            "screen_resolution": "2560x1440",
            "screen_color_depth": 24,
            "sec_ch_ua": f'"Not A(Brand";v="99", "Chromium";v="{chromium_major}"',
            "sec_ch_ua_platform": '"Windows"',
            "client_hints": {
                "architecture": "x86",
                "bitness": "64",
                "model": "",
                "platformVersion": "10.0.0",
                "uaFullVersion": chromium_full,
            },
            "canvas_noise": True,
            "webgl_noise": True,
            "audio_noise": True,
            "_is_fallback": False,
        }

        async def mock_generate_fingerprint_ai(**kwargs):
            generated_calls.append(kwargs)
            return copy.deepcopy(valid_fingerprint)

        async def mock_validate_fail(_profile, _fingerprint):
            raise RuntimeError("Simulated validation failure mid-lifecycle")

        creator_module.generate_fingerprint_ai = mock_generate_fingerprint_ai
        auto_validator.validate_profile = mock_validate_fail

        try:
            print("\n--- Testing Invalid Inputs ---")
            invalid_cases = (
                ({"name": "   "}, "Validation failed"),
                ({"name": "test/../../hacked"}, "traversal"),
                ({"name": "ValidName", "advanced_ui": {"cpu_cores": 99}}, "CPU cores"),
                ({"name": "ValidName", "advanced_ui": {"timezone": "Invalid/TZ"}}, "Timezone"),
                (
                    {"name": "ValidName", "advanced_ui": {"locale": "invalid-locale-format!!!"}},
                    "locale",
                ),
            )
            for kwargs, expected_message in invalid_cases:
                result = await creator_module.profile_creator.create_zero_leak_profile(**kwargs)
                assert result["status"] == "error"
                assert expected_message in result["message"]
            assert not generated_calls, "Invalid inputs reached the AI generation boundary"
            print("[PASS] Invalid inputs failed before AI generation")

            print("\n--- Testing Transactional Creation Failure Cleanup ---")
            result = await creator_module.profile_creator.create_zero_leak_profile(
                name="Transactional-Test-Profile"
            )
            assert result["status"] == "error"
            assert result["code"] == "CREATE_FAILED"
            assert result["message"] == "Profile creation failed safely."
            assert len(generated_calls) == 1, "Mock generator call count was not exactly one"

            lingering_temp = [
                name for name in os.listdir(profiles_dir) if name.startswith("temp_")
            ]
            lingering_records = [
                profile
                for profile in test_manager.list_profiles()
                if "Transactional-Test" in profile["name"]
            ]
            assert not lingering_temp, f"Lingering temporary directories: {lingering_temp}"
            assert not lingering_records, f"Lingering metadata records: {lingering_records}"
            assert not os.listdir(quarantine_dir), "Failure polluted the isolated quarantine"
            print("[PASS] Transaction rollback removed temporary files and metadata")
        finally:
            creator_module.generate_fingerprint_ai = original_generate
            auto_validator.validate_profile = original_validate
            creator_module.profile_manager = original_creator_manager
            manager_module.profile_manager = original_manager_singleton
            creator_module.QUARANTINE_DIR = original_quarantine_dir
            creator_module.QUARANTINE_META = original_quarantine_meta
            await creator_module.generate_fingerprint_ai.__globals__["_shared_client"].aclose()

            if original_cf_module is None:
                sys.modules.pop("backend.cloudflare_manager", None)
            else:
                sys.modules["backend.cloudflare_manager"] = original_cf_module

            sys.path[:] = original_sys_path
            os.environ.clear()
            os.environ.update(original_environ)

        assert creator_module.generate_fingerprint_ai is original_generate
        assert auto_validator.validate_profile is original_validate
        assert creator_module.profile_manager is original_creator_manager
        assert manager_module.profile_manager is original_manager_singleton
        assert sys.path == original_sys_path
        assert dict(os.environ) == original_environ

    assert production_hashes() == hashes_before, "Production files changed during offline test"
    print("[PASS] Production account, profile, and quarantine files unchanged")
    print("[PASS] Environment, sys.path, and monkeypatches restored")
    print("\nALL CREATION/TRANSACTION TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"\nTEST FAILURE: {exc}")
        raise SystemExit(1)
