import sys
import os
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient


def _get_client():
    with patch("backend.main.profile_manager") as mock_pm, \
         patch("backend.main.proxy_manager") as mock_prx, \
         patch("backend.main.is_profile_running") as mock_running, \
         patch("backend.main.system_monitor") as mock_sm, \
         patch("backend.main.scheduler_manager") as mock_sched, \
         patch("backend.main.macro_manager") as mock_mm:
        mock_sm.ram_usage = 42.5
        mock_sm.get_health.return_value = {"status": "ok"}
        mock_mm.list_macros.return_value = []
        mock_mm.get_macro.return_value = None
        mock_sched.list_schedules.return_value = []
        mock_prx._get_active_proxies.return_value = []
        from backend.main import app
        client = TestClient(app, raise_server_exceptions=False)
        return client, mock_pm, mock_prx, mock_running


def test_get_profiles_returns_list():
    client, mock_pm, _, _ = _get_client()
    mock_pm.list_profiles.return_value = [{"id": "test-1", "name": "Test Profile"}]
    mock_pm.get_profile.return_value = {"id": "test-1"}
    response = client.get("/api/profiles")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_get_profiles_no_auth_required():
    client, mock_pm, _, _ = _get_client()
    mock_pm.list_profiles.return_value = []
    response = client.get("/api/profiles")
    assert response.status_code == 200


def test_post_profiles_requires_auth():
    client, mock_pm, _, _ = _get_client()
    response = client.post("/api/profiles", json={"name": "Test"})
    assert response.status_code == 401


def test_post_profiles_with_valid_token():
    client, mock_pm, _, _ = _get_client()
    token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
    if not token:
        from backend.main import ADMIN_TOKEN
        token = ADMIN_TOKEN
    mock_pm.create_profile.return_value = {"id": "new-1", "name": "New Profile"}
    with patch("backend.main.profile_creator") as mock_pc:
        mock_pc.create_zero_leak_profile = AsyncMock(return_value={
            "status": "success",
            "profile": {"id": "new-1", "name": "New Profile"}
        })
        response = client.post(
            "/api/profiles",
            json={"name": "New Profile"},
            headers={"X-Admin-Token": token}
        )
    assert response.status_code == 200


def test_get_proxies_no_auth():
    client, _, mock_prx, _ = _get_client()
    mock_prx._get_active_proxies.return_value = []
    response = client.get("/api/proxies")
    assert response.status_code == 200


def test_get_proxies_returns_data():
    client, _, mock_prx, _ = _get_client()
    mock_prx._get_active_proxies.return_value = [{"server": "http://1.2.3.4:8080"}]
    response = client.get("/api/proxies")
    assert response.status_code == 200


def test_get_system_health():
    client, _, _, _ = _get_client()
    response = client.get("/api/system/health")
    assert response.status_code == 200


def test_launch_profile_requires_auth():
    client, _, _, mock_running = _get_client()
    mock_running.return_value = False
    response = client.post("/api/profiles/fake-id/launch")
    assert response.status_code == 401


def test_close_profile_requires_auth():
    client, _, _, mock_running = _get_client()
    mock_running.return_value = False
    response = client.post("/api/profiles/fake-id/close")
    assert response.status_code == 401


def test_get_macros_no_auth():
    client, _, _, _ = _get_client()
    response = client.get("/api/macros")
    assert response.status_code == 200


def test_delete_profile_requires_auth():
    client, _, _, _ = _get_client()
    response = client.delete("/api/profiles/fake-id")
    assert response.status_code == 401


def test_get_metrics_no_auth():
    client, _, _, _ = _get_client()
    response = client.get("/api/metrics")
    assert response.status_code == 200


def test_get_metrics_returns_data():
    client, _, _, _ = _get_client()
    response = client.get("/api/metrics")
    data = response.json()
    assert "active_profiles" in data
    assert "total_profiles" in data


def test_post_proxies_requires_auth():
    client, _, _, _ = _get_client()
    response = client.post("/api/proxies", json={"proxies": []})
    assert response.status_code == 401


def test_get_cloudflare_status():
    client, _, _, _ = _get_client()
    with patch("backend.main.cloudflare_manager") as mock_cf:
        mock_cf.load_accounts.return_value = None
        mock_cf.total_accounts = 0
        mock_cf.healthy_count = 0
        mock_cf.cooldown_count = 0
        mock_cf.get_all_status.return_value = []
        response = client.get("/api/cloudflare/status")
        assert response.status_code == 200


def test_rotator_status_no_auth():
    client, _, _, _ = _get_client()
    with patch("backend.main.rotator") as mock_rot:
        mock_rot.is_running = False
        mock_rot.max_concurrent = 15
        mock_rot.profile_session_times = {}
        response = client.get("/api/rotator/status")
        assert response.status_code == 200


def test_schedule_list_no_auth():
    client, _, _, _ = _get_client()
    response = client.get("/api/macros/schedule")
    assert response.status_code == 200


def test_invalid_token_rejected():
    client, _, _, _ = _get_client()
    response = client.post(
        "/api/profiles/fake-id/launch",
        headers={"X-Admin-Token": "invalid_token_12345"}
    )
    assert response.status_code in (400, 401, 404)


def test_launch_profile_with_valid_token():
    client, mock_pm, _, mock_running = _get_client()
    token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
    if not token:
        from backend.main import ADMIN_TOKEN
        token = ADMIN_TOKEN
    mock_running.return_value = False
    mock_pm.get_profile.return_value = {"id": "test-1", "name": "Test", "path": "/tmp/test"}
    with patch("backend.main.launch_profile", new_callable=AsyncMock) as mock_launch:
        mock_launch.return_value = {"status": "success", "message": "Browser launched"}
        response = client.post(
            "/api/profiles/test-1/launch",
            headers={"X-Admin-Token": token}
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"


def test_close_profile_with_valid_token():
    client, mock_pm, _, mock_running = _get_client()
    token = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN")
    if not token:
        from backend.main import ADMIN_TOKEN
        token = ADMIN_TOKEN
    mock_running.return_value = True
    mock_pm.get_profile.return_value = {"id": "test-1"}
    with patch("backend.main.close_profile", new_callable=AsyncMock) as mock_close:
        mock_close.return_value = {"status": "success", "message": "Browser closed"}
        response = client.post(
            "/api/profiles/test-1/close",
            headers={"X-Admin-Token": token}
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"


def test_get_fingerprint_no_auth():
    with patch("backend.main.profile_manager") as mock_pm:
        from backend.main import app
        mock_pm.get_profile.return_value = {"id": "test-1", "advanced": {"os": "Windows"}}
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/profiles/test-1/fingerprint")
        assert response.status_code == 200


def test_get_fingerprint_not_found():
    client, mock_pm, _, _ = _get_client()
    mock_pm.get_profile.return_value = None
    response = client.get("/api/profiles/nonexistent/fingerprint")
    assert response.status_code == 404


def test_post_profiles_bad_body():
    with patch("backend.main.profile_manager"), \
         patch("backend.main.profile_creator") as mock_pc:
        from backend.main import app, ADMIN_TOKEN
        mock_pc.create_zero_leak_profile = AsyncMock(return_value={"status": "error", "message": "fail"})
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/profiles",
            json={"name": "Test"},
            headers={"X-Admin-Token": ADMIN_TOKEN}
        )
        assert response.status_code in (400, 422, 503)
