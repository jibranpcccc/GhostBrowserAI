import asyncio
import os
import re
import json
import random
import time
import httpx
from datetime import datetime, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Import the cloudflare manager (still used for status dashboard)
from backend.cloudflare_manager import cloudflare_manager
from backend.config import get_installed_chromium_major_version, get_installed_chromium_version

# Global shared HTTP client for connection pooling (significantly speeds up bulk creation)
_shared_client = httpx.AsyncClient(
    timeout=httpx.Timeout(12.0, connect=4.0),
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
)

RACING_PROXY_TIMEOUT_SECONDS = 2.0
# Kimi commonly needs 20+ seconds even for a tiny successful response. A
# five-second cutoff incorrectly treated healthy accounts as unavailable.
DIRECT_REQUEST_TIMEOUT_SECONDS = 60.0
DIRECT_PRIORITY_RACE_SIZE = 3
DIRECT_STANDARD_RACE_SIZE = 12
DIRECT_STANDARD_MAX_ACCOUNT_ATTEMPTS = 12
# Backwards-compatible names used by older tests/integrations.
DIRECT_RACE_SIZE = DIRECT_STANDARD_RACE_SIZE
DIRECT_MAX_ACCOUNT_ATTEMPTS = DIRECT_STANDARD_MAX_ACCOUNT_ATTEMPTS

# ============================================================
# HOW THE AI CALLS WORK:
#
# PRIMARY: A small rotating wave from the private priority
# Cloudflare pool is called directly. This avoids any proxy
# dependency while spreading quota use across fresh accounts.
#
# FALLBACK: Hermes Racing Proxy, then the standard local
# Cloudflare account pool.
# ============================================================

RACING_PROXY_URL = "http://127.0.0.1:8005/v1/chat/completions"
KIMI_MODEL       = "@cf/moonshotai/kimi-k2.7-code"
PROFILE_SCHEMA_VERSION = "ghostbrowser-fingerprint-v1"
PROMPT_VERSION = "kimi-profile-details-v2"

# OpenCode Zen (DeepSeek) — OpenAI-compatible endpoint. The free DeepSeek
# model is used by default so profile generation costs nothing.
ZEN_API_URL  = "https://opencode.ai/zen/v1/chat/completions"
ZEN_MODEL    = "deepseek-v4-flash-free"


