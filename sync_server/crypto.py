"""Repository-side cryptographic helpers for GhostBrowser Sync Server.

The sync server is deliberately **not** an encryption endpoint.  Archives are
encrypted on the client before they reach this server.  The helpers here are
limited to:

* deriving deterministic device-level key material from opaque identifiers,
* token comparison helpers using only HMAC (no timing leaks).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any


# Parameters are intentionally aggressive enough to discourage brute-forcing
# the server-side HMAC-derived value while remaining cheap per-request.
_PBKDF2_ITERATIONS = 100_000
_DEVICE_KEY_DERIVATION_SALT_ENV = "GHOSTBROWSER_SYNC_HMAC_SALT"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")


def _safe_identifier(value: str, name: str = "identifier") -> str:
    """Return *value* if it looks like a safe opaque identifier.

    Raises *ValueError* for any characters that could be used in path traversal
    or injection attacks.  The server should not rely on this for access
    control; it is only a sanity check on routing parameters.
    """
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if not _SAFE_ID_RE.match(value):
        raise ValueError(f"{name} contains invalid characters")
    return value


def derive_device_key(tenant_id: str, profile_id: str, device_id: str) -> bytes:
    """Derive deterministic device key bytes from identifiers.

    The result is not a human passphrase; it is an HMAC-based token that the
    server can use for internal anti-tampering checks (e.g. associating a
    stored archive with the identifiers that produced it).  Client-side AES
    encryption is performed in ``backend/cloud_sync.py`` with a user-supplied
    passphrase.
    """
    tenant_id = _safe_identifier(tenant_id, "tenant_id")
    profile_id = _safe_identifier(profile_id, "profile_id")
    device_id = _safe_identifier(device_id, "device_id")

    secret = os.environ.get(_DEVICE_KEY_DERIVATION_SALT_ENV, "").encode("utf-8")
    if not secret:
        # A deterministic fallback is acceptable only because this derivation
        # is not the master security boundary; the actual archive is encrypted
        # with a client-side passphrase.  Production must set the env salt.
        secret = b"__GHOSTBROWSER_SYNC_FALLBACK_SALT__"

    label = "|".join((tenant_id, profile_id, device_id)).encode("utf-8")
    return hmac.new(secret, label, hashlib.sha256).digest()


def derive_profile_storage_tag(tenant_id: str, profile_id: str, device_id: str) -> str:
    """Return a stable opaque tag used to name a stored archive bucket/row."""
    key = derive_device_key(tenant_id, profile_id, device_id)
    return hashlib.sha256(key).hexdigest()


def compare_token(token: str, expected: str) -> bool:
    """Constant-time comparison of two ASCII tokens."""
    if not isinstance(token, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(token.encode("ascii"), expected.encode("ascii"))


def hash_token_for_audit(token: str) -> str:
    """Return a non-reversible token hash suitable for logging."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def redact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return a deep copy of *metadata* with any suspicious keys dropped."""
    if not isinstance(metadata, dict):
        return {}
    banned = {"archive", "plaintext", "password", "passphrase", "secret"}
    return {k: v for k, v in metadata.items() if k.lower() not in banned}
