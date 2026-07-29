"""Shared FastAPI authorization dependency.

All sensitive local-management endpoints require the operator-configured
``GHOSTBROWSER_ADMIN_TOKEN`` supplied in the ``X-Admin-Token`` header.

Routes that are safe for unauthenticated callers (health, CSRF token, version,
identity, static files) remain unprotected at router level.
"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request


ADMIN_TOKEN_ENV = "GHOSTBROWSER_ADMIN_TOKEN"
ADMIN_TOKEN_HEADER = "X-Admin-Token"


def _admin_token_configured() -> bool:
    return bool(os.environ.get(ADMIN_TOKEN_ENV, "").strip())


def require_admin_token(request: Request) -> None:
    """Fail-closed dependency: require a configured admin token header.

    Returns 503 if the server has no admin token configured.
    Returns 401 if the request omits the token.
    Returns 403 if the supplied token does not match.
    """
    expected = os.environ.get(ADMIN_TOKEN_ENV, "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin token is not configured. Set GHOSTBROWSER_ADMIN_TOKEN to enable protected endpoints.",
        )
    token = request.headers.get(ADMIN_TOKEN_HEADER, "").strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail=f"Admin token required in {ADMIN_TOKEN_HEADER} header",
        )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")
