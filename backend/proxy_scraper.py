import asyncio
import httpx
import json
import os
import re
import random
from backend.logging_config import logger

PROXY_STORE_FILE = os.path.join(os.path.dirname(__file__), "..", "profiles_data", "proxy_pool.json")

class ProxyScraper:
    def __init__(self):
        self.sources = [
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
            "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"
        ]
        self.working_proxies = []
        
    async def fetch_source(self, client: httpx.AsyncClient, url: str) -> list:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                # Find ip:port patterns
                return re.findall(r'[0-9]+(?:\.[0-9]+){3}:[0-9]+', resp.text)
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
        return []

    async def check_proxy(self, proxy_str: str, client: httpx.AsyncClient):
        # C3 FIX: httpx 0.28+ removed 'proxies=' parameter, use 'proxy=' instead
        if proxy_str.startswith("http"):
            url = proxy_str
        else:
            url = f"http://{proxy_str}"

        try:
            # Very fast timeout just to check absolute basic connectivity first
            async with httpx.AsyncClient(proxy=url, timeout=2.0) as test_client:
                # Google is highly available and blocks bad proxies, good test target
                resp = await test_client.get("http://www.google.com/generate_204")
                if resp.status_code == 204:
                    self.working_proxies.append({
                        "server": url,
                        "type": "free_scraped",
                        "health": "good"
                    })
                    logger.info(f"✅ Found working fast proxy: {url}")
                    return True
        except Exception:
            pass
        return False

    async def run_scraper(self, target_count=50):
        logger.info("Starting Auto-Scraper for free proxies...")
        self.working_proxies = []
        raw_proxies = set()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = [self.fetch_source(client, url) for url in self.sources]
            results = await asyncio.gather(*tasks)
            for res in results:
                raw_proxies.update(res)
                
        logger.info(f"Scraped {len(raw_proxies)} raw proxy candidates. Starting validation...")
        
        # Test them in batches to not blow up memory
        raw_proxies = list(raw_proxies)
        random.shuffle(raw_proxies) # Try random ones
        
        batch_size = 150
        async with httpx.AsyncClient() as client:
            for i in range(0, len(raw_proxies), batch_size):
                if len(self.working_proxies) >= target_count:
                    break
                    
                batch = raw_proxies[i:i+batch_size]
                check_tasks = [self.check_proxy(p, client) for p in batch]
                await asyncio.gather(*check_tasks)
                
                logger.info(f"Tested {i+len(batch)}... Found {len(self.working_proxies)} working so far.")

        if self.working_proxies:
            self._save_proxies()
        logger.info(f"Scrape complete. Saved {len(self.working_proxies)} working proxies to pool.")
        return len(self.working_proxies)

    def _save_proxies(self):
        # Load existing manually added proxies to preserve them
        existing = []
        if os.path.exists(PROXY_STORE_FILE):
            try:
                with open(PROXY_STORE_FILE, "r") as f:
                    existing = json.load(f)
            except Exception: pass
            
        # Keep non-free proxies, and append new free ones
        merged = [p for p in existing if p.get("type") != "free_scraped"]
        merged.extend(self.working_proxies)
        
        os.makedirs(os.path.dirname(PROXY_STORE_FILE), exist_ok=True)
        with open(PROXY_STORE_FILE, "w") as f:
            json.dump(merged, f, indent=4)
            
proxy_scraper = ProxyScraper()
