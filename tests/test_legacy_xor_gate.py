"""A17: legacy XOR-encrypted cloud-sync archives must fail closed.

``cloud_sync._decrypt`` rejects GBXORWARNv1 payloads unless the operator
explicitly opts in with GHOSTBROWSER_ALLOW_LEGACY_XOR=1, and keeps AES-GCM
round-trips working.
"""
import os
import sys
import unittest
from unittest import mock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import backend.cloud_sync as cs


def _xor_archive(passphrase: str, plaintext: bytes) -> bytes:
    """Build a GBXORWARNv1 envelope the way the legacy encoder did."""
    salt = os.urandom(cs._SALT_BYTES)
    key = cs._derive_key(passphrase, salt, 32)
    stream = cs._derive_key(key.hex(), salt, len(plaintext))
    obfuscated = bytes(a ^ b for a, b in zip(plaintext, stream))
    return cs._HEADER_XORWARN + salt + obfuscated


class LegacyXorGateTests(unittest.TestCase):
    def test_xor_archive_rejected_by_default(self):
        payload = _xor_archive("passphrase", b"secret profile data")
        with self.assertRaises(RuntimeError) as ctx:
            cs._decrypt("passphrase", payload)
        self.assertIn("GHOSTBROWSER_ALLOW_LEGACY_XOR", str(ctx.exception))
        self.assertIn("rejected", str(ctx.exception))

    def test_xor_archive_decrypts_when_opted_in(self):
        payload = _xor_archive("passphrase", b"secret profile data")
        with mock.patch.dict(os.environ, {"GHOSTBROWSER_ALLOW_LEGACY_XOR": "1"}):
            self.assertEqual(cs._decrypt("passphrase", payload), b"secret profile data")

    def test_xor_archive_fails_closed_on_bad_passphrase(self):
        payload = _xor_archive("passphrase", b"secret profile data")
        with mock.patch.dict(os.environ, {"GHOSTBROWSER_ALLOW_LEGACY_XOR": "1"}):
            self.assertNotEqual(cs._decrypt("wrong-passphrase", payload), b"secret profile data")

    def test_aes_gcm_round_trip_still_works(self):
        if not cs._HAS_CRYPTOGRAPHY:
            self.skipTest("cryptography not installed")
        encrypted = cs._encrypt("passphrase", b"modern payload")
        self.assertEqual(cs._decrypt("passphrase", encrypted), b"modern payload")

    def test_aes_gcm_bad_passphrase_rejected(self):
        if not cs._HAS_CRYPTOGRAPHY:
            self.skipTest("cryptography not installed")
        encrypted = cs._encrypt("passphrase", b"modern payload")
        with self.assertRaises(ValueError):
            cs._decrypt("wrong-passphrase", encrypted)


if __name__ == "__main__":
    unittest.main()
