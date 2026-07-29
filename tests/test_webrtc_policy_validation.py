import hashlib
import io
import os
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Paths to production files that must never be mutated by these tests.
_PRODUCT_FILES = [
    "cloudflare_accounts.txt",
    "cloudflare_accounts.priority.txt",
    "profiles_data/profiles_meta.json",
    "quarantined_profiles/quarantine_meta.json",
]


def _get_hashes() -> dict:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    hashes = {}
    for fpath in _PRODUCT_FILES:
        full = os.path.join(root, fpath)
        if os.path.exists(full):
            with open(full, "rb") as f:
                hashes[fpath] = hashlib.sha256(f.read()).hexdigest()
        else:
            hashes[fpath] = None
    return hashes


@pytest.fixture(scope="session", autouse=True)
def _production_file_integrity(request):
    """Snapshot production/account metadata once and verify at session end."""
    baseline = _get_hashes()
    yield baseline
    assert _get_hashes() == baseline, "production metadata/account files were modified"


@pytest.fixture(scope="module")
def backend_modules():
    """Import backend modules with deterministic, isolated environment.

    The fixture runs once per module and restores the global profile manager to
    its production configuration after the module finishes, so other test files
    are not polluted by our temporary directories.
    """
    env = {
        "GHOSTBROWSER_ADMIN_TOKEN": "test-token-123",
        "GHOSTBROWSER_TEST_ENV": "1",
    }
    with patch.dict(os.environ, env, clear=False), tempfile.TemporaryDirectory() as profiles_dir:
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = profiles_dir

        out_stream = io.StringIO()
        err_stream = io.StringIO()
        with redirect_stdout(out_stream), redirect_stderr(err_stream):
            import backend.main as main
            from backend.main import (
                AdvancedSettingsModel,
                CreateProfileModel,
                EditProfileModel,
                UpdateFingerprintRequest,
                edit_profile,
                update_fingerprint,
            )
            import backend.browser_manager as bm
            import backend.proxy_manager as prxm
            import backend.profile_manager as pm
            import backend.profile_creator as pc
            import backend.security_hardening as sh
            import backend.profile_transfer as pt

        captured_import_output = out_stream.getvalue() + err_stream.getvalue()
        assert "Bootstrap token generated" not in captured_import_output
        assert "X-Admin-Token" not in captured_import_output

        # Capture production paths before any test mutates them.
        prod_profiles_dir = pm.PROFILES_DIR
        prod_metadata_file = os.path.join(prod_profiles_dir, "profiles_meta.json")
        prod_key_file = pm.KEY_FILE

        yield SimpleNamespace(
            main=main,
            AdvancedSettingsModel=AdvancedSettingsModel,
            CreateProfileModel=CreateProfileModel,
            EditProfileModel=EditProfileModel,
            UpdateFingerprintRequest=UpdateFingerprintRequest,
            edit_profile=edit_profile,
            update_fingerprint=update_fingerprint,
            bm=bm,
            prxm=prxm,
            pm=pm,
            pc=pc,
            sh=sh,
            pt=pt,
        )

        # Restore global profile manager state so later modules see production paths.
        pm.profile_manager.PROFILES_DIR = prod_profiles_dir
        pm.profile_manager.metadata_file = prod_metadata_file
        pm.profile_manager.key_file = prod_key_file
        try:
            pm.profile_manager._load_metadata()
        except Exception:
            pass


def test_normalizer(backend_modules):
    normalize = backend_modules.bm.normalize_webrtc_mode

    assert normalize(None) == "protected"
    assert normalize("protected") == "protected"
    assert normalize("altered") == "protected"
    assert normalize("  protected  ") == "protected"

    invalid_values = [
        "",
        "   ",
        "real",
        "disabled",
        "unknown_string",
        False,
        0,
        ["protected"],
        {"mode": "protected"},
    ]
    for val in invalid_values:
        with pytest.raises(RuntimeError, match="FAIL-CLOSED"):
            normalize(val)


async def test_fail_closed_order(backend_modules, monkeypatch):
    called_boundaries = set()

    def make_wrapper(name, return_value):
        def wrapper(*args, **kwargs):
            called_boundaries.add(name)
            return return_value

        return wrapper

    monkeypatch.setattr(
        backend_modules.sh, "validate_extensions", make_wrapper("validate_extensions", [])
    )
    monkeypatch.setattr(
        backend_modules.prxm.proxy_manager,
        "get_proxy_for_profile",
        make_wrapper("get_proxy", {}),
    )
    monkeypatch.setattr(
        backend_modules.prxm.proxy_manager,
        "check_proxy_health",
        make_wrapper("check_proxy", {}),
    )
    monkeypatch.setattr(
        backend_modules.bm,
        "probe_native_metadata",
        make_wrapper("probe_metadata", {}),
    )

    for mode in ["real", "disabled", "unknown"]:
        with pytest.raises(RuntimeError, match="FAIL-CLOSED"):
            await backend_modules.bm.build_browser_launch_config(
                {"id": "11111111-2222-3333-4444-555555555555", "advanced": {"webrtc_mode": mode}}
            )
        assert not called_boundaries, f"boundaries called for mode={mode}: {called_boundaries}"
        called_boundaries.clear()


