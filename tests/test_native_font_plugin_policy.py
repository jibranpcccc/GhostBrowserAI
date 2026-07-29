import sys
import os
import tempfile
import asyncio
import json
import io
import shutil
import hashlib
from unittest.mock import patch

# 2.1 Keep TemporaryDirectory and environment setup before production imports.
orig_env = os.environ.copy()
orig_sys_path = list(sys.path)

td = tempfile.TemporaryDirectory()
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = td.name
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.ai_generator import sanitize_native_surface_fields
from backend.profile_creator import profile_creator
from backend.config import get_installed_chromium_major_version, get_installed_chromium_version

_INST_MAJ = get_installed_chromium_major_version()
_INST_FULL = get_installed_chromium_version()

async def main():
    loop = asyncio.get_running_loop()
    old_exception_handler = loop.get_exception_handler()
    unhandled_loop_errors = []

    def loop_exception_handler(loop, context):
        unhandled_loop_errors.append(
            context.get("exception") or context.get("message")
        )

    loop.set_exception_handler(loop_exception_handler)

    errors = []
    network_call_count = [0]

    def assert_no_network(*args, **kwargs):
        network_call_count[0] += 1
        raise AssertionError("NETWORK_CALL_ATTEMPTED")

    # 4.4 Direct sanitizer assertions
    test_cases = [
        {"fonts": ["A"], "plugins": ["B"], "other": "1"}, # normal arrays
        {"fonts": [], "plugins": [], "other": "2"}, # empty arrays
        {"fonts": "bad", "plugins": {"bad": "yes"}, "other": "3"}, # string/dict
        {"fonts": None, "plugins": None, "other": "4"} # null
    ]

    for case in test_cases:
        original = case.copy()
        sanitized = sanitize_native_surface_fields(case)
        if "fonts" in sanitized or "plugins" in sanitized:
            errors.append(f"Sanitizer failed to remove keys for case: {case}")
        if sanitized.get("other") != original.get("other"):
            errors.append(f"Sanitizer modified unrelated fields for case: {case}")
        if case != original:
            errors.append("Sanitizer mutated the original input")

    if sanitize_native_surface_fields("string") != "string":
        errors.append("Sanitizer modified non-dict input")

    # 4.5 Schema validation: fonts/plugins are forbidden
    from backend.ai_generator import validate_fingerprint_schema
    schema_fp = {
        "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_INST_FULL} Safari/537.36",
        "platform": "Win32", "os": "Windows", "hardwareConcurrency": 8, "deviceMemory": 16,
        "cpu_cores": 8, "memory_gb": 16, "screen_resolution": "1920x1080", "screen_color_depth": 24,
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "timezone": "America/New_York", "locale": "en-US", "languages": ["en-US", "en"],
        "sec_ch_ua": f'"Not)A;Brand";v="8", "Chromium";v="{_INST_MAJ}", "Google Chrome";v="{_INST_MAJ}"',
        "sec_ch_ua_platform": '"Windows"',
        "client_hints": {"architecture": "x86", "bitness": "64", "model": "", "platformVersion": "10.0.0", "uaFullVersion": _INST_FULL},
        "canvas_noise": True, "webgl_noise": True, "audio_noise": True,
    }
    for forbidden_key in ("fonts", "plugins"):
        bad_fp = schema_fp.copy()
        bad_fp[forbidden_key] = ["MadeUp"]
        schema_result = validate_fingerprint_schema(bad_fp, _INST_MAJ)
        if schema_result["passed"] or not any(forbidden_key in issue for issue in schema_result.get("issues", [])):
            errors.append(f"validate_fingerprint_schema did not reject {forbidden_key}")

    # 4.6 Profile creator input validation: fonts/plugins rejected in advanced_ui
    for forbidden_key in ("fonts", "plugins"):
        res = await profile_creator.create_zero_leak_profile(
            name=f"test_reject_{forbidden_key}",
            proxy=None,
            advanced_ui={forbidden_key: ["MadeUp"]}
        )
        if res.get("status") != "error" or "Validation failed" not in res.get("message", ""):
            errors.append(f"profile_creator did not reject advanced_ui {forbidden_key}: {res}")

    base_fp = {
        "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_INST_FULL} Safari/537.36",
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
        "sec_ch_ua": f'"Not)A;Brand";v="8", "Chromium";v="{_INST_MAJ}", "Google Chrome";v="{_INST_MAJ}"',
        "sec_ch_ua_platform": '"Windows"',
        "client_hints": {
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "platformVersion": "10.0.0",
            "uaFullVersion": _INST_FULL
        },
        "canvas_noise": True,
        "webgl_noise": True,
        "audio_noise": True
    }

    async def mock_generate_fp(*args, **kwargs):
        fp = base_fp.copy()
        fp["fonts"] = ["GhostProfileOneFont", "GhostProfileTwoFont"]
        fp["plugins"] = ["Ghost PDF Profile One", "Ghost PDF Profile Two"]
        fp["secret_marker"] = "DO_NOT_PRINT_NATIVE_POLICY_SECRET"
        fp["_is_fallback"] = False
        return fp

    async def mock_auto_validate(*args, **kwargs):
        return {
            "decision": "ACCEPT",
            "final_score": 100,
            "issues": []
        }

    def assert_no_quarantine(*args, **kwargs):
        errors.append("Quarantine attempted during successful creation!")

    def get_hash(path):
        if not os.path.exists(path): return None
        h = hashlib.sha256()
        with open(path, "rb") as f: h.update(f.read())
        return h.hexdigest()

    cf_accounts_path = os.path.join(os.path.dirname(__file__), "..", "cloudflare_accounts.txt")
    priority_cf_accounts_path = os.path.join(os.path.dirname(__file__), "..", "cloudflare_accounts.priority.txt")
    prod_meta_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "profiles_data", "profiles_meta.json"))
    prod_quar_meta_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "quarantined_profiles", "quarantine_meta.json"))

    orig_cf_hash = get_hash(cf_accounts_path)
    orig_priority_cf_hash = get_hash(priority_cf_accounts_path)
    orig_prod_meta_hash = get_hash(prod_meta_path)
    orig_prod_quar_hash = get_hash(prod_quar_meta_path)

    temp_quarantine_dir = os.path.join(td.name, "quarantine")
    os.makedirs(temp_quarantine_dir, exist_ok=True)
    temp_quar_meta = os.path.join(temp_quarantine_dir, "quarantine_meta.json")

    old_stdout, old_stderr = sys.stdout, sys.stderr
    captured_out = io.StringIO()
    sys.stdout = captured_out
    sys.stderr = captured_out

    created_profile = None
    try:
        with patch('backend.ai_generator._shared_client.post', side_effect=assert_no_network), \
             patch('backend.ai_generator._call_direct_cloudflare', side_effect=assert_no_network), \
             patch('backend.ai_generator._call_via_racing_proxy', side_effect=assert_no_network), \
             patch('httpx.AsyncClient.post', side_effect=assert_no_network), \
             patch('backend.profile_creator.generate_fingerprint_ai', side_effect=mock_generate_fp), \
             patch('backend.ai_auto_validator.auto_validator.validate_profile', side_effect=mock_auto_validate), \
             patch('backend.profile_creator.QUARANTINE_DIR', temp_quarantine_dir), \
             patch('backend.profile_creator.QUARANTINE_META', temp_quar_meta), \
             patch('backend.profile_creator.profile_creator._quarantine_profile', side_effect=assert_no_quarantine), \
             patch('backend.browser_manager.probe_native_metadata') as mock_probe:

            mock_probe.return_value = {
                "ua": base_fp["userAgent"],
                "uadata": {
                    "brands": [{"brand": "Chromium", "version": str(_INST_MAJ)}],
                    "mobile": False,
                    "platform": "Windows",
                    "architecture": "x86",
                    "bitness": "64",
                    "model": "",
                    "platformVersion": "10.0.0",
                    "uaFullVersion": _INST_FULL,
                    "fullVersionList": [{"brand": "Chromium", "version": _INST_FULL}]
                }
            }

            created_profile = await profile_creator.create_zero_leak_profile(
                name="test_native_policy_success",
                proxy=None,
                advanced_ui=None,
                skip_warming=True
            )

        sys.stdout = old_stdout
        sys.stderr = old_stderr

        out_text = captured_out.getvalue()
        if "DO_NOT_PRINT_NATIVE_POLICY_SECRET" in out_text:
            errors.append("Secret marker was printed to stdout/stderr!")

        temp_meta_path = os.path.join(td.name, "profiles_meta.json")

        if not created_profile or created_profile.get("status") != "success":
            errors.append(f"Profile creation failed: {created_profile}")
        else:
            prof = created_profile["profile"]

            p_real = os.path.realpath(prof["path"]).lower()
            td_real = os.path.realpath(td.name).lower()
            if os.path.commonpath([p_real, td_real]) != td_real:
                errors.append(f"Profile not created in temp dir! Path: {prof['path']}")

            adv = prof.get("advanced", {})
            if "fonts" in adv or "plugins" in adv:
                errors.append("Fonts or plugins present in advanced metadata")

            fp_meta = prof.get("fingerprint", {})
            if "fonts" in fp_meta or "plugins" in fp_meta:
                errors.append("Fonts or plugins present in nested fingerprint metadata")

            if not os.path.exists(temp_meta_path):
                errors.append("Temporary profiles_meta.json does not exist")
            else:
                with open(temp_meta_path, "r", encoding="utf-8") as f:
                    temp_meta = json.load(f)
                found = False
                for pid, t in temp_meta.items():
                    if t.get("id") == prof["id"] or pid == prof["id"]:
                        found = True
                        saved_advanced = t.get("advanced", {})
                        saved_fingerprint = t.get("fingerprint", {})

                        if "fonts" in saved_advanced or "plugins" in saved_advanced:
                            errors.append("Fonts/plugins remained in saved advanced metadata")

                        if "fonts" in saved_fingerprint or "plugins" in saved_fingerprint:
                            errors.append("Fonts/plugins remained in saved fingerprint metadata")
                if not found:
                    errors.append("Profile ID not found in temporary metadata")

            # 3.10 Delete the successfully created temporary profile using the temporary profile manager
            from backend.profile_manager import profile_manager
            profile_manager.delete_profile(prof["id"])
            if prof["id"] in profile_manager.profiles:
                errors.append("Failed to delete temp profile from temp manager")
            if os.path.exists(prof["path"]):
                errors.append("Failed to delete temp profile directory")

        # 4. Rollback test
        sys.stdout = captured_out
        sys.stderr = captured_out

        def assert_controlled_failure(*args, **kwargs):
            raise RuntimeError("CONTROLLED_REGISTER_FAILURE")

        fail_profile = None
        try:
            with patch('backend.ai_generator._shared_client.post', side_effect=assert_no_network), \
                 patch('backend.ai_generator._call_direct_cloudflare', side_effect=assert_no_network), \
                 patch('backend.ai_generator._call_via_racing_proxy', side_effect=assert_no_network), \
                 patch('httpx.AsyncClient.post', side_effect=assert_no_network), \
                 patch('backend.profile_creator.generate_fingerprint_ai', side_effect=mock_generate_fp), \
                 patch('backend.ai_auto_validator.auto_validator.validate_profile', side_effect=mock_auto_validate), \
                 patch('backend.profile_creator.QUARANTINE_DIR', temp_quarantine_dir), \
                 patch('backend.profile_creator.QUARANTINE_META', temp_quar_meta), \
                 patch('backend.profile_creator.profile_manager.register_profile', side_effect=assert_controlled_failure), \
                 patch('backend.browser_manager.probe_native_metadata') as mock_probe:

                mock_probe.return_value = {
                    "ua": base_fp["userAgent"],
                    "uadata": {
                        "brands": [{"brand": "Chromium", "version": str(_INST_MAJ)}],
                        "mobile": False,
                        "platform": "Windows",
                        "architecture": "x86",
                        "bitness": "64",
                        "model": "",
                        "platformVersion": "10.0.0",
                        "uaFullVersion": _INST_FULL,
                        "fullVersionList": [{"brand": "Chromium", "version": _INST_FULL}]
                    }
                }

                fail_profile = await profile_creator.create_zero_leak_profile(
                    name="test_native_policy_fail",
                    proxy=None,
                    advanced_ui=None,
                    skip_warming=True
                )
        except Exception as e:
            if "CONTROLLED_REGISTER_FAILURE" in str(e):
                errors.append("The controlled error escaped as an unhandled exception")
            else:
                errors.append(f"Unexpected exception during rollback test: {e}")

        sys.stdout = old_stdout
        sys.stderr = old_stderr

        if not fail_profile or fail_profile.get("status") != "error":
            errors.append(f"Rollback test did not return status error: {fail_profile}")

        contents = os.listdir(td.name)
        has_temp = any(c.startswith("temp_") for c in contents)
        has_tombstone = any(c.startswith("tombstone_") for c in contents)
        if has_temp: errors.append("temp_* directory remained after rollback")
        if has_tombstone: errors.append("tombstone_* remained after rollback")

        # Verify no temp metadata record remains
        with open(temp_meta_path, "r", encoding="utf-8") as f:
            tmeta = json.load(f)
        if any(t.get("name") == "test_native_policy_fail" for t in tmeta.values()):
            errors.append("Temporary metadata record remained after rollback")

        # Verify no production metadata changed
        if get_hash(prod_meta_path) != orig_prod_meta_hash:
            errors.append("Production profiles_meta.json was modified!")

        # Verify no production quarantine record created
        if get_hash(prod_quar_meta_path) != orig_prod_quar_hash:
            errors.append("Production quarantine_meta.json was modified!")

        # Verify account file unchanged
        if get_hash(cf_accounts_path) != orig_cf_hash:
            errors.append("cloudflare_accounts.txt was modified!")
        if get_hash(priority_cf_accounts_path) != orig_priority_cf_hash:
            errors.append("cloudflare_accounts.priority.txt was modified!")

        if network_call_count[0] != 0:
            errors.append(f"Network call count was {network_call_count[0]}, expected 0")

    except Exception as e:
        import traceback
        traceback.print_exc()
        errors.append(f"Unhandled test exception: {e}")
    finally:
        loop.set_exception_handler(old_exception_handler)
        if unhandled_loop_errors:
            errors.append(
                f"Unhandled asyncio exceptions: {unhandled_loop_errors}"
            )

        sys.stdout = old_stdout
        sys.stderr = old_stderr

        sys.path[:] = orig_sys_path
        if sys.path != orig_sys_path:
            errors.append("sys.path was not restored exactly")

        os.environ.clear()
        os.environ.update(orig_env)
        if dict(os.environ) != orig_env:
            errors.append("os.environ was not restored exactly")

        try:
            td.cleanup()
        except Exception as e:
            errors.append(f"Temporary directory cleanup failed: {e}")

    if errors:
        print("FAILURES:")
        for err in errors:
            print(f"- {err}")
        sys.exit(1)

    print("[PASS] Network call count is zero")
    print("[PASS] Temporary profile creation and persistence sanitization")
    print("[PASS] Controlled register failure rollback")
    print("[PASS] Production metadata unchanged")
    print("[PASS] Production quarantine unchanged")
    print("[PASS] Account file unchanged")
    print("[PASS] sys.path restored")
    print("[PASS] os.environ restored")
    print("[PASS] No unhandled asyncio exceptions")
    print("[PASS] TemporaryDirectory cleanup")
    print("PASS: Native Font/Plugin Policy verified.")

if __name__ == "__main__":
    asyncio.run(main())
