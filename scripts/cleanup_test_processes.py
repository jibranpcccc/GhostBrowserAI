"""Kill only GhostBrowser-owned Chromium processes that tests left behind.

Never kills the user's real Google Chrome. Matches processes whose command
line references this project's bundled Chromium (dist/GhostBrowser) or whose
--user-data-dir lives under this project's profiles_data directory.
"""
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _user_data_dir_from(cmdline):
    """Extract the --user-data-dir value from a command line (list of args).

    Args are matched as individual tokens so paths containing spaces are
    handled correctly (a naive whitespace split would break them).
    """
    for i, arg in enumerate(cmdline):
        if arg.lower() == "--user-data-dir":
            if i + 1 < len(cmdline):
                return cmdline[i + 1].strip().strip('"')
        if arg.lower().startswith("--user-data-dir="):
            return arg.split("=", 1)[1].strip().strip('"')
    return None


def _is_ghostbrowser_proc(cmdline):
    """Return True if the process belongs to this GhostBrowser project.

    ``cmdline`` is the raw argument list from psutil. Bundled Chromium shipped
    with this project matches on the dist/GhostBrowser path. Playwright's
    cached Chromium is a shared binary, so it only matches when its
    --user-data-dir lives *inside* this project's profiles_data directory
    (path-boundary checked so a sibling like ``profiles_data_backup`` is never
    mistaken for it).
    """
    if not cmdline:
        return False
    # Bundled Chromium only matches when the EXECUTABLE itself lives under
    # <PROJECT_ROOT>/dist/GhostBrowser/ (path-boundary checked). Substring
    # matching over the whole command line is forbidden: a real Chrome process
    # opened on a URL containing e.g. dist/ghostbrowser/chrome would match and
    # get taskkill'd.
    try:
        exe_norm = os.path.normcase(os.path.realpath(cmdline[0]))
        dist_norm = os.path.normcase(os.path.realpath(os.path.join(PROJECT_ROOT, "dist", "GhostBrowser")))
        if exe_norm == dist_norm or exe_norm.startswith(dist_norm + os.sep):
            return True
    except Exception:
        pass
    ud = _user_data_dir_from(cmdline)
    if ud:
        ud_norm = os.path.normcase(os.path.realpath(ud))
        profiles_norm = os.path.normcase(os.path.realpath(os.path.join(PROJECT_ROOT, "profiles_data")))
        return ud_norm == profiles_norm or ud_norm.startswith(profiles_norm + os.sep)
    return False


def _kill_pids(pids):
    if not pids:
        return
    subprocess.run(
        ["taskkill", "/F", "/T"] + [str(p) for p in pids],
        capture_output=True, text=True
    )


def main():
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
            name = (proc.info.get("name") or "").lower()
            if sys.platform == "win32":
                if name not in ("chrome.exe", "chrome_proxy.exe"):
                    continue
            elif name not in ("chrome", "chromium"):
                continue
            if _is_ghostbrowser_proc(proc.info.get("cmdline") or []):
                targets.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if targets:
        if sys.platform == "win32":
            _kill_pids(targets)
        else:
            for pid in targets:
                try:
                    psutil.Process(pid).kill()
                except Exception:
                    pass
        print(f"Killed {len(targets)} orphaned GhostBrowser process(es)")
    else:
        print("No orphaned GhostBrowser processes found")


if __name__ == "__main__":
    main()
