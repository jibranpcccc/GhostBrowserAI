"""Tests for the /api/cloud-sync remote endpoints."""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest import TestCase, mock

from fastapi.testclient import TestClient


class CloudSyncRouterTests(TestCase):
    ADMIN_TOKEN = "test-admin-token"

    def setUp(self):
        self.orig_env = {
            "GHOSTBROWSER_ADMIN_TOKEN": os.environ.get("GHOSTBROWSER_ADMIN_TOKEN"),
            "GHOSTBROWSER_SYNC_REMOTE_URL": os.environ.get("GHOSTBROWSER_SYNC_REMOTE_URL"),
            "GHOSTBROWSER_SYNC_TENANT_ID": os.environ.get("GHOSTBROWSER_SYNC_TENANT_ID"),
            "GHOSTBROWSER_SYNC_DEVICE_ID": os.environ.get("GHOSTBROWSER_SYNC_DEVICE_ID"),
            "GHOSTBROWSER_SYNC_REMOTE_TOKEN": os.environ.get("GHOSTBROWSER_SYNC_REMOTE_TOKEN"),
            "GHOSTBROWSER_DEV_MODE": os.environ.get("GHOSTBROWSER_DEV_MODE"),
        }
        os.environ.update({
            "GHOSTBROWSER_ADMIN_TOKEN": self.ADMIN_TOKEN,
            "GHOSTBROWSER_SYNC_REMOTE_URL": "http://127.0.0.1:8080",
            "GHOSTBROWSER_SYNC_TENANT_ID": "tenant-1",
            "GHOSTBROWSER_SYNC_DEVICE_ID": "device-a",
            "GHOSTBROWSER_SYNC_REMOTE_TOKEN": "token-secret",
            "GHOSTBROWSER_DEV_MODE": "1",
        })

    def tearDown(self):
        for key, value in self.orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @contextmanager
    def _client(self):
        from backend.main import app
        with TestClient(app) as client:
            yield client

    def _csrf_headers(self, client: TestClient) -> dict:
        client.get("/api/system/csrf-token")
        token = client.cookies.get("XSRF-TOKEN")
        if not token:
            token = client.get("/api/system/csrf-token").json()["token"]
        return {
            "X-XSRF-Token": token,
            "X-CSRF-Token": token,
        }

    def _admin_headers(self) -> dict:
        return {"X-Admin-Token": self.ADMIN_TOKEN}

    def test_upload_without_admin_token_disabled_returns_503(self):
        os.environ.pop("GHOSTBROWSER_ADMIN_TOKEN", None)
        with self._client() as client:
            headers = {**self._csrf_headers(client), **self._admin_headers()}
            response = client.post("/api/cloud-sync/upload", json={"profile_id": "p1", "archive_b64": "aGVsbG8="}, headers=headers)
            self.assertEqual(response.status_code, 503)

    def test_upload_without_admin_header_returns_403(self):
        with self._client() as client:
            headers = self._csrf_headers(client)
            response = client.post("/api/cloud-sync/upload", json={"profile_id": "p1", "archive_b64": "aGVsbG8="}, headers=headers)
            self.assertEqual(response.status_code, 401)

    def test_upload_without_config_returns_501(self):
        for key in ["GHOSTBROWSER_SYNC_REMOTE_URL", "GHOSTBROWSER_SYNC_TENANT_ID",
                    "GHOSTBROWSER_SYNC_DEVICE_ID", "GHOSTBROWSER_SYNC_REMOTE_TOKEN"]:
            os.environ.pop(key, None)
        with self._client() as client:
            headers = {**self._csrf_headers(client), **self._admin_headers()}
            response = client.post("/api/cloud-sync/upload", json={"profile_id": "p1", "archive_b64": "aGVsbG8="}, headers=headers)
            self.assertEqual(response.status_code, 501)

    def test_upload_rejects_invalid_profile_id(self):
        with self._client() as client:
            headers = {**self._csrf_headers(client), **self._admin_headers()}
            response = client.post("/api/cloud-sync/upload", json={"profile_id": "p1/invalid", "archive_b64": "aGVsbG8="}, headers=headers)
            self.assertEqual(response.status_code, 422)

    @mock.patch("backend.cloud_sync.CloudSyncClient.upload_profile")
    def test_upload_forwards_to_client(self, mock_upload):
        mock_upload.return_value = {"status": "stored", "size": 42}
        with self._client() as client:
            response = client.post(
                "/api/cloud-sync/upload",
                json={"profile_id": "p1", "archive_b64": "aGVsbG8="},
                headers={**self._csrf_headers(client), **self._admin_headers()},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "stored")
            mock_upload.assert_called_once_with("p1", "aGVsbG8=")

    @mock.patch("backend.cloud_sync.CloudSyncClient.download_profile")
    def test_download_forwards_to_client(self, mock_download):
        mock_download.return_value = {"archive_b64": "aGVsbG8="}
        with self._client() as client:
            response = client.get("/api/cloud-sync/download/p1", headers=self._admin_headers())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["archive_b64"], "aGVsbG8=")
            mock_download.assert_called_once_with("p1")

    @mock.patch("backend.cloud_sync.CloudSyncClient.revoke_device")
    def test_revoke_forwards_to_client(self, mock_revoke):
        mock_revoke.return_value = {"status": "revoked"}
        with self._client() as client:
            response = client.post(
                "/api/cloud-sync/revoke-device",
                json={"profile_id": "p1"},
                headers={**self._csrf_headers(client), **self._admin_headers()},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "revoked")
            mock_revoke.assert_called_once_with("p1", "operator request")

    @mock.patch("backend.cloud_sync.CloudSyncClient.upload_profile")
    @mock.patch("backend.cloud_sync.cloud_sync_manager.export_profile")
    @mock.patch("backend.profile_manager.profile_manager.get_profile")
    def test_legacy_remote_upload_requires_admin(self, mock_get, mock_export, mock_upload):
        mock_get.return_value = {"id": "p1"}
        mock_export.return_value = b"archive"
        mock_upload.return_value = {"stored": True}
        with self._client() as client:
            headers = self._csrf_headers(client)
            response = client.post(
                "/api/profiles/p1/sync/remote",
                json={"passphrase": "correct horse battery staple"},
                headers=headers,
            )
            self.assertEqual(response.status_code, 401)
            mock_upload.assert_not_called()

    @mock.patch("backend.cloud_sync.CloudSyncClient.upload_profile")
    @mock.patch("backend.cloud_sync.cloud_sync_manager.export_profile")
    @mock.patch("backend.profile_manager.profile_manager.get_profile")
    def test_legacy_remote_upload_success(self, mock_get, mock_export, mock_upload):
        mock_get.return_value = {"id": "p1"}
        mock_export.return_value = b"archive"
        mock_upload.return_value = {"stored": True}
        with self._client() as client:
            response = client.post(
                "/api/profiles/p1/sync/remote",
                json={"passphrase": "correct horse battery staple"},
                headers={**self._csrf_headers(client), **self._admin_headers()},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["remote"]["stored"])
            mock_export.assert_called_once_with("p1", "correct horse battery staple")
            mock_upload.assert_called_once()

    @mock.patch("backend.cloud_sync.CloudSyncClient.upload_profile")
    @mock.patch("backend.cloud_sync.cloud_sync_manager.export_profile")
    @mock.patch("backend.profile_manager.profile_manager.get_profile")
    def test_legacy_remote_upload_runtime_error_is_stable(self, mock_get, mock_export, mock_upload):
        mock_get.return_value = {"id": "p1"}
        mock_export.return_value = b"archive"
        mock_upload.side_effect = RuntimeError("secret remote sync detail 777")
        with self._client() as client:
            response = client.post(
                "/api/profiles/p1/sync/remote",
                json={"passphrase": "correct horse battery staple"},
                headers={**self._csrf_headers(client), **self._admin_headers()},
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Remote sync upload failed")
        self.assertNotIn("secret remote sync detail 777", response.text)


if __name__ == "__main__":
    unittest.main()
