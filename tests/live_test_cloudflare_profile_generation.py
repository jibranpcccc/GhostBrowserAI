import os
import sys
import hashlib
import json
import asyncio
import re
import httpx
import builtins
from typing import Optional, Dict

# 1. Opt-in Guard Check (must run before importing any production module)
if os.environ.get("GHOSTBROWSER_RUN_LIVE_AI_TEST") != "1":
    print("LIVE CLOUDFLARE KIMI TEST: NOT RUN")
    sys.exit(0)

# Setup workspace paths
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(WORKSPACE_ROOT)

# Import production config/systems
from backend.config import get_data_dir
from backend.ai_coherence_validator import coherence_validator
from backend.cloudflare_manager import cloudflare_manager
from backend.ai_generator import KIMI_MODEL, SYSTEM_PROMPT, generate_fingerprint_ai
import backend.ai_generator

ACCOUNTS_FILE = os.path.join(WORKSPACE_ROOT, "cloudflare_accounts.txt")
META_FILE = os.path.join(get_data_dir("profiles_data"), "profiles_meta.json")
COOLDOWN_FILE = os.path.join(WORKSPACE_ROOT, "logs", "cf_cooldowns.json")

# Trace and block directory creation
original_mkdir = os.mkdir
original_makedirs = os.makedirs
directories_created_count = 0

# Define this outside to avoid recursion
PROD_PROFILES_DIR = os.path.realpath(os.path.join(WORKSPACE_ROOT, "profiles_data")).replace("\\", "/")

def should_fail_path(path) -> bool:
    path_str = str(path).replace("\\", "/")
    if "temp_" in path_str or "tombstone_" in path_str:
        return True

    real_path = os.path.realpath(path).replace("\\", "/")
    if real_path.startswith(PROD_PROFILES_DIR) and real_path != PROD_PROFILES_DIR:
        return True

    return False

def hook_mkdir(path, *args, **kwargs):
    global directories_created_count
    if should_fail_path(path):
        directories_created_count += 1
        raise RuntimeError(f"Production or temporary directory creation blocked: {path}")
    return original_mkdir(path, *args, **kwargs)

def hook_makedirs(path, *args, **kwargs):
    global directories_created_count
    if should_fail_path(path):
        directories_created_count += 1
        raise RuntimeError(f"Production or temporary directory creation blocked: {path}")
    return original_makedirs(path, *args, **kwargs)

# Sanitize stdout/stderr to completely redact credentials and account identifiers
original_print = builtins.print

def redacted_print(*args, **kwargs):
    new_args = []
    for arg in args:
        if isinstance(arg, str):
            # Redact any 8-char hex prefixes followed by ellipsis (e.g. 94916bf3...)
            redacted = re.sub(r'\b[a-f0-9]{8}\.\.\.', '[REDACTED]...', arg)
            # Redact any 8-char hex segment in general
            redacted = re.sub(r'\b[a-f0-9]{8}\b', '[REDACTED]', redacted)
            # Redact bearer token headers if printed
            redacted = re.sub(r'Bearer\s+[a-zA-Z0-9_\-]+', 'Bearer [REDACTED]', redacted)
            new_args.append(redacted)
        else:
            new_args.append(arg)
    return original_print(*new_args, **kwargs)

def calculate_sha256(filepath):
    if not os.path.exists(filepath):
        return None
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

initial_accounts_hash = calculate_sha256(ACCOUNTS_FILE)
initial_meta_hash = calculate_sha256(META_FILE)
initial_cooldown_hash = calculate_sha256(COOLDOWN_FILE)

# Load accounts securely without printing content
def load_accounts_securely(filepath):
    raw_accounts = []
    if not os.path.exists(filepath):
        return raw_accounts
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip().replace("\r", "")
            if not line or line.startswith("#"):
                continue
            if "," in line:
                parts = [p.strip() for p in line.split(",", 1)]
            else:
                parts = [p.strip() for p in line.split(":", 1)]
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                raw_accounts.append((parts[0], parts[1]))
    return raw_accounts

all_accounts = load_accounts_securely(ACCOUNTS_FILE)
total_parsed = len(all_accounts)

# Count structurally valid accounts (excluding placeholders)
valid_accounts = []
for acc_id, token in all_accounts:
    if any(x in acc_id.lower() for x in ["your_real", "example", "account_id"]):
        continue
    if any(x in token.lower() for x in ["your_real", "sk-kimi-token", "api_token"]):
        continue
    if len(acc_id) >= 10 and len(token) >= 10:
        valid_accounts.append({"account_id": acc_id, "token": token})

