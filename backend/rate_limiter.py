"""Thread-safe, in-memory sliding-window request rate limiting."""
from __future__ import annotations

import math
import threading
import time
from typing import Callable


class SlidingWindowRateLimiter:
    """Limit each client to ``max_requests`` accepted requests per window.

    The limiter is intentionally process-local.  Deployments with multiple
    worker processes need a shared backend (such as Redis) to enforce a global
    limit across workers.
    """

    def __init__(
        self,
        window_seconds: float = 60,
        max_requests: int = 100,
        *,
        clock: Callable[[], float] = time.monotonic,
        cleanup_interval: float | None = None,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
        if max_requests <= 0:
            raise ValueError("max_requests must be greater than zero")

        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._clock = clock
        self._requests: dict[str, list[tuple[float, int]]] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = cleanup_interval if cleanup_interval is not None else window_seconds
        self._last_cleanup = 0.0

    def check(self, client_key: str) -> bool:
        """Record and allow a request, or return ``False`` if it is limited."""
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            entries = self._requests.setdefault(client_key, [])
            self._prune_entries_locked(entries, now)
            if sum(count for _, count in entries) >= self.max_requests:
                return False
            entries.append((now, 1))
            return True

    def get_headers(self, client_key: str) -> dict[str, str]:
        """Return response headers describing the current client quota."""
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            entries = self._requests.get(client_key, [])
            self._prune_entries_locked(entries, now)
            used = sum(count for _, count in entries)
            retry_after = 0
            if entries:
                retry_after = max(0, math.ceil(entries[0][0] + self.window_seconds - now))
            return {
                "RateLimit-Limit": str(self.max_requests),
                "RateLimit-Remaining": str(max(0, self.max_requests - used)),
                "RateLimit-Reset": str(retry_after),
                "Retry-After": str(retry_after),
            }

    def reset(self) -> None:
        """Clear tracked requests (primarily useful for isolated tests)."""
        with self._lock:
            self._requests.clear()
            self._last_cleanup = self._clock()

    def _cleanup_locked(self, now: float) -> None:
        if now - self._last_cleanup < self._cleanup_interval:
            return
        for client_key, entries in list(self._requests.items()):
            self._prune_entries_locked(entries, now)
            if not entries:
                del self._requests[client_key]
        self._last_cleanup = now

    def _prune_entries_locked(self, entries: list[tuple[float, int]], now: float) -> None:
        cutoff = now - self.window_seconds
        while entries and entries[0][0] <= cutoff:
            entries.pop(0)
