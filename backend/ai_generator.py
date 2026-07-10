import asyncio
import os
import re
import json
import random
import time
import httpx
from typing import Dict, Optional

# Import the cloudflare manager (still used for status dashboard)
# Import the cloudflare manager (still used for status dashboard)
from backend.cloudflare_manager import cloudflare_manager

# Global shared HTTP client for connection pooling (significantly speeds up bulk creation)
_shared_client = httpx.AsyncClient(timeout=45.0, limits=httpx.Limits(max_keepalive_connections=50, max_connections=100))

# ============================================================
# HOW THE AI CALLS WORK:
#
# PRIMARY: The Hermes Racing Proxy (port 8005) is already running
# with 434 Cloudflare accounts loaded from ~/.hermes/.env.
# We call it via the OpenAI-compatible endpoint. The proxy
# handles racing, rotation, and failover automatically.
#
# FALLBACK: If the racing proxy is down, we call Cloudflare
# directly using accounts from cloudflare_accounts.txt.
# ============================================================

RACING_PROXY_URL = "http://127.0.0.1:8005/v1/chat/completions"
KIMI_MODEL       = "@cf/moonshotai/kimi-k2.7-code"

SYSTEM_PROMPT = """You are a professional anti-detect browser fingerprint generator.
You MUST output ONLY a single valid JSON object — no markdown, no backticks, no explanation.
Generate a highly realistic, fully coherent hardware and software profile.

STRICT RULES:
- userAgent Chrome version MUST match version numbers in client_hints and sec_ch_ua.
- WebGL vendor + renderer must be a real matching GPU combo.
- Mac OS must use Apple or Intel GPU — NEVER NVIDIA with Direct3D.
- Windows must use ANGLE renderer strings.
- CPU cores and RAM must be realistic (e.g. 8 cores needs at least 16GB RAM).
- timezone and locale must logically match (Europe/Berlin -> de-DE).
- languages array must match locale (de-DE -> ["de-DE","de","en-US"]).
- fonts must be 5-8 real OS-specific system fonts.
- Chrome version must be between 130 and 137.

OUTPUT THIS EXACT JSON STRUCTURE (fill in real values):
{
  "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
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
  "sec_ch_ua": "\\\"Not)A;Brand\\\";v=\\\"8\\\", \\\"Chromium\\\";v=\\\"135\\\", \\\"Google Chrome\\\";v=\\\"135\\\"",
  "sec_ch_ua_platform": "\\\"Windows\\\"",
  "client_hints": {
    "architecture": "x86",
    "bitness": "64",
    "model": "",
    "platformVersion": "10.0.0",
    "uaFullVersion": "135.0.0.0"
  },
  "fonts": ["Arial", "Calibri", "Cambria", "Consolas", "Georgia"],
  "plugins": ["Chrome PDF Viewer", "Native Client"],
  "canvas_noise": true,
  "webgl_noise": true,
  "audio_noise": true,
  "behavior": {
    "typing_speed_wpm": 65,
    "mistake_probability": 0.04,
    "mouse_speed_multiplier": 0.9,
    "reading_speed_wpm": 220,
    "scroll_speed": 280
  }
}"""


async def _call_via_racing_proxy(target_os: str, target_browser: str) -> Optional[dict]:
    """
    Call Kimi AI through the Hermes Racing Proxy (port 8005).
    This proxy has 434 accounts loaded and handles rotation automatically.
    """
    user_prompt = (
        f"Generate a complete realistic fingerprint for a {target_os} machine "
        f"running {target_browser} with Chrome version between 130 and 137. "
        f"Output ONLY the JSON object with all required fields."
    )
    payload = {
        "model": KIMI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt}
        ],
        "max_tokens": 1024
    }
    try:
        response = await _shared_client.post(
            RACING_PROXY_URL,
            headers={"Content-Type": "application/json"},
            json=payload
        )

        if response.status_code == 200:
            data = response.json()
            # OpenAI-compatible response format
            raw_text = data["choices"][0]["message"]["content"]
            raw_text = re.sub(r'```json\s*', '', raw_text)
            raw_text = re.sub(r'```\s*', '', raw_text)
            raw_text = raw_text.strip()
            parsed = json.loads(raw_text)
            parsed["_is_fallback"] = False
            parsed["_source"] = "racing_proxy"
            print(f"[AI Generator] ✅ Kimi AI via Racing Proxy (434 accounts) — Success!")
            return parsed
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
        print(f"[AI Generator] ⚠️  Racing proxy error: {e}")
        return None


