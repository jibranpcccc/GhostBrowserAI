"""Standalone CLI to apply a verified GhostBrowser update archive."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.update_manager import (
    apply_update,
    download_update,
    generate_confirmation_token,
    get_current_version,
    rollback_update,
)


def main():
    parser = argparse.ArgumentParser(description="Apply or rollback GhostBrowser updates.")
    parser.add_argument("--archive", help="Path to a verified .zip update archive")
    parser.add_argument("--download-url", help="Download and apply from URL")
    parser.add_argument("--version", help="Expected version")
    parser.add_argument("--backup", action="store_true", help="Create backup before applying")
    parser.add_argument("--stage-only", action="store_true", help="Download and verify only, do not apply")
    parser.add_argument("--confirm", action="store_true", help="Auto confirm using env opt-in")
    parser.add_argument("--rollback", action="store_true", help="Roll back to previous backup")
    parser.add_argument(
        "--auto", action="store_true", help="Set GHOSTBROWSER_AUTO_UPDATE=1 for this run"
    )
    args = parser.parse_args()

    if args.auto:
        os.environ["GHOSTBROWSER_AUTO_UPDATE"] = "1"

    if args.rollback:
        result = rollback_update()
        print(result)
        return 0

    if not args.archive and not args.download_url:
        parser.error("Provide either --archive or --download-url")

    if args.download_url:
        if not args.version:
            args.version = get_current_version()
        metadata = {"latest_version": args.version, "download_url": args.download_url}
        dl = download_update(metadata, verify=True)
        print("Downloaded:", dl)
        if args.stage_only:
            return 0
        args.archive = dl["archive_path"]

    if not args.archive or not Path(args.archive).exists():
        print("Archive not found")
        return 1

    token = None
    if args.confirm or os.environ.get("GHOSTBROWSER_AUTO_UPDATE", "").strip().lower() in {"1", "true", "yes"}:
        if args.version:
            token = generate_confirmation_token(args.version)["confirmation_token"]
        else:
            token = generate_confirmation_token(get_current_version())["confirmation_token"]

    result = apply_update(args.archive, confirmation_token=token)
    print("Apply result:", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