def _extract_json_objects(raw_content) -> list[dict]:
    """Extract complete JSON objects from plain, fenced, or reasoned model output."""
    if isinstance(raw_content, dict):
        return [raw_content]
    if isinstance(raw_content, list):
        fragments = []
        for item in raw_content:
            if isinstance(item, str):
                fragments.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                fragments.append(item["text"])
        raw_content = "\n".join(fragments)
    if not isinstance(raw_content, str):
        return []

    cleaned = re.sub(r"```(?:json)?\s*", "", raw_content, flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    candidates = []

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            candidates.append(parsed)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            parsed, _ = decoder.raw_decode(cleaned[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed not in candidates:
            candidates.append(parsed)
    return candidates


def validate_fingerprint_schema(fingerprint: dict, expected_chrome_major: Optional[int] = None) -> dict:
    """Deterministically validate the profile fields used by the runtime.

    This is intentionally independent of an AI score: malformed data must fail
    before coherence scoring or persistence.
    """
    issues = []
    if not isinstance(fingerprint, dict):
        return {"passed": False, "issues": ["Fingerprint must be a JSON object."]}

    # Native font and plugin surfaces are inherited from the host/browser
    # environment; they must never be fabricated by the AI or API caller.
    for forbidden in ("fonts", "plugins"):
        if forbidden in fingerprint:
            issues.append(f"{forbidden} must not be present in fingerprint; surface is inherited from host")

    string_fields = (
        "userAgent", "platform", "os", "screen_resolution", "webgl_vendor",
        "webgl_renderer", "timezone", "locale", "sec_ch_ua",
        "sec_ch_ua_platform",
    )
    for field in string_fields:
        value = fingerprint.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{field} must be a non-empty string")

    if fingerprint.get("os") not in ("Windows", "Mac", "Linux"):
        issues.append("os must be Windows, Mac, or Linux")
    if fingerprint.get("platform") not in ("Win32", "MacIntel", "Linux x86_64", "Linux armv8l"):
        issues.append("platform is not an allowed native platform value")
    expected_platforms = {
        "Windows": {"Win32"},
        "Mac": {"MacIntel"},
        "Linux": {"Linux x86_64", "Linux armv8l"},
    }
    if fingerprint.get("platform") not in expected_platforms.get(fingerprint.get("os"), set()):
        issues.append("platform does not match os")
    if not re.fullmatch(r"\d{3,5}x\d{3,5}", str(fingerprint.get("screen_resolution", ""))):
        issues.append("screen_resolution must use WIDTHxHEIGHT")
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?", str(fingerprint.get("locale", ""))):
        issues.append("locale has an invalid format")

    bounded_ints = {
        "hardwareConcurrency": (1, 32),
        "deviceMemory": (1, 64),
        "cpu_cores": (1, 32),
        "memory_gb": (1, 64),
        "screen_color_depth": (16, 48),
    }
    for field, (minimum, maximum) in bounded_ints.items():
        value = fingerprint.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            issues.append(f"{field} must be an integer from {minimum} to {maximum}")

    languages = fingerprint.get("languages")
    if not isinstance(languages, list) or not languages or not all(
        isinstance(item, str) and item.strip() for item in languages
    ):
        issues.append("languages must be a non-empty array of strings")
    elif languages[0] != fingerprint.get("locale"):
        issues.append("languages[0] must match locale")

    try:
        ZoneInfo(str(fingerprint.get("timezone", "")))
    except (ZoneInfoNotFoundError, ValueError):
        issues.append("timezone must be a valid IANA timezone")

    if fingerprint.get("hardwareConcurrency") != fingerprint.get("cpu_cores"):
        issues.append("hardwareConcurrency must equal cpu_cores")
    if fingerprint.get("deviceMemory") != fingerprint.get("memory_gb"):
        issues.append("deviceMemory must equal memory_gb")

    expected_ch_platform = {"Windows": '"Windows"', "Mac": '"macOS"', "Linux": '"Linux"'}
    if fingerprint.get("sec_ch_ua_platform") != expected_ch_platform.get(fingerprint.get("os")):
        issues.append("sec_ch_ua_platform does not match os")

    hints = fingerprint.get("client_hints")
    if not isinstance(hints, dict):
        issues.append("client_hints must be an object")
        hints = {}
    for field in ("architecture", "bitness", "model", "platformVersion", "uaFullVersion"):
        if field not in hints or not isinstance(hints.get(field), str):
            issues.append(f"client_hints.{field} must be a string")

    for field in ("canvas_noise", "webgl_noise", "audio_noise"):
        if not isinstance(fingerprint.get(field), bool):
            issues.append(f"{field} must be a boolean")

    if expected_chrome_major is not None:
        ua_match = re.search(r"Chrome/(\d+)", str(fingerprint.get("userAgent", "")))
        sec_match = re.search(r'Chromium";v="(\d+)"', str(fingerprint.get("sec_ch_ua", "")))
        full_match = re.match(r"^(\d+)\.", str(hints.get("uaFullVersion", "")))
        versions = {
            "userAgent": ua_match,
            "sec_ch_ua": sec_match,
            "client_hints.uaFullVersion": full_match,
        }
        for field, match in versions.items():
            if not match or int(match.group(1)) != expected_chrome_major:
                issues.append(f"{field} Chrome version must be {expected_chrome_major}")

    return {"passed": not issues, "issues": issues}


def _finalize_ai_result(parsed: dict, response_data: dict, source: str, chrome_major_version: int, requested_model: Optional[str] = None) -> Optional[dict]:
    """Validate response attribution and attach honest generation provenance."""
    if not isinstance(parsed, dict):
        return None

    if requested_model is None:
        requested_model = KIMI_MODEL

    reported_model = response_data.get("model") if isinstance(response_data, dict) else None
    if reported_model is not None:
        # Accept the response if it reports the exact model we requested or matches family prefix
        is_model_match = (
            reported_model == requested_model
            or (isinstance(reported_model, str) and isinstance(requested_model, str) and (
                reported_model.startswith(requested_model.replace("-latest", ""))
                or requested_model.startswith(reported_model.replace("-latest", ""))
            ))
        )
        if not isinstance(reported_model, str) or not is_model_match:
            print(f"[AI Generator] Rejected response: reported model '{reported_model}' did not match requested '{requested_model}'.")
            return None

    parsed = sanitize_native_surface_fields(parsed)
    parsed.setdefault("notification_permission", "default")
    schema_result = validate_fingerprint_schema(parsed, chrome_major_version)
    if not schema_result["passed"]:
        print(f"[AI Generator] Rejected fingerprint schema: {schema_result['issues']}")
        return None

    parsed["_is_fallback"] = False
    parsed["_source"] = source
    parsed["_provenance"] = {
        "verified": True,
        "requested_model": requested_model,
        "reported_model": reported_model,
        "reported_model_verified": reported_model == requested_model,
        "source": source,
        "prompt_version": PROMPT_VERSION,
        "schema_version": PROFILE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": "generator_schema_passed",
    }
    return parsed

def build_system_prompt(chrome_major_version: int) -> str:
    return f"""You generate internally coherent browser profile metadata for privacy testing.
You MUST output ONLY a single valid JSON object — no markdown, no backticks, no explanation.
Generate a plausible, fully coherent hardware and software profile. Do not claim that it is undetectable.

STRICT RULES:
- userAgent Chrome version MUST match version numbers in client_hints and sec_ch_ua.
- WebGL vendor + renderer must be a real matching GPU combo.
- Mac OS must use Apple or Intel GPU — NEVER NVIDIA with Direct3D.
- Windows must use ANGLE renderer strings.
- CPU cores and RAM must be realistic (e.g. 8 cores needs at least 16GB RAM).
- Prefer common device classes over rare combinations: 8 cores/16GB/1080p or 16 cores/32GB/1440p on Windows.
- Do not maximize uniqueness. Common coherent values are preferred to unusual high-entropy values.
- timezone and locale must logically match (Europe/Berlin -> de-DE).
- languages array must match locale (de-DE -> ["de-DE","de","en-US"]).
- Do not generate `fonts`.
- Do not generate `plugins`.
- Font availability is inherited from the real host/browser environment.
- Plugin and MIME collections are inherited from the real Chromium binary.
- These surfaces must not be fabricated for profile uniqueness.
- Chrome version must be exactly {chrome_major_version}.

OUTPUT THIS EXACT JSON STRUCTURE (fill in real values):
{{
  "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_major_version}.0.0.0 Safari/537.36",
  "platform": "Win32",
  "os": "Windows",
  "hardwareConcurrency": 8,
  "deviceMemory": 16,
  "cpu_cores": 8,
  "memory_gb": 16,
  "screen_resolution": "1920x1080",
  "screen_color_depth": 24,
  "webgl_vendor": "Google Inc. (NVIDIA)",
  "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
  "timezone": "America/New_York",
  "locale": "en-US",
  "languages": ["en-US", "en"],
  "sec_ch_ua": "\\\"Not)A;Brand\\\";v=\\\"8\\\", \\\"Chromium\\\";v=\\\"{chrome_major_version}\\\", \\\"Google Chrome\\\";v=\\\"{chrome_major_version}\\\"",
  "sec_ch_ua_platform": "\\\"Windows\\\"",
  "client_hints": {{
    "architecture": "x86",
    "bitness": "64",
    "model": "",
    "platformVersion": "10.0.0",
    "uaFullVersion": "{chrome_major_version}.0.0.0"
  }},
  "canvas_noise": true,
  "webgl_noise": true,
  "audio_noise": true,
  "behavior": {{
    "typing_speed_wpm": 65,
    "mistake_probability": 0.04,
    "mouse_speed_multiplier": 0.9,
    "reading_speed_wpm": 220,
    "scroll_speed": 280
  }}
}}"""


async def _call_via_racing_proxy(target_os: str, target_browser: str, chrome_major_version: int) -> Optional[dict]:
    """
    Call Kimi AI through the Hermes Racing Proxy (port 8005).
    Account inventory is owned by that service and is not asserted here.
    """
    user_prompt = (
        f"Generate a complete realistic fingerprint for a {target_os} machine "
        f"running {target_browser} with Chrome major version {chrome_major_version}. "
        f"Output ONLY the JSON object with all required fields."
    )
    system_prompt = build_system_prompt(chrome_major_version)
    payload = {
        "model": KIMI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt}
        ],
        "max_tokens": 2048
    }
    try:
        response = await _shared_client.post(
            RACING_PROXY_URL,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=RACING_PROXY_TIMEOUT_SECONDS,
        )

        if response.status_code == 200:
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
            for parsed in _extract_json_objects(raw_content):
                finalized = _finalize_ai_result(parsed, data, "racing_proxy", chrome_major_version, requested_model=KIMI_MODEL)
                if finalized:
                    print("[AI Generator] Kimi profile details generated via racing proxy.")
                    return finalized
            print("[AI Generator] Racing proxy returned no valid fingerprint JSON object.")
            return None
        else:
            print(f"[AI Generator] ⚠️  Racing proxy returned HTTP {response.status_code}")
            return None
    except httpx.ConnectError:
        print(f"[AI Generator] ⚠️  Racing proxy not running on port 8005. Falling back to direct API...")
        return None
    except json.JSONDecodeError:
        print(f"[AI Generator] ⚠️  Racing proxy returned invalid JSON. Retrying...")
        return None
    except Exception as e:
        print(f"[AI Generator] Racing proxy failed: {type(e).__name__}")
        return None


def _get_mistral_api_keys() -> list[str]:
    """Return all configured Mistral API keys from environment variables or ~/.hermes/.env.

    Supports:
      - plain MISTRAL_API_KEY
      - numbered MISTRAL_API_KEY_1 ... MISTRAL_API_KEY_N
      - automatic fallback to ~/.hermes/.env
    """
    keys: list[str] = []
    plain = os.environ.get("MISTRAL_API_KEY")
    if plain:
        keys.append(plain)
    for i in range(1, 1000):
        key = os.environ.get(f"MISTRAL_API_KEY_{i}")
        if not key:
            continue
        if key not in keys:
            keys.append(key)

    if not keys:
        hermes_env = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(hermes_env):
            try:
                with open(hermes_env, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if "MISTRAL_API_KEY" in line and "=" in line:
                            val = line.split("=", 1)[1].strip()
                            if val and val not in keys:
                                keys.append(val)
            except Exception:
                pass

    return [k.strip() for k in keys if k.strip()]


async def _call_mistral_api(target_os: str, target_browser: str,
                            chrome_major_version: int) -> Optional[dict]:
    """
    Call Mistral AI (api.mistral.ai) with all configured API keys in parallel.

    Returns the first valid fingerprint JSON. Accounts are independent, so we
    race them similarly to the Cloudflare account pool.
    """
    api_keys = _get_mistral_api_keys()
    if not api_keys:
        return None

    model_name = os.environ.get("MISTRAL_MODEL", "codestral-latest")
    race_size = int(os.environ.get("MISTRAL_RACE_SIZE", "3"))
    timeout_seconds = float(os.environ.get("MISTRAL_REQUEST_TIMEOUT", "45.0"))
    inter_batch_delay = float(os.environ.get("MISTRAL_INTER_BATCH_DELAY", "1.0"))

    user_prompt = (
        f"Generate a complete realistic fingerprint for a {target_os} machine "
        f"running {target_browser} with Chrome major version {chrome_major_version}. "
        f"Output ONLY the JSON object with all required fields."
    )
    system_prompt = build_system_prompt(chrome_major_version)

    async def try_key(api_key: str) -> Optional[dict]:
        try:
            response = await _shared_client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 2048,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout_seconds,
            )

            if response.status_code == 200:
                data = response.json()
                raw_content = data["choices"][0]["message"]["content"]
                for parsed in _extract_json_objects(raw_content):
                    finalized = _finalize_ai_result(
                        parsed, data, "mistral_api", chrome_major_version, requested_model=model_name
                    )
                    if finalized:
                        print(f"[AI Generator] Mistral profile details generated (model={model_name}).")
                        return finalized
                print("[AI Generator] Mistral returned no valid fingerprint JSON object.")

            elif response.status_code in (401, 403):
                print(f"[AI Generator] Mistral auth failed ({response.status_code}). Check API key.")
            elif response.status_code == 429:
                print("[AI Generator] Mistral rate-limited. Key exhausted or too many requests.")
            else:
                print(f"[AI Generator] Mistral HTTP {response.status_code}.")

        except httpx.TimeoutException:
            print("[AI Generator] Mistral request timed out.")
        except Exception as e:
            print(f"[AI Generator] Mistral request failed: {type(e).__name__}")

        return None

    # Bounded parallel racing across keys. Trial/free keys often have per-key
    # rate limits, so keep batches small and add a short pause between batches.
    for offset in range(0, len(api_keys), race_size):
        batch = api_keys[offset:offset + race_size]
        tasks = {asyncio.create_task(try_key(key)) for key in batch}
        try:
            while tasks:
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    result = task.result()
                    if result:
                        for pending in tasks:
                            pending.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        return result
        finally:
            for pending in tasks:
                pending.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        if offset + race_size < len(api_keys):
            await asyncio.sleep(inter_batch_delay)

    print(f"[AI Generator] All {len(api_keys)} Mistral keys exhausted.")
    return None


def _get_zen_api_keys() -> list[str]:
    """Return all configured OpenCode Zen API keys from environment variables.

    Supports:
      - plain ZEN_API_KEY
      - numbered ZEN_API_KEY_1 ... ZEN_API_KEY_N
    """
    keys: list[str] = []
    plain = os.environ.get("ZEN_API_KEY")
    if plain:
        keys.append(plain)
    for i in range(1, 1000):
        key = os.environ.get(f"ZEN_API_KEY_{i}")
        if not key:
            continue
        if key not in keys:
            keys.append(key)
    return [k.strip() for k in keys if k.strip()]


def _safe_int_env(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    """Parse an integer env var, falling back to ``default`` and clamping to
    ``[minimum, maximum]`` (invalid/zero/oversized config must not raise: the
    caller falls through to the next provider)."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    if maximum is not None:
        value = min(maximum, value)
    return max(minimum, value)


def _safe_float_env(name: str, default: float, minimum: float) -> float:
    """Parse a positive float env var with the same fail-safe contract as
    ``_safe_int_env``. NaN/inf values are treated as invalid (they parse but
    are not usable timeout/delay values)."""
    import math

    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(minimum, value)


async def _call_zen_deepseek_api(target_os: str, target_browser: str,
                                 chrome_major_version: int) -> Optional[dict]:
    """Call DeepSeek through OpenCode Zen with all configured keys in parallel.

    OpenAI-compatible chat completions endpoint (opencode.ai/zen). Uses the
    free DeepSeek model by default. Returns the first valid fingerprint JSON,
    racing keys like the Mistral pool.
    """
    api_keys = _get_zen_api_keys()
    if not api_keys:
        return None

    raw_fallback_models = os.environ.get("ZEN_FALLBACK_MODELS", "")
    if raw_fallback_models.strip():
        models = [m.strip() for m in raw_fallback_models.split(",") if m.strip()]
    else:
        models = [os.environ.get("ZEN_MODEL", ZEN_MODEL)]

    race_size = _safe_int_env("ZEN_RACE_SIZE", 3, minimum=1, maximum=32)
    timeout_seconds = _safe_float_env("ZEN_REQUEST_TIMEOUT", 60.0, minimum=1.0)
    inter_batch_delay = _safe_float_env("ZEN_INTER_BATCH_DELAY", 1.0, minimum=0.0)

    user_prompt = (
        f"Generate a complete realistic fingerprint for a {target_os} machine "
        f"running {target_browser} with Chrome major version {chrome_major_version}. "
        f"Output ONLY the JSON object with all required fields."
    )
    system_prompt = build_system_prompt(chrome_major_version)

    for model_name in models:
        async def try_key(api_key: str) -> Optional[dict]:
            try:
                response = await _shared_client.post(
                    ZEN_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 2048,
                    },
                    timeout=timeout_seconds,
                )

                if response.status_code == 200:
                    data = response.json()
                    raw_content = data["choices"][0]["message"]["content"]
                    for parsed in _extract_json_objects(raw_content):
                        finalized = _finalize_ai_result(
                            parsed, data, "zen_deepseek", chrome_major_version, requested_model=model_name
                        )
                        if finalized:
                            print(f"[AI Generator] Zen DeepSeek profile details generated (model={model_name}).")
                            return finalized
                    print("[AI Generator] Zen DeepSeek returned no valid fingerprint JSON object.")

                elif response.status_code in (401, 403):
                    print(f"[AI Generator] Zen DeepSeek auth failed ({response.status_code}). Check ZEN_API_KEY.")
                elif response.status_code == 429:
                    print(f"[AI Generator] Zen DeepSeek rate-limited on {model_name}. Key exhausted or too many requests.")
                else:
                    print(f"[AI Generator] Zen DeepSeek HTTP {response.status_code}.")

            except httpx.TimeoutException:
                print(f"[AI Generator] Zen DeepSeek request timed out on {model_name}.")
            except Exception as e:
                print(f"[AI Generator] Zen DeepSeek request failed: {type(e).__name__}")

            return None

        # Bounded parallel racing across keys for this model
        for offset in range(0, len(api_keys), race_size):
            batch = api_keys[offset:offset + race_size]
            tasks = {asyncio.create_task(try_key(key)) for key in batch}
            try:
                while tasks:
                    done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        result = task.result()
                        if result:
                            for pending in tasks:
                                pending.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            return result
            finally:
                for pending in tasks:
                    pending.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            if offset + race_size < len(api_keys):
                await asyncio.sleep(inter_batch_delay)

    print(f"[AI Generator] All {len(api_keys)} Zen DeepSeek keys exhausted across {len(models)} models.")
    return None


async def _call_direct_cloudflare(target_os: str, target_browser: str,
                                  chrome_major_version: int,
                                  priority: Optional[bool] = None) -> Optional[dict]:
    """
    Call Cloudflare Workers AI using a bounded rotating account tier.

    ``priority=True`` selects only the private priority pool,
    ``priority=False`` selects only the standard pool, and ``None`` preserves
    the legacy all-account behavior for internal callers/tests.
    Uses the /ai/v1/chat/completions OpenAI-compatible endpoint.
    """
    cloudflare_manager.load_accounts()
    all_accounts = list(cloudflare_manager.accounts)

    if not all_accounts:
        print("[AI Generator] ❌ No local Cloudflare accounts are configured.")
        return None

    user_prompt = (
        f"Generate a complete realistic fingerprint for a {target_os} machine "
        f"running {target_browser} with Chrome major version {chrome_major_version}. "
        f"Output ONLY the JSON object with all required fields."
    )
    system_prompt = build_system_prompt(chrome_major_version)
    now = time.time()
    cooldowns = getattr(cloudflare_manager, "cooldowns", {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
    if priority is None:
        eligible_accounts = [
            account for account in all_accounts
            if now > cooldowns.get(account["account_id"], 0)
        ]
        random.SystemRandom().shuffle(eligible_accounts)
        eligible_accounts = eligible_accounts[:DIRECT_MAX_ACCOUNT_ATTEMPTS]
        race_size = DIRECT_RACE_SIZE
    else:
        race_size = DIRECT_PRIORITY_RACE_SIZE if priority else DIRECT_STANDARD_RACE_SIZE
        max_accounts = race_size if priority else DIRECT_STANDARD_MAX_ACCOUNT_ATTEMPTS
        get_candidates = getattr(cloudflare_manager, "get_account_candidates", None)
        if callable(get_candidates):
            eligible_accounts = get_candidates(
                priority=priority,
                max_accounts=max_accounts,
                rotate_by=race_size,
            )
        else:
            # Compatibility for lightweight test doubles and older integrations.
            eligible_accounts = [
                account for account in all_accounts
                if bool(account.get("priority")) is priority
                and now > cooldowns.get(account["account_id"], 0)
            ]
            random.SystemRandom().shuffle(eligible_accounts)
            eligible_accounts = eligible_accounts[:max_accounts]

    if not eligible_accounts:
        return None

    async def try_account(account: dict) -> Optional[dict]:
        account_id = account["account_id"]
        token       = account["token"]

        try:
            response = await _shared_client.post(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": KIMI_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt}
                    ],
                    "max_tokens": 2048
                },
                timeout=DIRECT_REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 200:
                data = response.json()
                raw_content = data["choices"][0]["message"]["content"]
                for parsed in _extract_json_objects(raw_content):
                    finalized = _finalize_ai_result(
                        parsed, data, "direct_cloudflare", chrome_major_version, requested_model=KIMI_MODEL
                    )
                    if finalized:
                        print("[AI Generator] Kimi profile details generated via direct Cloudflare.")
                        return finalized
                print("[AI Generator] Cloudflare returned no valid fingerprint JSON object.")
                # Continue with the remaining eligible accounts if attribution or
                # schema validation rejected this response.

            elif response.status_code in (401, 403):
                print(f"[AI Generator] ❌ Cloudflare authentication failed ({response.status_code}). Cooldown 60min.")
                cloudflare_manager.report_failure(account_id, cooldown_minutes=60)

            elif response.status_code == 404:
                print("[AI Generator] ❌ Cloudflare returned 404. Cooldown 60min.")
                cloudflare_manager.report_failure(account_id, cooldown_minutes=60)

            elif response.status_code == 429:
                print("[AI Generator] ⚠️  Cloudflare account rate limited. Cooldown 5min.")
                cloudflare_manager.report_failure(account_id, cooldown_minutes=5)

            else:
                print(f"[AI Generator] ⚠️  Cloudflare HTTP {response.status_code}. Cooldown 5min.")
                cloudflare_manager.report_failure(account_id, cooldown_minutes=5)

        except httpx.TimeoutException:
            print("[AI Generator] ⏱️  Cloudflare request timed out.")
            cloudflare_manager.report_failure(account_id, cooldown_minutes=2)
        except Exception as e:
            print(f"[AI Generator] ❌ Cloudflare request failed: {type(e).__name__}")

        return None

    # Race bounded batches rather than waiting on hundreds of exhausted
    # accounts sequentially. Stop on the first valid Kimi response.
    for offset in range(0, len(eligible_accounts), race_size):
        tasks = {
            asyncio.create_task(try_account(account))
            for account in eligible_accounts[offset:offset + race_size]
        }
        try:
            while tasks:
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    result = task.result()
                    if result:
                        for pending in tasks:
                            pending.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        return result
        finally:
            for pending in tasks:
                pending.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    return None


def sanitize_native_surface_fields(fingerprint: dict) -> dict:
    if not isinstance(fingerprint, dict):
        return fingerprint
    sanitized = fingerprint.copy()
    sanitized.pop("fonts", None)
    sanitized.pop("plugins", None)
    return sanitized

async def generate_fingerprint_ai(target_os: str = "Windows", target_browser: str = "Chrome", chrome_major_version: Optional[int] = None) -> dict:
    """
    Main entry point. Tries:
    1. OpenCode Zen (DeepSeek) — free model, rotating all configured ZEN_API_KEY_* env vars
    2. Local fallback generator (returns _is_fallback=True — STRICT MODE will refuse this)
    """
    if chrome_major_version is None:
        chrome_major_version = get_installed_chromium_major_version()

    # --- ATTEMPT 1: OpenCode Zen DeepSeek (fast, OpenAI-compatible, free model) ---
    result = await _call_zen_deepseek_api(target_os, target_browser, chrome_major_version)
    if result:
        return sanitize_native_surface_fields(result)

    # --- ATTEMPT 2: Local fallback (will be refused by strict mode) ---
    print("[AI Generator] ❌ All AI providers failed. Returning fallback (will be refused by strict mode).")
    fallback = sanitize_native_surface_fields(generate_fingerprint_fallback(target_os))
    fallback["_provenance"] = {
        "verified": True,
        "requested_model": "local-deterministic-generator",
        "reported_model": None,
        "reported_model_verified": False,
        "source": "local_fallback",
        "prompt_version": None,
        "schema_version": PROFILE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": "local_schema_passed",
    }
    return fallback


def generate_fingerprint_fallback(target_os: str = "Windows") -> dict:
    """
    Local fallback generator. Returns _is_fallback=True so the
    The validated profile orchestrator refuses to create the profile in strict mode.
    """
    chosen_os = target_os if target_os in ("Windows", "Mac") else "Windows"
    is_mac = chosen_os == "Mac"
    chrome_version = get_installed_chromium_major_version()
    current_chrome_full_ver = get_installed_chromium_version()

    if is_mac:
        gpu_vendor   = "Apple" if random.random() < 0.6 else "Intel Inc."
        gpu_renderer = random.choice(["Apple M1", "Apple M2", "Apple M3", "Apple M1 Pro"]) if "Apple" in gpu_vendor else random.choice(["Intel Iris OpenGL Engine", "Intel UHD Graphics 630 OpenGL Engine"])
        memory = random.choice([8, 16, 32])
        cores  = random.choice([8, 10, 12])
        screen_res = random.choice(["2560x1600", "2560x1664", "1440x900", "1920x1200"])
        platform   = "MacIntel"
        ua_os      = "Macintosh; Intel Mac OS X 10_15_7"
        sec_platform = '"macOS"'
        arch = "arm"; plat_version = "14.0.0"
        timezone = random.choice(["America/Los_Angeles", "America/New_York", "America/Chicago"])
        locale   = "en-US"
    else:
        gpu_vendor   = "Google Inc. (NVIDIA)"
        gpu_renderer = random.choice([
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ])
        memory = random.choice([16, 32])
        cores  = random.choice([8, 12, 16])
        screen_res   = random.choice(["1920x1080", "2560x1440", "1920x1200"])
        platform     = "Win32"
        ua_os        = "Windows NT 10.0; Win64; x64"
        sec_platform = '"Windows"'
        arch = "x86"; plat_version = "10.0.0"
        timezone = random.choice(["America/New_York", "America/Chicago", "Europe/London", "Europe/Berlin"])
        locale   = "en-US"

    return {
        "userAgent": f"Mozilla/5.0 ({ua_os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{current_chrome_full_ver} Safari/537.36",
        "platform": platform, "os": chosen_os,
        "hardwareConcurrency": cores, "deviceMemory": memory,
        "cpu_cores": cores, "memory_gb": memory,
        "screen_resolution": screen_res, "screen_color_depth": 24,
        "webgl_vendor": gpu_vendor, "webgl_renderer": gpu_renderer,
        "timezone": timezone, "locale": locale,
        "languages": [locale, locale.split("-")[0]] if locale != "en-US" else ["en-US", "en"],
        "sec_ch_ua": f'"Not)A;Brand";v="8", "Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}"',
        "sec_ch_ua_platform": sec_platform,
        "client_hints": {
            "architecture": arch,
            "bitness": "64",
            "model": "",
            "platformVersion": plat_version,
            "uaFullVersion": current_chrome_full_ver
        },
        "canvas_noise": True,
        "webgl_noise": True,
        "audio_noise": True,
        "notification_permission": "default",
        "_is_fallback": True
    }