async def _call_direct_cloudflare(target_os: str, target_browser: str) -> Optional[dict]:
    """
    Direct fallback: call Cloudflare Workers AI using accounts from cloudflare_accounts.txt.
    Uses the /ai/v1/chat/completions OpenAI-compatible endpoint.
    """
    cloudflare_manager.load_accounts()
    all_accounts = cloudflare_manager.accounts

    if not all_accounts:
        print("[AI Generator] ❌ No accounts in cloudflare_accounts.txt either.")
        return None

    user_prompt = (
        f"Generate a complete realistic fingerprint for a {target_os} machine "
        f"running {target_browser} with Chrome version between 130 and 137. "
        f"Output ONLY the JSON object with all required fields."
    )

    tried = set()
    max_tries = len(all_accounts)

    for attempt in range(max_tries):
        account = cloudflare_manager.get_account()
        if not account or account["account_id"] in tried:
            break
        tried.add(account["account_id"])

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
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_prompt}
                    ],
                    "max_tokens": 1024
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                raw_text = data["choices"][0]["message"]["content"]
                raw_text = re.sub(r'```json\s*', '', raw_text)
                raw_text = re.sub(r'```\s*', '', raw_text)
                raw_text = raw_text.strip()
                parsed = json.loads(raw_text)
                parsed["_is_fallback"] = False
                parsed["_source"] = "direct_cloudflare"
                print(f"[AI Generator] ✅ Kimi AI via direct Cloudflare ({account_id[:8]}...) — Success!")
                return parsed

            elif response.status_code in (401, 403):
                print(f"[AI Generator] ❌ {account_id[:8]}... auth failed ({response.status_code}). Cooldown 60min.")
                cloudflare_manager.report_failure(account_id, cooldown_minutes=60)

            elif response.status_code == 404:
                body = response.text[:200]
                print(f"[AI Generator] ❌ {account_id[:8]}... 404. Bad account ID? Body: {body}")
                cloudflare_manager.report_failure(account_id, cooldown_minutes=60)

            elif response.status_code == 429:
                print(f"[AI Generator] ⚠️  {account_id[:8]}... rate limited. Cooldown 5min.")
                cloudflare_manager.report_failure(account_id, cooldown_minutes=5)

            else:
                print(f"[AI Generator] ⚠️  {account_id[:8]}... HTTP {response.status_code}. Cooldown 5min.")
                cloudflare_manager.report_failure(account_id, cooldown_minutes=5)

            await asyncio.sleep(0.3)

        except httpx.TimeoutException:
            print(f"[AI Generator] ⏱️  {account_id[:8]}... timeout.")
            cloudflare_manager.report_failure(account_id, cooldown_minutes=2)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[AI Generator] ❌ {account_id[:8]}... exception: {e}")

    return None


async def generate_fingerprint_ai(target_os: str = "Windows", target_browser: str = "Chrome") -> dict:
    """
    Main entry point. Tries:
    1. Hermes Racing Proxy (port 8005) — 434 accounts, racing mode
    2. Direct Cloudflare API — accounts from cloudflare_accounts.txt
    3. Local fallback generator (returns _is_fallback=True — STRICT MODE will refuse this)
    """
    # --- ATTEMPT 1: Racing proxy (Hermes) ---
    result = await _call_via_racing_proxy(target_os, target_browser)
    if result:
        return result

    # --- ATTEMPT 2: Direct Cloudflare ---
    result = await _call_direct_cloudflare(target_os, target_browser)
    if result:
        return result

    # --- ATTEMPT 3: Local fallback (will be rejected by strict mode) ---
    print("[AI Generator] ❌ All Cloudflare methods failed. Returning fallback (will be refused by strict mode).")
    return generate_fingerprint_fallback()


def generate_fingerprint_fallback() -> dict:
    """
    Local fallback generator. Returns _is_fallback=True so the
    Zero-Leak Orchestrator REFUSES to create the profile in strict mode.
    """
    os_choices = ["Windows", "Mac"]
    chosen_os = random.choice(os_choices)
    is_mac = chosen_os == "Mac"
    chrome_version = random.randint(130, 137)

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
        fonts    = ["Arial", "Helvetica", "Georgia", "Courier New", "Monaco", "San Francisco"]
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
        fonts    = ["Arial", "Calibri", "Cambria", "Consolas", "Georgia", "Times New Roman", "Verdana"]

    return {
        "userAgent": f"Mozilla/5.0 ({ua_os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36",
        "platform": platform, "os": chosen_os,
        "hardwareConcurrency": cores, "deviceMemory": memory,
        "cpu_cores": cores, "memory_gb": memory,
        "screen_resolution": screen_res, "screen_color_depth": 24,
        "webgl_vendor": gpu_vendor, "webgl_renderer": gpu_renderer,
        "timezone": timezone, "locale": locale,
        "languages": [locale, locale.split("-")[0]] if locale != "en-US" else ["en-US", "en"],
        "sec_ch_ua": f'"Not)A;Brand";v="8", "Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}"',
        "sec_ch_ua_platform": sec_platform,
        "client_hints": {"architecture": arch, "bitness": "64", "model": "", "platformVersion": plat_version, "uaFullVersion": f"{chrome_version}.0.0.0"},
        "fonts": fonts,
        "plugins": ["Chrome PDF Viewer", "Native Client"],
        "canvas_noise": True, "webgl_noise": True, "audio_noise": True,
        "behavior": {
            "typing_speed_wpm": random.randint(45, 90),
            "mistake_probability": round(random.uniform(0.02, 0.08), 2),
            "mouse_speed_multiplier": round(random.uniform(0.7, 1.3), 1),
            "reading_speed_wpm": random.randint(180, 280),
            "scroll_speed": random.randint(200, 350)
        },
        "_is_fallback": True
    }
