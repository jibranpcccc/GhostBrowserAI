import ipaddress
import os
import random
import socket
import time
from urllib.parse import quote, unquote, urlsplit

import httpx
from httpx_socks import AsyncProxyTransport

import backend.db as db
from backend.logging_config import logger


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

# Canonical locale/timezone pairs for common proxy exit countries.
COUNTRY_TO_LOCALE = {
    "US": ("en-US", "America/New_York"),
    "GB": ("en-GB", "Europe/London"),
    "DE": ("de-DE", "Europe/Berlin"),
    "FR": ("fr-FR", "Europe/Paris"),
    "IT": ("it-IT", "Europe/Rome"),
    "ES": ("es-ES", "Europe/Madrid"),
    "PT": ("pt-PT", "Europe/Lisbon"),
    "NL": ("nl-NL", "Europe/Amsterdam"),
    "BE": ("fr-FR", "Europe/Brussels"),
    "CH": ("de-CH", "Europe/Zurich"),
    "AT": ("de-AT", "Europe/Vienna"),
    "RU": ("ru-RU", "Europe/Moscow"),
    "PL": ("pl-PL", "Europe/Warsaw"),
    "TR": ("tr-TR", "Europe/Istanbul"),
    "SE": ("sv-SE", "Europe/Stockholm"),
    "JP": ("ja-JP", "Asia/Tokyo"),
    "KR": ("ko-KR", "Asia/Seoul"),
    "CN": ("zh-CN", "Asia/Shanghai"),
    "IN": ("hi-IN", "Asia/Kolkata"),
    "AU": ("en-AU", "Australia/Sydney"),
    "CA": ("en-CA", "America/Toronto"),
    "BR": ("pt-BR", "America/Sao_Paulo"),
    "MX": ("es-MX", "America/Mexico_City"),
    "AR": ("es-AR", "America/Buenos_Aires"),
    "ZA": ("en-ZA", "Africa/Johannesburg"),
    "SG": ("en-SG", "Asia/Singapore"),
    "HK": ("zh-HK", "Asia/Hong_Kong"),
    "ID": ("id-ID", "Asia/Jakarta"),
    "TH": ("th-TH", "Asia/Bangkok"),
    "VN": ("vi-VN", "Asia/Ho_Chi_Minh"),
    "AE": ("ar-AE", "Asia/Dubai"),
    "NZ": ("en-NZ", "Pacific/Auckland"),
    "IE": ("en-IE", "Europe/Dublin"),
}


def _parse_proxy_record(proxy: dict) -> dict:
    """Normalize a proxy without ever returning credentials in its server URL."""
    if not isinstance(proxy, dict):
        raise ValueError("Proxy must be an object")
    raw_server = str(proxy.get("server") or "").strip()
    if not raw_server or "://" not in raw_server:
        raise ValueError("Proxy server is missing a supported scheme")
    parsed = urlsplit(raw_server)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "socks4", "socks5"}:
        raise ValueError("Proxy scheme is unsupported")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Proxy server must not contain a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Proxy port is invalid") from exc
    host = parsed.hostname
    if not host or port is None or not 1 <= port <= 65535:
        raise ValueError("Proxy host or port is invalid")
    rendered_host = f"[{host}]" if ":" in host else host
    username = proxy.get("username")
    password = proxy.get("password")
    if username is None:
        username = unquote(parsed.username or "")
    if password is None:
        password = unquote(parsed.password or "")
    return {
        "server": f"{scheme}://{rendered_host}:{port}",
        "protocol": scheme,
        "ip": host,
        "port": str(port),
        "username": str(username or ""),
        "password": str(password or ""),
    }


def _proxy_host(proxy_server: str) -> str:
    """Extract hostname from a proxy server URL."""
    parsed = urlsplit(proxy_server)
    return parsed.hostname or proxy_server


