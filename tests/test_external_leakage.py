"""Batch 15: external browser leakage validation scaffold.

This test validates that when a real proxy is configured, no external site can
see the machine's real IP, DNS, WebRTC IP, timezone, or locale. Because live
proxies and external sites may not be available in every test environment, the
file is structured as a conditional scaffold: it runs real checks when a proxy is
configured, and otherwise runs offline-safe checks that document what the live
path would validate.

Opt-in proxy configuration (checked in this order):
  1. Environment variables:
       GHOSTBROWSER_TEST_PROXY_SERVER
       GHOSTBROWSER_TEST_PROXY_USERNAME
       GHOSTBROWSER_TEST_PROXY_PASSWORD
  2. A project-root file ``test_proxy_config.json`` (NEVER committed) with a
     shape such as::

       {"proxy": {"server": "http://1.2.3.4:8080",
                  "username": "user",
                  "password": "pass"}}

The test never creates or modifies production ``profiles_data`` or cloudflare
account files. All browser state is created in a temporary directory.
"""

import asyncio
import ipaddress
import json
import os
import platform
import socket
import sys
import tempfile
import traceback
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest.mock import AsyncMock, patch

PROCESS_SYS_PATH = list(sys.path)
PROCESS_ENVIRON = dict(os.environ)
FAILURES = []
LIVE_PROXY_DETECTED = False


def _check(name: str, condition: bool) -> bool:
    if condition:
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name}")
        FAILURES.append(name)
    return condition


# Make project root importable exactly as the batch spec requests.
sys.path.append(os.getcwd())

# Force all backend state into a temporary directory so nothing in
# profiles_data or the production DB tree is touched.
_TEMP_ROOT = tempfile.TemporaryDirectory()
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = _TEMP_ROOT.name

# Import backend modules after the test environment is active.
_import_out = StringIO()
_import_err = StringIO()
with redirect_stdout(_import_out), redirect_stderr(_import_err):
    import backend.browser_manager as browser_manager
    import backend.proxy_manager as proxy_module
    from backend.proxy_manager import proxy_manager, redact_proxy_record
    from backend.browser_manager import parse_proxy_string, build_browser_launch_config
    from backend.config import get_installed_chromium_version
    from playwright.async_api import async_playwright

from httpx import AsyncClient as HttpxAsyncClient


