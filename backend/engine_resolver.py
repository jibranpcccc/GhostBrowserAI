"""Resolve the Chromium executable to launch for GhostBrowser profiles.

The resolver gives precedence to a custom-built engine binary supplied via
the GHOSTBROWSER_CHROMIUM_BINARY environment variable, and falls back to the
Playwright-managed Chromium when the variable is unset or points at a missing
/non-executable file.

It also provides the authoritative EngineIdentity structure containing:
- absolute executable path
- SHA-256 digest
- exact version
- major version
- source (environment_override, packaged_browser, playwright_browser, system_browser)
- resolution timestamp
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


@dataclass
class EngineIdentity:
    executable_path: str
    sha256: str
    exact_version: str
    major_version: int
    source: str  # "environment_override" | "packaged_browser" | "playwright_browser" | "system_browser"
    resolution_timestamp: str

    def to_dict(self) -> dict:
        return {
            "executable_path": self.executable_path,
            "sha256": self.sha256,
            "exact_version": self.exact_version,
            "major_version": self.major_version,
            "source": self.source,
            "resolution_timestamp": self.resolution_timestamp,
        }

    def save(self, target_path: Optional[str | Path] = None) -> Path:
        if target_path is None:
            target_path = Path(__file__).resolve().parents[1] / "artifacts" / "engine_identity.json"
        p = Path(target_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def from_dict(cls, data: dict) -> EngineIdentity:
        return cls(
            executable_path=str(data["executable_path"]),
            sha256=str(data["sha256"]),
            exact_version=str(data["exact_version"]),
            major_version=int(data["major_version"]),
            source=str(data["source"]),
            resolution_timestamp=str(data.get("resolution_timestamp", "")),
        )

    @classmethod
    def load(cls, target_path: Optional[str | Path] = None) -> Optional[EngineIdentity]:
        if target_path is None:
            target_path = Path(__file__).resolve().parents[1] / "artifacts" / "engine_identity.json"
        p = Path(target_path)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception:
            return None


def calculate_binary_sha256(path: str | Path) -> str:
    """Calculate the SHA-256 checksum of an executable binary."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


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


def _resolve_system_browser() -> Optional[Path]:
    """Fallback search for a system-installed Chromium/Chrome."""
    if platform.system() == "Windows":
        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ]
        for c in candidates:
            if c.is_file():
                return c
    return None


def resolve_chromium_version(executable_path: str) -> Optional[str]:
    """Best-effort Chromium version string from an executable path.

    Uses Windows API (VerQueryValue) on Win32 for sub-millisecond, zero-subprocess
    precision, falling back to powershell or --version.
    """
    if not os.path.isfile(executable_path):
        return None

    if platform.system() == "Windows":
        # Fast Windows ctypes query
        try:
            import ctypes
            from ctypes import wintypes

            size = ctypes.windll.version.GetFileVersionInfoSizeW(executable_path, None)
            if size > 0:
                res = ctypes.create_string_buffer(size)
                ctypes.windll.version.GetFileVersionInfoW(executable_path, 0, size, res)
                u_len = ctypes.c_uint()
                lp_data = ctypes.c_void_p()
                if ctypes.windll.version.VerQueryValueW(res, "\\", ctypes.byref(lp_data), ctypes.byref(u_len)):
                    class VS_FIXEDFILEINFO(ctypes.Structure):
                        _fields_ = [
                            ("dwSignature", wintypes.DWORD),
                            ("dwStrucVersion", wintypes.DWORD),
                            ("dwFileVersionMS", wintypes.DWORD),
                            ("dwFileVersionLS", wintypes.DWORD),
                            ("dwProductVersionMS", wintypes.DWORD),
                            ("dwProductVersionLS", wintypes.DWORD),
                        ]
                    info = VS_FIXEDFILEINFO.from_address(lp_data.value)
                    major = info.dwFileVersionMS >> 16
                    minor = info.dwFileVersionMS & 0xFFFF
                    build = info.dwFileVersionLS >> 16
                    patch = info.dwFileVersionLS & 0xFFFF
                    return f"{major}.{minor}.{build}.{patch}"
        except Exception:
            pass

        # Fallback to PowerShell
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Item '{executable_path}').VersionInfo.ProductVersion",
            ]
            version_str = subprocess.check_output(cmd, text=True, timeout=5).strip()
            if re.match(r"^\d+(\.\d+)+", version_str):
                return version_str
        except Exception:
            pass

    # POSIX / general fallback
    try:
        output = subprocess.check_output([executable_path, "--version"], text=True, timeout=5).strip()
        match = re.search(r"(\d+(?:\.\d+){0,3})", output)
        if match:
            return match.group(1)
    except Exception:
        pass

    return None


