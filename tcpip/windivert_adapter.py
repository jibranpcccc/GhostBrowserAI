"""Windows Filter Platform / WinDivert adapter skeleton.

A real packet-level normalizer requires the WinDivert driver:
  https://reqrypt.org/windivert.html

This module provides the integration interface and docs. If WinDivert and
pywin32 are not installed, it returns informational errors.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


def _has_windivert() -> bool:
    try:
        import WinDivert  # type: ignore
        return True
    except Exception:
        pass
    try:
        import pydivert  # type: ignore
        return True
    except Exception:
        return False


def _pywin32_available() -> bool:
    try:
        import win32api  # type: ignore
        return True
    except Exception:
        return False


def _driver_install_dir() -> Path:
    return Path(__file__).resolve().parent / "windivert"


def install_wfp_filter(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Install a WFP filter to normalize outbound TCP packets.

    Driver-level packet editing is not implemented in this repository.
    Even when WinDivert/pydivert/pywin32 are importable, the operation is not
    considered successful because no actual filter configuration is performed.
    """
    if not _has_windivert() or not _pywin32_available():
        return {
            "success": False,
            "output": (
                "WinDivert/pydivert/pywin32 are not available. "
                "To enable packet-layer normalization install pydivert and pywin32 "
                f"and place WinDivert driver files in {_driver_install_dir()}."
            ),
        }
    return {
        "success": False,
        "output": "WFP filter installation is not implemented. This skeleton only exposes the interface.",
    }


def uninstall_wfp_filter() -> Dict[str, Any]:
    """Remove the WFP filter."""
    return {
        "success": False,
        "output": "WFP filter removal is not implemented. No active filter is tracked by this skeleton.",
    }


def list_filters() -> List[Dict[str, Any]]:
    """List active WFP filters."""
    return []


def get_status() -> Dict[str, Any]:
    return {
        "windivert_available": _has_windivert(),
        "pywin32_available": _pywin32_available(),
        "driver_install_dir": str(_driver_install_dir()),
    }
