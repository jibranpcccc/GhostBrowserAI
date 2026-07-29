#!/usr/bin/env python3
"""GhostBrowser Chromium engine build orchestration.

This script is intentionally stdlib-only so it can bootstrap a Chromium build
environment without requiring any pre-installed Python packages.

Typical flow:

    python engine/build.py --fetch --sync --apply-patches --build

The resulting executable path is printed at the end. Use the
GHOSTBROWSER_CHROMIUM_VERSION environment variable to pin the Chromium branch.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable

# Default Chromium version (branch HEAD / tag) used when the caller does not set
# GHOSTBROWSER_CHROMIUM_VERSION. This should be bumped intentionally after QA.
DEFAULT_CHROMIUM_VERSION = "132.0.6834.110"

DEPOT_TOOLS_GIT_URL = "https://chromium.googlesource.com/chromium/tools/depot_tools.git"
DEPOT_TOOLS_ARCHIVE_URL = "https://storage.googleapis.com/chrome-infra/depot_tools.zip"
CHROMIUM_SRC_REPO = "https://chromium.googlesource.com/chromium/src.git"


def log(message: str) -> None:
    print(f"[ghost-engine] {message}", flush=True)


def fail(message: str, code: int = 1) -> None:
    print(f"[ghost-engine] ERROR: {message}", file=sys.stderr, flush=True)
    sys.exit(code)


def engine_root() -> Path:
    """Return the directory that contains this script (i.e. engine/)."""
    return Path(__file__).resolve().parent


def chromium_src_dir() -> Path:
    """Target directory for the Chromium source checkout."""
    return engine_root() / "src"


def out_dir() -> Path:
    """GN output directory."""
    return chromium_src_dir() / "out" / "Release"


def executable_name() -> str:
    return "chrome.exe" if platform.system() == "Windows" else "chrome"


def expected_executable_path() -> Path:
    return out_dir() / executable_name()


def _is_on_path(name: str) -> bool:
    for base in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(base) / name
        if candidate.is_file():
            return True
    return False


def ensure_depot_tools() -> Path:
    """Return a usable depot_tools directory, downloading if not on PATH."""
    if _is_on_path("gclient") and _is_on_path("gn") and _is_on_path("autoninja"):
        log("depot_tools already present in PATH")
        return Path(shutil.which("gclient")).resolve().parent

    local_depot_tools = engine_root() / "depot_tools"
    gclient = local_depot_tools / ("gclient.bat" if platform.system() == "Windows" else "gclient")

    if gclient.is_file():
        log(f"Using local depot_tools at {local_depot_tools}")
    else:
        log("depot_tools not found on PATH; downloading...")
        local_depot_tools.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.run(
                ["git", "clone", "--depth=1", DEPOT_TOOLS_GIT_URL, str(local_depot_tools)],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Git may not be available; fall back to the prebuilt zip.
            zip_path = engine_root() / "depot_tools.zip"
            log(f"Downloading depot_tools archive to {zip_path}")
            urllib.request.urlretrieve(DEPOT_TOOLS_ARCHIVE_URL, zip_path)
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(local_depot_tools)
            zip_path.unlink(missing_ok=True)

    # Ensure the local tools are listed first on PATH.
    os.environ["PATH"] = str(local_depot_tools) + os.pathsep + os.environ.get("PATH", "")
    log(f"depot_tools path prepended: {local_depot_tools}")
    return local_depot_tools


def checked_run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command and stream combined output."""
    log(f"Running: {' '.join(cmd)}")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        fail(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")
    return proc


def fetch_chromium(src: Path) -> None:
    """Initial `fetch chromium` to create the source tree."""
    if src.is_dir() and any(src.iterdir()):
        log(f"Source directory already populated: {src}")
        return

    src.parent.mkdir(parents=True, exist_ok=True)
    checked_run(["fetch", "--nohooks", "chromium"], cwd=src.parent)


def sync_chromium(src: Path, version: str) -> None:
    """Checkout the pinned Chromium version and run gclient sync."""
    if not (src / ".gclient").is_file():
        fail(
            "Chromium source tree not configured. Run with --fetch first, "
            "or point GHOSTBROWSER_CHROMIUM_SRC at an existing checkout."
        )

    log(f"Checking out Chromium {version}")
    checked_run(["git", "-C", str(src), "fetch", "--tags", "origin"], cwd=src.parent)
    checked_run(["git", "-C", str(src), "checkout", "--force", version], cwd=src.parent)

    log("Running gclient sync (this may take a while)...")
    checked_run(
        [
            "gclient",
            "sync",
            "-D",
            "--force",
            "--reset",
            "--with_branch_heads",
            "--with_tags",
        ],
        cwd=src.parent,
    )


def patches_dir() -> Path:
    return engine_root() / "patches"


def apply_patches(src: Path) -> None:
    """Apply sorted *.patch files from engine/patches onto the source tree."""
    patches = sorted(path for path in patches_dir().glob("*.patch") if path.is_file())
    if not patches:
        log("No patches found to apply")
        return

    log(f"Applying {len(patches)} patch(es) from {patches_dir()}")
    git = shutil.which("git")
    if not git:
        fail("git is required to apply patches")

    for patch in patches:
        log(f"Applying patch {patch.name}")
        # Use --3way to tolerate minor context shifts during rebases.
        checked_run(
            [git, "-C", str(src), "apply", "--3way", "--verbose", str(patch)],
            cwd=src,
            check=True,
        )


def generate_args_gn(src: Path) -> None:
    """Copy engine/gn/args.gn into out/Release/args.gn."""
    template = engine_root() / "gn" / "args.gn"
    if not template.is_file():
        fail(f"GN args template missing: {template}")

    out_dir_path = src / "out" / "Release"
    out_dir_path.mkdir(parents=True, exist_ok=True)
    dest = out_dir_path / "args.gn"

    shutil.copy2(template, dest)
    log(f"Generated {dest}")


def gn_gen(src: Path) -> None:
    """Run `gn gen` for the Release output directory."""
    log("Generating ninja files with gn gen...")
    checked_run(["gn", "gen", str(out_dir())], cwd=src)


def build_chrome(src: Path) -> None:
    """Compile Chrome with autoninja."""
    log("Starting Chromium build. This will take a while...")
    checked_run(["autoninja", "-C", str(out_dir()), "chrome"], cwd=src)


def clean_output(src: Path) -> None:
    """Remove the Release output directory to force a clean build."""
    target = src / "out" / "Release"
    if target.is_dir():
        log(f"Removing {target}")
        shutil.rmtree(target)
    else:
        log(f"No output directory to clean: {target}")


def resolve_chromium_version() -> str:
    return os.environ.get("GHOSTBROWSER_CHROMIUM_VERSION", DEFAULT_CHROMIUM_VERSION).strip()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a custom Chromium engine for GhostBrowser.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  GHOSTBROWSER_CHROMIUM_VERSION   Pinned Chromium version/tag (default: %(default)s).
  GHOSTBROWSER_CHROMIUM_SRC       Chromium source directory (default: engine/src).
        """.strip(),
    )

    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Run `fetch chromium` to create the source checkout.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Checkout the pinned version and run gclient sync.",
    )
    parser.add_argument(
        "--apply-patches",
        action="store_true",
        help="Apply GhostBrowser patches from engine/patches/.",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Generate GN files and run autoninja chrome.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove out/Release before building.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Override the Chromium version (default from GHOSTBROWSER_CHROMIUM_VERSION).",
    )
    parser.add_argument(
        "--src",
        type=Path,
        default=None,
        help="Override the Chromium source directory.",
    )

    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)

    version = args.version or resolve_chromium_version()
    src = args.src or chromium_src_dir()

    has_action = any((args.fetch, args.sync, args.apply_patches, args.build, args.clean))
    if not has_action:
        print("No action requested. Use --fetch, --sync, --apply-patches, --build, or --clean.")
        return 0

    ensure_depot_tools()

    if args.clean:
        clean_output(src)

    if args.fetch:
        fetch_chromium(src)

    if args.sync:
        sync_chromium(src, version)

    if args.apply_patches:
        apply_patches(src)

    if args.build:
        generate_args_gn(src)
        gn_gen(src)
        build_chrome(src)
        binary_path = expected_executable_path()
        print("\n" + "=" * 60)
        print("GhostBrowser Chromium engine build complete.")
        print(f"Version pin: {version}")
        print(f"Output path: {binary_path}")
        if binary_path.exists():
            print(f"Executable size: {binary_path.stat().st_size} bytes")
        print("=" * 60)
        print(
            f"\nSet GHOSTBROWSER_CHROMIUM_BINARY={binary_path} "
            "to use this binary in GhostBrowser."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
