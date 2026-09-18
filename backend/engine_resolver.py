"""Resolve the Chromium executable to launch for GhostBrowser profiles.

The resolver gives precedence to a custom-built engine binary supplied via
the GHOSTBROWSER_CHROMIUM_BINARY environment variable, and falls back to the
Playwright-managed Chromium when the variable is unset or points at a missing
/non-executable file.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Optional


def _frozen_bundled_candidate() -> Optional[Path]:
    """Chromium shipped next to the executable in frozen (PyInstaller) builds."""
    if not getattr(sys, "frozen", False):
        return None
    base = Path(sys.executable).parent / "playwright-browsers"
    if platform.system() == "Windows":
        candidates = [base / "chrome-win64" / "chrome.exe", base / "chrome-win" / "chrome.exe"]
    elif platform.system() == "Darwin":
        candidates = [
            base / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
            base / "chrome-mac-arm64" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
        ]
    else:
        candidates = [base / "chrome-linux64" / "chrome", base / "chrome-linux" / "chrome"]
    for candidate in candidates:
        if _is_executable(candidate):
            return candidate
    return None


def _is_executable(path: Path) -> bool:
    """Return True if the path looks executable on the current platform."""
    if not path.is_file():
        return False
    if platform.system() == "Windows":
        # os.access(path, os.X_OK) is not meaningful on Windows; use PATHEXT
        # instead. An empty extension is also accepted, because users may
        # reference a stub script or symlink without an extension.
        suffix = path.suffix.lower()
        pathext = [ext.lower() for ext in os.environ.get("PATHEXT", ".EXE").split(";")]
        return suffix in pathext or suffix == ""
    return os.access(str(path), os.X_OK)


def _resolve_from_env() -> Optional[Path]:
    """If the env variable points at a usable file, return its absolute path."""
    env_path = os.environ.get("GHOSTBROWSER_CHROMIUM_BINARY", "").strip()
    if not env_path:
        return None
    candidate = Path(env_path).expanduser().resolve()
    if _is_executable(candidate):
        return candidate
    return None


def get_chromium_executable_path() -> str:
    """Return the absolute path to the Chromium executable to use (sync API).

    Resolution order:
      1. ``GHOSTBROWSER_CHROMIUM_BINARY`` if set and the file exists and is
         executable (platform-dependent check).
      2. The executable reported by Playwright's Chromium browser cache.

    Returns:
        An absolute filesystem path to a Chromium executable.

    Raises:
        RuntimeError: If no Chromium executable can be resolved.
    """
    env_candidate = _resolve_from_env()
    if env_candidate:
        return str(env_candidate)

    bundled = _frozen_bundled_candidate()
    if bundled:
        return str(bundled)

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover - import failure path
        raise RuntimeError(
            "Unable to import Playwright for Chromium fallback resolution."
        ) from exc

    with sync_playwright() as playwright:
        executable_path = playwright.chromium.executable_path
    if not executable_path or not os.path.isfile(executable_path):
        raise RuntimeError(
            "GHOSTBROWSER_CHROMIUM_BINARY is not set and Playwright did not "
            "provide a Chromium executable path."
        )
    return executable_path


async def get_chromium_executable_path_async() -> str:
    """Async variant of :func:`get_chromium_executable_path`.

    This must be called from within an asyncio event loop (the normal
    GhostBrowser runtime path). It uses the async Playwright API for the
    fallback so it does not conflict with the running loop.
    """
    env_candidate = _resolve_from_env()
    if env_candidate:
        return str(env_candidate)

    bundled = _frozen_bundled_candidate()
    if bundled:
        return str(bundled)

    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover - import failure path
        raise RuntimeError(
            "Unable to import Playwright for Chromium fallback resolution."
        ) from exc

    async with async_playwright() as playwright:
        executable_path = playwright.chromium.executable_path
    if not executable_path or not os.path.isfile(executable_path):
        raise RuntimeError(
            "GHOSTBROWSER_CHROMIUM_BINARY is not set and Playwright did not "
            "provide a Chromium executable path."
        )
    return executable_path


def resolve_chromium_version(executable_path: str) -> Optional[str]:
    """Best-effort Chromium version string from an executable path.

    Uses ``--version`` on POSIX and a Windows file-version query on Win32.
    This is intentionally simple (std-library only) and mirrors the logic used
    by ``backend.config`` so the engine build can be version-checked.
    """
    import re
    import subprocess

    if not os.path.isfile(executable_path):
        return None

    if platform.system() == "Windows":
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Item '{executable_path}').VersionInfo.ProductVersion",
            ]
            version_str = subprocess.check_output(cmd, text=True).strip()
            if re.match(r"\d+(\.\d+)", version_str):
                return version_str
        except Exception:
            pass

    try:
        output = subprocess.check_output([executable_path, "--version"], text=True).strip()
        match = re.search(r"(\d+(?:\.\d+){0,3})", output)
        if match:
            return match.group(1)
    except Exception:
        pass

    return None
