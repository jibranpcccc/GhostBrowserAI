import httpx
import json

# HIGH-09 FIX: Made async to prevent blocking the event loop.
# Synchronous HTTP inside an async FastAPI handler freezes all requests for up to 10s.
async def resolve_geo_for_proxy(proxy_str: str) -> dict:
    """
    Given a proxy string (http://user:pass@ip:port or http://ip:port),
    resolves the location using a geolocation API to return the Timezone and Locale.
    """
    # Also use same proper language mapping as proxy_manager
    COUNTRY_LANG = {
        "US": "en", "GB": "en", "AU": "en", "CA": "en", "NZ": "en", "IE": "en",
        "DE": "de", "AT": "de", "CH": "de",
        "FR": "fr", "BE": "fr",
        "ES": "es", "MX": "es", "AR": "es",
        "IT": "it", "PT": "pt", "BR": "pt",
        "NL": "nl", "RU": "ru", "PL": "pl", "TR": "tr",
        "JP": "ja", "KR": "ko", "CN": "zh",
        "SA": "ar", "AE": "ar", "EG": "ar",
        "IN": "hi", "TH": "th", "VN": "vi", "ID": "id",
    }
    
    try:
        async with httpx.AsyncClient(proxy=proxy_str, timeout=10.0) as client:
            res = await client.get("http://ip-api.com/json/?fields=status,message,countryCode,timezone")
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "success":
                    country = data.get("countryCode", "US")
                    timezone = data.get("timezone", "UTC")
                    lang = COUNTRY_LANG.get(country, "en")
                    return {
                        "timezone": timezone,
                        "locale": f"{lang}-{country}"
                    }
    except Exception as e:
        print(f"Failed to resolve geo for proxy {proxy_str}: {e}")
        
    return {
        "timezone": "UTC",
        "locale": "en-US"
    }
