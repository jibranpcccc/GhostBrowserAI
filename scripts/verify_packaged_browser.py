"""Verify packaged Chromium browser binary integrity against saved EngineIdentity."""
from __future__ import annotations

import sys
from pathlib import Path

# Add repo root to sys.path
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from backend.engine_resolver import EngineIdentity, calculate_binary_sha256, resolve_chromium_version


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python verify_packaged_browser.py <packaged_chrome_exe>")
        return 1

    pkg = Path(sys.argv[1])
    if not pkg.is_file():
        print(f"Error: Browser executable does not exist: {pkg}")
        return 1

    exp = EngineIdentity.load()
    if not exp:
        print("Error: EngineIdentity artifact missing. Run engine resolver first.")
        return 1

    pkg_sha = calculate_binary_sha256(pkg)
    pkg_ver = resolve_chromium_version(str(pkg))

    print(f"Packaged version: {pkg_ver}, SHA-256: {pkg_sha}")
    print(f"Expected version: {exp.exact_version}, SHA-256: {exp.sha256}")

    if pkg_sha != exp.sha256:
        print(f"Error: Browser SHA-256 mismatch: {pkg_sha} != {exp.sha256}")
        return 1

    if pkg_ver != exp.exact_version:
        print(f"Error: Browser version mismatch: {pkg_ver} != {exp.exact_version}")
        return 1

    print("Packaged browser integrity verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
