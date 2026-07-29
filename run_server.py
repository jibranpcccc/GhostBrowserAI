"""Validated local launcher for the GhostBrowser FastAPI application."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
HOST_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 8000
ALREADY_RUNNING_EXIT = 10
REQUIRED_MODULES = (
    "fastapi",
    "uvicorn",
    "playwright",
    "playwright_stealth",
    "psutil",
    "httpx",
    "httpx_socks",
    "multipart",
    "croniter",
    "pydantic",
    "email_validator",
    "cryptography",
)


def _configure_process() -> None:
    multiprocessing.freeze_support()
    project = str(PROJECT_DIR)
    if project not in sys.path:
        sys.path.insert(0, project)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def _missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def _chromium_path() -> Path:
    from backend.config import get_installed_chromium_path

    return Path(get_installed_chromium_path()).resolve()


def _port_is_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


def _existing_ghostbrowser_is_ready(host: str, port: int) -> bool:
    """Return True only when the occupied port identifies this application."""
    request_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    identity_url = f"http://{request_host}:{port}/api/system/health"
    try:
        with urllib.request.urlopen(identity_url, timeout=1.5) as response:
            if not 200 <= response.status < 300:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return (
                isinstance(payload, dict)
                and payload.get("service") == "GhostBrowser"
                and payload.get("status") == "ready"
            )
    except (OSError, ValueError, UnicodeDecodeError, urllib.error.URLError):
        return False


def run_preflight(host: str, port: int, *, import_application: bool = False) -> list[str]:
    errors: list[str] = []

    missing = _missing_modules()
    if missing:
        errors.append("Missing Python modules: " + ", ".join(missing))

    if not errors:
        try:
            chromium = _chromium_path()
            if not chromium.is_file():
                errors.append(f"Playwright Chromium executable does not exist: {chromium}")
        except Exception as exc:
            if getattr(sys, "frozen", False):
                remedy = "This build does not contain its required Chromium bundle; rebuild it from a validated source environment."
            else:
                remedy = f"Run '{sys.executable} -m playwright install chromium' from the project environment."
            errors.append(
                "Playwright Chromium is unavailable. "
                f"{remedy} Details: {type(exc).__name__}: {exc}"
            )

    if not _port_is_available(host, port):
        errors.append(f"Port {host}:{port} is already in use; refusing to start a second server.")

    if import_application and not errors:
        try:
            from backend.main import app as _app  # noqa: F401
        except Exception as exc:
            errors.append(f"Application import failed: {type(exc).__name__}: {exc}")

    return errors


def _wait_for_readiness_and_open(host: str, port: int, timeout_seconds: float = 30.0) -> None:
    health_url = f"http://{host}:{port}/api/system/health"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                if 200 <= response.status < 300:
                    print(f"[System] Readiness check passed: {health_url}")
                    webbrowser.open(f"http://{host}:{port}/")
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    print(f"[System] ERROR: readiness did not succeed within {timeout_seconds:.0f} seconds.")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start or validate the GhostBrowser local server")
    parser.add_argument("--check", action="store_true", help="Run preflight checks and exit")
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Report an already-running GhostBrowser instance with exit code 10",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the management UI")
    parser.add_argument(
        "--import-accounts",
        metavar="FILE",
        help="Import a plaintext Cloudflare account file into this Windows user's protected store",
    )
    parser.add_argument(
        "--priority-accounts",
        metavar="FILE",
        help="Optional priority account file used with --import-accounts",
    )
    parser.add_argument("--host", default=os.environ.get("GHOSTBROWSER_HOST", HOST_DEFAULT))
    parser.add_argument("--port", type=int, default=os.environ.get("GHOSTBROWSER_PORT", str(PORT_DEFAULT)))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_process()
    raw_environment_port = os.environ.get("GHOSTBROWSER_PORT")
    if raw_environment_port is not None:
        try:
            int(raw_environment_port)
        except ValueError:
            print(f"[Preflight] ERROR: GHOSTBROWSER_PORT must be an integer, got {raw_environment_port!r}")
            return 2
    args = _parse_args(argv)

    if args.import_accounts:
        from backend.cloudflare_manager import CloudflareManager
        from backend.credential_store import save_accounts

        standard = Path(args.import_accounts).expanduser().resolve()
        priority = Path(args.priority_accounts).expanduser().resolve() if args.priority_accounts else None
        if not standard.is_file():
            print(f"[Import] ERROR: account file does not exist: {standard}")
            return 2
        if priority is not None and not priority.is_file():
            print(f"[Import] ERROR: priority account file does not exist: {priority}")
            return 2
        manager = CloudflareManager(
            accounts_file=str(standard),
            priority_accounts_file=str(priority or standard.parent / "__no_priority_accounts__.txt"),
            use_secure_store=False,
            allow_plaintext=True,
        )
        if not manager.accounts:
            print("[Import] ERROR: no valid Cloudflare credentials were found.")
            return 1
        count = save_accounts(manager.accounts)
        print(f"[Import] SUCCESS: protected {count} accounts for the current Windows user.")
        print("[Import] You may now securely delete the plaintext source file.")
        return 0

    if not (1 <= args.port <= 65535):
        print(f"[Preflight] ERROR: invalid TCP port {args.port}")
        return 2

    if (
        args.reuse_existing
        and not _port_is_available(args.host, args.port)
        and _existing_ghostbrowser_is_ready(args.host, args.port)
    ):
        print(f"[Preflight] ALREADY RUNNING: http://{args.host}:{args.port}")
        return ALREADY_RUNNING_EXIT

    errors = run_preflight(args.host, args.port, import_application=True)
    if errors:
        for error in errors:
            print(f"[Preflight] ERROR: {error}")
        if getattr(sys, "frozen", False):
            print("[Preflight] Frozen builds never invoke themselves as a Playwright installer.")
        return 1

    print(f"[Preflight] PASS: dependencies, Chromium, and {args.host}:{args.port}")
    if args.check:
        return 0

    try:
        from backend.main import app
        import uvicorn
    except Exception as exc:
        print(f"[Server] ERROR: application import failed: {type(exc).__name__}: {exc}")
        return 1

    print("==============================================")
    print("  GhostBrowser API Server")
    print("==============================================")
    print(f"Starting at http://{args.host}:{args.port}")

    should_open = not args.no_browser and os.environ.get("GHOSTBROWSER_NO_BROWSER") != "1"
    if should_open:
        threading.Thread(
            target=_wait_for_readiness_and_open,
            args=(args.host, args.port),
            daemon=True,
            name="ghostbrowser-readiness",
        ).start()

    try:
        uvicorn.run(app, host=args.host, port=args.port)
    except Exception as exc:
        print(f"[Server] ERROR: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
