"""Migrate legacy Cloudflare account files into the Windows DPAPI store."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.credential_store import save_accounts
from backend.cloudflare_manager import CloudflareManager


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove-plaintext", action="store_true")
    args = parser.parse_args()
    root = ROOT
    standard = root / "cloudflare_accounts.txt"
    priority = root / "cloudflare_accounts.priority.txt"
    manager = CloudflareManager(
        accounts_file=str(standard),
        priority_accounts_file=str(priority),
        use_secure_store=False,
        allow_plaintext=True,
    )
    count = save_accounts(manager.accounts)
    if count == 0:
        print("No valid credentials were found; nothing was changed.")
        return 1
    if args.remove_plaintext:
        replacement = (
            "# Credentials migrated to api_data/cloudflare_accounts.secure.json\n"
            "# Use scripts/migrate_credentials.py to migrate replacement credentials.\n"
        )
        for path in (priority, standard):
            if path.exists():
                path.write_text(replacement, encoding="utf-8")
    print(f"Migrated {count} accounts to the Windows-protected credential store.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
