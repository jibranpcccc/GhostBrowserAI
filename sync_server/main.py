"""GhostBrowser repository-side encrypted cloud sync server.

This server stores **already-encrypted** profile archives.  It does **not**
perform client encryption and does not need access to the user's passphrase.
Authentication is via a single ``Authorization: Bearer <token>`` header
validated against ``GHOSTBROWSER_SYNC_MASTER_TOKEN``.
"""

from __future__ import annotations

import base64
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from sync_server.crypto import (
    compare_token,
    derive_profile_storage_tag,
    hash_token_for_audit,
    redact_metadata,
)
from sync_server.models import (
    RevokeDeviceRequest,
    SyncArchiveResponse,
    SyncArchiveRetrieve,
    SyncArchiveUpload,
    SyncDeviceVersion,
    SyncProfileList,
    SyncStatusResponse,
)

logger = logging.getLogger("ghostbrowser.sync_server")

ENV_MASTER_TOKEN = "GHOSTBROWSER_SYNC_MASTER_TOKEN"
ENV_DATABASE = "GHOSTBROWSER_SYNC_DATABASE"
ENV_MAX_ARCHIVE_BYTES = "GHOSTBROWSER_SYNC_MAX_ARCHIVE_BYTES"
DEFAULT_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024


def _get_master_token() -> str:
    """Return the configured master token or raise on startup."""
    token = os.environ.get(ENV_MASTER_TOKEN, "")
    if not token:
        raise RuntimeError(f"{ENV_MASTER_TOKEN} must be set")
    return token


