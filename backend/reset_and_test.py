import os
import sys
import asyncio
import shutil
import json

# Add parent dir to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.profile_manager import profile_manager, PROFILES_DIR
from backend.ai_generator import generate_fingerprint_kimi
from playwright.async_api import async_playwright
import playwright_stealth

CF_ACCOUNTS_FILE = r"C:\Users\jibra\Desktop\1\hermes agent\cloudflare_working_accounts.txt"

async def reset_profiles():
    print("Deleting all existing profiles...")
    profiles = profile_manager.list_profiles()
    for p in profiles:
        profile_manager.delete_profile(p["id"])
    print("All profiles deleted.")

async def create_five_profiles():
    print("Creating exactly 5 new profiles...")
    if not os.path.exists(CF_ACCOUNTS_FILE):
        print("Accounts file not found.")
        return []

    with open(CF_ACCOUNTS_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    lines = lines[:5]
    created_profiles = []
    
    for idx, line in enumerate(lines):
        parts = line.split(",")
        account_id = parts[0].strip()
        api_token = parts[1].strip()
        
        print(f"[{idx+1}/5] Generating robust fingerprint for account {account_id[:8]}...")
        ai_fingerprint = await generate_fingerprint_kimi(account_id, api_token)
        
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
        
        if user_agent:
            profile["user_agent"] = user_agent
            profile_manager.profiles[profile["id"]] = profile
            profile_manager._save_metadata()

        print(f" -> Success! Profile ID: {profile['id']}")
        created_profiles.append(profile)
        
    return created_profiles

async def test_profiles(profiles):
    print("\nTesting profiles for uniqueness...")
    playwright = await async_playwright().start()
    
    os.makedirs(r"C:\Users\jibra\.gemini\antigravity\brain\19f79db5-76e3-409c-9fd8-996a3c208d7a\scratch", exist_ok=True)
    
    for idx, profile in enumerate(profiles):
        print(f"\n--- Testing Profile {idx+1}: {profile['name']} ---")
        
        args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-infobars',
            '--no-sandbox',
            '--disable-setuid-sandbox'
        ]
        
        width, height = map(int, profile["advanced"]["screen_resolution"].split("x"))
        args.append(f'--window-size={width},{height}')
        
        browser = await playwright.chromium.launch(
            headless=True,
            args=args
        )
        
        context = await browser.new_context(
            user_agent=profile["user_agent"],
            viewport={'width': width, 'height': height},
            locale=profile["locale"],
            timezone_id=profile["timezone"],
            device_scale_factor=1
        )
        
        if "sec_ch_ua" in profile["advanced"]:
            await context.set_extra_http_headers({
                "sec-ch-ua": profile["advanced"]["sec_ch_ua"],
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": profile["advanced"]["sec_ch_ua_platform"]
            })
            
        page = await context.new_page()
        
        stealth = playwright_stealth.stealth.Stealth()
        await stealth.apply_stealth_async(page)
        
        await page.add_init_script(f"""
            Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {profile["advanced"]["cpu_cores"]} }});
            Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {profile["advanced"]["memory_gb"]} }});
            
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) return '{profile["advanced"]["webgl_vendor"]}';
                if (parameter === 37446) return '{profile["advanced"]["webgl_renderer"]}';
                return getParameter.apply(this, arguments);
            }};
            
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function() {{
                const ctx = this.getContext('2d');
                if (ctx) {{
                    const text = "Noise_" + Math.random().toString(36).substring(7);
                    ctx.fillStyle = `rgba({profile["advanced"].get("canvas_r_offset", 1)}, {profile["advanced"].get("canvas_g_offset", 1)}, {profile["advanced"].get("canvas_b_offset", 1)}, 0.01)`;
                    ctx.fillText(text, 10, 10);
                }}
                return originalToDataURL.apply(this, arguments);
            }};
        """)
        
        # Test Canvas
        print("Navigating to browserleaks.com/canvas...")
        try:
            await page.goto("https://browserleaks.com/canvas", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            await page.screenshot(path=rf"C:\Users\jibra\.gemini\antigravity\brain\19f79db5-76e3-409c-9fd8-996a3c208d7a\scratch\canvas_profile_{idx+1}.png", full_page=True)
            print(f"Canvas screenshot saved to scratch/canvas_profile_{idx+1}.png")
        except Exception as e:
            print(f"Failed to load canvas test: {e}")
            
        await browser.close()
        
    await playwright.stop()
    print("\nTests complete!")

async def main():
    # await reset_profiles()
    profiles = await create_five_profiles()
    await test_profiles(profiles)

if __name__ == "__main__":
    asyncio.run(main())
