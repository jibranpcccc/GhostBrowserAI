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
    # The gate approves the Chromium SHIPPED with the app. A user-configured
    # real-Chrome engine (GHOSTBROWSER_CHROMIUM_BINARY) must not influence it.
    # Resolve the shipped browser explicitly: dist bundle > repo bundle >
    # highest-revision Playwright cache entry.
    import os
    os.environ.pop("GHOSTBROWSER_CHROMIUM_BINARY", None)
    repo_root = Path(__file__).resolve().parents[1]
    shipped_candidates = []
    cache_root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if cache_root.is_dir():
        revisions = sorted(
            (p for p in cache_root.iterdir() if p.name.startswith("chromium-")),
            key=lambda p: int(p.name.rsplit("-", 1)[-1]) if p.name.rsplit("-", 1)[-1].isdigit() else 0,
            reverse=True,
        )
        for rev in revisions:
            for sub in ("chrome-win64", "chrome-win"):
                shipped_candidates.append(rev / sub / "chrome.exe")
    shipped_candidates.extend([
        repo_root / "dist" / "GhostBrowser" / "playwright-browsers" / "chrome-win64" / "chrome.exe",
        repo_root / "playwright-browsers" / "chrome-win64" / "chrome.exe",
    ])
    shipped = next((p for p in shipped_candidates if p.is_file()), None)
    if not shipped:
        try:
            from backend.engine_resolver import get_chromium_executable_path
            shipped = Path(get_chromium_executable_path())
        except Exception:
            pass
    if shipped:
        os.environ["GHOSTBROWSER_CHROMIUM_BINARY"] = str(shipped)
    from backend import config as _config
    _config._cached_chromium_version = None
    _config._cached_chromium_path = None
    chromium_version = get_installed_chromium_version()
    chromium_major = get_installed_chromium_major_version()
    playwright_version = version("playwright")
    problems = []
    warnings = []
    if chromium_major in manifest.get("legacy_test_majors", []):
        override_env = manifest.get("legacy_override_env", "GHOSTBROWSER_ALLOW_LEGACY_CHROMIUM")
        is_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV") in ("1", "true")
        has_override = os.environ.get(override_env) in ("1", "true")
        if not is_test_env and not has_override:
            problems.append(
                f"Chromium {chromium_major} is a legacy test version and is obsolete for production. "
                f"Upgrade to a modern release (131+) or set {override_env}=1."
            )
        else:
            warnings.append(
                f"Chromium {chromium_major} is a legacy engine permitted under test/development override."
            )
    elif chromium_major not in manifest["approved_chromium_majors"]:
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
        "supported_production_majors": manifest.get("supported_production_majors", []),
        "warnings": warnings,
        "problems": problems,
    }
    print(json.dumps(result, indent=2))
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
