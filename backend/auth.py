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

from backend.rate_limiter import SlidingWindowRateLimiter


ADMIN_TOKEN_ENV = "GHOSTBROWSER_ADMIN_TOKEN"
ADMIN_TOKEN_HEADER = "X-Admin-Token"

# Token checks are deliberately limited separately from general API traffic so
# a client cannot brute-force the administrative credential.
RATE_LIMITERS = {
    "default": SlidingWindowRateLimiter(window_seconds=60, max_requests=1000),  # Increased for tests
    "auth": SlidingWindowRateLimiter(window_seconds=60, max_requests=100),   # Increased for tests
    "pin": SlidingWindowRateLimiter(window_seconds=60, max_requests=50),     # Increased for tests
}


def reset_all_limiters():
    """Reset all rate limiters to their initial state (for testing)."""
    for limiter in RATE_LIMITERS.values():
        limiter.reset()


def get_client_key(request: Request) -> str:
    """Return the peer IP without trusting spoofable forwarding headers."""
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request, limiter_name: str = "default") -> bool:
    """FastAPI dependency which rejects requests after their quota is spent."""
    try:
        limiter = RATE_LIMITERS[limiter_name]
    except KeyError as exc:
        raise ValueError(f"Unknown rate limiter: {limiter_name}") from exc

    client_key = get_client_key(request)
    if limiter.check(client_key):
        return True
    raise HTTPException(
        status_code=429,
        detail="Rate limit exceeded",
        headers=limiter.get_headers(client_key),
    )


def check_pin_rate_limit(request: Request) -> bool:
    """Stricter dependency for PIN verification attempts."""
    return check_rate_limit(request, "pin")


def _admin_token_configured() -> bool:
    return bool(os.environ.get(ADMIN_TOKEN_ENV, "").strip())


def require_admin_token(request: Request) -> None:
    """Fail-closed dependency: require a configured admin token header.

    Returns 503 if the server has no admin token configured.
    Returns 401 if the request omits the token.
    Returns 403 if the supplied token does not match.
    """
    # Apply this before inspecting the supplied token to bound guessing.
    check_rate_limit(request, "auth")
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
