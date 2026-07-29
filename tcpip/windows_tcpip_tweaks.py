"""Windows TCP/IP parameter helpers.

All set operations require administrator privileges. Each function returns a
safe dict with ``success`` and ``output`` keys even on failure.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Dict


def _run(cmd: list[str]) -> Dict[str, Any]:
    exe = shutil.which(cmd[0])
    if not exe:
        return {"success": False, "output": f"{cmd[0]} not found"}
    try:
        result = subprocess.run(
            [exe] + cmd[1:],
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
        )
        return {
            "success": result.returncode == 0,
            "output": (result.stdout or result.stderr or "").strip(),
            "returncode": result.returncode,
        }
    except Exception as exc:  # pragma: no cover
        return {"success": False, "output": str(exc)}


def get_default_ttl() -> Dict[str, Any]:
    """Read the IPv4 default TTL from the registry."""
    return _run([
        "reg", "query",
        "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters",
        "/v", "DefaultTTL",
    ])


def set_default_ttl(ttl: int) -> Dict[str, Any]:
    """Set the IPv4 default TTL (Windows requires admin)."""
    try:
        ttl = int(ttl)
        if not 1 <= ttl <= 255:
            raise ValueError("TTL must be 1-255")
    except Exception as exc:
        return {"success": False, "output": str(exc)}
    return _run([
        "reg", "add",
        "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters",
        "/v", "DefaultTTL", "/t", "REG_DWORD", "/d", str(ttl), "/f",
    ])


def get_tcp_window_auto_tuning() -> Dict[str, Any]:
    """Read netsh TCP global auto-tuning level."""
    return _run(["netsh", "interface", "tcp", "show", "global"])


def set_tcp_window_auto_tuning(state: str) -> Dict[str, Any]:
    """Set netsh TCP global auto-tuning level (disabled/highlyrestricted/etc.)."""
    return _run(["netsh", "interface", "tcp", "set", "global", f"autotuninglevel={state}"])


def disable_ipv6_os() -> Dict[str, Any]:
    """Disable IPv6 globally via netsh (admin required)."""
    return _run(["netsh", "interface", "ipv6", "set", "global", "state=disabled"])


def enable_ipv6_os() -> Dict[str, Any]:
    """Enable IPv6 globally via netsh (admin required)."""
    return _run(["netsh", "interface", "ipv6", "set", "global", "state=enabled"])


def get_mtu(interface: str = "Ethernet") -> Dict[str, Any]:
    """Read MTU for an interface."""
    return _run(["netsh", "interface", "ipv4", "show", "subinterface", interface])


def set_mtu(interface: str, mtu: int) -> Dict[str, Any]:
    """Set MTU for an interface (admin required)."""
    try:
        mtu = int(mtu)
        if mtu < 576 or mtu > 65535:
            raise ValueError("MTU out of range")
    except Exception as exc:
        return {"success": False, "output": str(exc)}
    return _run(["netsh", "interface", "ipv4", "set", "subinterface", interface, f"mtu={mtu}"])


def get_all_network_settings() -> Dict[str, Any]:
    """Return a snapshot of current TCP/IP settings."""
    return {
        "default_ttl": get_default_ttl(),
        "tcp_global": get_tcp_window_auto_tuning(),
        "mtu": get_mtu(),
        "ipv6_state": disable_ipv6_os() if False else enable_ipv6_os() if False else {"success": True, "output": "Use disable_ipv6_os() or enable_ipv6_os() to change"},
    }


def normalize_windows_fingerprint(ttl: int = 128, mtu: int = 1500, autotuning: str = "disabled") -> Dict[str, Any]:
    """Apply a normalized Windows TCP/IP fingerprint. Returns per-step results."""
    return {
        "default_ttl": set_default_ttl(ttl),
        "tcp_auto_tuning": set_tcp_window_auto_tuning(autotuning),
        "mtu": set_mtu("Ethernet", mtu),
        "ipv6": disable_ipv6_os(),
    }
