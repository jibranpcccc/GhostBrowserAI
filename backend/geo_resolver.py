import httpx
import json

def resolve_geo_for_proxy(proxy_str: str) -> dict:
    """
    Given a proxy string (http://user:pass@ip:port or http://ip:port),
    resolves the location using a geolocation API to return the Timezone and Locale.
    """
    # IP-API requires the proxy to make the request to get the proxy's location
    proxies = {
        "http://": proxy_str,
        "https://": proxy_str
    }
    
    try:
        # We use a synchronous request here for simplicity in the profile creation flow,
        # but could be async if needed.
        with httpx.Client(proxies=proxies, timeout=10.0) as client:
            res = client.get("http://ip-api.com/json/?fields=status,message,countryCode,timezone")
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "success":
                    country = data.get("countryCode", "US")
                    timezone = data.get("timezone", "UTC")
                    # Construct locale, e.g. en-US, fr-FR
                    locale = f"en-{country}" if country in ["US", "GB", "AU", "CA"] else f"{country.lower()}-{country}"
                    return {
                        "timezone": timezone,
                        "locale": locale
                    }
    except Exception as e:
        print(f"Failed to resolve geo for proxy {proxy_str}: {e}")
        
    return {
        "timezone": "UTC",
        "locale": "en-US"
    }