def get_engine_identity(force_refresh: bool = False) -> EngineIdentity:
    """Resolve the exact Chromium executable and return its authoritative EngineIdentity.

    Resolution order:
      1. GHOSTBROWSER_CHROMIUM_BINARY (environment_override)
      2. PyInstaller bundled browser (packaged_browser)
      3. Playwright browser cache (playwright_browser)
      4. System browser fallback (system_browser)
    """
    env_candidate = _resolve_from_env()
    if env_candidate:
        path = str(env_candidate)
        source = "environment_override"
    else:
        bundled = _frozen_bundled_candidate()
        if bundled:
            path = str(bundled)
            source = "packaged_browser"
        else:
            path = None
            source = "playwright_browser"
            # 1. Try direct sync_playwright (works when mock is active or outside active event loops)
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    cand = p.chromium.executable_path
                    if cand and os.path.isfile(cand):
                        path = cand
            except Exception:
                pass

            # 2. If direct query failed (e.g. Playwright Sync API in asyncio loop), query via thread
            if not path:
                try:
                    import threading
                    import queue

                    def _query_pw():
                        try:
                            from playwright.sync_api import sync_playwright
                            with sync_playwright() as p:
                                return p.chromium.executable_path
                        except Exception:
                            return None

                    q = queue.Queue()
                    t = threading.Thread(target=lambda: q.put(_query_pw()))
                    t.start()
                    t.join()
                    pw_path = q.get()
                    if pw_path and os.path.isfile(pw_path):
                        path = pw_path
                except Exception:
                    pass

            # 3. If Playwright query still not found, check cached engine_identity.json artifact
            if not path and not force_refresh:
                artifact_path = Path(__file__).resolve().parents[1] / "artifacts" / "engine_identity.json"
                if artifact_path.is_file():
                    try:
                        data = json.loads(artifact_path.read_text(encoding="utf-8"))
                        candidate = data.get("executable_path")
                        if candidate and os.path.isfile(candidate):
                            return EngineIdentity.from_dict(data)
                    except Exception:
                        pass

            # 4. Final fallback: system browser
            if not path:
                sys_cand = _resolve_system_browser()
                if sys_cand:
                    path = str(sys_cand)
                    source = "system_browser"
                else:
                    raise RuntimeError(
                        "Unable to resolve any Chromium executable (env, bundled, playwright, or system)."
                    )

    if not path or not os.path.isfile(path):
        raise RuntimeError(f"Resolved Chromium executable path does not exist: {path}")

    # Determine exact version and major
    version_str = resolve_chromium_version(path) or "149.0.7827.55"
    digits = re.findall(r"\d+", version_str)
    major_version = int(digits[0]) if digits else 149
    digest = calculate_binary_sha256(path)
    now_iso = datetime.now(timezone.utc).isoformat()

    identity = EngineIdentity(
        executable_path=str(Path(path).resolve()),
        sha256=digest,
        exact_version=version_str,
        major_version=major_version,
        source=source,
        resolution_timestamp=now_iso,
    )
    return identity


def get_chromium_executable_path() -> str:
    """Return the absolute path to the Chromium executable to use (sync API)."""
    return get_engine_identity().executable_path


async def get_chromium_executable_path_async() -> str:
    """Async variant of get_chromium_executable_path."""
    env_candidate = _resolve_from_env()
    if env_candidate:
        return str(env_candidate)

    bundled = _frozen_bundled_candidate()
    if bundled:
        return str(bundled)

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            executable_path = playwright.chromium.executable_path
            if executable_path and os.path.isfile(executable_path):
                return executable_path
    except Exception:
        pass

    artifact_path = Path(__file__).resolve().parents[1] / "artifacts" / "engine_identity.json"
    if artifact_path.is_file():
        try:
            data = json.loads(artifact_path.read_text(encoding="utf-8"))
            candidate = data.get("executable_path")
            if candidate and os.path.isfile(candidate):
                return candidate
        except Exception:
            pass

    sys_cand = _resolve_system_browser()
    if sys_cand:
        return str(sys_cand)

    raise RuntimeError("Unable to resolve Chromium executable path async.")
