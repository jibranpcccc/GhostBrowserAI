"""Tests for the GhostBrowser Sync Server.

These tests run against the FastAPI app using TestClient and therefore require
FastAPI to be installed in the environment.
"""

from __future__ import annotations

import base64
import json
import os
import unittest
import uuid


def _set_test_env():
    os.environ.setdefault("GHOSTBROWSER_SYNC_MASTER_TOKEN", "test-master-token-" + uuid.uuid4().hex)


_set_test_env()

from fastapi.testclient import TestClient

from sync_server.main import app


class TestSyncServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = os.environ["GHOSTBROWSER_SYNC_MASTER_TOKEN"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    def setUp(self):
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def _url(self, tenant_id: str, profile_id: str, device_id: str, suffix: str = "") -> str:
        base = f"/sync/v1/{tenant_id}/{profile_id}/{device_id}"
        return f"{base}{suffix}"

    def test_upload_and_download_round_trip(self):
        tenant_id = "tenant-1"
        profile_id = "profile-abc"
        device_id = "device-1"
        archive_b64 = base64.b64encode(b"encrypted-stub-archive").decode("ascii")

        response = self.client.post(
            self._url(tenant_id, profile_id, device_id),
            json={"archive_b64": archive_b64, "metadata": {"version": "1"}},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "stored")
        self.assertEqual(data["size"], len(base64.b64decode(archive_b64)))

        response = self.client.get(
            self._url(tenant_id, profile_id, device_id),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["archive_b64"], archive_b64)

    def test_list_devices(self):
        tenant_id = "tenant-2"
        profile_id = "profile-list"
        device_ids = ["device-a", "device-b"]
        for device_id in device_ids:
            archive_b64 = base64.b64encode(f"archive-{device_id}".encode()).decode("ascii")
            response = self.client.post(
                self._url(tenant_id, profile_id, device_id),
                json={"archive_b64": archive_b64},
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.get(f"/sync/v1/{tenant_id}/{profile_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual({d["device_id"] for d in data["devices"]}, set(device_ids))

    def test_revoke_blocks_download(self):
        tenant_id = "tenant-3"
        profile_id = "profile-revoke"
        device_id = "device-x"
        archive_b64 = base64.b64encode(b"revoked-archive").decode("ascii")

        self.client.post(
            self._url(tenant_id, profile_id, device_id),
            json={"archive_b64": archive_b64},
            headers=self.headers,
        )
        response = self.client.post(
            self._url(tenant_id, profile_id, device_id, "/revoke"),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            self._url(tenant_id, profile_id, device_id),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_removes_archive(self):
        tenant_id = "tenant-4"
        profile_id = "profile-delete"
        device_id = "device-y"
        archive_b64 = base64.b64encode(b"deleted-archive").decode("ascii")

        self.client.post(
            self._url(tenant_id, profile_id, device_id),
            json={"archive_b64": archive_b64},
            headers=self.headers,
        )
        response = self.client.delete(
            self._url(tenant_id, profile_id, device_id),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            self._url(tenant_id, profile_id, device_id),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_base64_rejected(self):
        tenant_id = "tenant-5"
        profile_id = "profile-bad"
        device_id = "device-z"
        response = self.client.post(
            self._url(tenant_id, profile_id, device_id),
            json={"archive_b64": "not-valid-base64!!!"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_auth_rejected(self):
        tenant_id = "tenant-6"
        profile_id = "profile-auth"
        device_id = "device-1"
        response = self.client.get(self._url(tenant_id, profile_id, device_id))
        self.assertEqual(response.status_code, 401)

    def test_invalid_token_rejected(self):
        tenant_id = "tenant-7"
        profile_id = "profile-auth2"
        device_id = "device-1"
        response = self.client.get(
            self._url(tenant_id, profile_id, device_id),
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_banned_metadata_keys_redacted(self):
        tenant_id = "tenant-8"
        profile_id = "profile-meta"
        device_id = "device-1"
        archive_b64 = base64.b64encode(b"meta-archive").decode("ascii")
        bad_metadata = {"password": "secret", "version": "2"}

        self.client.post(
            self._url(tenant_id, profile_id, device_id),
            json={"archive_b64": archive_b64, "metadata": bad_metadata},
            headers=self.headers,
        )
        response = self.client.get(
            self._url(tenant_id, profile_id, device_id),
            headers=self.headers,
        )
        data = response.json()
        self.assertNotIn("password", data["metadata"])
        self.assertIn("version", data["metadata"])


if __name__ == "__main__":
    unittest.main()
