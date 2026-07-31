import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.profile_manager import ProfileManager, _hash_pin


class ProfilePinAndTagTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ghost_new_features_")
        self.manager = ProfileManager(override_dir=self.temp_dir)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        finally:
            self.temp_dir = None
            self.manager = None

    def _created_profile(self, **kwargs):
        return self.manager.create_profile(name="test-profile", **kwargs)

    def test_pin_set_verify_clear(self):
        profile = self._created_profile()
        profile_id = profile["id"]

        self.assertTrue(self.manager.set_profile_pin(profile_id, "1234"))
        self.assertTrue(self.manager.verify_profile_pin(profile_id, "1234"))

        self.assertTrue(self.manager.clear_profile_pin(profile_id))
        self.assertFalse(self.manager.verify_profile_pin(profile_id, "1234"))
        self.assertNotIn("pin_hash", self.manager.profiles[profile_id])

    def test_pin_hash_not_plaintext(self):
        raw_pin = "1234"
        profile = self._created_profile()
        profile_id = profile["id"]

        self.assertTrue(self.manager.set_profile_pin(profile_id, raw_pin))

        with open(self.manager.metadata_file, "r", encoding="utf-8") as handle:
            stored = json.load(handle)

        pin_hash = stored[profile_id].get("pin_hash")
        self.assertIsInstance(pin_hash, str)
        self.assertNotEqual(pin_hash, raw_pin)
        self.assertTrue(pin_hash.startswith("pbkdf2_sha256"))

    def test_pin_wrong_rejected(self):
        profile = self._created_profile()
        profile_id = profile["id"]

        self.assertTrue(self.manager.set_profile_pin(profile_id, "1234"))
        self.assertFalse(self.manager.verify_profile_pin(profile_id, "5678"))
        self.assertFalse(self.manager.verify_profile_pin(profile_id, ""))

    def test_new_pin_policy_accepts_ascii_digit_boundaries(self):
        profile_id = self._created_profile()["id"]
        for pin in ("1234", "123456"):
            with self.subTest(pin=pin):
                self.assertTrue(self.manager.set_profile_pin(profile_id, pin))

    def test_new_pin_policy_rejects_invalid_values(self):
        profile_id = self._created_profile()["id"]
        for pin in ("123", "1234567", "12a4", "１２３４"):
            with self.subTest(pin=pin):
                self.assertFalse(self.manager.set_profile_pin(profile_id, pin))

    def test_legacy_pin_hash_remains_verifiable(self):
        profile_id = self._created_profile()["id"]
        self.manager.profiles[profile_id]["pin_hash"] = _hash_pin("legacy PIN")
        self.assertTrue(self.manager.verify_profile_pin(profile_id, "legacy PIN"))

    def test_normalize_tags(self):
        normalized = ProfileManager._normalize_tags([
            " WORK ",
            "work",
            "  ",
            "",
            "Gaming",
            "a" * 30,
        ])
        self.assertEqual(normalized, ["work", "gaming", "a" * 24])

    def test_add_remove_tags(self):
        profile = self._created_profile()
        profile_id = profile["id"]

        self.assertTrue(self.manager.add_tags(profile_id, ["social", "shopping", "SOCIAL"]))
        self.assertEqual(self.manager.profiles[profile_id]["tags"], ["social", "shopping"])

        self.assertTrue(self.manager.remove_tags(profile_id, ["shopping"]))
        self.assertEqual(self.manager.profiles[profile_id]["tags"], ["social"])

        self.assertTrue(self.manager.remove_tags(profile_id, ["missing"]))
        self.assertEqual(self.manager.profiles[profile_id]["tags"], ["social"])

    def test_create_profile_with_tags(self):
        tags = ["Email", "email", "  Work  ", ""]
        profile = self._created_profile(tags=tags)

        self.assertEqual(profile["tags"], ["email", "work"])
        self.assertEqual(self.manager.profiles[profile["id"]]["tags"], ["email", "work"])

        with open(self.manager.metadata_file, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored[profile["id"]]["tags"], ["email", "work"])


if __name__ == "__main__":
    unittest.main()
