"""Kill any orphaned Chromium processes that tests may have left behind."""
import subprocess
import sys


def main():
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print("Killed orphaned Chrome processes")
        else:
            print("No orphaned Chrome processes found")
    else:
        result = subprocess.run(
            ["pkill", "-f", "chrome"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print("Killed orphaned Chrome processes")
        else:
            print("No orphaned Chrome processes found")


if __name__ == "__main__":
    main()
