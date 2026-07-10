import asyncio
from playwright.async_api import async_playwright
import json
import os
import random
import time

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "profiles_data")

# High trust domains to build history and cookies
TRUST_DOMAINS = [
    "https://www.google.com",
    "https://www.wikipedia.org",
    "https://www.amazon.com",
    "https://www.reddit.com",
    "https://www.youtube.com",
    "https://www.microsoft.com",
    "https://www.apple.com",
    "https://www.bing.com",
    "https://www.yahoo.com",
    "https://www.quora.com"
]

class CookieWarmer:
    def __init__(self):
        pass

    async def warm_profile(self, profile_id: str):
        from backend.browser_manager import launch_profile, close_profile
        
        print(f"[CookieWarmer] ♨️ Warming profile {profile_id}...")
        
        try:
            page_data = await launch_profile(profile_id, force_headless=True)
            if page_data.get("status") == "error":
                print(f"[CookieWarmer] ❌ Failed to launch profile {profile_id}: {page_data}")
                return False
                
            from backend.browser_manager import active_browsers
            page = active_browsers[profile_id]["page"]

            # Pick a few random domains to visit to build history
            domains_to_visit = random.sample(TRUST_DOMAINS, k=random.randint(3, 6))
            
            for domain in domains_to_visit:
                try:
                    print(f"[CookieWarmer] -> Visiting {domain}")
                    await page.goto(domain, timeout=20000, wait_until="domcontentloaded")
                    
                    # Simulate human behavior
                    await self._simulate_human(page)
                    
                except Exception as e:
                    print(f"[CookieWarmer] ⚠️ Failed to visit {domain}: {e}")
                    continue

            print(f"[CookieWarmer] ✅ Successfully warmed profile {profile_id} with cookies and history.")
            await close_profile(profile_id)
            return True
            
        except Exception as e:
            print(f"[CookieWarmer] ❌ Exception warming profile {profile_id}: {e}")
            return False

    async def _simulate_human(self, page):
        """Simulates human interaction (scrolling, resting) on a loaded page."""
        # Random initial pause
        await asyncio.sleep(random.uniform(1.0, 3.0))
        
        # Scroll down
        scrolls = random.randint(2, 5)
        for _ in range(scrolls):
            scroll_amt = random.randint(200, 800)
            await page.evaluate(f"window.scrollBy(0, {scroll_amt})")
            await asyncio.sleep(random.uniform(0.5, 2.0))
            
        # Maybe scroll up a bit
        if random.random() > 0.5:
            await page.evaluate(f"window.scrollBy(0, -{random.randint(100, 400)})")
            await asyncio.sleep(random.uniform(0.5, 1.5))

cookie_warmer = CookieWarmer()