def _resolve_host(host: str) -> str:
    """Return an IP address; if host is already numeric, return it."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        return socket.getaddrinfo(host, None)[0][4][0]
    except Exception:
        return host


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def guess_locale_timezone(proxy_server: str) -> tuple[str, str]:
    """
    Guess a locale/timezone pair from a proxy server's exit country.
    No network calls in test mode or when the caller already has values.
    """
    if os.environ.get("GHOSTBROWSER_TEST_ENV", "") == "1":
        return ("en-US", "America/New_York")

    ip = _resolve_host(_proxy_host(proxy_server))
    country = None

    try:
        with httpx.Client(timeout=5.0) as client:
            try:
                resp = client.get(f"https://ipapi.co/{ip}/country_code/")
                if resp.status_code == 200:
                    country = resp.text.strip().upper()
            except Exception:
                pass
            if not country:
                resp = client.get(f"https://ipinfo.io/{ip}/country")
                if resp.status_code == 200:
                    country = resp.text.strip().upper()
    except Exception:
        logger.warning("GeoIP lookup failed for %s; using defaults", proxy_server)

    return COUNTRY_TO_LOCALE.get(country, ("en-US", "America/New_York"))


def apply_geo_to_profile(profile: dict, proxy_server: str) -> dict:
    """
    Fill missing timezone/locale fields in a profile from proxy geo data.
    Returns the mutated profile for convenience.
    """
    advanced = profile.get("advanced") or {}
    profile["advanced"] = advanced

    # If top-level values are already present, propagate them downward, no network.
    top_tz = profile.get("timezone")
    top_locale = profile.get("locale")
    if not _is_missing(top_tz) and not _is_missing(top_locale):
        if _is_missing(advanced.get("timezone")):
            advanced["timezone"] = top_tz
        if _is_missing(advanced.get("locale")):
            advanced["locale"] = top_locale
        return profile

    locale, tz = guess_locale_timezone(proxy_server)
    if _is_missing(profile.get("timezone")):
        profile["timezone"] = tz
    if _is_missing(profile.get("locale")):
        profile["locale"] = locale
    if _is_missing(advanced.get("timezone")):
        advanced["timezone"] = profile.get("timezone")
    if _is_missing(advanced.get("locale")):
        advanced["locale"] = profile.get("locale")
    return profile


def redact_proxy_record(proxy: dict) -> dict:
    """Return non-secret proxy metadata suitable for API responses."""
    try:
        normalized = _parse_proxy_record(proxy)
        return {
            "server": normalized["server"],
            "authenticated": bool(normalized["username"] or normalized["password"]),
        }
    except (TypeError, ValueError):
        return {"server": "", "authenticated": False}


def _client_for_proxy(proxy: dict, timeout: float) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient routed through the given proxy."""
    server = proxy["server"]
    username = proxy.get("username", "")
    password = proxy.get("password", "")

    if server.startswith("socks"):
        if username:
            protocol, rest = server.split("://", 1)
            socks_url = f"{protocol}://{quote(username, safe='')}:{quote(password, safe='')}@{rest}"
        else:
            socks_url = server
        transport = AsyncProxyTransport.from_url(socks_url)
        return httpx.AsyncClient(transport=transport, timeout=timeout)

    proxy_config = httpx.Proxy(
        server,
        auth=(username, password) if username else None,
    )
    return httpx.AsyncClient(proxy=proxy_config, timeout=timeout)


