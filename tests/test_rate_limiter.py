from fastapi import Request

from backend.auth import get_client_key
from backend.rate_limiter import SlidingWindowRateLimiter


def test_allows_requests_until_the_limit_then_blocks():
    limiter = SlidingWindowRateLimiter(window_seconds=60, max_requests=2)

    assert limiter.check("192.0.2.1")
    assert limiter.check("192.0.2.1")
    assert not limiter.check("192.0.2.1")
    assert limiter.get_headers("192.0.2.1")["RateLimit-Remaining"] == "0"
    assert limiter.get_headers("192.0.2.1")["Retry-After"] == "60"


def test_window_slides_after_old_requests_expire():
    now = [0.0]
    limiter = SlidingWindowRateLimiter(window_seconds=10, max_requests=2, clock=lambda: now[0])

    assert limiter.check("client")
    now[0] = 5.0
    assert limiter.check("client")
    assert not limiter.check("client")
    now[0] = 10.0
    assert limiter.check("client")


def test_periodic_cleanup_removes_expired_client_entries():
    now = [0.0]
    limiter = SlidingWindowRateLimiter(
        window_seconds=10,
        max_requests=1,
        clock=lambda: now[0],
        cleanup_interval=1,
    )

    assert limiter.check("old-client")
    now[0] = 11.0
    assert limiter.check("new-client")
    assert "old-client" not in limiter._requests


def test_client_key_uses_the_request_peer_ip():
    request = Request({"type": "http", "client": ("203.0.113.9", 54321), "headers": []})

    assert get_client_key(request) == "203.0.113.9"
