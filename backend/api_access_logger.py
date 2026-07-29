"""Lightweight per-site fingerprinting-API access logger (memory only)."""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from typing import Dict, List, Optional

site_access_log: Dict[str, List[Dict[str, str]]] = {}

MAX_ENTRIES_PER_ORIGIN = 100


def log_api_access(origin: str, api_name: str) -> None:
    """Append a timestamped API access entry for the given origin."""
    if not origin or not api_name:
        return
    now = datetime.now(timezone.utc).isoformat()
    entries = site_access_log.setdefault(origin, [])
    entries.append({"api": api_name, "timestamp": now})
    if len(entries) > MAX_ENTRIES_PER_ORIGIN:
        del entries[:-MAX_ENTRIES_PER_ORIGIN]


def get_site_log(origin_pattern: Optional[str] = None) -> Dict[str, List[Dict[str, str]]]:
    """Return access log grouped by origin, optionally filtered by glob pattern.

    Only the most recent MAX_ENTRIES_PER_ORIGIN entries are returned per origin.
    """
    result: Dict[str, List[Dict[str, str]]] = {}
    for origin, entries in site_access_log.items():
        if origin_pattern is None or fnmatch.fnmatch(origin, origin_pattern):
            result[origin] = entries[-MAX_ENTRIES_PER_ORIGIN:]
    return result


def clear_site_log() -> None:
    """Clear all in-memory API access logs."""
    site_access_log.clear()
