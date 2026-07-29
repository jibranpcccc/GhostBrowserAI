from backend.profile_manager import ProfileManager


def test_high_privacy_mode_applies_persisted_constraints(temp_profiles_dir, patched_chromium_version):
    manager = ProfileManager(override_dir=temp_profiles_dir)

    profile = manager.create_profile("High privacy", advanced={"privacy_mode": "high"})
    advanced = profile["advanced"]

    assert advanced["privacy_mode"] == "high"
    assert advanced["webrtc_mode"] == "disabled"
    assert advanced["block_service_workers"] is True
    assert advanced["canvas_noise"] is True
    assert advanced["audio_noise"] is True
    assert isinstance(advanced["canvas_noise_seed"], int)
    assert isinstance(advanced["audio_noise_seed"], int)
    assert advanced["canvas_noise_seed"] != advanced["audio_noise_seed"]
    assert advanced["webgl_vendor"]
    assert advanced["webgl_renderer"]


def test_high_privacy_mode_is_preserved_after_metadata_reload(temp_profiles_dir, patched_chromium_version):
    manager = ProfileManager(override_dir=temp_profiles_dir)
    created = manager.create_profile("Persistent high privacy", advanced={"privacy_mode": "high"})

    reloaded = ProfileManager(override_dir=temp_profiles_dir).get_profile(created["id"])

    assert reloaded["advanced"]["privacy_mode"] == "high"
    assert reloaded["advanced"]["webrtc_mode"] == "disabled"
    assert reloaded["advanced"]["block_service_workers"] is True


def test_enabling_high_privacy_on_an_existing_profile_applies_constraints(temp_profiles_dir, patched_chromium_version):
    manager = ProfileManager(override_dir=temp_profiles_dir)
    profile = manager.create_profile("Upgradeable")

    assert manager.update_profile(profile["id"], {"advanced": {"privacy_mode": "high"}})
    advanced = manager.get_profile(profile["id"])["advanced"]

    assert advanced["webrtc_mode"] == "disabled"
    assert advanced["block_service_workers"] is True
    assert advanced["canvas_noise_seed"]
