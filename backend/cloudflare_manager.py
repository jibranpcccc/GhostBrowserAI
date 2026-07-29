import os
import json
import time
from typing import Dict, List, Optional

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ACCOUNTS_FILE = os.path.join(WORKSPACE_ROOT, "cloudflare_accounts.txt")


class CloudflareManager:
    def __init__(self, accounts_file: Optional[str] = None,
                 priority_accounts_file: Optional[str] = None,
                 cooldowns_file: Optional[str] = None,
                 use_secure_store: bool = True,
                 allow_plaintext: Optional[bool] = None):
        self.accounts_file = accounts_file or ACCOUNTS_FILE
        self.priority_accounts_file = priority_accounts_file or os.path.join(
            WORKSPACE_ROOT, "cloudflare_accounts.priority.txt"
        )
        self.cooldowns_file = cooldowns_file or os.path.join(WORKSPACE_ROOT, "logs", "cf_cooldowns.json")
        self.accounts = []
        self.use_secure_store = bool(use_secure_store)
        self.allow_plaintext = (
            accounts_file is not None
            if allow_plaintext is None
            else bool(allow_plaintext)
        ) or os.getenv("GHOSTBROWSER_ALLOW_PLAINTEXT_CREDENTIALS", "").lower() in ("1", "true")
        self.cooldowns = {}  # account_id -> cooldown expiration time
        self._selection_cursors = {"priority": 0, "standard": 0}

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

    @staticmethod
    def _parse_account_line(line: str) -> Optional[Dict]:
        """Parse a credential line without ever returning/logging partial secrets."""
        if "," in line:
            parts = [part.strip() for part in line.split(",", 1)]
        else:
            parts = [part.strip() for part in line.split(":", 1)]
        parts = [part for part in parts if part]
        if len(parts) < 2:
            return None

        account_id, api_token = parts[0], parts[1]
        if any(marker in account_id.lower() for marker in ("your_real", "example", "account_id")):
            return None
        if any(marker in api_token.lower() for marker in ("your_real", "sk-kimi-token", "api_token")):
            return None
        return {"account_id": account_id, "token": api_token}

    def _read_account_file(self, path: str, priority: bool) -> List[Dict]:
        if not os.path.exists(path):
            # Be explicit instead of failing silently so callers know why the pool is empty.
            if not priority and path == self.accounts_file:
                print(f"[Cloudflare Manager] INFO: {path} not found. No plaintext accounts loaded.")
            return []

        loaded = []
        with open(path, "r", encoding="utf-8") as handle:
            for lineno, raw_line in enumerate(handle, 1):
                line = raw_line.strip().replace("\r", "")
                if not line or line.startswith("#"):
                    continue
                account = self._parse_account_line(line)
                if account is None:
                    print(f"[Cloudflare Manager] WARNING: Line {lineno} skipped (invalid format).")
                    continue
                account["priority"] = priority
                loaded.append(account)
        return loaded

    def load_accounts(self):
        """Load the private priority pool first, then the standard account pool.

        Both files accept ``ACCOUNT_ID,API_TOKEN`` or ``ACCOUNT_ID:API_TOKEN``.
        Duplicate account IDs are de-duplicated, with the priority copy winning.
        """
        self.accounts = []
        seen_account_ids = set()

        secure_accounts = []
        if self.use_secure_store:
            try:
                from backend.credential_store import load_accounts
                secure_accounts = load_accounts()
            except Exception as error:
                print(f"[Cloudflare Manager] Secure credential store unavailable: {type(error).__name__}")

        environment_accounts = []
        raw_environment = os.getenv("GHOSTBROWSER_CF_ACCOUNTS_JSON", "").strip()
        if raw_environment:
            try:
                decoded = json.loads(raw_environment)
                if isinstance(decoded, list):
                    environment_accounts = decoded
            except Exception:
                print("[Cloudflare Manager] GHOSTBROWSER_CF_ACCOUNTS_JSON is invalid JSON.")

        for raw_account in secure_accounts + environment_accounts:
            if not isinstance(raw_account, dict):
                continue
            account_id = str(raw_account.get("account_id", "")).strip()
            token = str(raw_account.get("token", "")).strip()
            if not account_id or not token or account_id in seen_account_ids:
                continue
            seen_account_ids.add(account_id)
            self.accounts.append({
                "account_id": account_id,
                "token": token,
                "priority": bool(raw_account.get("priority")),
            })

        if self.allow_plaintext:
            sources = (
                (self.priority_accounts_file, True),
                (self.accounts_file, False),
            )
            for path, priority in sources:
                for account in self._read_account_file(path, priority):
                    account_id = account["account_id"]
                    if account_id in seen_account_ids:
                        continue
                    seen_account_ids.add(account_id)
                    self.accounts.append(account)

        count = len(self.accounts)
        priority_count = sum(1 for account in self.accounts if account.get("priority"))
        print(
            f"[Cloudflare Manager] Loaded {count} real accounts "
            f"({priority_count} priority)."
        )

        if count == 0:
            if not os.path.exists(self.accounts_file):
                print(f"[Cloudflare Manager] ERROR: {self.accounts_file} not found!")
            print("[Cloudflare Manager] ERROR: NO REAL ACCOUNTS LOADED.")
            print("[Cloudflare Manager] Add accounts in ACCOUNT_ID,API_TOKEN format.")

    def get_healthy_accounts(self, priority: Optional[bool] = None) -> List[Dict]:
        """Return non-cooldown accounts, optionally restricted to one tier."""
        if not self.accounts:
            self.load_accounts()
        now = time.time()
        return [
            account for account in self.accounts
            if (priority is None or bool(account.get("priority")) is priority)
            and now > self.cooldowns.get(account["account_id"], 0)
        ]

    def get_account_candidates(self, priority: bool, max_accounts: Optional[int] = None,
                               rotate_by: int = 1) -> List[Dict]:
        """Return a round-robin ordered healthy tier without exposing credentials."""
        healthy = self.get_healthy_accounts(priority=priority)
        if not healthy:
            return []

        tier = "priority" if priority else "standard"
        cursor = self._selection_cursors[tier] % len(healthy)
        ordered = healthy[cursor:] + healthy[:cursor]
        advance = max(1, int(rotate_by))
        self._selection_cursors[tier] = (cursor + advance) % len(healthy)
        return ordered if max_accounts is None else ordered[:max_accounts]

    def get_account(self) -> Optional[Dict]:
        """Return a priority account first; use standard only when none are healthy."""
        for priority in (True, False):
            candidates = self.get_account_candidates(priority, max_accounts=1, rotate_by=1)
            if candidates:
                return candidates[0]

        if self.accounts:
            # MED-08 FIX: Don't reset all cooldowns at once - this causes a cascade of 429 rate limits
            # that can permanently blacklist API tokens. Instead, find the account whose cooldown
            # expires soonest and return None so the caller can show a proper error.
            now = time.time()
            soonest = min(self.cooldowns.values(), default=0)
            wait_s = max(0, int(soonest - now))
            print(f"[Cloudflare Manager] All accounts on cooldown. Shortest remaining: {wait_s}s.")
        return None

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
                "priority": bool(acc.get("priority")),
                "status": "cooldown" if is_cooling else "healthy",
                "cooldown_remaining_seconds": remaining
            })
        return result

    @property
    def total_accounts(self):
        return len(self.accounts)

    @property
    def priority_count(self):
        return sum(1 for account in self.accounts if account.get("priority"))

    @property
    def healthy_priority_count(self):
        return len(self.get_healthy_accounts(priority=True))

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
        print(f"[Cloudflare Manager] WARNING: An account was placed on cooldown for {cooldown_minutes} min.")

cloudflare_manager = CloudflareManager()
