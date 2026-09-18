import asyncio
import httpx
import json
import os
import re
import random
import time
from typing import List, Dict, Any

from httpx_socks import AsyncProxyTransport
import backend.db as db
from backend.logging_config import logger

PROXY_STORE_FILE = os.path.join(os.path.dirname(__file__), "..", "profiles_data", "proxy_pool.json")


class ProxyScraper:
    def __init__(self):
        self.sources = [
            # High-freshness HTTP/HTTPS lists (hourly/daily updates)
            {"name": "monosans HTTP", "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt", "protocol": "http"},
            {"name": "TheSpeedX HTTP", "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt", "protocol": "http"},
            {"name": "ErcinDedeoglu HTTP", "url": "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/http.txt", "protocol": "http"},
            {"name": "clarketm Daily", "url": "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt", "protocol": "http"},
            {"name": "roosterkid HTTPS", "url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt", "protocol": "https"},
            {"name": "vakhov HTTP", "url": "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt", "protocol": "http"},
            {"name": "ShiftyTR HTTP", "url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt", "protocol": "http"},
            # High-anonymity SOCKS5 lists
            {"name": "monosans SOCKS5", "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt", "protocol": "socks5"},
            {"name": "TheSpeedX SOCKS5", "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt", "protocol": "socks5"},
            {"name": "hookzof SOCKS5", "url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt", "protocol": "socks5"},
            {"name": "roosterkid SOCKS5", "url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt", "protocol": "socks5"},
            {"name": "vakhov SOCKS5", "url": "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/socks5.txt", "protocol": "socks5"},
        ]
        self.working_proxies = []

    async def fetch_source(self, client: httpx.AsyncClient, source: Dict[str, str]) -> List[Dict[str, str]]:
        url = source["url"]
        proto = source["protocol"]
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                raw_ips = re.findall(r'[0-9]+(?:\.[0-9]+){3}:[0-9]+', resp.text)
                return [{"endpoint": ip, "protocol": proto} for ip in raw_ips]
        except Exception as e:
            logger.debug(f"Failed to fetch {source['name']} ({url}): {e}")
        return []

    async def check_proxy(self, item: Dict[str, str], sem: asyncio.Semaphore) -> bool:
        endpoint = item["endpoint"]
        proto = item["protocol"]
        proxy_url = f"{proto}://{endpoint}"

        async with sem:
            t0 = time.perf_counter()
            try:
                if proto in ("socks5", "socks4"):
                    transport = AsyncProxyTransport.from_url(proxy_url)
                    async with httpx.AsyncClient(transport=transport, timeout=3.5) as client:
                        resp = await client.get("http://www.google.com/generate_204")
                        if resp.status_code in (200, 204):
                            latency = int((time.perf_counter() - t0) * 1000)
                            self._record_working_proxy(endpoint, proto, proxy_url, latency)
                            return True
                else:
                    async with httpx.AsyncClient(proxy=proxy_url, timeout=3.5) as client:
                        resp = await client.get("http://www.google.com/generate_204")
                        if resp.status_code in (200, 204):
                            latency = int((time.perf_counter() - t0) * 1000)
                            self._record_working_proxy(endpoint, proto, proxy_url, latency)
                            return True
            except Exception:
                pass
        return False

    def _record_working_proxy(self, endpoint: str, proto: str, proxy_url: str, latency: int):
        record = {
            "server": proxy_url,
            "type": "free_scraped",
            "health": "good",
            "protocol": proto,
            "endpoint": endpoint,
            "latency_ms": latency
        }
        self.working_proxies.append(record)
        logger.info(f"✅ Found working GitHub proxy: {proxy_url} ({latency}ms)")

    async def run_scraper(self, target_count: int = 50) -> int:
        logger.info("Starting Auto-Scraper for GitHub public proxies across 12+ repositories...")
        self.working_proxies = []
        candidates_by_endpoint: Dict[str, Dict[str, str]] = {}

        # 1. Fetch from all GitHub sources concurrently
        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = [self.fetch_source(client, src) for src in self.sources]
            results = await asyncio.gather(*tasks)
            for sublist in results:
                for item in sublist:
                    ep = item["endpoint"]
                    if ep not in candidates_by_endpoint or item["protocol"] == "socks5":
                        candidates_by_endpoint[ep] = item

        raw_candidates = list(candidates_by_endpoint.values())
        logger.info(f"Scraped {len(raw_candidates)} raw proxy candidates from GitHub. Starting validation...")

        random.shuffle(raw_candidates)
        sem = asyncio.Semaphore(75)

        batch_size = 150
        for i in range(0, len(raw_candidates), batch_size):
            if len(self.working_proxies) >= target_count:
                break
            batch = raw_candidates[i:i + batch_size]
            tasks = [self.check_proxy(item, sem) for item in batch]
            await asyncio.gather(*tasks)
            logger.info(f"Tested {min(i + batch_size, len(raw_candidates))}... Found {len(self.working_proxies)} working.")

        if self.working_proxies:
            self._save_proxies()

        logger.info(f"Scrape complete. Saved {len(self.working_proxies)} working proxies to pool and SQLite database.")
        return len(self.working_proxies)

    def _save_proxies(self):
        # 1. Save to JSON pool
        existing = []
        if os.path.exists(PROXY_STORE_FILE):
            try:
                with open(PROXY_STORE_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass

        merged = [p for p in existing if p.get("type") != "free_scraped"]
        merged.extend(self.working_proxies)

        os.makedirs(os.path.dirname(PROXY_STORE_FILE), exist_ok=True)
        with open(PROXY_STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=4)

        # 2. Upsert to Titan SQLite Database for live UI visibility
        for p in self.working_proxies:
            try:
                endpoint = p.get("endpoint", "")
                if ":" in endpoint:
                    ip, port = endpoint.split(":", 1)
                    db.upsert_proxy({
                        "ip": ip,
                        "port": port,
                        "protocol": p.get("protocol", "http"),
                        "country": "Global",
                        "city": "GitHub Pool",
                        "latency_ms": p.get("latency_ms", 300),
                        "status": "alive"
                    })
            except Exception as e:
                logger.debug(f"Failed to upsert proxy {p}: {e}")


proxy_scraper = ProxyScraper()
