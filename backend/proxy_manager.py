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
        # Fetch the top 200 best proxies from the Titan DB (includes credentials)
        best_proxies = db.get_best_proxies(limit=200)
        formatted_proxies = []
        for p in best_proxies:
            entry = {"server": f"{p['protocol']}://{p['ip']}:{p['port']}"}
            if p.get('username'):
                entry["username"] = p['username']
                entry["password"] = p.get('password', '')
            formatted_proxies.append(entry)
        return formatted_proxies

    def add_proxies(self, proxy_list: list):
        """
        Adds a list of proxies to the pool manually (bypassing titan scraper).
        Format expected: {"server": "http://ip:port"} or {"server": "socks5://ip:port"}
        CRIT-06 FIX: Added full input validation and try/except to prevent crashes on
        malformed proxy strings.
        """
        added = 0
        for p in proxy_list:
            server = p.get("server")
            if not server:
                continue
            try:
                if "://" not in server:
                    logger.warning(f"Skipping malformed proxy (no protocol): {server}")
                    continue
                protocol, rest = server.split("://", 1)
                # Strip auth from rest if present (user:pass@ip:port)
                if "@" in rest:
                    rest = rest.split("@", 1)[1]
                parts = rest.split(":")
                if len(parts) < 2:
                    logger.warning(f"Skipping malformed proxy (no port): {server}")
                    continue
                ip = parts[0]
                port = parts[1]
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
            except Exception as e:
                logger.warning(f"Failed to add proxy {server}: {e}")
        return added

    async def add_proxies_tested(self, proxy_list: list) -> dict:
        """
        Adds proxies one-by-one: tests health first, then resolves geo.
        Returns results per proxy for the frontend.
        """
        import asyncio
        import httpx
        from httpx_socks import AsyncProxyTransport

        results = []
        for p in proxy_list:
            server = p.get("server", "")
            username = p.get("username", "")
            password = p.get("password", "")
            result = {"server": server, "status": "unknown", "timezone": "UTC", "locale": "en-US"}

            try:
                if "://" not in server:
                    result["status"] = "invalid"
                    results.append(result)
                    continue
                protocol, rest = server.split("://", 1)
                if "@" in rest:
                    rest = rest.split("@", 1)[1]
                parts = rest.split(":")
                if len(parts) < 2:
                    result["status"] = "invalid"
                    results.append(result)
                    continue
                ip, port = parts[0], parts[1]
            except Exception:
                result["status"] = "invalid"
                results.append(result)
                continue

            # Build client
            try:
                if server.startswith("socks"):
                    if username:
                        socks_url = f"{protocol}://{username}:{password}@{rest}"
                    else:
                        socks_url = server
                    transport = AsyncProxyTransport.from_url(socks_url)
                    client = httpx.AsyncClient(transport=transport, timeout=8.0)
                else:
                    auth_tuple = (username, password) if username else None
                    client = httpx.AsyncClient(proxy=server, auth=auth_tuple, timeout=8.0)
            except Exception:
                result["status"] = "invalid"
                results.append(result)
                continue

            # Test health
            try:
                async with client as c:
                    t0 = __import__("time").time()
                    resp = await c.get("http://ip-api.com/json/?fields=status,countryCode,city,timezone")
                    latency = int((__import__("time").time() - t0) * 1000)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("status") == "success":
                            country = data.get("countryCode", "Unknown")
                            city = data.get("city", "Unknown")
                            tz = data.get("timezone", "UTC")
                            COUNTRY_LANG = {
                                "US": "en", "GB": "en", "AU": "en", "CA": "en", "DE": "de",
                                "FR": "fr", "ES": "es", "IT": "it", "PT": "pt", "BR": "pt",
                                "NL": "nl", "RU": "ru", "PL": "pl", "TR": "tr", "JP": "ja",
                                "KR": "ko", "CN": "zh", "SA": "ar", "AE": "ar", "IN": "hi",
                                "TH": "th", "VN": "vi", "ID": "id",
                            }
                            lang = COUNTRY_LANG.get(country, "en")
                            locale = f"{lang}-{country}"

                            db.upsert_proxy({
                                "ip": ip, "port": port, "protocol": protocol,
                                "country": country, "city": city,
                                "latency_ms": latency, "status": "alive",
                                "timezone": tz, "locale": locale,
                                "username": username, "password": password,
                            })
                            result["status"] = "alive"
                            result["country"] = country
                            result["city"] = city
                            result["timezone"] = tz
                            result["locale"] = locale
                            result["latency_ms"] = latency
                        else:
                            result["status"] = "dead"
                    else:
                        result["status"] = "dead"
            except Exception as e:
                logger.warning(f"Proxy test failed for {server}: {e}")
                result["status"] = "dead"

            results.append(result)

        alive = sum(1 for r in results if r["status"] == "alive")
        dead = sum(1 for r in results if r["status"] == "dead")
        return {"results": results, "alive": alive, "dead": dead, "total": len(results)}

    def remove_proxy(self, server: str):
        try:
            protocol, rest = server.split("://")
            ip, port = rest.split(":")
            db.mark_failure(ip, port)
            db.mark_failure(ip, port)
            db.mark_failure(ip, port) # Force 3 fails to mark dead
        except Exception:
            pass

    async def check_proxy_health(self, proxy: dict) -> bool:
        """
        Pings a reliable endpoint to ensure the proxy is alive.
        LOW-07 FIX: Changed from https://1.1.1.1 (returns 400 on plain IP) to
        http://ip-api.com/json/ which reliably returns 200 for all healthy proxies.
        MED-06 FIX: Properly applies auth credentials for SOCKS proxies by building
        the full auth URL instead of setting client.auth separately.
        """
        import httpx
        from httpx_socks import AsyncProxyTransport
        
        server = proxy["server"]
        username = proxy.get("username", "")
        password = proxy.get("password", "")
            
        try:
            if server.startswith("socks"):
                # MED-06 FIX: Embed credentials in the SOCKS URL so httpx_socks applies them
                if username:
                    protocol, rest = server.split("://", 1)
                    socks_url = f"{protocol}://{username}:{password}@{rest}"
                else:
                    socks_url = server
                transport = AsyncProxyTransport.from_url(socks_url)
                client = httpx.AsyncClient(transport=transport, timeout=5.0)
            else:
                # M9 FIX: Pass auth in constructor instead of setting as attribute after creation
                auth_tuple = (username, password) if username else None
                client = httpx.AsyncClient(proxy=server, auth=auth_tuple, timeout=5.0)
                
            async with client:
                # LOW-07 FIX: Use ip-api.com which reliably returns 200 for healthy proxies
                response = await client.get("http://ip-api.com/json/?fields=status")
                return response.status_code == 200
        except Exception as e:
            logger.warning(f"Proxy health check failed for {server}: {e}")
            self.remove_proxy(server)
            return False

    async def resolve_proxy_geo(self, proxy: dict) -> dict:
        """
        Resolves the Timezone and Locale of the proxy IP via ip-api.com.
        Routes the request strictly through the proxy itself.
        MED-09 FIX: Proper country-to-language mapping instead of always using 'en-{country}'.
        MED-06 FIX: SOCKS proxies now use auth-embedded URL.
        """
        import httpx
        from httpx_socks import AsyncProxyTransport
        
        # MED-09 FIX: Proper country-to-primary-language mapping
        COUNTRY_LANG = {
            "US": "en", "GB": "en", "AU": "en", "CA": "en", "NZ": "en", "IE": "en",
            "DE": "de", "AT": "de", "CH": "de",
            "FR": "fr", "BE": "fr",
            "ES": "es", "MX": "es", "AR": "es", "CL": "es", "CO": "es",
            "IT": "it",
            "PT": "pt", "BR": "pt",
            "NL": "nl",
            "RU": "ru",
            "PL": "pl",
            "TR": "tr",
            "JP": "ja",
            "KR": "ko",
            "CN": "zh",
            "SA": "ar", "AE": "ar", "EG": "ar",
            "IN": "hi",
            "TH": "th",
            "VN": "vi",
            "ID": "id",
        }
        
        server = proxy["server"]
        username = proxy.get("username", "")
        password = proxy.get("password", "")
            
        try:
            if server.startswith("socks"):
                if username:
                    protocol, rest = server.split("://", 1)
                    socks_url = f"{protocol}://{username}:{password}@{rest}"
                else:
                    socks_url = server
                transport = AsyncProxyTransport.from_url(socks_url)
                client = httpx.AsyncClient(transport=transport, timeout=10.0)
            else:
                auth_tuple = (username, password) if username else None
                client = httpx.AsyncClient(proxy=server, auth=auth_tuple, timeout=10.0)
                
            async with client:
                response = await client.get("http://ip-api.com/json/")
                if response.status_code == 200:
                    data = response.json()
                    country = data.get("countryCode", "US")
                    timezone = data.get("timezone", "UTC")
                    lang = COUNTRY_LANG.get(country, "en")
                    locale = f"{lang}-{country}"
                    return {
                        "timezone": timezone,
                        "locale": locale
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