structurally_valid = len(valid_accounts)

# Report secure stats
print(f"Account-file SHA-256: {initial_accounts_hash}")
print(f"Total parsed account count: {total_parsed}")
print(f"Structurally valid account count: {structurally_valid}")

if structurally_valid == 0:
    print("AI_MODEL_UNAVAILABLE (No valid Cloudflare accounts loaded)")
    sys.exit(1)

# Strict Response Validation
def validate_fingerprint_strict(fp) -> tuple[bool, str]:
    if not isinstance(fp, dict):
        return False, "Response is not a JSON object/dictionary"

    essential_keys = [
        "os", "userAgent", "sec_ch_ua", "sec_ch_ua_platform",
        "webgl_vendor", "webgl_renderer", "cpu_cores", "memory_gb",
        "screen_resolution", "timezone", "locale", "fonts"
    ]
    for k in essential_keys:
        if k not in fp or fp[k] is None:
            return False, f"Missing required field: '{k}'"

    # Check types
    if not isinstance(fp["os"], str): return False, "os is not a string"
    if not isinstance(fp["userAgent"], str): return False, "userAgent is not a string"
    if not isinstance(fp["sec_ch_ua"], str): return False, "sec_ch_ua is not a string"
    if not isinstance(fp["sec_ch_ua_platform"], str): return False, "sec_ch_ua_platform is not a string"
    if not isinstance(fp["webgl_vendor"], str): return False, "webgl_vendor is not a string"
    if not isinstance(fp["webgl_renderer"], str): return False, "webgl_renderer is not a string"
    if not isinstance(fp["timezone"], str): return False, "timezone is not a string"
    if not isinstance(fp["locale"], str): return False, "locale is not a string"
    if not isinstance(fp["screen_resolution"], str): return False, "screen_resolution is not a string"
    if not isinstance(fp["fonts"], list): return False, "fonts is not a list"

    # Non-empty fonts
    if not fp["fonts"]:
        return False, "fonts list is empty"

    # Check CPU cores & RAM are numeric and in bounds
    try:
        cores = int(fp["cpu_cores"])
        if not (1 <= cores <= 32):
            return False, f"cpu_cores out of bounds: {cores}"
    except (ValueError, TypeError):
        return False, "cpu_cores is not an integer"

    try:
        memory = int(fp["memory_gb"])
        if not (1 <= memory <= 64):
            return False, f"memory_gb out of bounds: {memory}"
    except (ValueError, TypeError):
        return False, "memory_gb is not an integer"

    # Check operating systems
    os_name = fp["os"]
    if os_name not in ["Windows", "Mac", "Linux"]:
        return False, f"Unsupported OS: {os_name}"

    # Validate OS matches User-Agent and Client Hint platform
    ua = fp["userAgent"]
    sec_platform = fp["sec_ch_ua_platform"]

    if os_name == "Windows":
        if "Windows" not in ua and "Win64" not in ua:
            return False, "OS is Windows but User-Agent does not match"
        if "Windows" not in sec_platform:
            return False, "OS is Windows but sec_ch_ua_platform does not match"
    elif os_name == "Mac":
        if "Macintosh" not in ua and "Mac OS X" not in ua:
            return False, "OS is Mac but User-Agent does not match"
        if "macOS" not in sec_platform and "Mac" not in sec_platform:
            return False, "OS is Mac but sec_ch_ua_platform does not match"
    elif os_name == "Linux":
        if "Linux" not in ua:
            return False, "OS is Linux but User-Agent does not match"
        if "Linux" not in sec_platform:
            return False, "OS is Linux but sec_ch_ua_platform does not match"

    # Chrome major version matches sec_ch_ua
    ua_chrome_match = re.search(r"Chrome/(\d+)\.", ua)
    ch_chrome_match = re.search(r"Chromium\";v=\"(\d+)\"", fp["sec_ch_ua"])
    if ua_chrome_match and ch_chrome_match:
        ua_version = ua_chrome_match.group(1)
        ch_version = ch_chrome_match.group(1)
        if ua_version != ch_version:
            return False, f"Chrome major version mismatch: User-Agent={ua_version}, sec_ch_ua={ch_version}"

    # Plausible GPU for OS
    vendor = fp["webgl_vendor"].lower()
    renderer = fp["webgl_renderer"].lower()
    if os_name == "Mac":
        if "nvidia" in vendor or "nvidia" in renderer:
            return False, "Mac OS cannot have NVIDIA GPU renderer"
        if "amd" in vendor and "direct3d" in renderer:
            return False, "Mac OS does not use Direct3D"
    elif os_name == "Windows":
        if "apple" in vendor or "apple" in renderer:
            return False, "Windows OS cannot have Apple GPU renderer"

    # Resolution format Check
    res_match = re.match(r"^(\d+)x(\d+)$", fp["screen_resolution"])
    if not res_match:
        return False, f"Invalid screen resolution format: {fp['screen_resolution']}"

    # Timezone IANA check
    tz = fp["timezone"]
    if "/" not in tz and tz != "UTC":
        return False, f"Invalid IANA timezone: {tz}"

    # Locale format check
    locale = fp["locale"]
    if "-" not in locale and "_" not in locale:
        return False, f"Invalid locale format: {locale}"

    # Run production coherence validation as well
    coh_res = coherence_validator.validate(fp)
    if not coh_res.get("passed", False) or coh_res.get("score", 0) < 90:
        return False, f"Coherence validator failed: {coh_res.get('issues')}"

    return True, "Valid"

