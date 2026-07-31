"""Kill only GhostBrowser-owned Chromium processes that tests left behind.

Never kills the user's real Google Chrome. Matches processes whose command
line references this project's bundled Chromium (dist/GhostBrowser) or whose
--user-data-dir lives under this project's profiles_data directory.
"""
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_ghostbrowser_proc(cmdline):
    """Return True if the process belongs to this GhostBrowser project."""
    if not cmdline:
        return False
    lowered = cmdline.lower()
    # Bundled Chromium shipped with this project.
    if "dist" in lowered and "ghostbrowser" in lowered and "chrome" in lowered:
        return True
    # Playwright's cached Chromium is a shared binary, but the project only
    # drives it with a --user-data-dir under this project's profiles_data.
    if "--user-data-dir=" in lowered:
        ud = None
        for arg in cmdline.split():
            if arg.lower().startswith("--user-data-dir="):
                ud = arg.split("=", 1)[1].strip('"').strip()
                break
        if ud:
            ud_norm = os.path.normcase(os.path.realpath(ud))
            profiles_norm = os.path.normcase(os.path.realpath(os.path.join(PROJECT_ROOT, "profiles_data")))
            return ud_norm.startswith(profiles_norm)
    return False


def _kill_pids(pids):
    if not pids:
        return
    subprocess.run(
        ["taskkill", "/F", "/T"] + [str(p) for p in pids],
        capture_output=True, text=True
    )


def main():
    if sys.platform != "win32":
        subprocess.run(["pkill", "-f", "chrome"], capture_output=True, text=True)
        return

    try:
        import psutil
    except ImportError:
        # psutil unavailable: cannot safely distinguish GhostBrowser from real
        # Chrome, so refuse to kill anything by image name.
        print("psutil not available - skipping cleanup to protect real Chrome")
        return

    targets = []
    for proc in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            if (proc.info.get("name") or "").lower() not in ("chrome.exe", "chrome_proxy.exe"):
                continue
            if _is_ghostbrowser_proc(" ".join(proc.info.get("cmdline") or [])):
                targets.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if targets:
        _kill_pids(targets)
        print(f"Killed {len(targets)} orphaned GhostBrowser process(es)")
    else:
        print("No orphaned GhostBrowser processes found")


if __name__ == "__main__":
    main()
