"""Unit tests for update_manager."""
import base64
import json
import os
import shutil
import tempfile
import zipfile
import stat
from pathlib import Path
from unittest import TestCase
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.update_manager import (
    check_for_update,
    generate_confirmation_token,
    get_current_version,
    verify_confirmation_token,
    _node_hash,
    _rollout_percent,
)


class UpdateManagerTests(TestCase):
    def setUp(self):
        self.original_env = os.environ.copy()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="gb_update_"))
        os.environ["GHOSTBROWSER_UPDATE_ROLLOUT_PERCENT"] = "100"
        os.environ["GHOSTBROWSER_UPDATE_NODE"] = "test-node"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_current_version(self):
        os.environ["GHOSTBROWSER_INSTALL_DIR"] = str(self.temp_dir)
        version = get_current_version()
        self.assertIsInstance(version, str)

    def test_check_for_update_without_repo(self):
        os.environ.pop("GHOSTBROWSER_UPDATE_REPO", None)
        result = check_for_update()
        self.assertTrue(result["up_to_date"])
        self.assertIn("current_version", result)

    def test_rollout_percent(self):
        os.environ["GHOSTBROWSER_UPDATE_ROLLOUT_PERCENT"] = "50"
        self.assertEqual(_rollout_percent(), 50)

    def test_node_hash_is_stable(self):
        v1 = _node_hash("0.0.1")
        v2 = _node_hash("0.0.1")
        self.assertEqual(v1, v2)
        self.assertTrue(0 <= v1 <= 99)

    def test_confirmation_token_round_trip(self):
        result = generate_confirmation_token("0.9.0", valid_seconds=60)
        self.assertIn("confirmation_token", result)
        self.assertTrue(verify_confirmation_token(result["confirmation_token"], "0.9.0"))
        self.assertFalse(verify_confirmation_token(result["confirmation_token"], "1.0.0"))

    def test_apply_update_flow_after_persisted_ed25519_verification(self):
        os.environ["GHOSTBROWSER_INSTALL_DIR"] = str(self.temp_dir / "install")
        os.environ["GHOSTBROWSER_AUTO_UPDATE"] = "1"
        os.environ["GHOSTBROWSER_UPDATE_DOWNLOAD_DIR"] = str(self.temp_dir / "downloads")
        install_dir = self.temp_dir / "install"
        install_dir.mkdir(parents=True)
        (install_dir / "manifest.json").write_text(json.dumps({"version": "0.0.1"}))

        update_dir = self.temp_dir / "update"
        update_dir.mkdir()
        new_file = update_dir / "manifest.json"
        new_file.write_text(json.dumps({"version": "0.9.0"}))
        archive_path = self.temp_dir / "update.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", new_file.read_text())

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        os.environ["GHOSTBROWSER_UPDATE_SIGNING_KEY"] = public_key.hex()
        signature_path = archive_path.with_suffix(".zip.sig")
        signature_path.write_bytes(private_key.sign(archive_path.read_bytes()))

        from backend.update_manager import apply_update, verify_signature
        verification = verify_signature(str(archive_path), sig_path=str(signature_path))
        self.assertTrue(verification["verified"])
        result = apply_update(str(archive_path))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["version"], "0.9.0")

    def test_extract_archive_rejects_zip_slip_and_symlink_members(self):
        from backend.update_manager import _extract_archive
        for name, mode in (("../escape.txt", None), ("/absolute.txt", None), ("link", stat.S_IFLNK | 0o777)):
            with self.subTest(name=name):
                archive = self.temp_dir / f"{len(name)}.zip"
                with zipfile.ZipFile(archive, "w") as zf:
                    info = zipfile.ZipInfo(name)
                    if mode is not None:
                        info.external_attr = mode << 16
                    zf.writestr(info, "malicious")
                with self.assertRaises(ValueError):
                    _extract_archive(str(archive), self.temp_dir / f"stage-{len(name)}")

    def test_apply_requires_persisted_ed25519_verification(self):
        os.environ["GHOSTBROWSER_INSTALL_DIR"] = str(self.temp_dir / "install")
        os.environ["GHOSTBROWSER_AUTO_UPDATE"] = "1"
        archive = self.temp_dir / "update.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"version": "0.9.0"}))
        from backend.update_manager import apply_update
        with self.assertRaisesRegex(RuntimeError, "pinned Ed25519"):
            apply_update(str(archive))

    def test_redirect_destinations_are_validated(self):
        from backend.update_manager import _LimitedRedirectHandler
        with self.assertRaisesRegex(RuntimeError, "Only HTTPS"):
            _LimitedRedirectHandler().redirect_request(None, None, 302, "Found", None, "http://example.test/update.zip")

    def test_apply_update_requires_confirmation_or_env(self):
        os.environ["GHOSTBROWSER_INSTALL_DIR"] = str(self.temp_dir / "install")
        os.environ.pop("GHOSTBROWSER_AUTO_UPDATE", None)
        install_dir = self.temp_dir / "install" / "ghostbrowser"
        install_dir.mkdir(parents=True)
        archive_path = self.temp_dir / "empty.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"version": "0.9.0"}))
        from backend.update_manager import apply_update
        with self.assertRaises(RuntimeError):
            apply_update(str(archive_path))
