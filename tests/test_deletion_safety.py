import sys
import os
import asyncio
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.getcwd())

from backend.profile_manager import ProfileManager


@pytest.fixture
def temporary_profile_manager(tmp_path):
    """Create an isolated on-disk profile that cannot affect user data."""
    manager = ProfileManager(override_dir=str(tmp_path / "profiles"))
    profile_id = "running-profile"
    profile_path = os.path.join(manager.PROFILES_DIR, profile_id)
    os.makedirs(profile_path)
    manager.profiles[profile_id] = {
        "id": profile_id,
        "name": "Running-Test",
        "path": profile_path,
    }
    return manager, profile_id

async def test_deletion_traversal(temporary_profile_manager):
    print("\n--- Testing Deletion Traversal Blocking ---")

    profile_manager, _ = temporary_profile_manager
    pid = "traversal-test-id"
    profile_manager.profiles[pid] = {
        "id": pid,
        "name": "Traversal-Test",
        "path": os.path.abspath(os.path.join(profile_manager.PROFILES_DIR, "..", "..", "unsafe_dir"))
    }

    try:
        profile_manager.delete_profile(pid)
        print(" -> FAIL: Allowed traversal path deletion!")
        assert False, "Traversal deletion was not blocked!"
    except ValueError as e:
        assert "Traversal" in str(e) or "outside" in str(e)
        print(" -> PASS: Blocked traversal path deletion successfully.")
    finally:
        profile_manager.profiles.pop(pid, None)

async def test_deletion_root_block(temporary_profile_manager):
    print("\n--- Testing Root Directory Deletion Blocking ---")

    profile_manager, _ = temporary_profile_manager
    pid = "root-test-id"
    profile_manager.profiles[pid] = {
        "id": pid,
        "name": "Root-Test",
        "path": os.path.abspath(profile_manager.PROFILES_DIR)
    }

    try:
        profile_manager.delete_profile(pid)
        print(" -> FAIL: Allowed profiles root deletion!")
        assert False, "Root profiles dir deletion was not blocked!"
    except ValueError as e:
        assert "Root" in str(e)
        print(" -> PASS: Blocked root profiles directory deletion successfully.")
    finally:
        profile_manager.profiles.pop(pid, None)

async def test_delete_running_profile(temporary_profile_manager):
    print("\n--- Testing Deletion of Running Profile ---")

    profile_manager, pid = temporary_profile_manager
    # Deletion safety is determined by the browser-process lookup.  Model a
    # launched browser without starting Chromium or depending on local data.
    with patch(
        "backend.browser_manager.find_profile_processes",
        return_value=[MagicMock(name="running_browser_process")],
    ) as find_processes:
        print(f"Attempting to delete mocked running profile {pid}...")
        with pytest.raises(RuntimeError, match="(?i)running"):
            profile_manager.delete_profile(pid)

    find_processes.assert_called_once_with(os.path.realpath(profile_manager.get_profile(pid)["path"]).lower())
    assert profile_manager.get_profile(pid) is not None
    print(" -> PASS: Blocked deletion of a mocked running profile.")

async def main():
    try:
        await test_deletion_traversal()
        await test_deletion_root_block()
        await test_delete_running_profile()
        print("\nALL DELETION SAFETY TESTS PASSED SUCCESSFULLY!")
    except AssertionError as e:
        print(f"\nTEST FAILURE: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUNEXPECTED EXCEPTION: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
