import os
import json
import random
import time
from typing import Optional, Dict

class CloudflareManager:
    def __init__(self):
        self.accounts_file = os.path.join(os.path.dirname(__file__), "..", "cloudflare_accounts.txt")
        self.cooldowns_file = os.path.join(os.path.dirname(__file__), "..", "logs", "cf_cooldowns.json")
        self.accounts = []
        self.cooldowns = {}  # account_id -> cooldown expiration time

        self._load_cooldowns()
        self.load_accounts()

    def _load_cooldowns(self):
        """Load cooldown state from disk so it survives server restarts."""
        if os.path.exists(self.cooldowns_file):
            try:
                with open(self.cooldowns_file, "r") as f:
                    raw = json.load(f)
                now = time.time()
                self.cooldowns = {k: v for k, v in raw.items() if v > now}
            except Exception:
                self.cooldowns = {}
        else:
            self.cooldowns = {}

    def _save_cooldowns(self):
        """Persist cooldown state to disk."""
        os.makedirs(os.path.dirname(self.cooldowns_file), exist_ok=True)
        with open(self.cooldowns_file, "w") as f:
            json.dump(self.cooldowns, f)

    def load_accounts(self):
        """
        Loads accounts from cloudflare_accounts.txt.
        SUPPORTED FORMATS (one per line):
          ACCOUNT_ID:API_TOKEN
          ACCOUNT_ID:GATEWAY_NAME:API_TOKEN   (legacy, gateway name is ignored)
        """
        self.accounts = []
        if not os.path.exists(self.accounts_file):
            print(f"[Cloudflare Manager] ❌ {self.accounts_file} not found!")
            return

        with open(self.accounts_file, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip().replace("\r", "")
                if not line or line.startswith("#"):
                    continue

                if "\t" in line:
                    parts = [p.strip() for p in line.split("\t", 1)]
                elif "," in line:
                    parts = [p.strip() for p in line.split(",", 1)]
                else:
                    parts = [p.strip() for p in line.split(":", 1)]
                
                # Must have at least 2 non-empty parts
                parts = [p for p in parts if p]

                if len(parts) >= 2:
                    account_id = parts[0]
                    api_token  = parts[1]

                    # Skip obvious placeholder lines
                    if any(x in account_id.lower() for x in ["your_real", "example", "account_id"]):
                        continue
                    if any(x in api_token.lower() for x in ["your_real", "sk-kimi-token", "api_token"]):
                        continue

                    self.accounts.append({
                        "account_id": account_id,
                        "token":      api_token
                    })
                else:
                    print(f"[Cloudflare Manager] ⚠️  Line {lineno} skipped (invalid format): {line[:40]}")

        count = len(self.accounts)
        print(f"[Cloudflare Manager] ✅ Loaded {count} real accounts.")
        
        if count == 0:
            print("[Cloudflare Manager] ❌ NO REAL ACCOUNTS LOADED.")
            print("[Cloudflare Manager] ➡  Add accounts to cloudflare_accounts.txt in format:")
            print("[Cloudflare Manager]    ACCOUNT_ID:API_TOKEN")

    def get_account(self) -> Optional[Dict]:
        """Returns a healthy, non-cooldown Cloudflare account."""
        if not self.accounts:
            self.load_accounts()

        now = time.time()
        healthy = [
            acc for acc in self.accounts
            if acc["account_id"] not in self.cooldowns or now > self.cooldowns[acc["account_id"]]
        ]

        if not healthy:
            # MED-08 FIX: Don't reset all cooldowns at once - this causes a cascade of 429 rate limits
            # that can permanently blacklist API tokens. Instead, find the account whose cooldown
            # expires soonest and return None so the caller can show a proper error.
            soonest = min(self.cooldowns.values(), default=0)
            wait_s = max(0, int(soonest - now))
            print(f"[Cloudflare Manager] ⏳ All accounts on cooldown. Shortest remaining: {wait_s}s.")
            return None

        if not healthy:
            return None

        return random.choice(healthy)

    def get_all_status(self) -> list:
        """Returns status of every account for the dashboard."""
        now = time.time()
        result = []
        for acc in self.accounts:
            aid = acc["account_id"]
            is_cooling = aid in self.cooldowns and now < self.cooldowns[aid]
            remaining = max(0, int(self.cooldowns.get(aid, 0) - now)) if is_cooling else 0
            result.append({
                "account_id": aid[:8] + "..." + aid[-4:] if len(aid) > 12 else aid,
                "status": "cooldown" if is_cooling else "healthy",
                "cooldown_remaining_seconds": remaining
            })
        return result

    @property
    def total_accounts(self):
        return len(self.accounts)

    @property
    def healthy_count(self):
        now = time.time()
        return sum(
            1 for acc in self.accounts
            if acc["account_id"] not in self.cooldowns or now > self.cooldowns[acc["account_id"]]
        )

    @property
    def cooldown_count(self):
        return self.total_accounts - self.healthy_count

    def report_failure(self, account_id: str, cooldown_minutes: int = 5):
        """Puts a specific account on cooldown after a failure."""
        expiry = time.time() + (cooldown_minutes * 60)
        self.cooldowns[account_id] = expiry
        self._save_cooldowns()
        print(f"[Cloudflare Manager] ⚠️  Account {account_id[:8]}... on cooldown for {cooldown_minutes} min.")

cloudflare_manager = CloudflareManager()
