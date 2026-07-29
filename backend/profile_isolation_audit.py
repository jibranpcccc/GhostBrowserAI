"""Offline audit of profile storage and network-policy isolation."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from backend.browser_manager import active_browsers, profile_opted_origins, session_opted_origins
from backend.profile_manager import profile_manager

_STORAGE_NAMES = {
    "Cookies", "Cache", "Code Cache", "Local Storage", "IndexedDB",
    "Service Worker", "Session Storage", "Storage", "Network",
}
_VALID_WEBRTC_MODES = {"protected", "disabled"}


def _canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _storage_artifacts(root: Path) -> set[str]:
    """Collect artifact paths only; never inspect profile storage contents."""
    found: set[str] = set()
    if not root.is_dir():
        return found
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            candidate = Path(current, name)
            if name in _STORAGE_NAMES or any(part in _STORAGE_NAMES for part in candidate.parts):
                found.add(_canonical(str(candidate)))
    return found


def audit_profile_isolation(profile_ids: list[str]) -> dict:
    """Audit existing metadata and disk/runtime records without launching browsers."""
    checks = {
        "profiles_exist": True,
        "dedicated_data_directories": True,
        "storage_isolation": True,
        "first_party_storage_boundaries": True,
        "temp_artifact_isolation": True,
        "network_policy_enforcement": True,
        "runtime_registry_isolation": True,
    }
    findings: list[str] = []
    profiles: dict[str, dict[str, Any]] = {}
    roots: dict[str, str] = {}
    artifacts: dict[str, set[str]] = {}

    for profile_id in profile_ids:
        profile = profile_manager.get_profile(profile_id)
        if not isinstance(profile, dict):
            checks["profiles_exist"] = False
            findings.append(f"missing_profile:{profile_id}")
            continue
        profiles[profile_id] = profile
        path = profile.get("path")
        if not isinstance(path, str) or not path or not os.path.isdir(path):
            checks["dedicated_data_directories"] = False
            findings.append(f"missing_data_directory:{profile_id}")
            continue
        roots[profile_id] = _canonical(path)
        artifacts[profile_id] = _storage_artifacts(Path(path))

    for profile_id, root in roots.items():
        for other_id, other_root in roots.items():
            if profile_id >= other_id:
                continue
            if root == other_root or root.startswith(other_root + os.sep) or other_root.startswith(root + os.sep):
                checks["dedicated_data_directories"] = False
                findings.append(f"shared_or_nested_data_directory:{profile_id}:{other_id}")
            if artifacts[profile_id] & artifacts[other_id]:
                checks["storage_isolation"] = False
                checks["temp_artifact_isolation"] = False
                findings.append(f"shared_storage_artifact:{profile_id}:{other_id}")

    for profile_id, profile in profiles.items():
        # First-party Accept-CH records are scoped by profile ID, and malformed
        # records are evidence that the boundary can no longer be trusted.
        for records in (profile_opted_origins.get(profile_id), session_opted_origins.get(profile_id)):
            if records is not None and not isinstance(records, dict):
                checks["first_party_storage_boundaries"] = False
                findings.append(f"invalid_first_party_record:{profile_id}")

        advanced = profile.get("advanced")
        if not isinstance(advanced, dict):
            checks["network_policy_enforcement"] = False
            findings.append(f"invalid_advanced_settings:{profile_id}")
            continue
        if str(advanced.get("webrtc_mode", "protected")).lower() not in _VALID_WEBRTC_MODES:
            checks["network_policy_enforcement"] = False
            findings.append(f"unsafe_webrtc_policy:{profile_id}")
        if advanced.get("privacy_mode") == "high" and not advanced.get("block_service_workers"):
            checks["network_policy_enforcement"] = False
            findings.append(f"high_privacy_service_workers_not_blocked:{profile_id}")
        proxy = profile.get("proxy")
        if proxy is not None and (not isinstance(proxy, dict) or not proxy.get("server")):
            checks["network_policy_enforcement"] = False
            findings.append(f"invalid_proxy_policy:{profile_id}")

    # The in-memory first-party registries must not reuse a mutable record
    # between profiles, which would let a later update cross the boundary.
    for records_by_profile in (profile_opted_origins, session_opted_origins):
        for profile_id in profiles:
            record = records_by_profile.get(profile_id)
            if record is None:
                continue
            for other_id in profiles:
                if profile_id >= other_id:
                    continue
                if record is records_by_profile.get(other_id):
                    checks["first_party_storage_boundaries"] = False
                    findings.append(f"shared_first_party_record:{profile_id}:{other_id}")

    orphaned = set(active_browsers) - set(profile_manager.list_profile_ids())
    if orphaned:
        checks["runtime_registry_isolation"] = False
        findings.extend(f"orphan_runtime_profile:{profile_id}" for profile_id in sorted(orphaned))
    for profile_id in set(profile_ids):
        if profile_id in active_browsers and profile_id not in profiles:
            checks["runtime_registry_isolation"] = False
            findings.append(f"runtime_for_missing_profile:{profile_id}")

    return {
        "passed": all(checks.values()),
        "checks": checks,
        "findings": findings,
        "profile_count": len(profile_ids),
    }
