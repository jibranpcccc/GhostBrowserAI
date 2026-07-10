import asyncio
import httpx
from httpx_socks import AsyncProxyTransport
import json
import time
import os
import sys
from typing import List, Dict, Set

# Add backend directory to path so we can import db
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
import db

PROXY_SOURCES = [
    # ==================== YOUR ORIGINALS ====================
    {"url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt", "protocol": "socks4"},
    {"url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt", "protocol": "socks5"},
    {"url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt", "protocol": "socks5"},
    {"url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all", "protocol": "http"},
    {"url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=10000&country=all&ssl=all&anonymity=all", "protocol": "socks4"},
    {"url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all&ssl=all&anonymity=all", "protocol": "socks5"},
    {"url": "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/http.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/socks4.txt", "protocol": "socks4"},
    {"url": "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/socks5.txt", "protocol": "socks5"},
    {"url": "https://raw.githubusercontent.com/VPSLabCloud/VPSLab-Free-Proxy-List/main/proxies/http.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/VPSLabCloud/VPSLab-Free-Proxy-List/main/proxies/socks4.txt", "protocol": "socks4"},
    {"url": "https://raw.githubusercontent.com/VPSLabCloud/VPSLab-Free-Proxy-List/main/proxies/socks5.txt", "protocol": "socks5"},

    # ==================== NEW & UPDATED SOURCES (2026) ====================

    # --- Proxifly ---
    {"url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt", "protocol": "http"},
    {"url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/https/data.txt", "protocol": "http"},
    {"url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks4/data.txt", "protocol": "socks4"},
    {"url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt", "protocol": "socks5"},

    # --- Jetkai ---
    {"url": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-https.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks4.txt", "protocol": "socks4"},
    {"url": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt", "protocol": "socks5"},

    # --- Vakhov Fresh Proxy List ---
    {"url": "https://vakhov.github.io/fresh-proxy-list/http.txt", "protocol": "http"},
    {"url": "https://vakhov.github.io/fresh-proxy-list/https.txt", "protocol": "http"},
    {"url": "https://vakhov.github.io/fresh-proxy-list/socks4.txt", "protocol": "socks4"},
    {"url": "https://vakhov.github.io/fresh-proxy-list/socks5.txt", "protocol": "socks5"},

    # --- r00tee ---
    {"url": "https://raw.githubusercontent.com/r00tee/Proxy-List/main/Https.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/r00tee/Proxy-List/main/Socks4.txt", "protocol": "socks4"},
    {"url": "https://raw.githubusercontent.com/r00tee/Proxy-List/main/Socks5.txt", "protocol": "socks5"},

    # --- Databay Labs ---
    {"url": "https://cdn.jsdelivr.net/gh/databay-labs/free-proxy-list/http.txt", "protocol": "http"},
    {"url": "https://cdn.jsdelivr.net/gh/databay-labs/free-proxy-list/socks4.txt", "protocol": "socks4"},
    {"url": "https://cdn.jsdelivr.net/gh/databay-labs/free-proxy-list/socks5.txt", "protocol": "socks5"},

    # --- gfpcom ---
    {"url": "https://raw.githubusercontent.com/wiki/gfpcom/free-proxy-list/lists/http.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/wiki/gfpcom/free-proxy-list/lists/socks4.txt", "protocol": "socks4"},
    {"url": "https://raw.githubusercontent.com/wiki/gfpcom/free-proxy-list/lists/socks5.txt", "protocol": "socks5"},

    # --- officialputuid ---
    {"url": "https://cdn.jsdelivr.net/gh/officialputuid/ProxyForEveryone@main/http/http.txt", "protocol": "http"},
    {"url": "https://cdn.jsdelivr.net/gh/officialputuid/ProxyForEveryone@main/https/https.txt", "protocol": "http"},
    {"url": "https://cdn.jsdelivr.net/gh/officialputuid/ProxyForEveryone@main/socks4/socks4.txt", "protocol": "socks4"},
    {"url": "https://cdn.jsdelivr.net/gh/officialputuid/ProxyForEveryone@main/socks5/socks5.txt", "protocol": "socks5"},

    # --- ProxyScrape v4 ---
    {"url": "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&protocol=http", "protocol": "http"},
    {"url": "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&protocol=socks4", "protocol": "socks4"},
    {"url": "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&protocol=socks5", "protocol": "socks5"},

    # --- proxy-list.download API ---
    {"url": "https://www.proxy-list.download/api/v1/get?type=http", "protocol": "http"},
    {"url": "https://www.proxy-list.download/api/v1/get?type=https", "protocol": "http"},
    {"url": "https://www.proxy-list.download/api/v1/get?type=socks4", "protocol": "socks4"},
    {"url": "https://www.proxy-list.download/api/v1/get?type=socks5", "protocol": "socks5"},

    # --- Extra good ones ---
    {"url": "https://raw.githubusercontent.com/mertguvencli/http-proxy-list/main/proxy-list/data.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt", "protocol": "http"},
    {"url": "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt", "protocol": "http"}
]

CONCURRENCY_LIMIT = 1000

class ProxyTitan:
    def __init__(self):
        self.raw_proxies: Dict[str, str] = {}  # proxy -> protocol
        
    async def fetch_source(self, client: httpx.AsyncClient, source: Dict):
        url = source["url"]
        protocol = source["protocol"]
        try:
            print(f"[Titan] Fetching from {url[:50]}...")
            response = await client.get(url, timeout=15.0)
            if response.status_code == 200:
                lines = response.text.splitlines()
                for line in lines:
                    proxy = line.strip()
                    if ":" in proxy and len(proxy) > 10:
                        # Only add if not already present or overwrite
                        self.raw_proxies[proxy] = protocol
                print(f"[Titan] Scraped {len(lines)} proxies from {url[:50]}...")
        except Exception as e:
            print(f"[Titan] Failed to fetch {url[:50]}: {e}")

    async def scrape_all(self):
        print("[Titan] Starting massive concurrent scrape...")
        async with httpx.AsyncClient() as client:
            tasks = [self.fetch_source(client, source) for source in PROXY_SOURCES]
            await asyncio.gather(*tasks)
        print(f"[Titan] Total unique raw proxies gathered: {len(self.raw_proxies)}")

    async def check_proxy(self, semaphore: asyncio.Semaphore, proxy: str, protocol: str):
        async with semaphore:
            formatted_proxy = f"{protocol}://{proxy}"
            try:
                start_time = time.time()
                
                # Use httpx_socks for socks, standard httpx for http
                if protocol.startswith("socks"):
                    transport = AsyncProxyTransport.from_url(formatted_proxy)
                    client = httpx.AsyncClient(transport=transport, timeout=7.0, verify=False)
                else:
                    client = httpx.AsyncClient(proxy=formatted_proxy, timeout=7.0, verify=False)
                
                async with client:
                    response = await client.get("http://ip-api.com/json")
                    if response.status_code == 200:
                        data = response.json()
                        ping = int((time.time() - start_time) * 1000)
                        
                        proxy_data = {
                            "ip": data.get("query", proxy.split(":")[0]),
                            "port": proxy.split(":")[1],
                            "protocol": protocol,
                            "country": data.get("country", "Unknown"),
                            "city": data.get("city", "Unknown"),
                            "latency_ms": ping,
                            "status": "alive"
                        }
                        
                        # Upsert and Mark Success in SQLite
                        db.upsert_proxy(proxy_data)
                        db.mark_success(proxy_data['ip'], proxy_data['port'], ping)
                        
                        print(f"[Titan] ✅ ALIVE: {proxy_data['ip']}:{proxy_data['port']} ({proxy_data['protocol'].upper()}) - {ping}ms")
            except Exception:
                # If failure during full scrape/validation, we can mark failure if it exists in DB, 
                # but for an initial scrape of unknown proxies, we just ignore bad ones.
                # However, if this is called from the daemon on an existing proxy, it'll mark failure.
                # Let's check if we know it.
                ip, port = proxy.split(":", 1)
                db.mark_failure(ip, port)
                pass

    async def validate_all(self):
        print(f"[Titan] Commencing ultra-fast multi-protocol validation of {len(self.raw_proxies)} proxies...")
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        tasks = [self.check_proxy(semaphore, proxy, protocol) for proxy, protocol in self.raw_proxies.items()]
        
        # Run validations concurrently
        await asyncio.gather(*tasks)
        
        alive = db.get_all_alive_proxies()
        print(f"\n[Titan] Validation complete. {len(alive)} Elite/Anonymous proxies currently alive in Database.")

async def run_titan():
    titan = ProxyTitan()
    start = time.time()
    await titan.scrape_all()
    await titan.validate_all()
    end = time.time()
    print(f"\n🚀 Proxy Titan finished in {round(end - start, 2)} seconds.")

if __name__ == "__main__":
    asyncio.run(run_titan())
