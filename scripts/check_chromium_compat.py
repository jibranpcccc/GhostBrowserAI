from __future__ import annotations

import json
import sys
from importlib.metadata import version
from pathlib import Path

# Ensure backend package is resolvable when run as `python scripts/check_chromium_compat.py`
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from backend.config import get_installed_chromium_major_version, get_installed_chromium_version


def version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def main() -> int:
    manifest_path = Path(__file__).resolve().parents[1] / "backend" / "chromium_compat.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chromium_version = get_installed_chromium_version()
    chromium_major = get_installed_chromium_major_version()
    playwright_version = version("playwright")
    problems = []
    if chromium_major not in manifest["approved_chromium_majors"]:
        problems.append(
            f"Chromium {chromium_major} is not approved; run the complete regression suite before release."
        )
    minimum = manifest["minimum_playwright_version"]
    if version_tuple(playwright_version) < version_tuple(minimum):
        problems.append(f"Playwright {playwright_version} is older than required {minimum}.")
    result = {
        "passed": not problems,
        "chromium_version": chromium_version,
        "chromium_major": chromium_major,
        "playwright_version": playwright_version,
        "approved_majors": manifest["approved_chromium_majors"],
        "problems": problems,
    }
    print(json.dumps(result, indent=2))
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
