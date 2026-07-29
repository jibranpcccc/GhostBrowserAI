from backend.profile_isolation_audit import audit_profile_isolation
from backend.profile_manager import ProfileManager


def test_audit_passes_for_dedicated_profile_directories(monkeypatch, temp_profiles_dir, patched_chromium_version):
    manager = ProfileManager(override_dir=temp_profiles_dir)
    first = manager.create_profile("First", advanced={"webrtc_mode": "protected"})
    second = manager.create_profile("Second", advanced={"webrtc_mode": "disabled"})
    monkeypatch.setattr("backend.profile_isolation_audit.profile_manager", manager)

    result = audit_profile_isolation([first["id"], second["id"]])

    assert result["passed"] is True
    assert all(result["checks"].values())
    assert result["findings"] == []
    assert result["profile_count"] == 2


def test_audit_reports_shared_profile_directory(monkeypatch, temp_profiles_dir, patched_chromium_version):
    manager = ProfileManager(override_dir=temp_profiles_dir)
    first = manager.create_profile("First")
    second = manager.create_profile("Second")
    second["path"] = first["path"]
    monkeypatch.setattr("backend.profile_isolation_audit.profile_manager", manager)

    result = audit_profile_isolation([first["id"], second["id"]])

    assert result["passed"] is False
    assert result["checks"]["dedicated_data_directories"] is False
    assert any(item.startswith("shared_or_nested_data_directory") for item in result["findings"])


def test_audit_reports_missing_profiles(monkeypatch, temp_profiles_dir):
    manager = ProfileManager(override_dir=temp_profiles_dir)
    monkeypatch.setattr("backend.profile_isolation_audit.profile_manager", manager)

    result = audit_profile_isolation(["unknown-profile"])

    assert result["profile_count"] == 1
    assert result["passed"] is False
    assert result["findings"] == ["missing_profile:unknown-profile"]
