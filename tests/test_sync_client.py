"""Tests for backend.cloud_sync.CloudSyncClient.

These tests mock stdlib ``urllib.request`` so they do not need a running
sync server or browser infrastructure.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch, MagicMock

from backend.cloud_sync import CloudSyncClient


class FakeHTTPResponse(io.BytesIO):
    def __init__(self, payload: bytes, code: int = 200):
        super().__init__(payload)
        self.code = code

    @property
    def status(self):
        return self.code

    def read(self, *args, **kwargs):
        return self.getvalue()


class TestCloudSyncClient(unittest.TestCase):
    def setUp(self):
        self._original_dev_mode = os.environ.get("GHOSTBROWSER_DEV_MODE")
        os.environ["GHOSTBROWSER_DEV_MODE"] = "1"

    def tearDown(self):
        if self._original_dev_mode is None:
            os.environ.pop("GHOSTBROWSER_DEV_MODE", None)
        else:
            os.environ["GHOSTBROWSER_DEV_MODE"] = self._original_dev_mode

    def _client(self):
        return CloudSyncClient(
            base_url="http://127.0.0.1:8080",
            tenant_id="tenant-test",
            device_id="device-test",
            token="test-token",
        )

    def _expected_url(self, profile_id: str, suffix: str = "") -> str:
        base = f"http://127.0.0.1:8080/sync/v1/tenant-test/{profile_id}/device-test"
        if suffix:
            return f"{base}/{suffix.lstrip('/')}"
        return base

    def test_http_url_rejected_outside_development_mode(self):
        os.environ.pop("GHOSTBROWSER_DEV_MODE", None)
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            self._client()

    def test_https_url_accepted_outside_development_mode(self):
        os.environ.pop("GHOSTBROWSER_DEV_MODE", None)
        client = CloudSyncClient(
            base_url="https://sync.example.test",
            tenant_id="tenant-test",
            device_id="device-test",
            token="test-token",
        )
        self.assertEqual(client.base_url, "https://sync.example.test")

    @patch("urllib.request.urlopen")
    def test_upload_profile(self, mock_urlopen):
        client = self._client()
        archive_b64 = "ZW5jcnlwdGVkLWFyY2hpdmU="
        response_payload = {"status": "stored", "stored_at": "2025-01-01T00:00:00", "size": 17}
        mock_urlopen.return_value = FakeHTTPResponse(json.dumps(response_payload).encode())

        result = client.upload_profile("profile-1", archive_b64)

        self.assertEqual(result, response_payload)
        self.assertEqual(mock_urlopen.call_count, 1)
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, self._expected_url("profile-1"))
        self.assertEqual(request.method, "POST")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["archive_b64"], archive_b64)
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")

    @patch("urllib.request.urlopen")
    def test_download_profile(self, mock_urlopen):
        client = self._client()
        response_payload = {
            "status": "ok",
            "tenant_id": "tenant-test",
            "profile_id": "profile-1",
            "device_id": "device-test",
            "archive_b64": "ZW5jcnlwdGVkLWFyY2hpdmU=",
            "size": 17,
            "metadata": {},
        }
        mock_urlopen.return_value = FakeHTTPResponse(json.dumps(response_payload).encode())

        result = client.download_profile("profile-1")

        self.assertEqual(result, response_payload)
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, self._expected_url("profile-1"))
        self.assertEqual(request.method, "GET")

    def test_upload_profile_rejects_empty_archive(self):
        client = self._client()
        with self.assertRaises(ValueError):
            client.upload_profile("profile-1", "")
        with self.assertRaises(ValueError):
            client.upload_profile("profile-1", 123)

    @patch("urllib.request.urlopen")
    def test_delete_profile(self, mock_urlopen):
        client = self._client()
        mock_urlopen.return_value = FakeHTTPResponse(json.dumps({"status": "ok"}).encode())

        result = client.delete_profile("profile-1")

        self.assertEqual(result["status"], "ok")
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, self._expected_url("profile-1"))
        self.assertEqual(request.method, "DELETE")

    @patch("urllib.request.urlopen")
    def test_revoke_device(self, mock_urlopen):
        client = self._client()
        mock_urlopen.return_value = FakeHTTPResponse(json.dumps({"status": "ok"}).encode())

        result = client.revoke_device("profile-1", reason="lost device")

        self.assertEqual(result["status"], "ok")
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, self._expected_url("profile-1", "/revoke"))
        self.assertEqual(request.method, "POST")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["reason"], "lost device")

    @patch("urllib.request.urlopen")
    def test_list_profile_devices(self, mock_urlopen):
        client = self._client()
        response_payload = {
            "status": "ok",
            "tenant_id": "tenant-test",
            "profile_id": "profile-1",
            "devices": [{"device_id": "device-test", "size": 0}],
        }
        mock_urlopen.return_value = FakeHTTPResponse(json.dumps(response_payload).encode())

        result = client.list_profile_devices("profile-1")

        self.assertEqual(result["devices"][0]["device_id"], "device-test")
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8080/sync/v1/tenant-test/profile-1")
        self.assertEqual(request.method, "GET")

    @patch("urllib.request.urlopen")
    def test_http_error_raises_runtime_error(self, mock_urlopen):
        from urllib.error import HTTPError

        client = self._client()
        mock_urlopen.side_effect = HTTPError(
            url=self._expected_url("profile-1"),
            code=404,
            msg="Not Found",
            hdrs={},
            fp=FakeHTTPResponse(json.dumps({"detail": "Archive not found"}).encode(), code=404),
        )

        with self.assertRaises(RuntimeError):
            client.download_profile("profile-1")


if __name__ == "__main__":
    unittest.main()
