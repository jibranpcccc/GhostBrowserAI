"""Build a standalone PyInstaller executable for the GhostBrowser backend.

This script does not require PyInstaller to be installed at import time.
If PyInstaller is missing it prints an install command and exits gracefully.

It mirrors the authoritative ``build.bat`` release path:
  - entrypoint ``run_server.py`` (the validated launcher)
  - onedir mode so the validated Playwright Chromium runtime can be bundled
    beside the executable
  - the playwright_stealth JavaScript data assets are collected
  - Chromium is copied to ``playwright-browsers\\chrome-win64`` and verified
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def _find_project_root() -> Path:
    """Return the project root assuming this script lives in scripts/."""
    return Path(__file__).resolve().parent.parent


def _find_entrypoint(project_root: Path) -> Path:
    """Locate the validated launcher run_server.py (build.bat entrypoint)."""
    entrypoint = project_root / "run_server.py"
    if not entrypoint.exists():
        print(f"Error: run_server.py not found at {entrypoint}", file=sys.stderr)
        sys.exit(1)
    return entrypoint


def _resolve_output_dir(project_root: Path, output_dir: str) -> Path:
    """Return an absolute output directory, resolving relative to project root."""
    path = Path(output_dir)
    if not path.is_absolute():
        path = project_root / path
    return path


def _pyinstaller_data_separator() -> str:
    """PyInstaller uses ';' on Windows and ':' elsewhere for --add-data."""
    return ";" if sys.platform == "win32" else ":"


def _build_data_args(
    project_root: Path,
    data_files: list[tuple[Path, str]],
) -> list[str]:
    """Build the --add-data arguments for PyInstaller."""
    sep = _pyinstaller_data_separator()
    args: list[str] = []
    for src, dest in data_files:
        if not src.exists():
            print(f"Warning: skipping missing data file {src}", file=sys.stderr)
            continue
        args.extend(["--add-data", f"{src}{sep}{dest}"])
    return args


def _chromium_source() -> Path | None:
    """Locate the validated Playwright Chromium executable."""
    try:
        from backend.config import get_installed_chromium_path
        return Path(get_installed_chromium_path()).resolve()
    except Exception:
        return None


def _check_pyinstaller() -> str:
    """Return the PyInstaller executable path, or print install help and exit 0."""
    pyinstaller = shutil.which("pyinstaller")
    if pyinstaller:
        return pyinstaller

    # Also try python -m PyInstaller in case only the package is installed.
    python = sys.executable
    try:
        subprocess.run(
            [python, "-m", "PyInstaller", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("PyInstaller is not installed.")
        print("Install it with: pip install pyinstaller")
        print("Skipping executable build.")
        sys.exit(0)
    return python


def _copy_chromium(chromium_exe: Path, dist_path: Path) -> None:
    """Copy the validated Chromium runtime beside the executable."""
    target = dist_path / "playwright-browsers" / "chrome-win64"
    if chromium_exe.name.lower() == "chrome.exe":
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(chromium_exe.parent, target, dirs_exist_ok=True)
        if not (target / "chrome.exe").is_file():
            print("Error: Chromium copy failed (chrome.exe missing).", file=sys.stderr)
            sys.exit(1)
    else:
        print(
            f"Warning: chromium executable is {chromium_exe.name!r}, expected chrome.exe; "
            "release audit may fail.",
            file=sys.stderr,
        )


def build(args: argparse.Namespace) -> int:
    """Prepare and run the PyInstaller build."""
    project_root = _find_project_root()
    _find_entrypoint(project_root)

    name = "GhostBrowser"

    # Release contract is onedir (bundled Chromium beside the executable).
    if args.onefile:
        print("Error: release builds must be --onedir so Chromium can be bundled.", file=sys.stderr)
        return 2
    if args.console and args.windowed:
        print("Error: cannot specify both --console and --windowed", file=sys.stderr)
        return 2

    mode_flag = "--onedir"
    window_flag = "--windowed" if args.windowed else "--console"

    output_dir = _resolve_output_dir(project_root, args.output_dir)
    workpath = output_dir / "build"
    distpath = output_dir

    output_dir.mkdir(parents=True, exist_ok=True)

    # Verify PyInstaller is available; skip gracefully if missing.
    pyinstaller_cmd = _check_pyinstaller()

    entrypoint = _find_entrypoint(project_root)

    data_files = [
        (project_root / "frontend", "frontend"),
        (project_root / "backend" / "extensions", "backend/extensions"),
        (project_root / ".env.example", "."),
        (project_root / "VERSION", "."),
        (project_root / "requirements.txt", "."),
    ]
    add_data_args = _build_data_args(project_root, data_files)

    base_cmd = [
        pyinstaller_cmd,
    ]
    if pyinstaller_cmd == sys.executable:
        base_cmd.extend(["-m", "PyInstaller"])

    cmd = [
        *base_cmd,
        mode_flag,
        window_flag,
        "--noconfirm",
        "--clean",
        "--distpath",
        str(distpath),
        "--workpath",
        str(workpath),
        "--specpath",
        str(output_dir / "spec"),
        "--paths",
        str(project_root),
        "--hidden-import",
        "playwright.async_api",
        "--hidden-import",
        "playwright_stealth",
        "--hidden-import",
        "uvicorn",
        "--hidden-import",
        "fastapi",
        "--hidden-import",
        "httpx_socks",
        "--hidden-import",
        "croniter",
        "--hidden-import",
        "cryptography",
        "--hidden-import",
        "backend.credential_store",
        "--hidden-import",
        "backend.device_cohorts",
        "--hidden-import",
        "backend.launch_policy",
        # playwright_stealth reads .js data files from its package at runtime;
        # without this the packaged app crashes on import with FileNotFoundError.
        "--collect-data",
        "playwright_stealth",
        *add_data_args,
        str(entrypoint),
    ]

    print("Running PyInstaller command:")
    print(" ".join(shlex.quote(str(part)) for part in cmd))

    try:
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode != 0:
            return result.returncode
    except FileNotFoundError:
        print("Error: PyInstaller could not be invoked.", file=sys.stderr)
        return 1

    dist_path = distpath / name
    if not (dist_path / "GhostBrowser.exe").is_file():
        print("Error: build returned success but GhostBrowser.exe is missing.", file=sys.stderr)
        return 1

    chromium = _chromium_source()
    if chromium and chromium.is_file():
        _copy_chromium(chromium, dist_path)
    else:
        print(
            "Warning: could not resolve Playwright Chromium; the distribution will "
            "lack its runtime and the release audit will fail.",
            file=sys.stderr,
        )

    try:
        from release_audit import audit_distribution  # type: ignore
    except ImportError:  # pragma: no cover
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "release_audit", Path(__file__).with_name("release_audit.py")
        )
        release_audit = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(release_audit)
        audit_distribution = release_audit.audit_distribution
    report = audit_distribution(dist_path)
    if not report["passed"]:
        print("Release audit failed:", file=sys.stderr)
        for problem in report["problems"]:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"Build complete. Output: {dist_path / 'GhostBrowser.exe'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a standalone PyInstaller executable for GhostBrowser.",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Build a directory containing the executable (default; required).",
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Rejected: release builds must be onedir for the bundled Chromium.",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        default=True,
        help="Show a console window for the executable (default).",
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Build a windowed executable without a console.",
    )
    parser.add_argument(
        "--output-dir",
        default="dist/GhostBrowser",
        help="Directory for PyInstaller output (default: dist/GhostBrowser).",
    )
    args = parser.parse_args(argv)

    if not args.console and not args.windowed:
        args.console = True

    return build(args)


if __name__ == "__main__":
    sys.exit(main())
