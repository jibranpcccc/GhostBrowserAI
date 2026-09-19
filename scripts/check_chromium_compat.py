from __future__ import annotations

import json
import os
import sys
from importlib.metadata import version
from pathlib import Path

# Ensure backend package is resolvable
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from backend.engine_resolver import get_engine_identity


def version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def main() -> int:
    manifest_path = _repo_root / "backend" / "chromium_compat.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Resolve the exact engine identity used by runtime
    identity = get_engine_identity()

    supported_production_majors = manifest.get("supported_production_majors", [])
    legacy_test_majors = manifest.get("legacy_test_majors", [])
    approved_majors = manifest.get("approved_chromium_majors", [])
    min_playwright = manifest.get("minimum_playwright_version", "1.61.0")

    problems: list[str] = []
    major = identity.major_version

    is_production_supported = major in supported_production_majors
    is_legacy_test = major in legacy_test_majors

    if is_legacy_test:
        override_env = manifest.get("legacy_override_env", "GHOSTBROWSER_ALLOW_LEGACY_CHROMIUM")
        is_test_env = os.environ.get("GHOSTBROWSER_TEST_ENV", "").lower() in ("1", "true")
        has_override = os.environ.get(override_env, "").lower() in ("1", "true")
        if not is_test_env and not has_override:
            problems.append(
                f"Chromium {major} ({identity.exact_version}) is legacy/test-only and obsolete for production. "
                f"Upgrade runtime engine or set {override_env}=1 for test runs."
            )
        is_production_supported = False
    elif not is_production_supported:
        problems.append(
            f"Chromium {major} ({identity.exact_version}) is not in supported production majors {supported_production_majors}."
        )

    # Validate Playwright package version
    try:
        pw_version = version("playwright")
        if version_tuple(pw_version) < version_tuple(min_playwright):
            problems.append(f"Playwright {pw_version} is older than required minimum {min_playwright}.")
    except Exception as exc:
        problems.append(f"Unable to verify Playwright version: {exc}")

    result = {
        "exact_executable": identity.executable_path,
        "sha256": identity.sha256,
        "exact_version": identity.exact_version,
        "major": identity.major_version,
        "source": identity.source,
        "production_supported": is_production_supported,
        "legacy_test_only": is_legacy_test,
        "problems": problems,
    }

    print(json.dumps(result, indent=2))
    return 0 if (not problems and (is_production_supported or is_legacy_test and (is_test_env or has_override))) else 2


if __name__ == "__main__":
    raise SystemExit(main())
