import os
import random
import backend.db as db
from backend.logging_config import logger

class ProxyManager:
    """
    Manages a pool of proxies and assigns them to profiles.
    Reads dynamically from the SQLite db maintained by Proxy Titan.
    Ensures sticky sessions (a profile keeps the same proxy).
    """
    def __init__(self):
        pass

    def _get_active_proxies(self):
        # Fetch the top 200 best proxies from the Titan DB
        best_proxies = db.get_best_proxies(limit=200)
        formatted_proxies = []
        for p in best_proxies:
            formatted_proxies.append({
                "server": f"{p['protocol']}://{p['ip']}:{p['port']}"
            })
        return formatted_proxies

    def add_proxies(self, proxy_list: list):
        """
        Adds a list of proxies to the pool manually (bypassing titan scraper).
        Format expected: {"server": "http://ip:port"}
        """
        added = 0
        for p in proxy_list:
            server = p.get("server")
            if server:
                protocol, rest = server.split("://")
                ip, port = rest.split(":")
                db.upsert_proxy({
                    "ip": ip,
                    "port": port,
                    "protocol": protocol,
                    "country": "Unknown",
                    "city": "Unknown",
                    "latency_ms": 1000,
                    "status": "alive"
                })
                added += 1
        return added

    def remove_proxy(self, server: str):
        try:
            protocol, rest = server.split("://")
            ip, port = rest.split(":")
            db.mark_failure(ip, port)
            db.mark_failure(ip, port)
            db.mark_failure(ip, port) # Force 3 fails to mark dead
        except:
            pass

    async def check_proxy_health(self, proxy: dict) -> bool:
        """
        Pings a fast endpoint to ensure the proxy is alive.
        """
        import httpx
        from httpx_socks import AsyncProxyTransport
        
        server = proxy["server"]
        auth = None
        if proxy.get("username"):
            auth = (proxy["username"], proxy.get("password", ""))
            
        try:
            if server.startswith("socks"):
                transport = AsyncProxyTransport.from_url(server)
                client = httpx.AsyncClient(transport=transport, timeout=5.0)
            else:
                client = httpx.AsyncClient(proxy=server, timeout=5.0)
                
            async with client:
                if auth and not server.startswith("socks"):
                    client.auth = auth
                response = await client.get("https://1.1.1.1")
                return response.status_code in [200, 301, 302]
        except Exception as e:
            logger.warning(f"Proxy health check failed for {server}: {e}")
            self.remove_proxy(server)
            return False

    async def resolve_proxy_geo(self, proxy: dict) -> dict:
        """
        Resolves the Timezone and Locale of the proxy IP via ip-api.com.
        Routes the request strictly through the proxy itself.
        """
        import httpx
        from httpx_socks import AsyncProxyTransport
        
        server = proxy["server"]
        auth = None
        if proxy.get("username"):
            auth = (proxy["username"], proxy.get("password", ""))
            
        try:
            if server.startswith("socks"):
                transport = AsyncProxyTransport.from_url(server)
                client = httpx.AsyncClient(transport=transport, timeout=10.0)
            else:
                client = httpx.AsyncClient(proxy=server, timeout=10.0)
                
            async with client:
                if auth and not server.startswith("socks"):
                    client.auth = auth
                response = await client.get("http://ip-api.com/json/")
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "timezone": data.get("timezone", "UTC"),
                        "locale": "en-US" if data.get("countryCode") == "US" else f"en-{data.get('countryCode', 'US')}"
                    }
        except Exception as e:
            logger.warning(f"Failed to resolve proxy geo for {server}: {e}")
            
        return {"timezone": "UTC", "locale": "en-US"}

    async def get_proxy_for_profile(self, profile_id: str, force_new: bool = False):
        """
        Assigns a proxy from the pool to a profile.
        If the profile already has a proxy, it returns it (Sticky Sessions),
        unless force_new is True. Validates health before returning.
        """
        proxies = self._get_active_proxies()
        if not proxies:
            return None
            
        attempts = 0
        current_offset = 0
        
        while attempts < 3:
            if force_new:
                proxy = random.choice(proxies)
            else:
                index = (hash(profile_id) + current_offset) % len(proxies)
                proxy = proxies[index]
                
            is_healthy = await self.check_proxy_health(proxy)
            if is_healthy:
                return proxy
                
            attempts += 1
            current_offset += 1
            force_new = True 
            
        logger.error(f"Failed to find a healthy proxy for profile {profile_id} after 3 attempts.")
        return None

proxy_manager = ProxyManager()