async def test_build_config(
    backend_modules, monkeypatch, patched_chromium_version, patched_security_extensions
):
    proxy_boundary_calls = []
    proxy_health_calls = []

    async def fail_pool_selection(*args, **kwargs):
        proxy_boundary_calls.append((args, kwargs))
        raise RuntimeError("Proxy pool selection called!")

    async def healthy_configured_proxy(proxy):
        proxy_health_calls.append(proxy.get("server"))
        return True

    async def deterministic_native_metadata(force_headless=True):
        return {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "uadata": {
                "brands": [{"brand": "Chromium", "version": "149"}],
                "mobile": False,
                "platform": "Windows",
                "architecture": "x86",
                "bitness": "64",
                "model": "",
                "platformVersion": "19.0.0",
                "uaFullVersion": "149.0.0.0",
                "fullVersionList": [{"brand": "Chromium", "version": "149.0.0.0"}],
            },
        }

    monkeypatch.setattr(
        backend_modules.prxm.proxy_manager, "get_proxy_for_profile", fail_pool_selection
    )
    monkeypatch.setattr(
        backend_modules.prxm.proxy_manager, "check_proxy_health", healthy_configured_proxy
    )
    monkeypatch.setattr(
        backend_modules.bm, "probe_native_metadata", deterministic_native_metadata
    )

    prof = {
        "id": "11111111-2222-3333-4444-555555555555",
        "advanced": {"webrtc_mode": "protected"},
        "proxy_pin": "127.0.0.1:12345:dummy:dummy",
    }
    config = await backend_modules.bm.build_browser_launch_config(prof)
    args = config.get("args", [])
    script = config.get("spoofing_script", "")

    assert args.count("--force-webrtc-ip-handling-policy=disable_non_proxied_udp") == 1
    assert args.count("--disable-quic") == 1
    assert "--no-sandbox" not in args and "--disable-setuid-sandbox" not in args

    assert "RTCPeerConnection" not in script
    assert "MediaStreamTrack" not in script
    assert "onicecandidate" not in script
    assert "mediaDevices" not in script
    assert "getUserMedia" not in script
    assert "ServiceWorkerContainer" not in script
    assert "Service workers are blocked" not in script

    prof_alt = {
        "id": "11111111-2222-3333-4444-555555555555",
        "advanced": {"webrtc_mode": "altered"},
        "proxy_pin": "127.0.0.1:12345:dummy:dummy",
    }
    config_alt = await backend_modules.bm.build_browser_launch_config(prof_alt)
    assert config_alt.get("args") == config.get("args")
    assert config_alt.get("spoofing_script") == config.get("spoofing_script")

    assert not proxy_boundary_calls
    assert len(proxy_health_calls) == 2


def test_profile_input_validation(backend_modules, monkeypatch):
    _validate_inputs = backend_modules.pc._validate_inputs
    monkeypatch.setattr(backend_modules.pm.profile_manager, "list_profiles", lambda: [])

    for mode, expected in [("protected", "protected"), ("altered", "protected")]:
        advanced = {"webrtc_mode": mode}
        _validate_inputs("test", None, advanced)
        assert advanced["webrtc_mode"] == expected

    for invalid in ["real", "disabled", "", 123]:
        with pytest.raises(ValueError):
            _validate_inputs("test", None, {"webrtc_mode": invalid})


def test_api_model(backend_modules):
    AdvancedSettingsModel = backend_modules.AdvancedSettingsModel

    m1 = AdvancedSettingsModel()
    # The model defaults to "altered"; both "protected" and "altered" are allowed.
    assert m1.webrtc_mode == "altered"

    m2 = AdvancedSettingsModel(webrtc_mode="protected")
    assert m2.webrtc_mode == "protected"

    m3 = AdvancedSettingsModel(webrtc_mode="altered")
    assert m3.webrtc_mode == "altered"

    # Note: AdvancedSettingsModel currently accepts any string here; unsafe values
    # are rejected later by normalize_webrtc_mode/_validate_inputs.  This test keeps
    # the original policy surface by validating only the allowed values.


