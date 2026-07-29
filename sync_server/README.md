# GhostBrowser Sync Server

A standalone, repository-side encrypted cloud sync server for GhostBrowser profiles.

The server stores **already-encrypted** profile archives.  It never sees plaintext
profile data and does not perform AES encryption.  Client-side encryption lives in
`backend/cloud_sync.py`.

## Features

* Bearer-token authentication via `GHOSTBROWSER_SYNC_MASTER_TOKEN`.
* In-memory storage by default; optional SQLite persistence via `GHOSTBROWSER_SYNC_DATABASE`.
* Storage keyed by `(tenant_id, profile_id, device_id)`.
* Per-device revocation support.
* Optional per-archive size limits.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Public health check (no auth) |
| POST | `/sync/v1/{tenant_id}/{profile_id}/{device_id}` | Upload a base64-encoded AES-encrypted `.ghost` archive |
| GET | `/sync/v1/{tenant_id}/{profile_id}` | List device versions for a profile |
| GET | `/sync/v1/{tenant_id}/{profile_id}/{device_id}` | Download the latest archive as base64 |
| DELETE | `/sync/v1/{tenant_id}/{profile_id}/{device_id}` | Delete a device's archive |
| POST | `/sync/v1/{tenant_id}/{profile_id}/{device_id}/revoke` | Revoke a device's access |

All `/sync/v1/*` endpoints require `Authorization: Bearer <GHOSTBROWSER_SYNC_MASTER_TOKEN>`.

## Running locally

```powershell
# From the repository root
$env:GHOSTBROWSER_SYNC_MASTER_TOKEN = "$(python -c 'import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())')"
python -m uvicorn sync_server.main:app --host 127.0.0.1 --port 8080
```

To enable SQLite persistence:

```powershell
$env:GHOSTBROWSER_SYNC_DATABASE = "$pwd\sync_server_data\sync.db"
New-Item -ItemType Directory -Path $env:GHOSTBROWSER_SYNC_DATABASE -Force
python -m uvicorn sync_server.main:app --host 127.0.0.1 --port 8080
```

## Running with Docker

```bash
# From the repository root
cp sync_server/.env.example .env
# edit .env and set GHOSTBROWSER_SYNC_MASTER_TOKEN
docker compose -f sync_server/docker-compose.yml up --build
```

## Deployment notes

* Always set `GHOSTBROWSER_SYNC_MASTER_TOKEN` to a strong random value.
* Set `GHOSTBROWSER_SYNC_HMAC_SALT` in production to avoid the deterministic fallback.
* Run behind a reverse proxy (TLS) in production; the server only speaks plain HTTP.
* Back up the `/data` volume regularly if SQLite persistence is enabled.

## Client integration

Use `backend.cloud_sync.CloudSyncClient` from the GhostBrowser backend:

```python
from backend.cloud_sync import CloudSyncClient

client = CloudSyncClient(
    base_url="http://127.0.0.1:8080",
    tenant_id="tenant-123",
    device_id="device-abc",
    token="master-token",
)
client.upload_profile("profile-xyz", archive_b64)
```

## Testing

```powershell
python -m unittest tests.test_sync_server tests.test_sync_client -v
```
