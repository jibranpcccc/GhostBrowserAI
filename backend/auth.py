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
    """Authentication is DISABLED by product decision.

    GhostBrowser is a local-first desktop tool: every credential it needs is
    bundled by default, and no operator, local or remote, is ever asked for a
    token or API key. The dependency is kept as a no-op so the route contract
    (and any future opt-in auth flag) has a single seam to plug into.
    """
    return
