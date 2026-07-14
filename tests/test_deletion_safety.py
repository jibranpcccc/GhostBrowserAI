import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.profile_manager import profile_manager


def test_delete_nonexistent():
    pid = "nonexistent-fake-id-12345"
    result = profile_manager.delete_profile(pid)
    assert result is False, "Nonexistent profile deletion should return False"


def test_delete_existing():
    created = profile_manager.create_profile("Deletion-Test-Profile")
    pid = created["id"]
    profiles = profile_manager.list_profiles()
    assert any(p["id"] == pid for p in profiles), "Profile was not created"
    result = profile_manager.delete_profile(pid)
    assert result is True, "Existing profile deletion should return True"
    profiles_after = profile_manager.list_profiles()
    assert not any(p["id"] == pid for p in profiles_after), "Profile should be removed"


def test_delete_already_deleted():
    created = profile_manager.create_profile("Double-Delete-Test")
    pid = created["id"]
    profile_manager.delete_profile(pid)
    result = profile_manager.delete_profile(pid)
    assert result is False, "Double deletion should return False"


if __name__ == "__main__":
    test_delete_nonexistent()
    test_delete_existing()
    test_delete_already_deleted()
    print("ALL DELETION SAFETY TESTS PASSED!")