class ProxyManager:
    """
    Manages a pool of proxies and assigns them to profiles.
    Reads dynamically from the SQLite db maintained by Proxy Titan.
    Ensures sticky sessions (a profile keeps the same proxy).
    """

    def __init__(self):
        # In-memory proxy health state: {"server": {"status": "healthy|unhealthy|cooldown", "until": float}}
        self._proxy_health: dict[str, dict] = {}

    def _get_active_proxies(self):
        # Fetch the top 200 best proxies from the Titan DB
        best_proxies = db.get_best_proxies(limit=200)
        formatted_proxies = []
        for p in best_proxies:
            item = {"server": f"{p['protocol']}://{p['ip']}:{p['port']}"}
            if p.get("username"):
                item["username"] = p["username"]
            if p.get("password"):
                item["password"] = p["password"]
            formatted_proxies.append(item)
        return formatted_proxies

    def add_proxies(self, proxy_list: list):
        """
        Adds a list of proxies to the pool manually (bypassing titan scraper).
        Format expected: {"server": "http://ip:port"} or {"server": "socks5://ip:port"}
        Invalid proxies are silently skipped.
        """
        added = 0
        for index, p in enumerate(proxy_list):
            try:
                normalized = _parse_proxy_record(p)
                db.upsert_proxy({
                    "ip": normalized["ip"],
                    "port": normalized["port"],
                    "protocol": normalized["protocol"],
                    "country": "Unknown",
                    "city": "Unknown",
                    "latency_ms": 1000,
                    "status": "alive",
                    "username": normalized["username"],
                    "password": normalized["password"],
                })
                added += 1
            except Exception:
                logger.warning("Skipping malformed proxy at input index %d", index)
        return added

    def remove_proxy(self, server: str):
        try:
            normalized = _parse_proxy_record({"server": server})
            # Rely on the DB dead threshold instead of forcing multiple marks.
            db.mark_failure(normalized["ip"], normalized["port"])
        except Exception:
            pass

    def health_check_proxy(self, server: str) -> bool:
        """
        Verify a proxy is reachable by opening a TCP socket to its host:port.
        Returns True if the connection succeeds within 5 seconds.
        """
        try:
            parsed = _parse_proxy_record({"server": server})
            host = parsed["ip"]
            port = int(parsed["port"])
        except Exception:
            logger.warning("Invalid proxy server format for health check: %s", server)
            return False

        try:
            with socket.create_connection((host, port), timeout=5.0) as sock:
                return True
        except Exception:
            return False

    def report_proxy_failure(self, server: str) -> None:
        """Mark a proxy as unhealthy in memory for 5 minutes."""
        normalized = server
        try:
            parsed = _parse_proxy_record({"server": server})
            normalized = parsed["server"]
        except Exception:
            pass
        logger.warning("Reporting proxy failure and cooling down: %s", normalized)
        self._proxy_health[normalized] = {
            "status": "unhealthy",
            "until": time.time() + 300,
        }

    def _is_proxy_healthy(self, server: str) -> bool:
        """Check cached state and, if allowed, perform a live TCP check."""
        now = time.time()
        state = self._proxy_health.get(server)
        if state:
            if state["status"] in ("unhealthy", "cooldown"):
                if now < state.get("until", 0):
                    return False
                # Cooldown expired; try again.
                self._proxy_health.pop(server, None)
        healthy = self.health_check_proxy(server)
        if not healthy:
            self.report_proxy_failure(server)
        else:
            self._proxy_health.pop(server, None)
        return healthy

    def get_healthy_proxy(self, preferred: str = None) -> dict | None:
        """
        Return a healthy proxy from the active pool. Optionally prefer the
        same server if it is healthy, otherwise fall back to any reachable proxy.
        """
        proxies = self._get_active_proxies()
        if not proxies:
            return None

        if preferred:
            for p in proxies:
                if p.get("server") == preferred and self._is_proxy_healthy(p["server"]):
                    return p

        random.shuffle(proxies)
        for p in proxies:
            if self._is_proxy_healthy(p["server"]):
                return p

        return None

    def get_health_status(self) -> list[dict]:
        """Return the cached health status for all active proxies."""
        statuses = []
        now = time.time()
        for p in self._get_active_proxies():
            server = p.get("server", "")
            state = self._proxy_health.get(server)
            if state and now < state.get("until", 0):
                status = state["status"]
                until = state.get("until")
            else:
                status = "healthy"
                until = None
            statuses.append({
                "server": server,
                "status": status,
                "healthy": status == "healthy",
                "expires_at": until,
            })
        return statuses

    async def check_proxy_health(self, proxy: dict, record_failure: bool = True) -> bool:
        """
        Pings ip-api.com through the proxy to verify it is alive.
        Auth credentials are embedded in the proxy URL for SOCKS transports.
        """
        server = proxy["server"]
        try:
            async with _client_for_proxy(proxy, timeout=5.0) as client:
                response = await client.get("http://ip-api.com/json/?fields=status")
                return response.status_code == 200
        except Exception:
            logger.warning("Proxy health check failed")
            if record_failure:
                self.remove_proxy(server)
            return False

    async def resolve_proxy_geo(self, proxy: dict) -> dict:
        """
        Resolves the Timezone and Locale of the proxy IP via ip-api.com.
        Routes the request strictly through the proxy itself.
        """
        try:
            async with _client_for_proxy(proxy, timeout=10.0) as client:
                response = await client.get("http://ip-api.com/json/")
                if response.status_code == 200:
                    data = response.json()
                    country = data.get("countryCode", "US")
                    timezone = data.get("timezone", "UTC")
                    lang = COUNTRY_LANG.get(country, "en")
                    locale = f"{lang}-{country}"
                    return {
                        "timezone": timezone,
                        "locale": locale,
                    }
        except Exception:
            logger.warning("Proxy geolocation failed; using conservative defaults")

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
        while attempts < 3:
            if force_new:
                proxy = random.choice(proxies)
            else:
                index = (hash(profile_id) + attempts) % len(proxies)
                proxy = proxies[index]

            if await self.check_proxy_health(proxy):
                return proxy

            attempts += 1
            force_new = True

        logger.error("No healthy proxy is available for profile %s", profile_id)
        return None


proxy_manager = ProxyManager()


def health_check_proxy(server: str) -> bool:
    """Module-level TCP reachability check for a proxy server URL."""
    return proxy_manager.health_check_proxy(server)


def report_proxy_failure(server: str) -> None:
    """Module-level helper to mark a proxy unhealthy for 5 minutes."""
    proxy_manager.report_proxy_failure(server)
