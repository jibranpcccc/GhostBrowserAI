import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Guard against loading production Cloudflare credentials during test discovery.
# The autouse fixture below clears any stray singleton state for each test.
os.environ["GHOSTBROWSER_ALLOW_PLAINTEXT_CREDENTIALS"] = "0"
os.environ["GHOSTBROWSER_CF_ACCOUNTS_JSON"] = ""
import backend.credential_store as _credential_store
_credential_store.DEFAULT_STORE_PATH = Path(tempfile.mktemp(suffix=".secure.json"))


FAKE_NATIVE_METADATA = {
    "ua": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "uadata": {
        "brands": [
            {"brand": "Chromium", "version": "149"},
            {"brand": "Not)A;Brand", "version": "24"},
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
            {"brand": "Not)A;Brand", "version": "24.0.0.0"},
        ],
    },
}


@pytest.fixture
def temp_profiles_dir():
    """Provide an isolated temporary directory and clean it up after the test."""
    with tempfile.TemporaryDirectory() as td:
        old = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = td
        try:
            yield td
        finally:
            if old is None:
                os.environ.pop("GHOSTBROWSER_TEST_PROFILES_DIR", None)
            else:
                os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = old


@pytest.fixture
def fake_env():
    """Snapshot environment variables and restore them after the test."""
    original = os.environ.copy()
    try:
        yield original
    finally:
        os.environ.clear()
        os.environ.update(original)


@pytest.fixture
def patched_security_extensions():
    """Patch extension validation to return an empty list for faster tests."""
    with patch("backend.security_hardening.validate_extensions", return_value=[]):
        yield


@pytest.fixture
def patched_chromium_version():
    """Patch Chromium version resolution to a stable test value."""
    with patch("backend.config.get_installed_chromium_version", return_value="149.0.0.0"):
        yield


@pytest.fixture(autouse=True)
def _no_cloudflare_accounts(monkeypatch):
    """Prevent Cloudflare manager from loading real accounts during tests."""
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    tmp.write("# test\n")
    tmp.close()
    # Point the default plaintext account file to a safe temp location.
    monkeypatch.setattr("backend.cloudflare_manager.ACCOUNTS_FILE", tmp.name)
    # Keep the secure credential store path away from production data.
    monkeypatch.setattr("backend.credential_store.DEFAULT_STORE_PATH", Path(tmp.name).with_suffix(".secure.json"))
    # Do not load accounts from environment variables during tests.
    monkeypatch.setenv("GHOSTBROWSER_CF_ACCOUNTS_JSON", "")
    monkeypatch.setenv("GHOSTBROWSER_ALLOW_PLAINTEXT_CREDENTIALS", "0")
    # If the module-level singleton was already instantiated, clear it and
    # make sure subsequent reloads stay empty.
    try:
        import backend.cloudflare_manager as _cfm
        _cfm.cloudflare_manager.accounts = []
        _cfm.cloudflare_manager.use_secure_store = False
        _cfm.cloudflare_manager.allow_plaintext = False
        monkeypatch.setattr(_cfm.cloudflare_manager, "load_accounts", lambda *args, **kwargs: None)
    except Exception:
        pass
    yield
    try:
        os.unlink(tmp.name)
    except FileNotFoundError:
        pass



@pytest.fixture
def sanitize_env(monkeypatch):
    """Ensure GHOSTBROWSER_ADMIN_TOKEN is set for tests and restored after."""
    token = f"gb-test-token-{uuid.uuid4().hex}"
    monkeypatch.setenv("GHOSTBROWSER_ADMIN_TOKEN", token)
    yield token


@pytest.fixture(scope="session", autouse=True)
def cleanup_chromium():
    """Kill any leftover Chromium processes after the test session."""
    yield
    script = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_test_processes.py"
    subprocess.run([sys.executable, str(script)], check=False)