class ProfileSyncStore:
    """In-memory datastore with optional SQLite persistence.

    The canonical primary key is ``(tenant_id, profile_id, device_id)``.
    Storage rows are keyed by an HMAC-derived tag so that the persistence
    layer never exposes the raw identifiers directly.
    """

    def __init__(self, database_path: Optional[str] = None):
        self._memory: Dict[str, Dict[str, Any]] = {}
        self.database_path = database_path
        if self.database_path:
            self._init_sqlite()

    def _init_sqlite(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.database_path)), exist_ok=True)
        with self._sqlite() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_archives (
                    storage_tag TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    archive_b64 TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    stored_at TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sync_archives_lookup
                ON sync_archives(tenant_id, profile_id, device_id);
                """
            )
            conn.commit()

    @contextmanager
    def _sqlite(self):
        conn = sqlite3.connect(self.database_path, timeout=10.0)
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _tag(tenant_id: str, profile_id: str, device_id: str) -> str:
        return derive_profile_storage_tag(tenant_id, profile_id, device_id)

    def _to_row(self, tenant_id: str, profile_id: str, device_id: str) -> Optional[Dict[str, Any]]:
        tag = self._tag(tenant_id, profile_id, device_id)
        row = self._memory.get(tag)
        if row is None and self.database_path:
            with self._sqlite() as conn:
                cur = conn.execute(
                    "SELECT * FROM sync_archives WHERE storage_tag = ?;",
                    (tag,),
                )
                raw = cur.fetchone()
                if raw:
                    row = dict(zip((c[0] for c in cur.description), raw))
                    import json

                    row["metadata"] = json.loads(row.get("metadata", "{}"))
                    self._memory[tag] = row
        return row

    def store(
        self,
        tenant_id: str,
        profile_id: str,
        device_id: str,
        archive_b64: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Store or overwrite an archive.  Returns decoded size in bytes."""
        import json

        try:
            archive_bytes = base64.b64decode(archive_b64.encode("ascii"), validate=True)
        except Exception as exc:
            raise ValueError("archive_b64 is not valid base64") from exc

        max_bytes = int(os.environ.get(ENV_MAX_ARCHIVE_BYTES, DEFAULT_MAX_ARCHIVE_BYTES))
        if len(archive_bytes) > max_bytes:
            raise ValueError(f"archive exceeds maximum size of {max_bytes} bytes")

        metadata = redact_metadata(metadata)
        now = datetime.now(timezone.utc).isoformat()
        tag = self._tag(tenant_id, profile_id, device_id)
        row: Dict[str, Any] = {
            "storage_tag": tag,
            "tenant_id": tenant_id,
            "profile_id": profile_id,
            "device_id": device_id,
            "archive_b64": archive_b64,
            "metadata": metadata,
            "stored_at": now,
            "size": len(archive_bytes),
            "revoked": 0,
        }
        self._memory[tag] = row

        if self.database_path:
            import json

            with self._sqlite() as conn:
                conn.execute(
                    """
                    INSERT INTO sync_archives
                        (storage_tag, tenant_id, profile_id, device_id,
                         archive_b64, metadata, stored_at, size, revoked)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(storage_tag)
                    DO UPDATE SET
                        archive_b64=excluded.archive_b64,
                        metadata=excluded.metadata,
                        stored_at=excluded.stored_at,
                        size=excluded.size,
                        revoked=0;
                    """,
                    (
                        tag,
                        tenant_id,
                        profile_id,
                        device_id,
                        archive_b64,
                        json.dumps(metadata),
                        now,
                        len(archive_bytes),
                        0,
                    ),
                )
                conn.commit()
        return len(archive_bytes)

    def retrieve(
        self,
        tenant_id: str,
        profile_id: str,
        device_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the stored row or ``None`` if absent/revoked."""
        row = self._to_row(tenant_id, profile_id, device_id)
        if row is None:
            return None
        if row.get("revoked", 0):
            return None
        return row

    def list_profile(self, tenant_id: str, profile_id: str) -> List[Tuple[str, int, Optional[str]]]:
        """Return ``(device_id, size, stored_at)`` tuples for the profile."""
        results: List[Tuple[str, int, Optional[str]]] = []
        if self.database_path:
            with self._sqlite() as conn:
                cur = conn.execute(
                    """
                    SELECT device_id, size, stored_at
                    FROM sync_archives
                    WHERE tenant_id = ? AND profile_id = ? AND revoked = 0;
                    """,
                    (tenant_id, profile_id),
                )
                results.extend(cur.fetchall())
        else:
            for row in self._memory.values():
                if (
                    row.get("tenant_id") == tenant_id
                    and row.get("profile_id") == profile_id
                    and not row.get("revoked", 0)
                ):
                    results.append(
                        (
                            row["device_id"],
                            row["size"],
                            row.get("stored_at"),
                        )
                    )
        return results

    def revoke(self, tenant_id: str, profile_id: str, device_id: str) -> bool:
        """Mark a device revoked.  Returns ``True`` if a row existed."""
        tag = self._tag(tenant_id, profile_id, device_id)
        existed = False
        if tag in self._memory:
            self._memory[tag]["revoked"] = 1
            existed = True
        if self.database_path:
            with self._sqlite() as conn:
                cur = conn.execute(
                    "UPDATE sync_archives SET revoked = 1 WHERE storage_tag = ?;",
                    (tag,),
                )
                conn.commit()
                existed = existed or cur.rowcount > 0
        return existed

    def delete(
        self,
        tenant_id: str,
        profile_id: str,
        device_id: str,
    ) -> bool:
        """Permanently delete a device's archive."""
        tag = self._tag(tenant_id, profile_id, device_id)
        existed = self._memory.pop(tag, None) is not None
        if self.database_path:
            with self._sqlite() as conn:
                cur = conn.execute(
                    "DELETE FROM sync_archives WHERE storage_tag = ?;",
                    (tag,),
                )
                conn.commit()
                existed = existed or cur.rowcount > 0
        return existed


# Lazily initialised at startup so tests can override before creating the app.
_store: Optional[ProfileSyncStore] = None
_master_token: Optional[str] = None


def get_store() -> ProfileSyncStore:
    if _store is None:
        raise RuntimeError("Sync store has not been initialised")
    return _store


def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """Dependency that validates the master bearer token."""
    if _master_token is None:
        raise HTTPException(status_code=500, detail="Server auth is not configured")

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    if not compare_token(token, _master_token):
        logger.warning(
            "Rejected sync authorization attempt with token hash %s",
            hash_token_for_audit(token),
        )
        raise HTTPException(status_code=403, detail="Invalid token")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialise store and master token once at startup."""
    global _store, _master_token
    db_path = os.environ.get(ENV_DATABASE)
    _store = ProfileSyncStore(database_path=db_path)
    _master_token = _get_master_token()
    logger.info(
        "GhostBrowser sync server starting; persistence=%s",
        "sqlite" if db_path else "memory",
    )
    yield
    _store = None
    _master_token = None
    logger.info("GhostBrowser sync server shutting down")


app = FastAPI(
    title="GhostBrowser Sync Server",
    description="Repository-side encrypted profile archive sync",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    """Public health endpoint for load balancers and Docker healthchecks."""
    return {"status": "ok", "service": "ghostbrowser-sync-server"}


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
    )


@app.post(
    "/sync/v1/{tenant_id}/{profile_id}/{device_id}",
    response_model=SyncArchiveResponse,
)
async def upload_archive(
    tenant_id: str = Path(..., min_length=1, max_length=128),
    profile_id: str = Path(..., min_length=1, max_length=128),
    device_id: str = Path(..., min_length=1, max_length=128),
    payload: SyncArchiveUpload = ...,
    _auth_ok: None = Depends(require_auth),
):
    """Store a base64-encoded, already-encrypted profile archive."""
    store = get_store()
    size = store.store(
        tenant_id,
        profile_id,
        device_id,
        payload.archive_b64,
        payload.metadata or {},
    )
    return SyncArchiveResponse(status="stored", size=size)


@app.get(
    "/sync/v1/{tenant_id}/{profile_id}",
    response_model=SyncProfileList,
)
async def list_archives(
    tenant_id: str = Path(..., min_length=1, max_length=128),
    profile_id: str = Path(..., min_length=1, max_length=128),
    _auth_ok: None = Depends(require_auth),
):
    """List device versions stored for a profile."""
    store = get_store()
    devices = store.list_profile(tenant_id, profile_id)
    return SyncProfileList(
        tenant_id=tenant_id,
        profile_id=profile_id,
        devices=[
            SyncDeviceVersion(device_id=did, size=size, stored_at=stored_at)
            for did, size, stored_at in devices
        ],
    )


@app.get(
    "/sync/v1/{tenant_id}/{profile_id}/{device_id}",
    response_model=SyncArchiveRetrieve,
)
async def download_archive(
    tenant_id: str = Path(..., min_length=1, max_length=128),
    profile_id: str = Path(..., min_length=1, max_length=128),
    device_id: str = Path(..., min_length=1, max_length=128),
    _auth_ok: None = Depends(require_auth),
):
    """Retrieve the latest archive for a device as base64."""
    store = get_store()
    row = store.retrieve(tenant_id, profile_id, device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Archive not found or device revoked")
    return SyncArchiveRetrieve(
        tenant_id=tenant_id,
        profile_id=profile_id,
        device_id=device_id,
        archive_b64=row["archive_b64"],
        stored_at=row.get("stored_at"),
        size=row["size"],
        metadata=row.get("metadata", {}),
    )


@app.delete(
    "/sync/v1/{tenant_id}/{profile_id}/{device_id}",
    response_model=SyncStatusResponse,
)
async def delete_archive(
    tenant_id: str = Path(..., min_length=1, max_length=128),
    profile_id: str = Path(..., min_length=1, max_length=128),
    device_id: str = Path(..., min_length=1, max_length=128),
    _auth_ok: None = Depends(require_auth),
):
    """Permanently delete a device's stored archive."""
    store = get_store()
    existed = store.delete(tenant_id, profile_id, device_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Archive not found")
    return SyncStatusResponse(status="ok", message="Archive deleted")


@app.post(
    "/sync/v1/{tenant_id}/{profile_id}/{device_id}/revoke",
    response_model=SyncStatusResponse,
)
async def revoke_device(
    tenant_id: str = Path(..., min_length=1, max_length=128),
    profile_id: str = Path(..., min_length=1, max_length=128),
    device_id: str = Path(..., min_length=1, max_length=128),
    payload: RevokeDeviceRequest = RevokeDeviceRequest(),
    _auth_ok: None = Depends(require_auth),
):
    """Revoke a device's access to future downloads for this profile."""
    store = get_store()
    existed = store.revoke(tenant_id, profile_id, device_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Archive not found")
    return SyncStatusResponse(status="ok", message="Device revoked")