def test_creation_input_normalizer(backend_modules, monkeypatch):
    """_validate_inputs normalizes WebRTC values and rejects unsafe input."""
    _validate_inputs = backend_modules.pc._validate_inputs
    monkeypatch.setattr(backend_modules.pm.profile_manager, "list_profiles", lambda: [])

    caller_advanced = {"webrtc_mode": "altered", "other_key": 456}
    original_webrtc = caller_advanced["webrtc_mode"]
    _validate_inputs("test", None, caller_advanced)
    assert caller_advanced["webrtc_mode"] == "protected"
    assert caller_advanced["other_key"] == 456
    assert original_webrtc == "altered"

    advanced_protected = {"webrtc_mode": "protected"}
    _validate_inputs("test2", None, advanced_protected)
    assert advanced_protected["webrtc_mode"] == "protected"

    # _validate_inputs only normalizes the key when present; the default is filled
    # in by the API model (see test_api_model) before reaching this path.

    for unsafe in ["real", "disabled", ""]:
        with pytest.raises(ValueError):
            _validate_inputs("unsafe", None, {"webrtc_mode": unsafe})


async def test_edit_and_patch_persist_valid_webrtc(backend_modules, monkeypatch):
    """Edit/PATCH endpoints persist valid WebRTC values without mutating callers."""
    sent_to_persistence = []

    def fake_update(profile_id, updates):
        sent_to_persistence.append(updates)
        return True

    monkeypatch.setattr(backend_modules.pm.profile_manager, "update_profile", fake_update)
    monkeypatch.setattr(
        backend_modules.pm.profile_manager, "get_profile", lambda profile_id: {"id": profile_id, "name": "Fake"}
    )

    caller_advanced = {"webrtc_mode": "altered", "other_key": 456}
    req_patch = backend_modules.UpdateFingerprintRequest(advanced=caller_advanced)
    await backend_modules.update_fingerprint("1", req_patch)
    assert sent_to_persistence[-1]["advanced"]["webrtc_mode"] == "altered"
    assert sent_to_persistence[-1]["advanced"]["other_key"] == 456
    assert caller_advanced["webrtc_mode"] == "altered"
    assert req_patch.advanced["webrtc_mode"] == "altered"

    req_patch2 = backend_modules.UpdateFingerprintRequest(advanced={"webrtc_mode": "protected"})
    await backend_modules.update_fingerprint("1", req_patch2)
    assert sent_to_persistence[-1]["advanced"]["webrtc_mode"] == "protected"

    data_put = backend_modules.EditProfileModel(
        name="Test",
        timezone="UTC",
        locale="en",
        advanced=backend_modules.AdvancedSettingsModel(webrtc_mode="altered"),
        proxy=None,
        proxy_string="",
    )
    await backend_modules.edit_profile("1", data_put)
    assert sent_to_persistence[-1]["advanced"]["webrtc_mode"] == "altered"
    assert data_put.advanced.webrtc_mode == "altered"


async def test_json_csv_transfer(backend_modules, monkeypatch):
    pm = backend_modules.pm
    pt = backend_modules.pt

    with tempfile.TemporaryDirectory() as isolated_dir:
        monkeypatch.setattr(pm.profile_manager, "profiles", {})
        monkeypatch.setattr(pm.profile_manager, "PROFILES_DIR", isolated_dir)
        monkeypatch.setattr(
            pm.profile_manager, "metadata_file", os.path.join(isolated_dir, "profiles_meta.json")
        )
        monkeypatch.setattr(pm.profile_manager, "key_file", os.path.join(isolated_dir, ".master.key"))
        monkeypatch.setattr(pm.profile_manager, "_save_metadata", lambda: None)
        monkeypatch.setattr(pt, "PROFILES_DIR", isolated_dir)

        transfer = pt.ProfileTransfer()

        json_source = {
            "id": "json-altered",
            "name": "JSON altered",
            "advanced": {"webrtc_mode": "altered", "other_key": 7},
        }
        transfer._import_single(json_source, False)
        assert pm.profile_manager.profiles["json-altered"]["advanced"]["webrtc_mode"] == "protected"
        assert json_source["advanced"]["webrtc_mode"] == "altered"

        json_count_before_unsafe = len(pm.profile_manager.profiles)
        with pytest.raises(ValueError):
            transfer._import_single(
                {"id": "json-real", "advanced": {"webrtc_mode": "real"}},
                False,
            )
        assert len(pm.profile_manager.profiles) == json_count_before_unsafe

        csv_content = "name,webrtc_mode\nTest1,altered\nTest2,real\nTest3,"
        res = transfer.import_from_csv(csv_content)

        assert res["imported"] == 1
        assert len(res["errors"]) == 2

        imported_webrtc = next(
            (
                profile.get("advanced", {}).get("webrtc_mode")
                for profile in pm.profile_manager.profiles.values()
                if profile.get("name") == "Test1"
            ),
            None,
        )
        assert imported_webrtc == "protected"
        assert any("real" in error for error in res["errors"])
        assert any("empty" in error for error in res["errors"])
