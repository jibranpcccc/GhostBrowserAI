import os
import sys
import tempfile
import asyncio
import re
import pytest

orig_sys_path = list(sys.path)
orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")

bm_module = None

temp_dir = tempfile.TemporaryDirectory()
test_success = False
cleanup_success = True

try:
    os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir.name
    os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

    sys.path.append(os.getcwd())

    import backend.browser_manager as bm

    bm_module = bm

    def parse_offsets(script):
        r = re.search(r"canvas_r_offset.*?(\d+)", script)
        g = re.search(r"canvas_g_offset.*?(\d+)", script)
        b = re.search(r"canvas_b_offset.*?(\d+)", script)
        if r and g and b:
            return (int(r.group(1)), int(g.group(1)), int(b.group(1)))
        return None

    @pytest.mark.asyncio
    async def test_canvas_seed_stability():
        all_passed = True

        p1 = {
            "id": "00000001-0000-0000-0000-000000000000",
            "path": os.path.join(temp_dir.name, "00000001-0000-0000-0000-000000000000"),
            "advanced": {
                "os": "Windows",
                "screen_resolution": "1920x1080",
                "canvas_noise": True
            }
        }

        res1_a = await bm.build_browser_launch_config(p1)
        res1_b = await bm.build_browser_launch_config(p1)

        t1_a = parse_offsets(res1_a["spoofing_script"])
        t1_b = parse_offsets(res1_b["spoofing_script"])

        if t1_a == t1_b:
            print(f"[PASS] Both calls for Profile 1 return exactly the same offset tuple: {t1_a}")
        else:
            print(f"[FAIL] Calls for Profile 1 returned different tuples: {t1_a} vs {t1_b}")
            all_passed = False

        if t1_a == (1, 7, 13):
            print("[PASS] Profile 1 tuple is expected (1, 7, 13)")
        else:
            print(f"[FAIL] Profile 1 tuple was {t1_a}, expected (1, 7, 13)")
            all_passed = False

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

        if t2 == (2, 14, 26):
            print("[PASS] Profile 2 tuple is expected (2, 14, 26)")
        else:
            print(f"[FAIL] Profile 2 tuple was {t2}, expected (2, 14, 26)")
            all_passed = False

        if t2 != t1_a:
            print("[PASS] Profile 2 offset tuple differs from Profile 1.")
        else:
            print("[FAIL] Profile 2 offset tuple matches Profile 1.")
            all_passed = False

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

        if t3 == (21, 22, 23):
            print("[PASS] Profile 3 with explicit overrides returns exact (21, 22, 23).")
        else:
            print(f"[FAIL] Profile 3 tuple was {t3}, expected (21, 22, 23)")
            all_passed = False

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

    async def run_test():
        return await test_canvas_seed_stability()

    test_success = asyncio.run(run_test())
except Exception as e:
    print(f"[FAIL] Test execution threw exception: {e}")
    test_success = False
finally:
    sys.path = list(orig_sys_path)
    if orig_dir is not None:
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = orig_dir
    else:
        os.environ.pop("GHOSTBROWSER_TEST_PROFILES_DIR", None)
    if orig_env is not None:
        os.environ["GHOSTBROWSER_TEST_ENV"] = orig_env
    else:
        os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
    try:
        temp_dir.cleanup()
    except Exception:
        pass

if __name__ == '__main__':
    if test_success and cleanup_success:
        sys.exit(0)
    else:
        sys.exit(1)
