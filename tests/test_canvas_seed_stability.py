import os
import sys
import tempfile
import asyncio
import re

def main():

    # 1. Initialize variables safely for finally block
    orig_sys_path = list(sys.path)
    orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
    orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")

    orig_probe_native_metadata = None
    orig_get_proxy_for_profile = None

    bm_module = None
    pm_module = None

    temp_dir = tempfile.TemporaryDirectory()
    test_success = False
    cleanup_success = True

    try:
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir.name
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

        sys.path.append(os.getcwd())

        # 2. Import modules
        import backend.browser_manager as bm
        import backend.proxy_manager as pm

        bm_module = bm
        pm_module = pm

        # Store originals
        orig_probe_native_metadata = bm.probe_native_metadata
        orig_get_proxy_for_profile = pm.proxy_manager.get_proxy_for_profile

        # Monkeypatch
        async def mock_probe_native_metadata(force_headless=True):
            return {
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                "uadata": {
                    "brands": [
                        {"brand": "Chromium", "version": "149"},
                        {"brand": "Not)A;Brand", "version": "24"}
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
                        {"brand": "Not)A;Brand", "version": "24.0.0.0"}
                    ]
                }
            }

        bm.probe_native_metadata = mock_probe_native_metadata

        async def mock_get_proxy_for_profile(profile_id):
            return None

        pm.proxy_manager.get_proxy_for_profile = mock_get_proxy_for_profile

        async def run_test():
            all_passed = True

            # Helper to parse offsets
            def parse_offsets(script):
                r = re.search(r"const r_offset\s*=\s*(\d+);", script)
                g = re.search(r"const g_offset\s*=\s*(\d+);", script)
                b = re.search(r"const b_offset\s*=\s*(\d+);", script)
                if r and g and b:
                    return (int(r.group(1)), int(g.group(1)), int(b.group(1)))
                return None

            # Profile 1
            p1 = {
                "id": "00000001-0000-0000-0000-000000000000",
                "path": os.path.join(temp_dir.name, "00000001-0000-0000-0000-000000000000"),
                "advanced": {
                    "os": "Windows",
                    "screen_resolution": "1920x1080",
                    "canvas_noise": True
                }
            }

            # Call build_browser_launch_config twice for profile 1
            res1_a = await bm.build_browser_launch_config(p1)
            res1_b = await bm.build_browser_launch_config(p1)

            t1_a = parse_offsets(res1_a["spoofing_script"])
            t1_b = parse_offsets(res1_b["spoofing_script"])

            # Assertion 1: both calls return the same offset tuple
            if t1_a == t1_b:
                print(f"[PASS] Both calls for Profile 1 return exactly the same offset tuple: {t1_a}")
            else:
                print(f"[FAIL] Calls for Profile 1 returned different tuples: {t1_a} vs {t1_b}")
                all_passed = False

            # Assertion 2: the expected tuple is (1, 7, 13)
            if t1_a == (1, 7, 13):
                print("[PASS] Profile 1 tuple is expected (1, 7, 13)")
            else:
                print(f"[FAIL] Profile 1 tuple was {t1_a}, expected (1, 7, 13)")
                all_passed = False

            # Profile 2
            p2 = {
                "id": "00000002-0000-0000-0000-000000000000",
                "path": os.path.join(temp_dir.name, "00000002-0000-0000-0000-000000000000"),
                "advanced": {
                    "os": "Windows",
                    "screen_resolution": "1920x1080",
                    "canvas_noise": True
                }
            }

            res2 = await bm.build_browser_launch_config(p2)
            t2 = parse_offsets(res2["spoofing_script"])

            # Assertion 3: Profile 2 tuple is (2, 14, 26)
            if t2 == (2, 14, 26):
                print("[PASS] Profile 2 tuple is expected (2, 14, 26)")
            else:
                print(f"[FAIL] Profile 2 tuple was {t2}, expected (2, 14, 26)")
                all_passed = False

            # Assertion 4: Profile 2 differs from Profile 1
            if t2 != t1_a:
                print("[PASS] Profile 2 offset tuple differs from Profile 1.")
            else:
                print("[FAIL] Profile 2 offset tuple matches Profile 1.")
                all_passed = False

            # Profile 3 with explicit overrides
            p3 = {
                "id": "00000003-0000-0000-0000-000000000000",
                "path": os.path.join(temp_dir.name, "00000003-0000-0000-0000-000000000000"),
                "advanced": {
                    "os": "Windows",
                    "screen_resolution": "1920x1080",
                    "canvas_noise": True,
                    "canvas_r_offset": 21,
                    "canvas_g_offset": 22,
                    "canvas_b_offset": 23
                }
            }

            res3 = await bm.build_browser_launch_config(p3)
            t3 = parse_offsets(res3["spoofing_script"])

            # Assertion 5: Profile 3 script contains exactly (21, 22, 23)
            if t3 == (21, 22, 23):
                print("[PASS] Profile 3 with explicit overrides returns exact (21, 22, 23).")
            else:
                print(f"[FAIL] Profile 3 tuple was {t3}, expected (21, 22, 23)")
                all_passed = False

            # Check browser_manager.py content
            with open("backend/browser_manager.py", "r", encoding="utf-8") as f:
                bm_content = f.read()

            has_jitter = "_session_jitter" in bm_content
            has_random = "import random as _random" in bm_content

            if not has_jitter:
                print("[PASS] browser_manager.py does not contain '_session_jitter'")
            else:
                print("[FAIL] browser_manager.py contains '_session_jitter'")
                all_passed = False

            if not has_random:
                print("[PASS] browser_manager.py does not contain 'import random as _random'")
            else:
                print("[FAIL] browser_manager.py contains 'import random as _random'")
                all_passed = False

            return all_passed

        test_success = asyncio.run(run_test())
    except Exception as e:
        print(f"[FAIL] Test execution threw exception: {e}")
        test_success = False

    finally:
        # Restore monkeypatches
        if bm_module is not None and orig_probe_native_metadata is not None:
            bm_module.probe_native_metadata = orig_probe_native_metadata
            if bm_module.probe_native_metadata is orig_probe_native_metadata:
                print("[PASS] probe_native_metadata restored.")
            else:
                print("[FAIL] probe_native_metadata not restored correctly.")
                cleanup_success = False
        else:
            print("[FAIL] probe_native_metadata restoration failed (module or original function unavailable).")
            cleanup_success = False

        if pm_module is not None and orig_get_proxy_for_profile is not None:
            pm_module.proxy_manager.get_proxy_for_profile = orig_get_proxy_for_profile
            if pm_module.proxy_manager.get_proxy_for_profile is orig_get_proxy_for_profile:
                print("[PASS] get_proxy_for_profile restored.")
            else:
                print("[FAIL] get_proxy_for_profile not restored correctly.")
                cleanup_success = False
        else:
            print("[FAIL] get_proxy_for_profile restoration failed (module or original function unavailable).")
            cleanup_success = False

        # Restore sys.path
        sys.path = list(orig_sys_path)
        if sys.path == orig_sys_path:
            print("[PASS] sys.path restored.")
        else:
            print("[FAIL] sys.path not restored correctly.")
            cleanup_success = False

        # Restore environment variables
        if orig_dir is not None:
            os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = orig_dir
        else:
            os.environ.pop("GHOSTBROWSER_TEST_PROFILES_DIR", None)

        if orig_env is not None:
            os.environ["GHOSTBROWSER_TEST_ENV"] = orig_env
        else:
            os.environ.pop("GHOSTBROWSER_TEST_ENV", None)

        if os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR") == orig_dir and os.environ.get("GHOSTBROWSER_TEST_ENV") == orig_env:
            print("[PASS] environment variables restored.")
        else:
            print("[FAIL] environment variables not restored correctly.")
            cleanup_success = False

        # Clean up TemporaryDirectory
        try:
            temp_dir.cleanup()
        except Exception:
            pass

        if test_success and cleanup_success:
            return True

        else:
            return False


if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