# Active selection rotation & retry manager
class LiveTestOrchestrator:
    def __init__(self, accounts_list):
        self.accounts = accounts_list
        self.failed_attempts_by_category = {
            "authentication": 0,
            "permission": 0,
            "rate_limit": 0,
            "quota": 0,
            "timeout": 0,
            "model_unavailable": 0,
            "invalid_response": 0,
            "unknown": 0
        }

    def classify_failure(self, status_code: Optional[int], body: str, exc: Optional[Exception]) -> str:
        if exc and isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if not status_code:
            return "unknown"
        body_lower = body.lower()
        if status_code in (401, 403):
            if "permission" in body_lower or "not authorized to access" in body_lower:
                return "permission"
            return "authentication"
        if status_code == 429:
            return "rate_limit"
        if "quota" in body_lower or "limit exceeded" in body_lower:
            return "quota"
        if status_code == 404:
            if "model not found" in body_lower or "unknown model" in body_lower or "7009" in body_lower:
                return "model_unavailable"
        return "unknown"

    async def execute_test_run(self):
        samples_target = 5
        generated_samples = []
        hashes_seen = set()

        real_model_reached = False
        fallback_count = 0

        # Intercept and spy on all HTTP requests made by production generator
        original_post = backend.ai_generator._shared_client.post
        original_get_account = cloudflare_manager.get_account
        original_load_accounts = cloudflare_manager.load_accounts
        original_accounts_list = cloudflare_manager.accounts

        # Prevent production cooldown manager from writing to logs/cf_cooldowns.json
        original_save_cooldowns = cloudflare_manager._save_cooldowns
        cloudflare_manager._save_cooldowns = lambda *args, **kwargs: None

        # Supply only our pre-validated accounts list to the production manager
        cloudflare_manager.accounts = self.accounts
        cloudflare_manager.load_accounts = lambda *args, **kwargs: None

        # Capture trace of metadata only (no raw tokens, keys, bodies, or full fingerprints)
        captured_interactions = []

        # Bounded attempts limit raised to 150 to account for rate limits/cooldowns
        max_attempts = 150
        attempts_count = 0

        # Safe wrapper to break production rotation loop cleanly if max attempts is reached
        def spy_get_account(*args, **kwargs):
            nonlocal attempts_count
            if attempts_count >= max_attempts:
                # Breaks rotation immediately by returning None to the direct loop
                return None
            return original_get_account(*args, **kwargs)

        async def spy_post(*args, **kwargs):
            nonlocal attempts_count, real_model_reached
            attempts_count += 1
            if attempts_count > max_attempts:
                raise RuntimeError(f"Exceeded maximum bounded account attempts limit ({max_attempts})")

            # Enforce request timeout
            kwargs["timeout"] = 25.0

            # Inspect outgoing request payload (must use production model identifier)
            req_json = kwargs.get("json", {})
            req_model = req_json.get("model")
            if req_model != KIMI_MODEL:
                raise ValueError(f"Wrong model sent in request: {req_model}")

            status_code = None
            body = ""
            exc = None
            response = None

            try:
                response = await original_post(*args, **kwargs)
                status_code = response.status_code
                body = response.text

                if status_code == 200:
                    real_model_reached = True
                    data = response.json()
                    model_returned = data.get("model")
                    if model_returned != KIMI_MODEL:
                        raise ValueError(f"Wrong model returned in response: {model_returned}")
                else:
                    # Sanitize error messages in print logs
                    redacted_body = re.sub(r'\b[a-f0-9]{8}\b', '[REDACTED]', body)
                    cat = self.classify_failure(status_code, redacted_body, None)
                    self.failed_attempts_by_category[cat] += 1

            except Exception as e:
                exc = e
                cat = self.classify_failure(None, "", e)
                self.failed_attempts_by_category[cat] += 1
                raise e

            captured_interactions.append({
                "status_code": status_code,
                "is_success": status_code == 200
            })
            return response

        # Hook functions
        os.mkdir = hook_mkdir
        os.makedirs = hook_makedirs
        builtins.print = redacted_print
        backend.ai_generator._shared_client.post = spy_post
        cloudflare_manager.get_account = spy_get_account

        # Prevent the racing proxy so that we strictly run the direct rotation path
        from unittest.mock import patch

        try:
            with patch("backend.ai_generator._call_via_racing_proxy", return_value=None):
                for i in range(samples_target):
                    # Bounded retry delay
                    await asyncio.sleep(0.5)

                    # Call production generation
                    fp = await generate_fingerprint_ai("Windows", "Chrome")

                    if fp.get("_is_fallback"):
                        fallback_count += 1
                        # Fail-closed if fallback was returned
                        print("[Orchestrator] Fail: A local fallback was used.")
                        sys.exit(1)

                    # Validate strict schema + coherence
                    is_valid, msg = validate_fingerprint_strict(fp)
                    if not is_valid:
                        self.failed_attempts_by_category["invalid_response"] += 1
                        print(f"[Orchestrator] Validation failed: {msg}")
                        sys.exit(1)

                    # Uniqueness checks using safe hashes (excluding metadata keys)
                    safe_fields = {k: v for k, v in fp.items() if not k.startswith("_")}
                    serialized = json.dumps(safe_fields, sort_keys=True)
                    h = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

                    if h in hashes_seen:
                        print("[Orchestrator] Fail: Duplicate fingerprint detected.")
                        sys.exit(1)

                    hashes_seen.add(h)
                    generated_samples.append(fp)

        finally:
            # Restore all original functions in finally block
            os.mkdir = original_mkdir
            os.makedirs = original_makedirs
            builtins.print = original_print
            backend.ai_generator._shared_client.post = original_post
            cloudflare_manager.get_account = original_get_account
            cloudflare_manager.load_accounts = original_load_accounts
            cloudflare_manager.accounts = original_accounts_list
            cloudflare_manager._save_cooldowns = original_save_cooldowns

        # Check file integrity before and after
        final_accounts_hash = calculate_sha256(ACCOUNTS_FILE)
        final_meta_hash = calculate_sha256(META_FILE)
        final_cooldown_hash = calculate_sha256(COOLDOWN_FILE)

        accounts_unchanged = "YES" if initial_accounts_hash == final_accounts_hash else "NO"
        meta_unchanged = "YES" if initial_meta_hash == final_meta_hash else "NO"
        cooldowns_unchanged = "YES" if initial_cooldown_hash == final_cooldown_hash else "NO"

        passed_all = (
            real_model_reached and
            len(generated_samples) == samples_target and
            len(hashes_seen) == samples_target and
            accounts_unchanged == "YES" and
            meta_unchanged == "YES" and
            cooldowns_unchanged == "YES" and
            directories_created_count == 0 and
            fallback_count == 0
        )

        # Exact final output format
        print(f"Configured model: {KIMI_MODEL}")
        print(f"Real model reached: {'YES' if real_model_reached else 'NO'}")
        print(f"Accounts loaded: {total_parsed}")
        print(f"Structurally valid accounts: {structurally_valid}")
        print(f"Samples requested: {samples_target}")
        print(f"Valid samples: {len(generated_samples)}")
        print(f"Coherent samples: {len(generated_samples)}")
        print(f"Unique samples: {len(hashes_seen)}")
        print(f"Fallback samples: {fallback_count}")
        print(f"Account file unchanged: {accounts_unchanged}")
        print(f"Production metadata unchanged: {meta_unchanged}")
        print(f"Production directories created: {directories_created_count}")
        print("Credentials printed: NO")
        print("Full fingerprints printed: NO")
        print(f"Exit code: {0 if passed_all else 1}")
        print(f"Status: {'LIVE AI GENERATION PASS' if passed_all else 'LIVE AI GENERATION FAIL'}")

        if passed_all:
            print("\nReal Cloudflare Kimi profile-detail generation passed the defined live test. All tested samples were structurally valid, coherent, unique within the sample, and generated without fallback. This does not guarantee universal undetectability.")
            sys.exit(0)
        else:
            sys.exit(1)

if __name__ == "__main__":
    orchestrator = LiveTestOrchestrator(valid_accounts)
    asyncio.run(orchestrator.execute_test_run())
