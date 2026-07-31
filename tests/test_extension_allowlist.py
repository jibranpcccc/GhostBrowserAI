"""A16: extension validation fail-closed contract.

``security_hardening.validate_extensions`` must refuse extensions that request
dangerous permissions unless the extension name is on the explicit
GHOSTBROWSER_EXTENSION_ALLOWLIST, and must never crash on malformed manifests.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend import security_hardening as sh


class ValidateExtensionsTests(unittest.TestCase):
    def _make_extension(self, root: Path, name: str, permissions, host_permissions=None):
        ext_dir = root / name
        ext_dir.mkdir()
        manifest = {"manifest_version": 3, "name": name, "version": "1.0"}
        if permissions is not None:
            manifest["permissions"] = permissions
        if host_permissions is not None:
            manifest["host_permissions"] = host_permissions
        (ext_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return ext_dir

    def test_dangerous_extension_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext = self._make_extension(root, "spy_ext", ["webRequest"], ["<all_urls>"])
            with mock.patch.object(sh, "_ALLOWLISTED_EXTENSIONS", set()):
                self.assertEqual(sh.validate_extensions(str(root)), [])

    def test_dangerous_extension_allowed_when_allowlisted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext = self._make_extension(root, "trusted_ext", ["debugger"])
            with mock.patch.object(sh, "_ALLOWLISTED_EXTENSIONS", {"trusted_ext"}):
                self.assertEqual(sh.validate_extensions(str(root)), [str(ext)])

    def test_benign_extension_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext = self._make_extension(root, "harmless", [])
            with mock.patch.object(sh, "_ALLOWLISTED_EXTENSIONS", set()):
                self.assertEqual(sh.validate_extensions(str(root)), [str(ext)])

    def test_invalid_manifest_skipped_without_crash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "broken").mkdir()
            (root / "broken" / "manifest.json").write_text("{not json", encoding="utf-8")
            with mock.patch.object(sh, "_ALLOWLISTED_EXTENSIONS", set()):
                self.assertEqual(sh.validate_extensions(str(root)), [])

    def test_missing_manifest_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "no_manifest").mkdir()
            with mock.patch.object(sh, "_ALLOWLISTED_EXTENSIONS", set()):
                self.assertEqual(sh.validate_extensions(str(root)), [])

    def test_empty_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(sh.validate_extensions(td), [])

    def test_dummy_extension_skipped_outside_test_env(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_extension(root, "dummy_extension", [])
            with mock.patch.object(sh, "_ALLOWLISTED_EXTENSIONS", set()), \
                 mock.patch.dict(os.environ, {"GHOSTBROWSER_TEST_ENV": "0"}):
                self.assertEqual(sh.validate_extensions(str(root)), [])
            with mock.patch.object(sh, "_ALLOWLISTED_EXTENSIONS", set()), \
                 mock.patch.dict(os.environ, {"GHOSTBROWSER_TEST_ENV": "1"}):
                self.assertEqual(sh.validate_extensions(str(root)), [str(root / "dummy_extension")])


class CheckExtensionsForPrivacyTests(unittest.TestCase):
    def test_broad_permissions_produce_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext_dir = self._make_extension(root, "broad_ext", ["cookies"], ["*://*/*"])
            warnings = sh.check_extensions_for_privacy([str(ext_dir)])
            self.assertEqual(len(warnings), 1)
            self.assertEqual(warnings[0]["extension"], "broad_ext")
            self.assertIn("cookies", warnings[0]["permissions"])

    def test_no_broad_permissions_no_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext_dir = self._make_extension(root, "lean_ext", [])
            self.assertEqual(sh.check_extensions_for_privacy([str(ext_dir)]), [])

    def _make_extension(self, root: Path, name: str, permissions, host_permissions=None):
        ext_dir = root / name
        ext_dir.mkdir()
        manifest = {"manifest_version": 3, "name": name, "version": "1.0"}
        if permissions is not None:
            manifest["permissions"] = permissions
        if host_permissions is not None:
            manifest["host_permissions"] = host_permissions
        (ext_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return ext_dir


if __name__ == "__main__":
    unittest.main()
