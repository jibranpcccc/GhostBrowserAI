import sys
import os

# Default timeout for AI fingerprint generation (seconds).
AI_GENERATION_TIMEOUT = 60.0

def get_base_dir():
    """
    Returns the absolute path to the root directory of the application.
    If running as a PyInstaller executable, returns the directory containing the .exe.
    If running as a Python script, returns the directory containing 'backend', 'frontend', etc.
    """
    if getattr(sys, 'frozen', False):
        # Running in a PyInstaller bundle
        return os.path.dirname(sys.executable)
    else:
        # Running in normal Python environment (one level up from backend)
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def get_data_dir(*paths):
    """
    Returns an absolute path relative to the base user data directory.
    Creates the directory if it doesn't exist (unless it has an extension).
    """
    base = get_base_dir()
    full_path = os.path.join(base, *paths)

    # If the path looks like a directory (no extension), ensure it exists
    if not os.path.splitext(full_path)[1]:
        os.makedirs(full_path, exist_ok=True)

    return full_path

def get_bundled_dir(*paths):
    """
    Returns an absolute path to read-only bundled assets (like frontend html, default extensions).
    In PyInstaller, these extract to sys._MEIPASS.
    """
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, *paths)

_cached_chromium_version = None
_cached_chromium_path = None

def _resolve_chromium_info():
    global _cached_chromium_version, _cached_chromium_path
    if _cached_chromium_version is not None and _cached_chromium_path is not None:
        return

    import platform
    import subprocess
    import re
    import os
    import threading
    import queue
    from playwright.sync_api import sync_playwright
    from backend.engine_resolver import get_chromium_executable_path

    exe_path = None

    # Prefer a custom-built Chromium engine supplied by the user. This lets
    # GhostBrowser use a hardened fork while still falling back to the
    # Playwright-managed binary when no custom build is configured.
    try:
        engine_path = get_chromium_executable_path()
        if engine_path and os.path.isfile(engine_path):
            exe_path = engine_path
    except Exception:
        pass

    # Frozen distributions carry a validated Chromium tree next to the EXE.
    # Prefer it over any browser installed for a different Playwright release.
    if not exe_path and getattr(sys, "frozen", False):
        bundled_candidate = os.path.join(
            os.path.dirname(sys.executable),
            "playwright-browsers",
            "chrome-win64",
            "chrome.exe",
        )
        if os.path.isfile(bundled_candidate):
            exe_path = bundled_candidate

    # Run sync_playwright in a separate thread to ensure event-loop safety
    def _query_playwright():
        try:
            with sync_playwright() as p:
                return p.chromium.executable_path
        except Exception:
            return None

    if not exe_path:
        try:
            q = queue.Queue()
            t = threading.Thread(target=lambda: q.put(_query_playwright()))
            t.start()
            t.join()
            exe_path = q.get()
        except Exception:
            pass

    # Fallback to searching Playwright directory structure manually
    if not exe_path or not os.path.exists(exe_path):
        path_candidates = []
        if platform.system() == "Windows":
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            if local_app_data:
                ms_playwright_dir = os.path.join(local_app_data, "ms-playwright")
                if os.path.exists(ms_playwright_dir):
                    subdirs = sorted(os.listdir(ms_playwright_dir), reverse=True)
                    for subdir in subdirs:
                        for win_dir in ["chrome-win64", "chrome-win"]:
                            candidate = os.path.join(ms_playwright_dir, subdir, win_dir, "chrome.exe")
                            if os.path.exists(candidate):
                                path_candidates.append(candidate)
                                break
        for candidate in path_candidates:
            if os.path.exists(candidate):
                exe_path = candidate
                break

    if exe_path and os.path.exists(exe_path):
        _cached_chromium_path = exe_path
        if platform.system() == "Windows":
            try:
                cmd = f'powershell -NoProfile -Command "(Get-Item \'{exe_path}\').VersionInfo.ProductVersion"'
                version_str = subprocess.check_output(cmd, shell=True, text=True).strip()
                if version_str:
                    _cached_chromium_version = version_str
                    return
            except Exception:
                pass
        else:
            try:
                res = subprocess.check_output([exe_path, "--version"], text=True).strip()
                match = re.search(r'(\d+\.\d+\.\d+\.\d+)', res)
                if match:
                    _cached_chromium_version = match.group(1)
                    return
            except Exception:
                pass

    # Fallback to querying command line directly
    for command in ["google-chrome", "chrome", "chromium", "chromium-browser"]:
        try:
            res = subprocess.check_output([command, "--version"], text=True).strip()
            match = re.search(r'(\d+\.\d+\.\d+\.\d+)', res)
            if match:
                _cached_chromium_version = match.group(1)
                import shutil
                _cached_chromium_path = shutil.who(command) or command
                return
        except Exception:
            pass

    if not _cached_chromium_version or not _cached_chromium_path:
        raise RuntimeError("FAIL-SAFE: Unable to determine the installed Playwright Chromium info. Aborting to prevent version mismatch leaks.")

def get_installed_chromium_version() -> str:
    _resolve_chromium_info()
    return _cached_chromium_version

def get_installed_chromium_path() -> str:
    _resolve_chromium_info()
    return _cached_chromium_path

def get_installed_chromium_major_version() -> int:
    version = get_installed_chromium_version()
    import re
    match = re.match(r'^(\d+)\.', version)
    if match:
        return int(match.group(1))
    raise RuntimeError("FAIL-SAFE: Unable to parse the installed Playwright Chromium major version.")
