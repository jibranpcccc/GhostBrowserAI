"""Pydantic request/response models for the GhostBrowser Sync Server.

The sync server never handles plaintext archives.  All payloads treat
``archive_b64`` as opaque base64 bytes that were AES-encrypted by the client.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, field_validator


MAX_METADATA_JSON_BYTES = 10 * 1024
MAX_METADATA_NESTING_DEPTH = 5


def _metadata_exceeds_depth_limit(value: Any, depth: int = 0) -> bool:
    """Return whether a JSON-compatible value nests beyond the allowed depth."""
    if not isinstance(value, (dict, list)):
        return False
    if depth > MAX_METADATA_NESTING_DEPTH:
        return True
    values = value.values() if isinstance(value, dict) else value
    return any(_metadata_exceeds_depth_limit(item, depth + 1) for item in values)


class SyncArchiveUpload(BaseModel):
    """Client upload request for an encrypted profile archive."""

    archive_b64: str = Field(
        ...,
        min_length=1,
        description="Base64-encoded AES-encrypted .ghost archive bytes.",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Optional, non-secret metadata about the archive.",
    )

    @field_validator("archive_b64")
    @classmethod
    def _b64_looks_reasonable(cls, value: str) -> str:
        # Extra newlines are harmless; strip them before length checks.
        value = value.strip()
        if len(value) < 4:
            raise ValueError("archive_b64 is too short to be valid base64")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata_is_bounded(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Reject metadata that would be unsafe to persist as SQLite JSON."""
        if value is None:
            return value
        if _metadata_exceeds_depth_limit(value):
            raise ValueError(
                f"metadata nesting must not exceed {MAX_METADATA_NESTING_DEPTH} levels"
            )
        try:
            json_size = len(json.dumps(value).encode("utf-8"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("metadata must be JSON serializable") from exc
        if json_size > MAX_METADATA_JSON_BYTES:
            raise ValueError(
                f"metadata JSON must not exceed {MAX_METADATA_JSON_BYTES} bytes"
            )
        return value


class SyncArchiveResponse(BaseModel):
    """Response returned after a successful upload."""

    status: Literal["stored", "ok"] = "stored"
    stored_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of the store operation.",
    )
    size: int = Field(..., ge=0, description="Decoded archive size in bytes.")


class SyncArchiveRetrieve(BaseModel):
    """Response returned when fetching a stored archive."""

    status: Literal["ok"] = "ok"
    tenant_id: str
    profile_id: str
    device_id: str
    archive_b64: str
    stored_at: Optional[str] = None
    size: int = Field(..., ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SyncDeviceVersion(BaseModel):
    """A single device/version entry in a profile listing."""

    device_id: str
    stored_at: Optional[str] = None
    size: int = Field(..., ge=0)


class SyncProfileList(BaseModel):
    """Response returned when listing versions/devices for a profile."""

    status: Literal["ok"] = "ok"
    tenant_id: str
    profile_id: str
    devices: list[SyncDeviceVersion] = Field(default_factory=list)


class SyncStatusResponse(BaseModel):
    """Generic status response for delete/revoke operations."""

    status: Literal["ok", "not_found"] = "ok"
    message: str = ""


class RevokeDeviceRequest(BaseModel):
    """Optional payload for a device revocation request."""

    reason: Optional[str] = None
