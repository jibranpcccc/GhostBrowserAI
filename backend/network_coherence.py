"""Truthful network, IP, and timezone/locale coherence validator for GhostBrowser AI."""

from __future__ import annotations

from typing import Dict, Any, Optional, List


# Mapping of timezone prefixes to expected geographic country codes/names
TIMEZONE_REGION_MAP = {
    "America/New_York": ["US", "USA", "United States"],
    "America/Chicago": ["US", "USA", "United States"],
    "America/Los_Angeles": ["US", "USA", "United States"],
    "America/Denver": ["US", "USA", "United States"],
    "America/Toronto": ["CA", "CAN", "Canada"],
    "Europe/London": ["GB", "GBR", "United Kingdom"],
    "Europe/Paris": ["FR", "FRA", "France"],
    "Europe/Berlin": ["DE", "DEU", "Germany"],
    "Asia/Tokyo": ["JP", "JPN", "Japan"],
    "Asia/Karachi": ["PK", "PAK", "Pakistan"],
    "Asia/Dubai": ["AE", "ARE", "United Arab Emirates"],
}


def validate_network_coherence(
    profile_data: dict,
    observed_public_ip: Optional[str] = None,
    observed_country: Optional[str] = None,
    observed_webrtc_ip: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate network coherence truthfully.
    Does not conceal or fabricate network identity.
    Returns structured analysis with status: COHERENT, POSSIBLE_MISMATCH, or UNKNOWN.
    """
    profile_tz = profile_data.get("timezone", "UTC")
    profile_locale = profile_data.get("locale", "en-US")
    proxy = profile_data.get("proxy") or profile_data.get("proxy_pin")
    
    is_proxied = bool(proxy)
    proxy_server = None
    if isinstance(proxy, dict):
        proxy_server = proxy.get("server")
    elif isinstance(proxy, str):
        proxy_server = proxy

    # Expected regions for configured timezone
    expected_regions = TIMEZONE_REGION_MAP.get(profile_tz, [])

    status = "UNKNOWN"
    notes = []

    if not is_proxied:
        connection_mode = "direct"
        notes.append("Profile is using a direct network connection (no proxy configured).")
        if observed_country and expected_regions:
            matches_tz = any(observed_country.upper() == r.upper() for r in expected_regions)
            if matches_tz:
                status = "COHERENT"
                notes.append(f"Direct connection location ({observed_country}) matches timezone ({profile_tz}).")
            else:
                status = "POSSIBLE_MISMATCH"
                notes.append(
                    f"Direct connection location ({observed_country}) does not align with configured timezone ({profile_tz}). "
                    f"This is expected on direct connections without an outbound proxy."
                )
        else:
            status = "UNKNOWN"
            notes.append("Outbound public geolocation was not provided or could not be determined.")
    else:
        connection_mode = "proxied"
        notes.append(f"Profile is configured with proxy: {proxy_server}")
        if observed_country and expected_regions:
            matches_tz = any(observed_country.upper() == r.upper() for r in expected_regions)
            if matches_tz:
                status = "COHERENT"
                notes.append(f"Proxy egress location ({observed_country}) aligns with timezone ({profile_tz}).")
            else:
                status = "POSSIBLE_MISMATCH"
                notes.append(f"Proxy egress location ({observed_country}) does not match timezone ({profile_tz}).")
        else:
            status = "UNKNOWN"
            notes.append("Proxy egress location could not be verified against external trusted source.")

    webrtc_leak_detected = False
    if observed_webrtc_ip and observed_public_ip:
        if observed_webrtc_ip != observed_public_ip:
            webrtc_leak_detected = True
            notes.append(f"WebRTC IP ({observed_webrtc_ip}) diverges from HTTP egress IP ({observed_public_ip}).")

    return {
        "status": status,
        "connection_mode": connection_mode,
        "is_proxied": is_proxied,
        "proxy_server": proxy_server,
        "configured_timezone": profile_tz,
        "configured_locale": profile_locale,
        "observed_public_ip": observed_public_ip,
        "observed_country": observed_country,
        "webrtc_leak_detected": webrtc_leak_detected,
        "notes": notes,
    }


def validate_webrtc_candidates(candidates: List[str]) -> Dict[str, Any]:
    """Validate WebRTC ICE candidates for private IP leaks.
    
    Returns dict with is_safe (bool), leaked_ips (list), and mdns_count (int).
    """
    import ipaddress
    leaked_ips = []
    mdns_count = 0
    for cand in candidates:
        parts = cand.strip().split()
        if len(parts) >= 8 and parts[6] == "typ" and parts[7] == "host":
            addr = parts[4]
            if addr.endswith(".local"):
                mdns_count += 1
            else:
                try:
                    ip = ipaddress.ip_address(addr)
                    if ip.is_private or ip.is_loopback:
                        leaked_ips.append(str(ip))
                except ValueError:
                    pass
    return {
        "is_safe": len(leaked_ips) == 0,
        "leaked_ips": leaked_ips,
        "mdns_count": mdns_count,
    }