def _detect_proxy_config() -> dict | None:
    """Return a normalized proxy dict if one is configured, otherwise None."""
    server = os.environ.get("GHOSTBROWSER_TEST_PROXY_SERVER", "").strip()
    if server:
        return {
            "server": server,
            "username": os.environ.get("GHOSTBROWSER_TEST_PROXY_USERNAME", "").strip(),
            "password": os.environ.get("GHOSTBROWSER_TEST_PROXY_PASSWORD", "").strip(),
        }

    config_path = os.path.join(os.getcwd(), "test_proxy_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        proxy = payload.get("proxy") if isinstance(payload.get("proxy"), dict) else payload
        server = str(proxy.get("server", "")).strip()
        if server:
            return {
                "server": server,
                "username": str(proxy.get("username", "")).strip(),
                "password": str(proxy.get("password", "")).strip(),
            }
    return None


def _normalize_proxy(proxy: dict) -> dict:
    """Return a Playwright-friendly proxy dict with credentials separated out."""
    parsed = parse_proxy_string(str(proxy.get("server")))
    return {
        "server": parsed["server"],
        "username": proxy.get("username") or parsed.get("username") or "",
        "password": proxy.get("password") or parsed.get("password") or "",
    }


def _is_private_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved


async def _run_offline_checks() -> None:
    print("\n--- Offline parse/format checks ---")

    ipv4 = parse_proxy_string("1.2.3.4:8080")
    _check("IPv4 host:port without scheme defaults to http", ipv4["scheme"] == "http" and ipv4["server"] == "http://1.2.3.4:8080")
    _check("IPv4 port parsed correctly", ipv4["port"] == 8080)
    _check("IPv4 host parsed correctly", ipv4["host"] == "1.2.3.4")

    https = parse_proxy_string("https://5.6.7.8:9090")
    _check("Explicit scheme preserved", https["scheme"] == "https" and https["server"] == "https://5.6.7.8:9090")

    ipv6 = parse_proxy_string("[2001:db8::1]:8080")
    _check("IPv6 bracketed host preserved", ipv6["host"] == "2001:db8::1" and ipv6["server"] == "http://[2001:db8::1]:8080")

    legacy = parse_proxy_string("1.2.3.4:8080:user1:pass1")
    _check("Legacy host:port:user:pass supported", legacy["username"] == "user1" and legacy["password"] == "pass1")
    _check("Legacy server string strips credentials", legacy["server"] == "http://1.2.3.4:8080")

    ipv6_legacy = parse_proxy_string("[2001:db8::1]:8080:user1:pass1")
    _check("IPv6 legacy credentials parsed", ipv6_legacy["username"] == "user1")
    _check("IPv6 legacy server strips credentials", ipv6_legacy["server"] == "http://[2001:db8::1]:8080")

    print("\n--- Offline credential-leakage checks ---")
    secret_password = "S3cr3t#P@ss"
    url_cred = f"http://myuser:{secret_password.replace('@', '%40').replace('#', '%23')}@9.8.7.6:3128"
    cred_parsed = parse_proxy_string(url_cred)
    _check("Credentials in server URL are removed from returned server", cred_parsed["server"] == "http://9.8.7.6:3128")
    _check("Credentials are still extracted", cred_parsed["username"] == "myuser" and secret_password in (cred_parsed["password"], ""))

    # Exception paths must never echo the secret.
    leak_in_exc = False
    for bad in [
        f"ftp://myuser:{secret_password}@9.8.7.6:3128",
        f"http://myuser:{secret_password}@9.8.7.6:99999",
    ]:
        try:
            parse_proxy_string(bad)
        except ValueError as exc:
            if secret_password in str(exc):
                leak_in_exc = True
        except Exception as exc:
            if secret_password in str(exc):
                leak_in_exc = True
    _check("Proxy exceptions do not echo credentials", not leak_in_exc)

    redacted = redact_proxy_record({"server": "http://9.8.7.6:3128", "username": "u", "password": "p"})
    _check("Redacted record marks authenticated", redacted.get("authenticated") is True)
    _check("Redacted record contains no username", "username" not in redacted)
    _check("Redacted record contains no password", "password" not in redacted)

    print("\n--- Offline resolve_proxy_geo contract ---")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"countryCode": "US", "timezone": "America/New_York", "city": "New York"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    fake_client = AsyncMock()
    fake_client.get.return_value = FakeResponse()
    fake_client.__aenter__.return_value = fake_client

    with patch("httpx.AsyncClient", return_value=fake_client):
        geo = await proxy_manager.resolve_proxy_geo({"server": "http://1.2.3.4:8080"})

    _check("resolve_proxy_geo returns timezone", "timezone" in geo)
    _check("resolve_proxy_geo returns locale", "locale" in geo)
    _check("Mock geo timezone parsed", geo.get("timezone") == "America/New_York")
    _check("Mock geo locale mapped", geo.get("locale") == "en-US")


async def _get_host_public_ip(timeout: float = 10.0) -> str | None:
    endpoints = [
        "https://api.ipify.org?format=json",
        "https://httpbin.org/ip",
    ]
    for endpoint in endpoints:
        try:
            async with HttpxAsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(endpoint)
                if response.status_code == 200:
                    text = response.text.strip()
                    if endpoint.endswith("ipify.org?format=json"):
                        data = response.json()
                        return data.get("ip")
                    # httpbin returns {"origin": "ip"}
                    data = response.json()
                    return data.get("origin", data.get("ip"))
        except Exception:
            continue
    return None


async def _get_browser_ip(page) -> str | None:
    try:
        await page.goto("https://api.ipify.org?format=json", wait_until="networkidle", timeout=20000)
        text = await page.locator("body").inner_text()
        data = json.loads(text)
        return data.get("ip")
    except Exception:
        pass
    try:
        await page.goto("https://httpbin.org/ip", wait_until="networkidle", timeout=20000)
        text = await page.locator("body").inner_text()
        data = json.loads(text)
        return data.get("origin", data.get("ip"))
    except Exception:
        return None


async def _gather_webrtc_candidates(page) -> set[str]:
    candidates = await page.evaluate(
        """async () => {
            const found = new Set();
            const collect = (value) => {
                if (value == null) return;
                const s = String(value);
                for (const m of s.matchAll(/(?:\\d{1,3}\\.){3}\\d{1,3}|[0-9a-fA-F:]{3,}/g)) {
                    found.add(m[0]);
                }
            };
            const pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
            try {
                pc.createDataChannel("audit");
                pc.addEventListener("icecandidate", (event) => {
                    if (event.candidate) {
                        collect(event.candidate.candidate);
                        collect(event.candidate.address);
                    }
                });
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                await new Promise((resolve) => setTimeout(resolve, 3500));
                collect(pc.localDescription && pc.localDescription.sdp);
                const stats = await pc.getStats();
                stats.forEach((report) => {
                    if (report.type === "local-candidate") {
                        collect(report.address);
                        collect(report.ip);
                        collect(report.candidate);
                    }
                });
            } finally {
                pc.close();
            }
            return [...found];
        }"""
    )
    valid = set()
    for value in candidates:
        try:
            valid.add(str(ipaddress.ip_address(value)))
        except ValueError:
            pass
    return valid


async def _run_live_checks(proxy: dict) -> None:
    global LIVE_PROXY_DETECTED
    LIVE_PROXY_DETECTED = True
    print("\n--- Live proxy detected; running external leakage checks ---")
    print(f"Proxy server: {proxy['server']}")
    print(f"Proxy username present: {bool(proxy.get('username'))}")

    host_os = platform.system()
    if host_os == "Windows":
        os_value = "Windows"
        expected_platform = "Win32"
        ua_platform_token = "Windows NT 10.0; Win64; x64"
    elif host_os == "Darwin":
        os_value = "Mac"
        expected_platform = "MacIntel"
        ua_platform_token = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        os_value = "Linux"
        expected_platform = "Linux x86_64"
        ua_platform_token = "X11; Linux x86_64"

    cpu_cores = 4
    memory_gb = 8
    profile_tz = "America/New_York"
    profile_locale = "en-US"
    chrome_version = get_installed_chromium_version()
    user_agent = (
        f"Mozilla/5.0 ({ua_platform_token}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
    )

    profile_dir = os.path.join(_TEMP_ROOT.name, "external_leakage_profile")
    os.makedirs(profile_dir, exist_ok=True)

    profile = {
        "id": "external-leak-test",
        "name": "External Leakage Test",
        "path": profile_dir,
        "proxy": proxy,
        "advanced": {
            "os": os_value,
            "screen_resolution": "1920x1080",
            "cpu_cores": cpu_cores,
            "memory_gb": memory_gb,
            "webrtc_mode": "protected",
            "disable_automation": True,
            "headless": False,
        },
        "timezone": profile_tz,
        "locale": profile_locale,
        "user_agent": user_agent,
    }

    print("\n--- Building browser launch config ---")
    try:
        # Patch native metadata probing so the test only launches one browser instance.
        fake_native_metadata = {
            "ua": user_agent,
            "uadata": {
                "brands": [
                    {"brand": "Chromium", "version": chrome_version.split(".")[0]},
                    {"brand": "Not)A;Brand", "version": "24"},
                ],
                "mobile": False,
                "platform": os_value if os_value != "Mac" else "macOS",
                "architecture": "x86",
                "bitness": "64",
                "model": "",
                "platformVersion": "19.0.0",
                "uaFullVersion": chrome_version,
                "fullVersionList": [
                    {"brand": "Chromium", "version": chrome_version},
                    {"brand": "Not)A;Brand", "version": "24.0.0.0"},
                ],
            },
        }
        original_probe = browser_manager.probe_native_metadata
        browser_manager.probe_native_metadata = AsyncMock(return_value=fake_native_metadata)
        config = await build_browser_launch_config(profile, force_headless=True)
    except Exception as exc:
        traceback.print_exc()
        _check(f"Browser launch config built for proxy ({exc})", False)
        browser_manager.probe_native_metadata = original_probe
        return
    finally:
        browser_manager.probe_native_metadata = original_probe

    required_args = {
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--enforce-webrtc-ip-permission-check",
    }
    _check("Required network-protection args present", required_args.issubset(set(config.get("args", []))))
    _check("Browser proxy matches configured proxy", config.get("proxy") == proxy)

    playwright = None
    context = None
    try:
        playwright = await async_playwright().start()
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=config.get("headless", False),
            args=config.get("args", []),
            proxy=config.get("proxy"),
            user_agent=config.get("user_agent"),
            timezone_id=config.get("timezone_id"),
            locale=config.get("locale"),
            viewport=config.get("viewport"),
            device_scale_factor=config.get("device_scale_factor", 1),
            extra_http_headers=config.get("extra_http_headers"),
        )
        context.add_init_script(config.get("spoofing_script", ""))

        page = context.pages[0] if context.pages else await context.new_page()

        print("\n--- External IP leak check ---")
        host_ip = await _get_host_public_ip()
        browser_ip = await _get_browser_ip(page)

        if host_ip:
            print(f"Host public IP: {host_ip}")
        else:
            print("Could not determine host public IP; skipping host comparison")
        if browser_ip:
            print(f"Browser-reported IP: {browser_ip}")
        else:
            print("Could not determine browser-reported IP")

        if host_ip and browser_ip:
            _check("Browser IP differs from host public IP", host_ip != browser_ip)
            _check("Browser-reported IP is a valid public address", not _is_private_ip(browser_ip))
        else:
            _check("IP-comparison data available", False)

        print("\n--- WebRTC candidate leak check ---")
        candidates = await _gather_webrtc_candidates(page)
        print(f"WebRTC candidate IPs observed: {candidates or 'none'}")
        private_candidates = {c for c in candidates if _is_private_ip(c)}
        _check("No private IPv4/IPv6 addresses in WebRTC candidates", not private_candidates)
        if host_ip:
            _check("Host public IP not present in WebRTC candidates", host_ip not in candidates)

        print("\n--- DNS leak check (best effort) ---")
        try:
            await page.goto("https://www.dnsleaktest.com/api/servers?json=1", wait_until="networkidle", timeout=15000)
            dns_text = await page.locator("body").inner_text()
            dns_data = json.loads(dns_text)
            resolver_ips = set()
            for entry in dns_data if isinstance(dns_data, list) else []:
                for key in ("ip", "ip_address"):
                    val = entry.get(key)
                    if val:
                        resolver_ips.add(str(val))
            print(f"DNS resolver IPs observed: {resolver_ips or 'none parsed'}")
            if host_ip:
                _check("Host public IP not observed as DNS resolver", host_ip not in resolver_ips)
            _check("No private DNS resolver IPs", not any(_is_private_ip(ip) for ip in resolver_ips))
        except Exception as exc:
            print(f"DNS leak endpoint unavailable ({exc}); would verify no host resolver leakage")
            # Mark as informational rather than hard failure.
            _check("DNS leak endpoint reachable", False)

        print("\n--- Fingerprint consistency check ---")
        observed = await page.evaluate(
            """() => ({
                userAgent: navigator.userAgent,
                platform: navigator.platform,
                hardwareConcurrency: navigator.hardwareConcurrency,
                deviceMemory: navigator.deviceMemory,
                language: navigator.language,
                languages: navigator.languages,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            })"""
        )
        print(f"Observed fingerprint: {observed}")
        _check("navigator.userAgent matches configured UA", observed.get("userAgent") == user_agent)
        _check("navigator.platform matches configured platform", observed.get("platform") == expected_platform)
        _check("navigator.hardwareConcurrency matches config", observed.get("hardwareConcurrency") == cpu_cores)
        _check("navigator.deviceMemory matches config", observed.get("deviceMemory") == memory_gb)
        _check("Observed timezone matches config", observed.get("timezone") == profile_tz)
        _check("Observed locale/language matches config", observed.get("language") == profile_locale)

    except Exception as exc:
        traceback.print_exc()
        _check(f"Live browser checks completed without exception ({exc})", False)
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass


async def _cleanup() -> None:
    # Release the temporary directory last so earlier cleanup can reference it.
    try:
        _TEMP_ROOT.cleanup()
    except Exception:
        pass

    os.environ.clear()
    os.environ.update(PROCESS_ENVIRON)
    sys.path[:] = PROCESS_SYS_PATH


async def main() -> int:
    proxy_config = _detect_proxy_config()

    if proxy_config is None:
        print("=" * 72)
        print("EXTERNAL LEAKAGE TEST: NO LIVE PROXY CONFIGURED")
        print("The environment variables GHOSTBROWSER_TEST_PROXY_SERVER,")
        print("GHOSTBROWSER_TEST_PROXY_USERNAME, GHOSTBROWSER_TEST_PROXY_PASSWORD")
        print("are not set, and test_proxy_config.json was not found in the project root.")
        print("Running offline-safe scaffold checks only.")
        print("When a proxy IS configured, the live branch would verify:")
        print("  - external site IP != host public IP")
        print("  - WebRTC exposes no private/local IP addresses")
        print("  - DNS resolvers do not include the host connection")
        print("  - navigator.*, timezone, and locale match the configured fingerprint")
        print("=" * 72)
        await _run_offline_checks()
    else:
        proxy = _normalize_proxy(proxy_config)
        await _run_live_checks(proxy)

    await _cleanup()

    print("\n--- Summary ---")
    if proxy_config is None:
        print("Live proxy detected: False")
    else:
        print("Live proxy detected: True")
    print(f"Failures: {len(FAILURES)}")
    for failure in FAILURES:
        print(f"  - {failure}")

    if FAILURES:
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
