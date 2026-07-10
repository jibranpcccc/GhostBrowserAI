import asyncio
import os
import sys

# Add parent directory to path so we can import backend modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.ai_generator import generate_fingerprint_ai
from backend.profile_manager import profile_manager
from backend.config import get_data_dir

CF_ACCOUNTS_FILE = os.path.join(get_data_dir(), "cloudflare_accounts.txt")

async def process_accounts():
    if not os.path.exists(CF_ACCOUNTS_FILE):
        print(f"File not found: {CF_ACCOUNTS_FILE}")
        return

    with open(CF_ACCOUNTS_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    print(f"Found {len(lines)} Cloudflare accounts.")
    
    for idx, line in enumerate(lines):
        parts = line.split(",")
        if len(parts) != 2:
            continue
        
        account_id = parts[0].strip()
        api_token = parts[1].strip()
        
        print(f"[{idx+1}/{len(lines)}] Generating robust fingerprint for account {account_id[:8]}...")
        
        try:
            # We call the Kimi AI generator (uses racing proxy with all Cloudflare accounts)
            ai_fingerprint = await generate_fingerprint_ai()
            
            # The generator returns specific fields, we map them into our "advanced" structure
            advanced = {
                "os": ai_fingerprint.get("os", "Windows"),
                "screen_resolution": ai_fingerprint.get("screen_resolution", "1920x1080"),
                "cpu_cores": ai_fingerprint.get("cpu_cores", 4),
                "memory_gb": ai_fingerprint.get("memory_gb", 8),
                "canvas_noise": True,
                "webgl_noise": True,
                "audio_noise": True,
                "webrtc_mode": "disabled",
                "cloudflare_account_id": account_id,
                "cloudflare_token": api_token,
                "sec_ch_ua": ai_fingerprint.get("sec_ch_ua", '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"'),
                "sec_ch_ua_platform": ai_fingerprint.get("sec_ch_ua_platform", '"Windows"'),
                "webgl_vendor": ai_fingerprint.get("webgl_vendor", "Google Inc. (NVIDIA)"),
                "webgl_renderer": ai_fingerprint.get("webgl_renderer", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)")
            }
            
            user_agent = ai_fingerprint.get("user_agent", "")
            
            profile = profile_manager.create_profile(
                name=f"CF_Bot_{idx+1}_{account_id[:4]}",
                timezone="UTC",
                locale="en-US",
                advanced=advanced
            )
            
            # Manually override the user_agent since profile_manager overrides it based on OS currently
            if user_agent:
                profile["user_agent"] = user_agent
                profile_manager.profiles[profile["id"]] = profile
                profile_manager._save_metadata()

            print(f" -> Success! Profile ID: {profile['id']}")
            
        except Exception as e:
            print(f" -> Failed to generate fingerprint for {account_id}: {e}")

if __name__ == "__main__":
    asyncio.run(process_accounts())
