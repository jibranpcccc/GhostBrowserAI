"""Build a standalone PyInstaller executable for the GhostBrowser backend.

This script does not require PyInstaller to be installed at import time.
If PyInstaller is missing it prints an install command and exits gracefully.
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
    """Locate the Python entrypoint backend.__main__."""
    entrypoint = project_root / "backend" / "__main__.py"
    if not entrypoint.exists():
        print(f"Error: backend.__main__ not found at {entrypoint}", file=sys.stderr)
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


def _create_temporary_entrypoint(output_dir: Path, name: str) -> Path:
    """Create a small bootstrap script that calls backend.__main__.main()."""
    entrypoint = output_dir / f"{name}.py"
    entrypoint.write_text(
        'import sys\nimport backend.__main__\n'
        'if __name__ == "__main__":\n'
        '    backend.__main__.main()\n',
        encoding="utf-8",
    )
    return entrypoint


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


def build(args: argparse.Namespace) -> int:
    """Prepare and run the PyInstaller build."""
    project_root = _find_project_root()
    _find_entrypoint(project_root)

    name = "GhostBrowser"

    # Determine bundle and console modes.
    if args.onefile and args.onedir:
        print("Error: cannot specify both --onefile and --onedir", file=sys.stderr)
        return 2
    if args.console and args.windowed:
        print("Error: cannot specify both --console and --windowed", file=sys.stderr)
        return 2

    mode_flag = "--onedir" if args.onedir else "--onefile"
    window_flag = "--windowed" if args.windowed else "--console"

    output_dir = _resolve_output_dir(project_root, args.output_dir)
    workpath = output_dir / "build"
    distpath = output_dir

    output_dir.mkdir(parents=True, exist_ok=True)

    # Verify PyInstaller is available; skip gracefully if missing.
    pyinstaller_cmd = _check_pyinstaller()

    # Create a temporary entrypoint script for PyInstaller.
    entrypoint = _create_temporary_entrypoint(output_dir, name)

    data_files = [
        (project_root / "frontend", "frontend"),
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
        "backend.__main__",
        "--collect-all",
        "backend",
        *add_data_args,
        str(entrypoint),
    ]

    print("Running PyInstaller command:")
    print(" ".join(shlex.quote(str(part)) for part in cmd))

    try:
        result = subprocess.run(cmd, cwd=project_root)
        return result.returncode
    finally:
        # Do not leave the temporary entrypoint outside the build tree.
        try:
            entrypoint.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a standalone PyInstaller executable for GhostBrowser.",
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Build a single executable file (default).",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Build a directory containing the executable.",
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

    if not args.onefile and not args.onedir:
        args.onefile = True
    if not args.console and not args.windowed:
        args.console = True

    return build(args)


if __name__ == "__main__":
    sys.exit(main())
