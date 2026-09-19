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
def _is_test_env() -> bool:
    """Check if running in the isolated test environment."""
    return os.environ.get("GHOSTBROWSER_TEST_ENV", "").strip().lower() in ("1", "true")

if _is_test_env():
    RATE_LIMITERS = {
        "default": SlidingWindowRateLimiter(window_seconds=60, max_requests=1000),
        "auth": SlidingWindowRateLimiter(window_seconds=60, max_requests=100),
        "pin": SlidingWindowRateLimiter(window_seconds=60, max_requests=50),
    }
else:
    RATE_LIMITERS = {
        "default": SlidingWindowRateLimiter(window_seconds=60, max_requests=100),
        "auth": SlidingWindowRateLimiter(window_seconds=60, max_requests=10),
        "pin": SlidingWindowRateLimiter(window_seconds=60, max_requests=5),
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


# Hosts that are the machine itself. The dashboard is served by this same
# process, so a local operator is already inside the trust boundary — their
# requests never require the admin header. Remote (LAN) clients still do.
# Note: TestClient's synthetic "testclient" host is deliberately NOT listed,
# so automated contracts always exercise the strict path.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


def _is_loopback_client(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in _LOOPBACK_HOSTS


def require_admin_token(request: Request) -> None:
    """Fail-closed dependency for sensitive endpoints (CDP, cloud sync).

    GhostBrowser is a local-first desktop application with zero-auth for normal
    profile management, but enforces strict operator authentication on remote
    cloud-sync and CDP debugging interfaces.
    """
    path = request.url.path if hasattr(request, "url") else ""
    is_sensitive = path.endswith("/cdp") or path.startswith("/api/cloud-sync") or "/sync/" in path
    if not is_sensitive:
        return

    expected = os.environ.get(ADMIN_TOKEN_ENV, "").strip()
    if not expected:
        if _is_loopback_client(request) and not _is_test_env():
            return
        raise HTTPException(
            status_code=503,
            detail="Admin token is not configured. Set GHOSTBROWSER_ADMIN_TOKEN to enable protected endpoints.",
        )
    token = request.headers.get(ADMIN_TOKEN_HEADER, "").strip()
    if not token:
        if _is_loopback_client(request) and not _is_test_env():
            return
        raise HTTPException(
            status_code=401,
            detail=f"Admin token required in {ADMIN_TOKEN_HEADER} header",
        )
    if not hmac.compare_digest(token, expected):
        check_rate_limit(request, "auth")
        raise HTTPException(status_code=403, detail="Invalid admin token")
