"""Import-time test-environment isolation shared by every test runner.

``tests/test_00_isolation.py`` imports it before ``unittest discover`` loads any
other test module (discovery imports modules in sorted order), and
``tests/conftest.py`` imports it before pytest collects anything. Without this,
test discovery imports ``backend.cloudflare_manager`` whose module singleton
loads real Cloudflare credentials from the DPAPI secure store and can trigger
live network calls. The guard redirects the credential store to a temp path,
clears the singleton, and disables plaintext/environment account loading.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def _secure_temp_path(suffix: str) -> Path:
    """Create a unique temp path without ``tempfile.mktemp``'s TOCTOU race."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    os.remove(path)
    return Path(path)


def apply() -> None:
    """Sanitize the environment before any production module is imported."""
    os.environ["GHOSTBROWSER_ALLOW_PLAINTEXT_CREDENTIALS"] = "0"
    os.environ["GHOSTBROWSER_CF_ACCOUNTS_JSON"] = ""
    os.environ.setdefault("GHOSTBROWSER_TEST_ENV", "1")
    os.environ["GHOSTBROWSER_ENABLE_COOKIE_WARMER"] = "0"

    if str(WORKSPACE_ROOT) not in sys.path:
        sys.path.insert(0, str(WORKSPACE_ROOT))

    import backend.credential_store as _credential_store
    _credential_store.DEFAULT_STORE_PATH = _secure_temp_path(".secure.json")

    try:
        import backend.cloudflare_manager as _cfm
        _cfm.ACCOUNTS_FILE = str(_secure_temp_path(".txt"))
        manager = _cfm.cloudflare_manager
        manager.accounts = []
        manager.use_secure_store = False
        manager.allow_plaintext = False
        manager.load_accounts = lambda *args, **kwargs: None
    except Exception:
        pass


apply()
