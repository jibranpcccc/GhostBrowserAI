import asyncio
import json
import os
import re
import shutil
from urllib.parse import urlparse, unquote
from playwright.async_api import async_playwright
import playwright_stealth
from backend.profile_manager import profile_manager
from backend.ai_scanner import ai_scanner
from backend.launch_policy import build_surface_launch_policy, get_privacy_launch_flags
from backend.api_access_logger import log_api_access
from backend.webhook_notifier import webhook_notifier
from backend.engine_resolver import (
    get_chromium_executable_path,
    get_chromium_executable_path_async,
)
from backend.logging_config import logger

active_browsers = {}
profile_states = {}
profile_client_hints = {}
profile_cdp_tasks = {}

_probed_metadata_cache = {}
profile_page_events = {}
profile_page_futures = {}
profile_worker_registry = {}
profile_cleanup_running = {}
profile_opted_origins = {}
session_opted_origins = {}
_simulate_cdp_error = False


def _origin_allows_client_hints(response_url: str) -> bool:
    """Return True if an origin is secure enough to persist Accept-CH opt-ins."""
    try:
        parsed = urlparse(response_url)
        hostname = (parsed.hostname or "").lower()
        return parsed.scheme == "https" or hostname in ("localhost", "127.0.0.1")
    except Exception:
        return False


def _persist_accept_ch_opt_in(
    profile_id: str,
    profile_path: str,
    response_url: str,
    accept_ch_header: str,
):
    """Store Accept-CH opt-ins only for secure origins."""
    if not _origin_allows_client_hints(response_url):
        return
    try:
        parsed = urlparse(response_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return
    hints = [h.strip().lower() for h in accept_ch_header.split(",")]
    origins_dict = profile_opted_origins.setdefault(profile_id, {})
    origins_dict[origin] = hints
    save_opted_origins(profile_id, profile_path, origins_dict)
    session_dict = session_opted_origins.setdefault(profile_id, {})
    session_dict[origin] = hints


def _safe_url_for_log(value: str) -> str:
    """Return a URL without credentials, query parameters, or fragments."""
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return f"{parsed.scheme}://{host}{parsed.path or '/'}"
    except Exception:
        return "<invalid-url>"

def load_opted_origins(profile_id: str, profile_path: str) -> dict:
    import json
    import os
    try:
        path = os.path.join(profile_path, "opted_origins.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_opted_origins(profile_id: str, profile_path: str, origins_dict: dict):
    import json
    import os
    try:
        path = os.path.join(profile_path, "opted_origins.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(origins_dict, f)
    except Exception:
        pass

def clear_opted_origins(profile_id: str, profile_path: str = None):
    profile_opted_origins[profile_id] = {}
    session_opted_origins[profile_id] = {}
    if profile_path:
        import os
        path = os.path.join(profile_path, "opted_origins.json")
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

probe_futures = {}
probe_lock = asyncio.Lock()

def parse_proxy_string(proxy_str: str) -> dict:
    if not proxy_str:
        raise ValueError("Empty proxy string")

    scheme = "http"
    remainder = proxy_str
    for pfx in ["http://", "https://", "socks4://", "socks5://"]:
        if proxy_str.startswith(pfx):
            scheme = pfx[:-3]
            remainder = proxy_str[len(pfx):]
            break
    else:
        if "://" in proxy_str:
            invalid_scheme = proxy_str.split("://")[0]
            raise ValueError(f"Unsupported proxy scheme: {invalid_scheme}")

    legacy_match = None
    if remainder.startswith("["):
        end_bracket = remainder.find("]")
        if end_bracket != -1:
            ipv6_host = remainder[1:end_bracket]
            rest = remainder[end_bracket+1:]
            parts = rest.split(":")
            if len(parts) == 4:
                legacy_match = (f"[{ipv6_host}]", parts[1], parts[2], parts[3])
            elif len(parts) == 2:
                legacy_match = (f"[{ipv6_host}]", parts[1], None, None)
    else:
        parts = remainder.split(":")
        if len(parts) == 4 and parts[0].count(":") == 0:
            legacy_match = (parts[0], parts[1], parts[2], parts[3])

    if legacy_match:
        host, port_str, user, password = legacy_match
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"Invalid proxy port: {port_str}")
    else:
        url_to_parse = f"{scheme}://{remainder}"
        parsed = urlparse(url_to_parse)

        if parsed.path and parsed.path != "/":
            raise ValueError(f"Proxy URL contains invalid path: {parsed.path}")
        if parsed.query:
            raise ValueError(f"Proxy URL contains invalid query: {parsed.query}")
        if parsed.fragment:
            raise ValueError(f"Proxy URL contains invalid fragment: {parsed.fragment}")

        netloc = parsed.netloc
        if "@" in netloc:
            creds, host_port = netloc.rsplit("@", 1)
        else:
            host_port = netloc

        if host_port.startswith("["):
            end_bracket = host_port.find("]")
            if end_bracket == -1:
                raise ValueError("Malformed bracketed IPv6 address")
            host = host_port[:end_bracket+1]
            port_part = host_port[end_bracket+1:]
            if port_part.startswith(":"):
                try:
                    port = int(port_part[1:])
                except ValueError:
                    raise ValueError(f"Invalid proxy port: {port_part[1:]}")
            else:
                port = 80 if scheme in ("http", "ws") else 443
        else:
            if ":" in host_port:
                if host_port.count(":") > 1:
                    raise ValueError(f"Ambiguous unbracketed IPv6 address: {host_port}")
                h, p_str = host_port.rsplit(":", 1)
                host = h
                try:
                    port = int(p_str)
                except ValueError:
                    raise ValueError(f"Invalid proxy port: {p_str}")
            else:
                host = host_port
                port = 80 if scheme in ("http", "ws") else 443

        user = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password else None

    if not host or any(character.isspace() for character in host):
        raise ValueError("Invalid proxy host")
    if not port or not (1 <= port <= 65535):
        raise ValueError(f"Invalid proxy port: {port}")

    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        if host.count(":") > 1:
            raise ValueError(f"Ambiguous unbracketed IPv6 address: {host}")

    clean_host = host
    if clean_host.startswith("[") and clean_host.endswith("]"):
        clean_host = clean_host[1:-1]

    server_host = f"[{clean_host}]" if ":" in clean_host else clean_host
    server_str = f"{scheme}://{server_host}:{port}"

    return {
        "server": server_str,
        "username": user,
        "password": password,
        "host": clean_host,
        "port": port,
        "scheme": scheme
    }

async def _probe_native_metadata_impl(force_headless: bool = True):
    """Resolve, cache, and probe native browser metadata.

    Kept separate from the public hook so launch-focused tests may replace
    ``probe_native_metadata`` without obscuring this implementation's unit
    tests or its executable-resolver dependency seam.
    """
    from backend.config import get_installed_chromium_version
    try:
        exe_path = await get_chromium_executable_path_async()
        version = get_installed_chromium_version()
    except Exception as e:
        raise RuntimeError(f"FAIL-CLOSED: Cannot resolve installed Chromium details: {e}")

    cache_key = (force_headless, exe_path, version)

    async with probe_lock:
        if cache_key in _probed_metadata_cache:
            return _probed_metadata_cache[cache_key]
        if cache_key in probe_futures:
            fut = probe_futures[cache_key]
        else:
            fut = asyncio.Future()
            probe_futures[cache_key] = fut

            async def run_and_resolve():
                try:
                    res = await run_metadata_probe(exe_path, force_headless)
                    fut.set_result(res)
                    async with probe_lock:
                        _probed_metadata_cache[cache_key] = res
                except Exception as ex:
                    fut.set_exception(ex)
                finally:
                    async with probe_lock:
                        probe_futures.pop(cache_key, None)
            asyncio.create_task(run_and_resolve())

    return await fut


async def probe_native_metadata(force_headless: bool = True):
    """Public metadata-probe hook used by browser launch configuration."""
    return await _probe_native_metadata_impl(force_headless)

async def run_metadata_probe(exe_path: str, force_headless: bool):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading
    import psutil

    metadata_result = {}

    class ProbeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <!DOCTYPE html>
                <html>
                <body>
                    <script>
                        async function runProbe() {
                            const res = {
                                isSecure: window.isSecureContext,
                                ua: navigator.userAgent,
                                uadata: null
                            };
                            if (navigator.userAgentData) {
                                try {
                                    const highEntropy = await navigator.userAgentData.getHighEntropyValues([
                                        'architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion', 'fullVersionList'
                                    ]);
                                    res.uadata = {
                                        brands: navigator.userAgentData.brands,
                                        mobile: navigator.userAgentData.mobile,
                                        platform: navigator.userAgentData.platform,
                                        architecture: highEntropy.architecture || "",
                                        bitness: highEntropy.bitness || "",
                                        model: highEntropy.model || "",
                                        platformVersion: highEntropy.platformVersion || "",
                                        uaFullVersion: highEntropy.uaFullVersion || "",
                                        fullVersionList: highEntropy.fullVersionList || []
                                    };
                                } catch (e) {
                                    res.error = e.message;
                                }
                            }
                            window.__metadata = res;
                        }
                        runProbe();
                    </script>
                </body>
                </html>
            """)
        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), ProbeHandler)
    port = server.server_address[1]

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    from playwright.async_api import async_playwright
    p = None
    browser = None
    stored_pid = None
    try:
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            headless=force_headless,
            executable_path=exe_path,
            timeout=5000
        )

        if hasattr(browser, "_impl_obj") and hasattr(browser._impl_obj, "_process") and browser._impl_obj._process:
            stored_pid = browser._impl_obj._process.pid

        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=3000)

        for _ in range(50):
            res = await page.evaluate("window.__metadata")
            if res:
                metadata_result = res
                break
            await asyncio.sleep(0.05)

    finally:
        if browser:
            try:
                await asyncio.wait_for(browser.close(), timeout=2.0)
            except Exception:
                pass
        if p:
            try:
                await asyncio.wait_for(p.stop(), timeout=2.0)
            except Exception:
                pass
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass

        if stored_pid:
            try:
                proc = psutil.Process(stored_pid)
                kill_process_tree(proc)
            except Exception:
                pass

    if not metadata_result or not metadata_result.get("uadata"):
        raise RuntimeError("FAIL-CLOSED: Native metadata probe failed. Cannot prove native browser parameters safely.")

    return metadata_result

async def fail_closed_profile(profile_id: str, context, playwright, reason: str):
    if profile_cleanup_running.get(profile_id):
        return
    profile_cleanup_running[profile_id] = True

    from backend.logging_config import logger
    logger.error(f"FAIL-CLOSED triggering for profile {profile_id}: {reason}")
    set_profile_state(profile_id, "error")

    # Cancel / set exception on all pending page futures
    page_futures = profile_page_futures.pop(profile_id, {})
    for fut in page_futures.values():
        if not fut.done():
            fut.set_exception(RuntimeError(f"FAIL-CLOSED: {reason}"))

    # Also pop and set page events for backward compatibility
    page_events = profile_page_events.pop(profile_id, {})
    for event in page_events.values():
        event.set()

    # Cancel outstanding CDP tasks
    tasks = profile_cdp_tasks.pop(profile_id, set())
    for task in tasks:
        if not task.done():
            task.cancel()

    profile_worker_registry.pop(profile_id, None)

    bd = active_browsers.pop(profile_id, None)
    if bd:
        health_task = bd.get("health_task")
        if health_task and not health_task.done():
            health_task.cancel()

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

    if bd:
        stored_pid = bd.get("pid")
        if stored_pid:
            import psutil
            try:
                proc = psutil.Process(stored_pid)
                kill_process_tree(proc)
            except Exception:
                pass

    # Fallback: find and terminate any remaining processes matching the profile directory path
    profile = profile_manager.get_profile(profile_id)
    if profile and profile.get("path"):
        import os
        canonical_path = os.path.normcase(os.path.realpath(profile["path"]))
        procs = find_profile_processes(canonical_path)
        for proc in procs:
            try:
                kill_process_tree(proc)
            except Exception:
                pass

    profile_cleanup_running[profile_id] = False

def register_cdp_task(profile_id, task, context, playwright):
    if profile_id not in profile_cdp_tasks:
        profile_cdp_tasks[profile_id] = set()
    profile_cdp_tasks[profile_id].add(task)

    def on_complete(t):
        if profile_id in profile_cdp_tasks:
            profile_cdp_tasks[profile_id].discard(t)
        try:
            exc = t.exception()
            if exc:
                from backend.logging_config import logger
                logger.error(f"FAIL-CLOSED: Background CDP task failed for profile {profile_id}: {exc}")
                asyncio.create_task(fail_closed_profile(profile_id, context, playwright, f"CDP background failure: {exc}"))
        except asyncio.CancelledError:
            pass

    task.add_done_callback(on_complete)

def get_profile_state(profile_id: str) -> str:
    state = profile_states.get(profile_id)
    if state in ["launching", "closing", "error"]:
        return state

    profile = profile_manager.get_profile(profile_id)
    if profile:
        path = profile.get("path")
        if path and find_profile_processes(path):
            return "running"

    if profile_id in active_browsers:
        return "running"
    return "stopped"

def set_profile_state(profile_id: str, state: str):
    profile_states[profile_id] = state

def find_profile_processes(profile_path: str) -> list:
    import psutil
    import os
    procs = []
    if not profile_path:
        return procs
    normalized_path = os.path.realpath(profile_path).lower()
    for proc in psutil.process_iter(attrs=['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline')
            if cmdline:
                for arg in cmdline:
                    if arg.lower().startswith('--user-data-dir='):
                        val = arg.split('=', 1)[1]
                        if os.path.realpath(val).lower() == normalized_path:
                            procs.append(proc)
                            break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return procs

def kill_process_tree(proc):
    import psutil
    try:
        try:
            logger.info("DEBUG KILL PROCESS: %s", proc.pid)
        except Exception:
            pass

        try:
            children = proc.children(recursive=True)
        except Exception:
            children = []

        for child in children:
            try:
                logger.info("DEBUG KILL CHILD PROCESS: %s", child.pid)
                child.terminate()
            except Exception:
                pass

        try:
            proc.terminate()
        except Exception:
            pass

        try:
            gone, alive = psutil.wait_procs(children + [proc], timeout=2.0)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass

def normalize_webrtc_mode(mode) -> str:
    if mode is None:
        return "protected"
    if not isinstance(mode, str):
        raise RuntimeError("FAIL-CLOSED: WebRTC mode must be a string.")
    mode = mode.strip().lower()
    if mode in ["protected", "altered"]:
        return "protected"
    if not mode:
        raise RuntimeError("FAIL-CLOSED: WebRTC mode cannot be empty.")
    if mode == "real":
        raise RuntimeError("FAIL-CLOSED: WebRTC 'real' mode can expose host IP information and is not supported. Use 'protected'.")
    if mode == "disabled":
        raise RuntimeError("FAIL-CLOSED: WebRTC 'disabled' mode requires detectable JavaScript/API mutilation and is unsupported. Use 'protected'.")
    raise RuntimeError(f"FAIL-CLOSED: Unknown WebRTC mode: {mode}")


def _is_explicit_test_environment() -> bool:
    marker = os.getenv("GHOSTBROWSER_TEST_ENV", "").strip().lower()
    override = os.getenv("GHOSTBROWSER_TEST_PROFILES_DIR", "").strip()
    if marker not in {"1", "true", "yes"} or not override:
        return False
    try:
        from backend.profile_manager import PROFILES_DIR as production_profiles_dir

        candidate = os.path.normcase(os.path.realpath(os.path.abspath(override)))
        production = os.path.normcase(os.path.realpath(production_profiles_dir))
        active_manager_root = os.path.normcase(
            os.path.realpath(os.path.abspath(profile_manager.PROFILES_DIR))
        )
        if candidate != active_manager_root:
            return False
        # Test data must be completely outside the production profile tree.
        if os.path.splitdrive(candidate)[0] != os.path.splitdrive(production)[0]:
            return True
        return os.path.commonpath([candidate, production]) != production
    except (OSError, ValueError):
        return False


def _profile_has_verified_provenance(profile: dict) -> bool:
    """Only launch profiles that completed an approved AI generation path."""
    if _is_explicit_test_environment():
        return True
    fingerprint = profile.get("fingerprint")
    provenance = profile.get("ai_provenance")
    if not isinstance(fingerprint, dict) or not fingerprint:
        return False
    if not isinstance(provenance, dict) or provenance.get("verified") is not True:
        return False
    source = provenance.get("source")
    requested_model = provenance.get("requested_model")

    # Approved generation sources and their expected model identifiers.
    approved_sources_models = {
        ("racing_proxy", "@cf/moonshotai/kimi-k2.7-code"),
        ("direct_cloudflare", "@cf/moonshotai/kimi-k2.7-code"),
        ("local_fallback", "local-deterministic-generator"),
    }
    # Mistral models are reported exactly as requested (e.g. mistral-small-latest,
    # mistral-large-latest, codestral-latest). Approve the source family by prefix.
    if source == "mistral_api" and requested_model and "mistral" in requested_model.lower():
        approved_sources_models.add((source, requested_model))

    if (source, requested_model) not in approved_sources_models:
        return False
    if str(profile.get("verification_status") or "verified").lower() != "verified":
        return False
    if fingerprint.get("_is_fallback") and source != "local_fallback":
        return False
    return True


def _build_native_surface(seed: int, os_val: str):
    if os_val == "Mac":
        templates = [
            {"name": "Default Browser", "filename": "Default Browser.plugin", "description": "Default browser plugin", "mime": "application/x-default-browser", "suffixes": ""},
            {"name": "Chrome PDF Plugin", "filename": "internal-pdf-viewer.plugin", "description": "Portable Document Format plugin", "mime": "application/pdf", "suffixes": "pdf"},
            {"name": "Native Client module", "filename": "ppGoogleNaClPluginChrome.plugin", "description": "Native Client module", "mime": "application/x-nacl", "suffixes": ""},
            {"name": "Widevine Content Decryption Module", "filename": "widevinecdmadapter.plugin", "description": "Widevine Content Decryption Module", "mime": "application/x-widevine-plugin", "suffixes": ""},
            {"name": "Google Update", "filename": "npGoogleUpdate3.plugin", "description": "Google Update", "mime": "application/x-google-update", "suffixes": ""},
        ]
    elif os_val == "Linux":
        templates = [
            {"name": "Chrome PDF Plugin", "filename": "internal-pdf-viewer.so", "description": "Portable Document Format plugin", "mime": "application/pdf", "suffixes": "pdf"},
            {"name": "Native Client module", "filename": "ppGoogleNaClPluginChrome.so", "description": "Native Client module", "mime": "application/x-nacl", "suffixes": ""},
            {"name": "Widevine Content Decryption Module", "filename": "libwidevinecdmadapter.so", "description": "Widevine Content Decryption Module", "mime": "application/x-widevine-plugin", "suffixes": ""},
            {"name": "Shockwave Flash", "filename": "libflashplayer.so", "description": "Shockwave Flash", "mime": "application/x-shockwave-flash", "suffixes": "swf"},
            {"name": "VLC Web Plugin", "filename": "libvlcplugin.so", "description": "VLC multimedia plugin", "mime": "application/x-vlc-plugin", "suffixes": ""},
        ]
    else:
        templates = [
            {"name": "Chrome PDF Plugin", "filename": "internal-pdf-viewer.dll", "description": "Portable Document Format plugin", "mime": "application/pdf", "suffixes": "pdf"},
            {"name": "Native Client module", "filename": "ppGoogleNaClPluginChrome.dll", "description": "Native Client module", "mime": "application/x-nacl", "suffixes": ""},
            {"name": "Widevine Content Decryption Module", "filename": "widevinecdmadapter.dll", "description": "Widevine Content Decryption Module", "mime": "application/x-widevine-plugin", "suffixes": ""},
            {"name": "Google Update", "filename": "npGoogleUpdate3.dll", "description": "Google Update", "mime": "application/x-google-update", "suffixes": ""},
            {"name": "Microsoft Office Live Plug-in", "filename": "npOLW.dll", "description": "Microsoft Office Live Plug-in", "mime": "application/x-msoffice-live", "suffixes": ""},
        ]
    count = 3 + (seed % 3)
    plugins = []
    mime_types = []
    for i in range(count):
        t = templates[(seed + i) % len(templates)]
        v = f"{1 + ((seed + i) % 30)}.{(seed + i) % 100}.{(seed + i * 7) % 1000}"
        plugins.append({
            "name": t["name"],
            "description": t["description"],
            "filename": t["filename"],
            "version": v,
            "length": 1,
        })
        mime_types.append({
            "type": t["mime"],
            "suffixes": t["suffixes"],
            "description": t["description"],
            "enabledPlugin": None,
        })
    return plugins, mime_types


async def _proxy_health_loop(profile_id: str, proxy: dict):
    try:
        while True:
            await asyncio.sleep(30)
            try:
                from backend.proxy_manager import proxy_manager
                healthy = await proxy_manager.check_proxy_health(proxy)
            except Exception:
                healthy = False
            if not healthy:
                logger.warning(
                    "WARNING: proxy for profile %s is unhealthy; closing profile",
                    profile_id,
                )
                if profile_id in active_browsers:
                    await close_profile(profile_id)
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def build_browser_launch_config(profile: dict, force_headless: bool = False, forced_proxy: dict = None) -> dict:

    profile_id = profile["id"]

    advanced = profile.get("advanced", {})
    if not isinstance(advanced, dict):
        raise RuntimeError("FAIL-CLOSED: Advanced profile settings must be a dictionary.")

    _privacy_raw = str(advanced.get("privacy_mode", "standard")).strip().lower()
    privacy_mode = _privacy_raw if _privacy_raw in ("standard", "strict", "ephemeral", "high") else "standard"
    advanced["privacy_mode"] = privacy_mode

    # AI-declared native surfaces that are not genuinely applied. Ignore them
    # even if they somehow reach the launch path so the browser keeps its
    # coherent native font/plugin surface.
    advanced = {k: v for k, v in advanced.items() if k not in ("fonts", "plugins")}

    fingerprint = profile.get("fingerprint", {}) or {}
    if not isinstance(fingerprint, dict):
        fingerprint = {}
    is_mobile_profile = advanced.get("device_type") == "mobile" or fingerprint.get("is_mobile_profile") is True

    # High privacy profiles created by older configuration code stored
    # ``disabled``; retain their privacy intent with the supported protected
    # transport policy rather than rejecting the profile at launch.
    webrtc_mode = (
        "protected"
        if privacy_mode == "high" and advanced.get("webrtc_mode") == "disabled"
        else normalize_webrtc_mode(advanced.get("webrtc_mode"))
    )
    def _add_unique_arg(existing, arg):
        key = arg.split("=", 1)[0]
        if not any(a.split("=", 1)[0] == key for a in existing):
            existing.append(arg)
        return existing

    def _remove_args(existing, prefix):
        return [a for a in existing if not a.startswith(prefix)]

    args = [
        '--disable-blink-features=AutomationControlled',
        '--disable-infobars',
        # QUIC is UDP-based and is not carried by ordinary HTTP/SOCKS proxy
        # configuration. Disable it so proxied profiles cannot bypass their
        # configured transport through a direct QUIC path.
        '--disable-quic',
        # Disable IPv6 to prevent UDP/proxy routing differences from leaking
        # the network path and to keep DNS resolution predictable.
        '--disable-ipv6',
        # Disable DNS prefetching so the browser cannot resolve domains in the
        # background before the proxy routing barrier is ready.
        '--dns-prefetch-disable',
        # Disable Chromium's async DNS resolver for predictable, proxy-aware
        # resolution behavior.
        '--disable-async-dns',
        # Disable features that can issue unexpected background network calls.
        '--disable-features=InterestFeedContentSuggestions,MediaRouter',
    ]
    for privacy_arg in get_privacy_launch_flags(advanced):
        if privacy_arg.startswith("--disable-features="):
            args[args.index('--disable-features=InterestFeedContentSuggestions,MediaRouter')] += "," + privacy_arg.split("=", 1)[1]
        else:
            args.append(privacy_arg)

    for anti_arg in [
        '--disable-dev-shm-usage',
        '--no-default-browser-check',
        '--no-first-run',
        '--password-store=basic',
        '--use-mock-keychain',
        '--disable-background-networking',
        '--disable-client-side-phishing-detection',
        '--disable-default-apps',
        '--disable-hang-monitor',
        '--disable-popup-blocking',
        '--disable-prompt-on-repost',
        '--disable-sync',
        '--disable-web-resources',
        '--metrics-recording-only',
        '--safebrowsing-disable-auto-update',
    ]:
        _add_unique_arg(args, anti_arg)

    if privacy_mode in ("strict", "ephemeral", "high"):
        args.append("--disable-extensions")
        extension_paths = []
    else:
        from backend.config import get_bundled_dir
        extensions_dir = get_bundled_dir("backend", "extensions")
        from backend.security_hardening import validate_extensions
        extension_paths = validate_extensions(extensions_dir)

    if extension_paths:
        paths_str = ",".join(extension_paths)
        args.append(f"--disable-extensions-except={paths_str}")
        args.append(f"--load-extension={paths_str}")

    proxy = None

    pinned_proxy_str = profile.get("proxy_pin")
    explicit_proxy = profile.get("proxy")

    from backend.proxy_manager import proxy_manager

    configured_proxy = forced_proxy
    if not configured_proxy and pinned_proxy_str:
        try:
            parsed_prx = parse_proxy_string(pinned_proxy_str)
            configured_proxy = {
                "server": parsed_prx["server"],
                "username": parsed_prx.get("username") or "",
                "password": parsed_prx.get("password") or "",
            }
        except Exception:
            raise RuntimeError("FAIL-CLOSED: The pinned proxy configuration is invalid.")
    elif not configured_proxy and explicit_proxy:
        if not isinstance(explicit_proxy, dict):
            raise RuntimeError("FAIL-CLOSED: The profile proxy configuration is invalid.")
        try:
            parsed_prx = parse_proxy_string(str(explicit_proxy.get("server") or ""))
            configured_proxy = {
                "server": parsed_prx["server"],
                "username": explicit_proxy.get("username") or parsed_prx.get("username") or "",
                "password": explicit_proxy.get("password") or parsed_prx.get("password") or "",
            }
        except Exception:
            raise RuntimeError("FAIL-CLOSED: The profile proxy configuration is invalid.")

    if configured_proxy is not None:
        if forced_proxy:
            # The caller already verified the proxy via TCP health check.
            assigned_proxy = configured_proxy
        else:
            # An explicitly configured profile proxy is authoritative. Never replace
            # it with a pool entry; an unhealthy result aborts the launch.
            try:
                configured_is_healthy = await proxy_manager.check_proxy_health(configured_proxy)
            except Exception:
                configured_is_healthy = False
            if not configured_is_healthy:
                raise RuntimeError("FAIL-CLOSED: The configured proxy is unavailable.")
            assigned_proxy = configured_proxy
    else:
        # No proxy is a supported, explicit direct-connection mode. Explicitly
        # configured proxies still fail closed above, so an unavailable proxy
        # can never silently fall back to the machine's connection.
        assigned_proxy = None

    args.extend([
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
        '--enforce-webrtc-ip-permission-check'
    ])

    if os.getenv("GHOSTBROWSER_TEST_ENV") in ("1", "true"):
        args.append('--host-resolver-rules=MAP example-redirect-target.local 127.0.0.1')

    if assigned_proxy:
        try:
            parsed_prx = parse_proxy_string(assigned_proxy["server"])
            proxy = {"server": parsed_prx["server"]}
            if parsed_prx.get("username"):
                proxy["username"] = parsed_prx["username"]
                proxy["password"] = parsed_prx["password"]
            elif assigned_proxy.get("username"):
                proxy["username"] = assigned_proxy["username"]
                proxy["password"] = assigned_proxy["password"]

            exclude_host = f"[{parsed_prx['host']}]" if ":" in parsed_prx["host"] else parsed_prx["host"]
            # Allow known DoH endpoints to resolve so a future
            # --dns-over-https option is not broken by the catch-all rule.
            args.append(
                f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {exclude_host} , "
                "EXCLUDE dns.google , EXCLUDE one.one.one.one , EXCLUDE cloudflare-dns.com , "
                "EXCLUDE mozilla.cloudflare-dns.com"
            )
        except Exception as e:
            raise RuntimeError(f"FAIL-CLOSED: Invalid proxy configuration: {e}")

    args = _remove_args(args, '--enable-automation')
    _add_unique_arg(args, '--disable-blink-features=AutomationControlled')

    # Pure surface policy is kept separate from process/network orchestration.
    # For mobile cohorts, inherit scale/resolution from the fingerprint when the
    # advanced dict has not been explicitly populated by the creation flow.
    _surface_advanced = dict(advanced)
    if is_mobile_profile:
        _surface_advanced.setdefault("screen_resolution", fingerprint.get("screen_resolution", "390x844"))
        _surface_advanced.setdefault("device_scale_factor", fingerprint.get("device_scale_factor", 2.0))
    surface_policy = build_surface_launch_policy(_surface_advanced, assigned_proxy is not None)
    block_service_workers = surface_policy.block_service_workers or privacy_mode in ("strict", "ephemeral", "high")
    if privacy_mode in ("strict", "ephemeral", "high"):
        _service_worker_added = False
        for _i, _arg in enumerate(args):
            if _arg.startswith("--disable-features="):
                args[_i] = _arg + ",ServiceWorker"
                _service_worker_added = True
                break
        if not _service_worker_added:
            args.append("--disable-features=ServiceWorker")
    _vp_w = surface_policy.viewport_width
    _vp_h = surface_policy.viewport_height

    is_headless = force_headless or advanced.get("headless", False)
    playwright_headless = False
    if is_headless:
        args.append('--headless=new')

    cpu_cores = advanced.get("cpu_cores") or 4
    memory_gb = advanced.get("memory_gb") or 8
    try:
        cpu_cores = int(cpu_cores)
    except (TypeError, ValueError):
        cpu_cores = 4
    try:
        memory_gb = int(memory_gb)
    except (TypeError, ValueError):
        memory_gb = 8

    canvas_noise = advanced.get("canvas_noise", True)
    webgl_noise = advanced.get("webgl_noise", True)
    audio_noise = advanced.get("audio_noise", True)

    ai_webgl_vendor = advanced.get("webgl_vendor", "Google Inc. (NVIDIA)")
    ai_webgl_renderer = advanced.get("webgl_renderer", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)")
    from backend.config import get_installed_chromium_version, get_installed_chromium_major_version
    current_chrome_ver = get_installed_chromium_version()
    _installed_major = str(get_installed_chromium_major_version())

    # Reconcile stored User-Agent Chrome version with the installed Chromium
    # version to prevent version-mismatch leaks.
    _stored_ua = profile.get("user_agent") or advanced.get("user_agent")
    if _stored_ua:
        _stored_major_match = re.search(r"Chrome/(\d+)", _stored_ua)
        if _stored_major_match and _stored_major_match.group(1) != _installed_major:
            _reconciled_ua = re.sub(r"Chrome/[\d.]+", f"Chrome/{current_chrome_ver}", _stored_ua)
            profile["user_agent"] = _reconciled_ua
            advanced["user_agent"] = _reconciled_ua

    ua = profile.get("user_agent", f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{current_chrome_ver} Safari/537.36")

    profile_locale = profile.get("locale", advanced.get("locale", "en-US"))
    profile_languages = advanced.get("languages", [profile_locale, profile_locale.split("-")[0]] if "-" in profile_locale else [profile_locale])
    if "en" not in profile_languages:
        profile_languages.append("en")
    languages_js = str(profile_languages).replace("'", '"')

    import re as _re
    _pid_raw = profile.get("id", "ffffffff-0000-0000-0000-000000000000")
    _seed = int(_pid_raw.replace("-", "")[:8], 16)
    canvas_r_offset = advanced.get("canvas_r_offset", (_seed % 250))
    canvas_g_offset = advanced.get("canvas_g_offset", ((_seed * 7) % 250))
    canvas_b_offset = advanced.get("canvas_b_offset", ((_seed * 13) % 250))
    _dom_noise = ((_seed % 1000) + 1) / 10_000_000.0

    device_scale_factor = surface_policy.device_scale_factor

    if os.getenv("GHOSTBROWSER_TEST_ENV") not in ("1", "true"):
        from datetime import date
        _jitter = (_seed + date.today().toordinal()) & 0xFF
        _jitter_sign = 1 if (_jitter & 1) else -1
        device_scale_factor = max(0.5, min(4.0, round(device_scale_factor + _jitter_sign * 0.05, 2)))
        cpu_cores = max(2, min(16, cpu_cores + _jitter_sign))
        memory_gb = max(2, min(16, memory_gb + _jitter_sign))
        for _hk in ("canvas_noise_hash", "audio_noise_hash"):
            _hv = advanced.get(_hk)
            if isinstance(_hv, str) and _hv:
                advanced[_hk] = f"{_hv}{_jitter:02x}"

    ua_os_val = advanced.get("os", "Windows")
    ua_platform_str  = "macOS" if ua_os_val == "Mac" else ("Linux" if ua_os_val == "Linux" else "Windows")
    current_major = _installed_major
    _ua_raw = profile.get("user_agent", f"Chrome/{current_major}")
    _chrome_m = _re.search(r"Chrome/(\d+)", _ua_raw)
    chrome_major_ver = _chrome_m.group(1) if _chrome_m else current_major

    profile_tz = profile.get("timezone", "UTC")

    if ua_os_val == "Mac":
        speech_voices_json = '["Alex","Samantha","Victoria","Daniel","Karen","Moira","Tessa"]'
    else:
        speech_voices_json = '["Microsoft David Desktop","Microsoft Zira Desktop","Microsoft Mark Desktop","Microsoft David - English (United States)"]'

    fingerprint = profile.get("fingerprint", {})
    if not isinstance(fingerprint, dict):
        fingerprint = {}
    client_hints = fingerprint.get("client_hints", {})
    if not isinstance(client_hints, dict):
        client_hints = {}

    chrome_full_ver = current_chrome_ver if chrome_major_ver == current_major else f"{chrome_major_ver}.0.0.0"

    native_meta = await probe_native_metadata(force_headless=is_headless)
    native_ua = native_meta["ua"]
    native_uadata = native_meta["uadata"] or {}

    sec_ch_ua = advanced.get("sec_ch_ua")
    if not sec_ch_ua and "brands" in native_uadata:
        sec_ch_ua = ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in native_uadata["brands"])
    if not sec_ch_ua:
        sec_ch_ua = f'"Not A(Brand";v="99", "Chromium";v="{chrome_major_ver}"'

    sec_ch_ua_platform = advanced.get("sec_ch_ua_platform") or f'"{native_uadata.get("platform", ua_platform_str)}"'

    ch_architecture = native_uadata.get("architecture") or client_hints.get("architecture") or "x86"
    ch_bitness = native_uadata.get("bitness") or client_hints.get("bitness") or "64"
    ch_model = native_uadata.get("model") or client_hints.get("model") or ""
    ch_platform_version = native_uadata.get("platformVersion") or client_hints.get("platformVersion") or "10.0.0"
    ch_ua_full_version = native_uadata.get("uaFullVersion") or client_hints.get("uaFullVersion") or chrome_full_ver

    import json
    brands = []
    if "brands" in native_uadata and native_uadata["brands"]:
        brands = native_uadata["brands"]
    else:
        for m in _re.finditer(r'"([^"]+)";v="([^"]+)"', sec_ch_ua):
            brands.append({
                "brand": m.group(1),
                "version": m.group(2)
            })
    if not brands:
        brands = [
            {"brand": "Not A(Brand", "version": "99"},
            {"brand": "Chromium", "version": chrome_major_ver}
        ]

    full_version_list = []
    if "fullVersionList" in native_uadata and native_uadata["fullVersionList"]:
        full_version_list = native_uadata["fullVersionList"]
    else:
        for b in brands:
            v = "99.0.0.0" if "Not" in b["brand"] else ch_ua_full_version
            full_version_list.append({
                "brand": b["brand"],
                "version": v
            })

    ua_json = json.dumps(ua)
    if is_mobile_profile:
        max_touch_points = int(
            advanced.get("max_touch_points")
            or fingerprint.get("max_touch_points")
            or (_seed % 5 + 2)
        )
    else:
        _is_desktop_platform = any(p in ua_os_val for p in ("Win", "Mac", "Linux", "X11"))
        if _is_desktop_platform:
            max_touch_points = _seed % 2
        else:
            max_touch_points = 1 + (_seed % 10)

    extra_http_headers = {}
    spoofing_script = f"""
            const spoofedFunctions = new WeakMap();
            const originalToString = Function.prototype.toString;
            Function.prototype.toString = new Proxy(originalToString, {{
                apply: function(target, thisArg, args) {{
                    if (spoofedFunctions.has(thisArg)) {{
                        return `function ${{spoofedFunctions.get(thisArg)}}() {{ [native code] }}`;
                    }}
                    return Reflect.apply(target, thisArg, args);
                }}
            }});
            const makeNative = (func, name, nativeLength) => {{
                const finalName = name || func.name || '';
                spoofedFunctions.set(func, finalName);
                Object.defineProperty(func, 'name', {{
                    value: finalName,
                    writable: false,
                    enumerable: false,
                    configurable: true
                }});
                if (Number.isInteger(nativeLength)) {{
                    Object.defineProperty(func, 'length', {{
                        value: nativeLength,
                        writable: false,
                        enumerable: false,
                        configurable: true
                    }});
                }}
                return func;
            }};
            const safeDefineProperty = (obj, prop, getter) => {{
                try {{
                    const desc = Object.getOwnPropertyDescriptor(obj, prop);
                    const descriptor = {{ get: makeNative(getter, `get ${{prop}}`, 0) }};
                    if (desc) {{
                        descriptor.enumerable = desc.enumerable;
                        descriptor.configurable = desc.configurable;
                    }} else {{
                        descriptor.enumerable = true;
                        descriptor.configurable = true;
                    }}
                    Object.defineProperty(obj, prop, descriptor);
                }} catch (e) {{
                    try {{
                        Object.defineProperty(obj, prop, {{
                            get: makeNative(getter, `get ${{prop}}`, 0),
                            enumerable: true,
                            configurable: true
                        }});
                    }} catch (e2) {{}}
                }}
            }};
            safeDefineProperty(navigator, 'hardwareConcurrency', () => {cpu_cores});
            safeDefineProperty(navigator, 'deviceMemory', () => {memory_gb});
            safeDefineProperty(navigator, 'userAgent', () => {ua_json});
            safeDefineProperty(navigator, 'languages', () => {languages_js});
            safeDefineProperty(navigator, 'language', () => {languages_js}[0]);
            safeDefineProperty(navigator, 'maxTouchPoints', () => {max_touch_points});
            const screenRes = "{advanced.get("screen_resolution", "1920x1080")}".split('x');
            const width = parseInt(screenRes[0]);
            const height = parseInt(screenRes[1]);
            safeDefineProperty(window.screen, 'width', () => width);
            safeDefineProperty(window.screen, 'height', () => height);
            safeDefineProperty(window.screen, 'availWidth', () => width);
            safeDefineProperty(window.screen, 'availHeight', () => height);
            try {{
                const originalDateTimeFormat = Intl.DateTimeFormat;
                Intl.DateTimeFormat = function(locales, options) {{
                    const defaults = {{ timeZone: "{profile_tz}" }};
                    return new originalDateTimeFormat(locales, Object.assign(defaults, options));
                }};
                Intl.DateTimeFormat.prototype = originalDateTimeFormat.prototype;
                Intl.DateTimeFormat.supportedLocalesOf = originalDateTimeFormat.supportedLocalesOf;
            }} catch (e) {{}}
            if ({str(audio_noise).lower()}) {{
                (function() {{
                    const audioProfileSeed = {_seed} >>> 0;
                    const mixAudioIndex = function(channelIndex, sampleIndex) {{
                        let h = (
                            audioProfileSeed ^
                            Math.imul((channelIndex + 1) >>> 0, 0x9e3779b1) ^
                            Math.imul((sampleIndex + 1) >>> 0, 0x85ebca6b)
                        ) >>> 0;

                        h = Math.imul(h ^ (h >>> 16), 0x7feb352d) >>> 0;
                        h = Math.imul(h ^ (h >>> 15), 0x846ca68b) >>> 0;
                        return (h ^ (h >>> 16)) >>> 0;
                    }};

                    const origStartRendering = OfflineAudioContext.prototype.startRendering;
                    const origGetChannelData = AudioBuffer.prototype.getChannelData;
                    const origRenderedBufferDesc = Object.getOwnPropertyDescriptor(OfflineAudioCompletionEvent.prototype, 'renderedBuffer');

                    const processedAudioBuffers = new WeakSet();

                    const applyAudioBufferNoise = function(audioBuffer) {{
                        if (!(audioBuffer instanceof AudioBuffer)) return;
                        if (processedAudioBuffers.has(audioBuffer)) return;

                        const channelCount = audioBuffer.numberOfChannels;
                        for (let c = 0; c < channelCount; c++) {{
                            const channelData = origGetChannelData.call(audioBuffer, c);
                            const len = channelData.length;
                            for (let s = 0; s < len; s++) {{
                                const hash = mixAudioIndex(c, s);
                                if ((hash & 1023) === 0) {{
                                    const val = channelData[s];
                                    if (val !== 0 && Number.isFinite(val)) {{
                                        const delta = ((hash >>> 10) & 1) === 0 ? -1e-7 : 1e-7;
                                        let newVal = val + delta;
                                        if (newVal > 1) newVal = 1;
                                        else if (newVal < -1) newVal = -1;
                                        channelData[s] = Math.fround(newVal);
                                    }}
                                }}
                            }}
                        }}
                        processedAudioBuffers.add(audioBuffer);
                        return audioBuffer;
                    }};

                    OfflineAudioContext.prototype.startRendering = makeNative(function startRendering() {{
                        const promise = origStartRendering.apply(this, arguments);
                        if (promise && typeof promise.then === 'function') {{
                            return promise.then(function(buffer) {{
                                applyAudioBufferNoise(buffer);
                                return buffer;
                            }});
                        }}
                        return promise;
                    }}, origStartRendering.name || 'startRendering', origStartRendering.length);

                    if (origRenderedBufferDesc && origRenderedBufferDesc.get) {{
                        const origGet = origRenderedBufferDesc.get;
                        const newGet = function get() {{
                            const buffer = origGet.call(this);
                            if (buffer) applyAudioBufferNoise(buffer);
                            return buffer;
                        }};
                        Object.defineProperty(OfflineAudioCompletionEvent.prototype, 'renderedBuffer', {{
                            get: makeNative(newGet, origGet.name || 'get renderedBuffer', origGet.length),
                            set: origRenderedBufferDesc.set,
                            enumerable: origRenderedBufferDesc.enumerable,
                            configurable: origRenderedBufferDesc.configurable
                        }});
                    }}

                    const protectAnalyser = function(methodName, domainConstant, isByte, isFreq) {{
                        if (typeof AnalyserNode.prototype[methodName] !== 'function') return;
                        const origMethod = AnalyserNode.prototype[methodName];
                        const wrapper = function() {{
                            const res = origMethod.apply(this, arguments);
                            const array = arguments[0];
                            if (!array) return res;
                            const len = array.length;
                            for (let i = 0; i < len; i++) {{
                                const hash = mixAudioIndex(domainConstant, i);
                                if ((hash & 127) === 0) {{
                                    const val = array[i];
                                    if (isByte) {{
                                        const sentinel = isFreq ? 0 : 128;
                                        if (val !== sentinel) {{
                                            const delta = ((hash >>> 7) & 1) === 0 ? -1 : 1;
                                            let newVal = val + delta;
                                            if (newVal > 255) newVal = 255;
                                            else if (newVal < 0) newVal = 0;
                                            array[i] = newVal;
                                        }}
                                    }} else {{
                                        if (isFreq) {{
                                            if (Number.isFinite(val)) {{
                                                const delta = ((hash >>> 7) & 1) === 0 ? -1e-5 : 1e-5;
                                                array[i] = Math.fround(val + delta);
                                            }}
                                        }} else {{
                                            if (val !== 0 && Number.isFinite(val)) {{
                                                const delta = ((hash >>> 7) & 1) === 0 ? -1e-7 : 1e-7;
                                                let newVal = val + delta;
                                                if (newVal > 1) newVal = 1;
                                                else if (newVal < -1) newVal = -1;
                                                array[i] = Math.fround(newVal);
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                            return res;
                        }};
                        AnalyserNode.prototype[methodName] = makeNative(wrapper, origMethod.name || methodName, origMethod.length);
                    }};

                    protectAnalyser('getFloatFrequencyData', 101, false, true);
                    protectAnalyser('getByteFrequencyData', 102, true, true);
                    protectAnalyser('getFloatTimeDomainData', 103, false, false);
                    protectAnalyser('getByteTimeDomainData', 104, true, false);
                }})();
            }}
            if ({str(canvas_noise).lower()}) {{
                const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
                const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
                const originalToBlob = HTMLCanvasElement.prototype.toBlob;

                const applyCanvasNoise = function(imgData, originX, originY, surfaceWidth) {{
                    const data = imgData.data;
                    const r_offset = {canvas_r_offset};
                    const g_offset = {canvas_g_offset};
                    const b_offset = {canvas_b_offset};
                    const noiseSeed = (
                        Math.imul(r_offset, 73856093) ^
                        Math.imul(g_offset, 19349663) ^
                        Math.imul(b_offset, 83492791)
                    ) >>> 0;
                    const ox = (originX === undefined || originX === null) ? 0 : (originX | 0);
                    const oy = (originY === undefined || originY === null) ? 0 : (originY | 0);
                    const sw = (surfaceWidth === undefined || surfaceWidth === null) ? imgData.width : (surfaceWidth | 0);
                    const imgW = imgData.width;

                    for (let i = 0; i < data.length; i += 4) {{
                        const localIndex = i >>> 2;
                        const localCol = localIndex % imgW;
                        const localRow = (localIndex - localCol) / imgW;
                        const globalX = ox + localCol;
                        const globalY = oy + localRow;
                        const pixelIndex = globalY * sw + globalX;
                        let hash = Math.imul((pixelIndex ^ noiseSeed) >>> 0, 0x45d9f3b);
                        hash ^= hash >>> 16;

                        if ((hash & 63) === 0 && data[i + 3] !== 0) {{
                            const channel = (hash >>> 6) % 3;
                            const delta = ((hash >>> 8) & 1) === 0 ? -1 : 1;
                            const targetIndex = i + channel;
                            data[targetIndex] = Math.max(
                                0,
                                Math.min(255, data[targetIndex] + delta)
                            );
                        }}
                    }}
                    return imgData;
                }};

                CanvasRenderingContext2D.prototype.getImageData = makeNative(function(x, y, w, h) {{
                    const imgData = originalGetImageData.apply(this, arguments);
                    const nx = x | 0;
                    const ny = y | 0;
                    return applyCanvasNoise(imgData, nx, ny, this.canvas.width);
                }}, "getImageData", 4);

                const makeNoisedCanvasClone = function(sourceCanvas) {{
                    const clone = document.createElement("canvas");
                    clone.width = sourceCanvas.width;
                    clone.height = sourceCanvas.height;

                    const cloneContext = clone.getContext("2d");
                    cloneContext.drawImage(sourceCanvas, 0, 0);

                    const imgData = originalGetImageData.call(
                        cloneContext,
                        0,
                        0,
                        clone.width,
                        clone.height
                    );

                    applyCanvasNoise(imgData);
                    cloneContext.putImageData(imgData, 0, 0);
                    return clone;
                }};

                HTMLCanvasElement.prototype.toDataURL = makeNative(function() {{
                    if (this.width === 0 || this.height === 0) {{
                        return originalToDataURL.apply(this, arguments);
                    }}

                    const clone = makeNoisedCanvasClone(this);
                    return originalToDataURL.apply(clone, arguments);
                }}, "toDataURL", 0);

                HTMLCanvasElement.prototype.toBlob = makeNative(function(callback, type, quality) {{
                    if (this.width === 0 || this.height === 0) {{
                        return originalToBlob.apply(this, arguments);
                    }}

                    const clone = makeNoisedCanvasClone(this);
                    return originalToBlob.call(clone, callback, type, quality);
                }}, "toBlob", 1);

                if (
                    typeof OffscreenCanvas !== 'undefined' &&
                    typeof OffscreenCanvasRenderingContext2D !== 'undefined' &&
                    typeof OffscreenCanvas.prototype.convertToBlob === 'function'
                ) {{
                    const originalOCGetImageData = OffscreenCanvasRenderingContext2D.prototype.getImageData;
                    const originalConvertToBlob  = OffscreenCanvas.prototype.convertToBlob;

                    const origOCGIDDesc = Object.getOwnPropertyDescriptor(
                        OffscreenCanvasRenderingContext2D.prototype, 'getImageData'
                    );

                    const ocGetImageDataWrapper = function(x, y, w, h) {{
                        if (!(this instanceof OffscreenCanvasRenderingContext2D)) {{
                            return originalOCGetImageData.apply(this, arguments);
                        }}
                        const imgData = originalOCGetImageData.apply(this, arguments);
                        const nx = x | 0;
                        const ny = y | 0;
                        return applyCanvasNoise(imgData, nx, ny, this.canvas.width);
                    }};

                    makeNative(ocGetImageDataWrapper, 'getImageData', originalOCGetImageData.length);

                    const ocGIDDescFlags = origOCGIDDesc
                        ? {{ writable: origOCGIDDesc.writable, enumerable: origOCGIDDesc.enumerable, configurable: origOCGIDDesc.configurable }}
                        : {{ writable: true, enumerable: false, configurable: true }};

                    Object.defineProperty(
                        OffscreenCanvasRenderingContext2D.prototype,
                        'getImageData',
                        Object.assign({{ value: ocGetImageDataWrapper }}, ocGIDDescFlags)
                    );

                    const makeNoisedOffscreenClone = function(sourceOC) {{
                        const clone = new OffscreenCanvas(sourceOC.width, sourceOC.height);
                        const cloneCtx = clone.getContext('2d');
                        cloneCtx.drawImage(sourceOC, 0, 0);
                        const imgData = originalOCGetImageData.call(
                            cloneCtx,
                            0, 0,
                            clone.width, clone.height
                        );
                        applyCanvasNoise(imgData, 0, 0, clone.width);
                        cloneCtx.putImageData(imgData, 0, 0);
                        return clone;
                    }};

                    OffscreenCanvas.prototype.convertToBlob = makeNative(
                        function(options) {{
                            if (this.width === 0 || this.height === 0) {{
                                return originalConvertToBlob.apply(this, arguments);
                            }}
                            const clone = makeNoisedOffscreenClone(this);
                            return originalConvertToBlob.call(clone, options);
                        }},
                        'convertToBlob',
                        originalConvertToBlob.length
                    );
                }}

                // Inject the same OffscreenCanvas noise into dedicated/blob workers.
                // Page init scripts do not run in worker realms; prepend a worker-safe
                // bootstrap to JS Blob sources and data: worker URLs.
                (function installWorkerCanvasBootstrap() {{
                    const workerBootstrap = '(function(){{' +
                        'if(typeof self!=="undefined"&&self.__ghostWorkerPatchInstalled)return;' +
                        'try{{' +
                        'var R={canvas_r_offset},G={canvas_g_offset},B={canvas_b_offset};' +
                        'function noise(id,ox,oy,sw){{var d=id.data,seed=(Math.imul(R,73856093)^Math.imul(G,19349663)^Math.imul(B,83492791))>>>0;' +
                        'var x0=(ox==null)?0:(ox|0),y0=(oy==null)?0:(oy|0),W=(sw==null)?id.width:(sw|0),iw=id.width;' +
                        'for(var i=0;i<d.length;i+=4){{var li=i>>>2,lc=li%iw,lr=(li-lc)/iw,pi=(y0+lr)*W+(x0+lc);' +
                        'var h=Math.imul(((pi^seed)>>>0),0x45d9f3b);h^=(h>>>16);' +
                        'if((h&63)===0&&d[i+3]!==0){{var ch=(h>>>6)%3,dt=((h>>>8)&1)===0?-1:1,ti=i+ch;' +
                        'd[ti]=Math.max(0,Math.min(255,d[ti]+dt));}}}}return id;}}' +
                        'if(typeof OffscreenCanvas==="undefined"){{if(typeof self!=="undefined")self.__ghostWorkerPatchInstalled=true;return;}}' +
                        'var proto=null;try{{var t=new OffscreenCanvas(1,1),c=t.getContext("2d");if(c)proto=Object.getPrototypeOf(c);}}catch(e){{}}' +
                        'if(!proto){{if(typeof self!=="undefined")self.__ghostWorkerPatchInstalled=true;return;}}' +
                        'var oGID=proto.getImageData,oCTB=OffscreenCanvas.prototype.convertToBlob;' +
                        'proto.getImageData=function(x,y,w,h){{var img=oGID.apply(this,arguments);return noise(img,x|0,y|0,this.canvas?this.canvas.width:img.width);}};' +
                        'OffscreenCanvas.prototype.convertToBlob=function(opts){{if(this.width===0||this.height===0)return oCTB.apply(this,arguments);' +
                        'var cl=new OffscreenCanvas(this.width,this.height),cx=cl.getContext("2d");cx.drawImage(this,0,0);' +
                        'var id=oGID.call(cx,0,0,cl.width,cl.height);noise(id,0,0,cl.width);cx.putImageData(id,0,0);return oCTB.call(cl,opts);}};' +
                        'if(typeof self!=="undefined")self.__ghostWorkerPatchInstalled=true;' +
                        '}}catch(_e){{try{{if(typeof self!=="undefined")self.__ghostWorkerPatchInstalled=true;}}catch(_e2){{}}}}' +
                        '}})();';

                    const isJsBlobType = (type) => {{
                        const t = String(type || '').toLowerCase();
                        return t.includes('javascript') || t.includes('ecmascript') || t === 'text/js' || t === 'application/js';
                    }};

                    const OrigBlob = typeof Blob !== 'undefined' ? Blob : null;
                    if (OrigBlob) {{
                        const PatchedBlob = function(parts, options) {{
                            let nextParts = parts;
                            let nextOptions = options;
                            try {{
                                const type = options && options.type;
                                if (isJsBlobType(type)) {{
                                    const list = parts == null ? [] : (Array.isArray(parts) ? parts.slice() : Array.from(parts));
                                    list.unshift(workerBootstrap + '\\n');
                                    nextParts = list;
                                }}
                            }} catch (_blobErr) {{}}
                            return new OrigBlob(nextParts, nextOptions);
                        }};
                        PatchedBlob.prototype = OrigBlob.prototype;
                        try {{ Object.defineProperty(PatchedBlob, 'name', {{ value: 'Blob', configurable: true }}); }} catch (_n) {{}}
                        try {{ Object.defineProperty(PatchedBlob, 'length', {{ value: OrigBlob.length, configurable: true }}); }} catch (_l) {{}}
                        self.Blob = PatchedBlob;
                    }}

                    const wrapWorkerCtor = (OrigCtor, name) => {{
                        if (typeof OrigCtor !== 'function') return OrigCtor;
                        const Wrapped = function(scriptURL, options) {{
                            let url = scriptURL;
                            try {{
                                if (typeof scriptURL === 'string' && scriptURL.startsWith('data:')) {{
                                    const comma = scriptURL.indexOf(',');
                                    if (comma > 0) {{
                                        const header = scriptURL.slice(0, comma);
                                        const body = scriptURL.slice(comma + 1);
                                        const isBase64 = /;base64/i.test(header);
                                        if (!isBase64 && /javascript|ecmascript/i.test(header)) {{
                                            url = header + ',' + encodeURIComponent(workerBootstrap + '\\n') + body;
                                        }}
                                    }}
                                }}
                            }} catch (_wErr) {{}}
                            if (options === undefined) {{
                                return new OrigCtor(url);
                            }}
                            return new OrigCtor(url, options);
                        }};
                        Wrapped.prototype = OrigCtor.prototype;
                        try {{ Object.defineProperty(Wrapped, 'name', {{ value: name, configurable: true }}); }} catch (_wn) {{}}
                        try {{ Object.defineProperty(Wrapped, 'length', {{ value: OrigCtor.length, configurable: true }}); }} catch (_wl) {{}}
                        return Wrapped;
                    }};

                    if (typeof Worker !== 'undefined') {{
                        self.Worker = wrapWorkerCtor(Worker, 'Worker');
                    }}
                    if (typeof SharedWorker !== 'undefined') {{
                        self.SharedWorker = wrapWorkerCtor(SharedWorker, 'SharedWorker');
                    }}
                }})();
            }}
            if ({str(webgl_noise).lower()}) {{
                const spoofedRenderer = '{ai_webgl_renderer}';
                const spoofedVendor = '{ai_webgl_vendor}';
                const spoofedOS = {json.dumps(ua_os_val)};

                const gpuProfileForRenderer = (renderer) => {{
                    const r = renderer.toLowerCase();
                    const baseProfile = (gl) => ({{
                        [gl.LOW_FLOAT]: {{ rangeMin: 127, rangeMax: 127, precision: 1 }},
                        [gl.MEDIUM_FLOAT]: {{ rangeMin: 127, rangeMax: 127, precision: 10 }},
                        [gl.HIGH_FLOAT]: {{ rangeMin: 127, rangeMax: 127, precision: 23 }},
                        [gl.LOW_INT]: {{ rangeMin: 31, rangeMax: 31, precision: 0 }},
                        [gl.MEDIUM_INT]: {{ rangeMin: 31, rangeMax: 31, precision: 0 }},
                        [gl.HIGH_INT]: {{ rangeMin: 31, rangeMax: 31, precision: 0 }}
                    }});
                    if (r.includes('intel')) {{
                        return baseProfile;
                    }}
                    if (r.includes('apple') || r.includes('m2') || r.includes('metal') || r.includes('mali') || r.includes('adreno')) {{
                        return baseProfile;
                    }}
                    return baseProfile;
                }};

                const gpuLimitsForRenderer = (renderer) => {{
                    const r = renderer.toLowerCase();
                    const GL = WebGLRenderingContext;
                    if (r.includes('intel')) {{
                        return {{
                            [GL.MAX_TEXTURE_SIZE]: 8192,
                            [GL.MAX_VIEWPORT_DIMS]: new Float32Array([8192, 8192]),
                            [GL.MAX_RENDERBUFFER_SIZE]: 8192,
                            [GL.ALIASED_POINT_SIZE_RANGE]: new Float32Array([1, 255]),
                            [GL.MAX_VERTEX_ATTRIBS]: 16,
                            [GL.MAX_VERTEX_UNIFORM_VECTORS]: 2048,
                            [GL.MAX_FRAGMENT_UNIFORM_VECTORS]: 1024
                        }};
                    }}
                    if (r.includes('apple') || r.includes('m2') || r.includes('metal') || r.includes('mali') || r.includes('adreno')) {{
                        return {{
                            [GL.MAX_TEXTURE_SIZE]: 8192,
                            [GL.MAX_VIEWPORT_DIMS]: new Float32Array([8192, 8192]),
                            [GL.MAX_RENDERBUFFER_SIZE]: 8192,
                            [GL.ALIASED_POINT_SIZE_RANGE]: new Float32Array([1, 511]),
                            [GL.MAX_VERTEX_ATTRIBS]: 16,
                            [GL.MAX_VERTEX_UNIFORM_VECTORS]: 2048,
                            [GL.MAX_FRAGMENT_UNIFORM_VECTORS]: 1024
                        }};
                    }}
                    return {{
                        [GL.MAX_TEXTURE_SIZE]: 16384,
                        [GL.MAX_VIEWPORT_DIMS]: new Float32Array([16384, 16384]),
                        [GL.MAX_RENDERBUFFER_SIZE]: 16384,
                        [GL.ALIASED_POINT_SIZE_RANGE]: new Float32Array([1, 2047]),
                        [GL.MAX_VERTEX_ATTRIBS]: 16,
                        [GL.MAX_VERTEX_UNIFORM_VECTORS]: 4096,
                        [GL.MAX_FRAGMENT_UNIFORM_VECTORS]: 2048
                    }};
                }};

                const precisionProfileBuilder = gpuProfileForRenderer(spoofedRenderer);
                const limitsMap = gpuLimitsForRenderer(spoofedRenderer);

                const GL = WebGLRenderingContext;
                const GL2 = (typeof WebGL2RenderingContext !== 'undefined') ? WebGL2RenderingContext : null;
                const isWebGL2Context = (ctx) => GL2 && ctx instanceof GL2;
                const webglVersion = spoofedOS === 'Mac' ? 'WebGL 1.0 (OpenGL)' : (spoofedOS === 'Windows' ? 'WebGL 1.0 (OpenGL ES 2.0 Chromium)' : 'WebGL 1.0');
                const webglShading = spoofedOS === 'Mac' ? 'WebGL GLSL ES 1.0 (OpenGL)' : 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
                const webgl2Version = spoofedOS === 'Mac' ? 'WebGL 2.0 (OpenGL)' : (spoofedOS === 'Windows' ? 'WebGL 2.0 (OpenGL ES 3.0 Chromium)' : 'WebGL 2.0');
                const webgl2Shading = spoofedOS === 'Mac' ? 'WebGL GLSL ES 3.00 (OpenGL)' : 'WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)';
                const commonExtensions = [
                    'WEBGL_debug_renderer_info', 'WEBGL_lose_context',
                    'EXT_texture_filter_anisotropic', 'OES_texture_float_linear',
                    'OES_element_index_uint', 'OES_standard_derivatives',
                    'WEBGL_depth_texture', 'WEBGL_draw_buffers',
                    'ANGLE_instanced_arrays', 'OES_vertex_array_object',
                    'WEBGL_compressed_texture_s3tc'
                ];

                const __webGLNoiseSeed = {_seed};
                const __addWebGLReadPixelsNoise = function(pixels) {{
                    if (!(pixels instanceof Uint8Array)) return;
                    const len = pixels.length;
                    for (let i = 0; i < len; i += 4) {{
                        let h = Math.imul(((i >>> 2) ^ __webGLNoiseSeed) >>> 0, 0x45d9f3b);
                        h = (h ^ (h >>> 16)) >>> 0;
                        if ((h & 63) === 0) {{
                            const channel = (h >>> 6) % 3;
                            const delta = ((h >>> 8) & 1) === 0 ? -1 : 1;
                            pixels[i + channel] = Math.max(0, Math.min(255, pixels[i + channel] + delta));
                        }}
                    }}
                }};

                if (typeof WebGLRenderingContext !== 'undefined') {{
                    const getParameter = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = makeNative(function(parameter) {{
                        if (parameter === 37445) return spoofedVendor;
                        if (parameter === 37446) return spoofedRenderer;
                        if (parameter === GL.VERSION) return isWebGL2Context(this) ? webgl2Version : webglVersion;
                        if (parameter === GL.SHADING_LANGUAGE_VERSION) return isWebGL2Context(this) ? webgl2Shading : webglShading;
                        if (parameter in limitsMap) return limitsMap[parameter];
                        return getParameter.apply(this, [parameter]);
                    }}, 'getParameter', getParameter.length);

                    const _origGetShaderPrecisionFormat = WebGLRenderingContext.prototype.getShaderPrecisionFormat;
                    WebGLRenderingContext.prototype.getShaderPrecisionFormat = makeNative(function(shaderType, precisionType) {{
                        const profile = precisionProfileBuilder(this);
                        if (profile && profile[precisionType]) {{
                            return profile[precisionType];
                        }}
                        return _origGetShaderPrecisionFormat.apply(this, arguments);
                    }}, 'getShaderPrecisionFormat', _origGetShaderPrecisionFormat.length);

                    const _origGetExt = WebGLRenderingContext.prototype.getExtension;
                    WebGLRenderingContext.prototype.getExtension = makeNative(function(name) {{
                        const ext = _origGetExt.apply(this, arguments);
                        if (name === 'WEBGL_debug_renderer_info' && ext) {{
                            return new Proxy(ext, {{ get: function(t, p) {{
                                if (p === 'UNMASKED_VENDOR_WEBGL') return 37445;
                                if (p === 'UNMASKED_RENDERER_WEBGL') return 37446;
                                return t[p];
                            }} }});
                        }}
                        return ext;
                    }}, 'getExtension', _origGetExt.length);

                    const _origGetSupportedExtensions = WebGLRenderingContext.prototype.getSupportedExtensions;
                    WebGLRenderingContext.prototype.getSupportedExtensions = makeNative(function() {{
                        const native = _origGetSupportedExtensions.apply(this, arguments) || [];
                        const out = commonExtensions.filter(e => native.includes(e));
                        return out.length ? out : commonExtensions.slice();
                    }}, 'getSupportedExtensions', _origGetSupportedExtensions.length);

                    const _origReadPixels = WebGLRenderingContext.prototype.readPixels;
                    WebGLRenderingContext.prototype.readPixels = makeNative(function(x, y, width, height, format, type, pixels) {{
                        const result = _origReadPixels.apply(this, arguments);
                        if (pixels instanceof Uint8Array && format === this.RGBA && type === this.UNSIGNED_BYTE) {{
                            __addWebGLReadPixelsNoise(pixels);
                        }}
                        return result;
                    }}, 'readPixels', _origReadPixels.length);
                }}

                if (typeof WebGL2RenderingContext !== 'undefined') {{
                    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                    WebGL2RenderingContext.prototype.getParameter = makeNative(function(parameter) {{
                        if (parameter === 37445) return spoofedVendor;
                        if (parameter === 37446) return spoofedRenderer;
                        if (parameter === GL.VERSION) return isWebGL2Context(this) ? webgl2Version : webglVersion;
                        if (parameter === GL.SHADING_LANGUAGE_VERSION) return isWebGL2Context(this) ? webgl2Shading : webglShading;
                        if (parameter in limitsMap) return limitsMap[parameter];
                        return getParameter2.apply(this, [parameter]);
                    }}, 'getParameter', getParameter2.length);

                    const _origGetShaderPrecisionFormat2 = WebGL2RenderingContext.prototype.getShaderPrecisionFormat;
                    WebGL2RenderingContext.prototype.getShaderPrecisionFormat = makeNative(function(shaderType, precisionType) {{
                        const profile = precisionProfileBuilder(this);
                        if (profile && profile[precisionType]) {{
                            return profile[precisionType];
                        }}
                        return _origGetShaderPrecisionFormat2.apply(this, arguments);
                    }}, 'getShaderPrecisionFormat', _origGetShaderPrecisionFormat2.length);

                    const _origGetExt2 = WebGL2RenderingContext.prototype.getExtension;
                    WebGL2RenderingContext.prototype.getExtension = makeNative(function(name) {{
                        const ext = _origGetExt2.apply(this, arguments);
                        if (name === 'WEBGL_debug_renderer_info' && ext) {{
                            return new Proxy(ext, {{ get: function(t, p) {{
                                if (p === 'UNMASKED_VENDOR_WEBGL') return 37445;
                                if (p === 'UNMASKED_RENDERER_WEBGL') return 37446;
                                return t[p];
                            }} }});
                        }}
                        return ext;
                    }}, 'getExtension', _origGetExt2.length);

                    const _origGetSupportedExtensions2 = WebGL2RenderingContext.prototype.getSupportedExtensions;
                    WebGL2RenderingContext.prototype.getSupportedExtensions = makeNative(function() {{
                        const native = _origGetSupportedExtensions2.apply(this, arguments) || [];
                        const out = commonExtensions.filter(e => native.includes(e));
                        return out.length ? out : commonExtensions.slice();
                    }}, 'getSupportedExtensions', _origGetSupportedExtensions2.length);

                    const _origReadPixels2 = WebGL2RenderingContext.prototype.readPixels;
                    WebGL2RenderingContext.prototype.readPixels = makeNative(function(x, y, width, height, format, type, pixels) {{
                        const result = _origReadPixels2.apply(this, arguments);
                        if (pixels instanceof Uint8Array && format === this.RGBA && type === this.UNSIGNED_BYTE) {{
                            __addWebGLReadPixelsNoise(pixels);
                        }}
                        return result;
                    }}, 'readPixels', _origReadPixels2.length);
                }}
            }}
            // ponytail: deterministic per-profile pixel jitter on geometry APIs
            // to defeat glyph and font-based geometry fingerprinting.
            const __geomSign = ((_seed >> 4) & 1) === 0 ? -1 : 1;
            const __geomOffset = __geomSign * 1;
            const originalGetBoundingClientRect = Element.prototype.getBoundingClientRect;
            const originalGetClientRects = Element.prototype.getClientRects;

            const __shiftRect = function(rect, offset) {{
                return new DOMRect(rect.x + offset, rect.y + offset, rect.width, rect.height);
            }};

            Element.prototype.getBoundingClientRect = makeNative(function() {{
                return __shiftRect(originalGetBoundingClientRect.apply(this, arguments), __geomOffset);
            }}, 'getBoundingClientRect', originalGetBoundingClientRect.length);

            Element.prototype.getClientRects = makeNative(function() {{
                const rects = originalGetClientRects.apply(this, arguments);
                const shifted = [];
                for (let i = 0; i < rects.length; i++) {{
                    shifted.push(__shiftRect(rects[i], __geomOffset));
                }}
                Object.defineProperty(shifted, 'length', {{ value: shifted.length, writable: false, configurable: true }});
                shifted.item = makeNative(function(index) {{ return shifted[index] || null; }}, 'item', 1);
                return shifted;
            }}, 'getClientRects', originalGetClientRects.length);
        """

    plugins_json, mime_types_json = _build_native_surface(_seed, ua_os_val)
    spoofing_script += f"""
        const __fakePlugins = {json.dumps(plugins_json)};
        const __fakeMimeTypes = {json.dumps(mime_types_json)};
        __fakeMimeTypes.forEach((m, i) => {{ m.enabledPlugin = __fakePlugins[i] || null; }});

        const __pluginArray = (() => {{
            const arr = {{ length: __fakePlugins.length }};
            for (let i = 0; i < __fakePlugins.length; i++) {{
                const p = __fakePlugins[i];
                arr[i] = p;
                if (p.name) arr[p.name] = p;
                if (p.filename) arr[p.filename] = p;
            }}
            arr.item = makeNative(function item(index) {{ return arr[index] || null; }}, 'item', 1);
            arr.namedItem = makeNative(function namedItem(name) {{ return arr[name] || null; }}, 'namedItem', 1);
            arr.refresh = makeNative(function refresh() {{}}, 'refresh', 0);
            return arr;
        }})();

        const __mimeTypeArray = (() => {{
            const arr = {{ length: __fakeMimeTypes.length }};
            for (let i = 0; i < __fakeMimeTypes.length; i++) {{
                const m = __fakeMimeTypes[i];
                arr[i] = m;
                if (m.type) arr[m.type] = m;
            }}
            arr.item = makeNative(function item(index) {{ return arr[index] || null; }}, 'item', 1);
            arr.namedItem = makeNative(function namedItem(name) {{ return arr[name] || null; }}, 'namedItem', 1);
            return arr;
        }})();

        safeDefineProperty(navigator, 'plugins', () => __pluginArray);
        safeDefineProperty(navigator, 'mimeTypes', () => __mimeTypeArray);

        // ponytail: canonical Chromium navigator property stubs
        safeDefineProperty(navigator, 'vendor', () => "Google Inc.");
        safeDefineProperty(navigator, 'product', () => "Gecko");
        safeDefineProperty(navigator, 'cookieEnabled', () => true);
        safeDefineProperty(navigator, 'pdfViewerEnabled', () => true);
        safeDefineProperty(navigator, 'doNotTrack', () => null);
        navigator.javaEnabled = makeNative(function javaEnabled() {{ return false; }}, 'javaEnabled', 0);

        // ponytail: spoof performance.memory to match profile hardware
        (function() {{
            try {{
                Object.defineProperty(performance, 'memory', {{
                    get: makeNative(function() {{
                        return {{
                            jsHeapSizeLimit: {memory_gb} * 1073741824,
                            totalJSHeapSize: Math.round({memory_gb} * 0.3 * 1073741824),
                            usedJSHeapSize: Math.round({memory_gb} * 0.15 * 1073741824)
                        }};
                    }}, 'get memory'),
                    configurable: true,
                    enumerable: true
                }});
            }} catch (e) {{}}
        }})();
    """

    userAgentMetadata = {
        "brands": brands,
        "fullVersionList": full_version_list,
        "fullVersion": ch_ua_full_version,
        "platform": sec_ch_ua_platform.replace('"', ''),
        "platformVersion": ch_platform_version,
        "architecture": ch_architecture,
        "bitness": ch_bitness,
        "model": ch_model,
        "mobile": is_mobile_profile,
        "wow64": False
    }

    worker_canvas_patch = ""
    if canvas_noise:
        worker_canvas_patch = (
            "(function(){"
            "if(typeof self!==\"undefined\"&&self.__ghostWorkerPatchInstalled)return;"
            "try{"
            f"var R={int(canvas_r_offset)},G={int(canvas_g_offset)},B={int(canvas_b_offset)};"
            "function noise(id,ox,oy,sw){var d=id.data,seed=(Math.imul(R,73856093)^Math.imul(G,19349663)^Math.imul(B,83492791))>>>0;"
            "var x0=(ox==null)?0:(ox|0),y0=(oy==null)?0:(oy|0),W=(sw==null)?id.width:(sw|0),iw=id.width;"
            "for(var i=0;i<d.length;i+=4){var li=i>>>2,lc=li%iw,lr=(li-lc)/iw,pi=(y0+lr)*W+(x0+lc);"
            "var h=Math.imul(((pi^seed)>>>0),0x45d9f3b);h^=(h>>>16);"
            "if((h&63)===0&&d[i+3]!==0){var ch=(h>>>6)%3,dt=((h>>>8)&1)===0?-1:1,ti=i+ch;"
            "d[ti]=Math.max(0,Math.min(255,d[ti]+dt));}}}return id;}"
            "if(typeof OffscreenCanvas===\"undefined\"){if(typeof self!==\"undefined\")self.__ghostWorkerPatchInstalled=true;return;}"
            "var proto=null;try{var t=new OffscreenCanvas(1,1),c=t.getContext(\"2d\");if(c)proto=Object.getPrototypeOf(c);}catch(e){}"
            "if(!proto){if(typeof self!==\"undefined\")self.__ghostWorkerPatchInstalled=true;return;}"
            "var oGID=proto.getImageData,oCTB=OffscreenCanvas.prototype.convertToBlob;"
            "proto.getImageData=function(x,y,w,h){var img=oGID.apply(this,arguments);return noise(img,x|0,y|0,this.canvas?this.canvas.width:img.width);};"
            "OffscreenCanvas.prototype.convertToBlob=function(opts){if(this.width===0||this.height===0)return oCTB.apply(this,arguments);"
            "var cl=new OffscreenCanvas(this.width,this.height),cx=cl.getContext(\"2d\");cx.drawImage(this,0,0);"
            "var id=oGID.call(cx,0,0,cl.width,cl.height);noise(id,0,0,cl.width);cx.putImageData(id,0,0);return oCTB.call(cl,opts);};"
            "if(typeof self!==\"undefined\")self.__ghostWorkerPatchInstalled=true;"
            "}catch(_e){try{if(typeof self!==\"undefined\")self.__ghostWorkerPatchInstalled=true;}catch(_e2){}}"
            "})();"
        )

    return {
        "headless": playwright_headless,
        "args": args,
        "proxy": proxy,
        "assigned_proxy": assigned_proxy,
        "user_agent": profile.get("user_agent") or ua,
        "timezone_id": profile.get("timezone"),
        "locale": profile.get("locale"),
        "viewport": {"width": _vp_w, "height": _vp_h},
        "device_scale_factor": device_scale_factor,
        "webrtc_mode": webrtc_mode,
        "spoofing_script": spoofing_script,
        "worker_canvas_patch": worker_canvas_patch,
        "proxy_warning": None,
        "block_trackers": advanced.get("block_trackers", False),
        "extra_http_headers": extra_http_headers,
        "userAgentMetadata": userAgentMetadata,
        "block_service_workers": block_service_workers,
        "service_worker_policy": surface_policy.service_worker_policy,
    }

async def _do_launch_profile(profile_id: str, force_headless: bool = False, pin: str = None):
    import os
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        return {"status": "error", "message": "Profile not found"}

    # PIN-lock gate: bypass in explicit test environments so internal callers keep working.
    pin_hash = profile.get("pin_hash")
    if pin_hash and not _is_explicit_test_environment():
        if not isinstance(pin, str) or not pin:
            return {
                "status": "error",
                "message": "PIN required to launch this profile",
                "code": "PIN_REQUIRED",
            }
        if not profile_manager.verify_profile_pin(profile_id, pin):
            return {
                "status": "error",
                "message": "Incorrect PIN",
                "code": "PIN_INVALID",
            }

    canonical_profiles_dir = os.path.normcase(os.path.realpath(profile_manager.PROFILES_DIR))
    canonical_profile_path = os.path.normcase(os.path.realpath(profile["path"]))
    if not canonical_profile_path.startswith(canonical_profiles_dir + os.sep) and canonical_profile_path != canonical_profiles_dir:
        return {"status": "error", "message": "FAIL-CLOSED: Directory traversal blocked."}

    if not os.path.exists(canonical_profile_path):
        return {"status": "error", "message": "FAIL-CLOSED: Profile directory is missing on disk."}

    if not _profile_has_verified_provenance(profile):
        return {
            "status": "error",
            "message": "FAIL-CLOSED: Profile fingerprint provenance is missing or unverified.",
        }

    scan_result = ai_scanner.scan_profile_before_launch(profile)
    if scan_result["status"] != "clean":
        return {"status": "error", "message": f"AI Scanner blocked launch: {scan_result['message']}"}

    fingerprint = profile.get("fingerprint") if isinstance(profile, dict) else None
    if fingerprint:
        try:
            from backend.ai_coherence_validator import coherence_validator
            coherence_result = coherence_validator.validate(fingerprint)
            if not coherence_result.get("passed"):
                issues = coherence_result.get("issues", ["Coherence validation failed"])
                raise RuntimeError(f"FAIL-CLOSED: {issues[0]}")
        except ImportError:
            pass

    from backend.proxy_manager import proxy_manager
    configured_server = None
    fallback_proxy = None
    pinned_proxy_str = profile.get("proxy_pin")
    explicit_proxy = profile.get("proxy")
    if pinned_proxy_str:
        try:
            configured_server = parse_proxy_string(pinned_proxy_str)["server"]
        except Exception:
            return {"status": "error", "message": "FAIL-CLOSED: The pinned proxy configuration is invalid."}
    elif explicit_proxy and isinstance(explicit_proxy, dict):
        configured_server = explicit_proxy.get("server")
    if configured_server:
        healthy = proxy_manager.health_check_proxy(configured_server)
        if not healthy:
            fallback_proxy = proxy_manager.get_healthy_proxy(preferred=configured_server)
            if fallback_proxy is None:
                return {"status": "error", "message": "No healthy proxy available"}

    from backend.lock_manager import lock_manager
    await lock_manager.acquire(profile_id)
    playwright = None
    try:
        # The state check and transition must occur while holding the same lock.
        # Checking before acquiring the lock allowed a concurrent launcher to see
        # a transient "launching" state with no active browser registration.
        current_state = get_profile_state(profile_id)
        if current_state in ["launching", "running"]:
            return {"status": "error", "message": "Browser is already launching or running"}

        set_profile_state(profile_id, "launching")
        if profile_id in active_browsers:
            set_profile_state(profile_id, "running")
            return {"status": "success", "message": "Browser already running"}

        config = await build_browser_launch_config(profile, force_headless, forced_proxy=fallback_proxy)
        playwright = await async_playwright().start()

        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile["path"],
            headless=config["headless"],
            executable_path=await get_chromium_executable_path_async(),
            args=config["args"],
            proxy=config["proxy"],
            user_agent=config["user_agent"],
            timezone_id=config["timezone_id"],
            locale=config["locale"],
            viewport=config["viewport"],
            device_scale_factor=config["device_scale_factor"],
            extra_http_headers=config["extra_http_headers"],
            # Keep ServiceWorkerContainer and its native methods untouched.
            # Playwright blocks registration at the browser-context boundary,
            # preventing a background network path without a detectable JS mock.
            service_workers="block" if config["block_service_workers"] else "allow"
        )

        profile_cdp_tasks[profile_id] = set()
        profile_page_futures[profile_id] = {}
        profile_worker_registry[profile_id] = {}
        profile_opted_origins[profile_id] = load_opted_origins(profile_id, profile["path"])
        pages_under_setup = set()

        async def setup_page_cdp(p):
            from backend.logging_config import logger
            logger.debug(
                "CDP setup started for %s",
                _safe_url_for_log(p.url) if hasattr(p, "url") else "<unknown>",
            )
            futures_dict = profile_page_futures.setdefault(profile_id, {})
            if p in futures_dict:
                logger.debug("CDP setup already registered for page")
                return await futures_dict[p]

            future = asyncio.Future()
            # The routing barrier awaits this future when a request exists, but a
            # page can fail CDP setup before issuing any request. Always retrieve
            # a terminal exception so fail-closed startup never emits an
            # unhandled "Future exception was never retrieved" warning.
            def consume_future_exception(done_future):
                if done_future.cancelled():
                    return
                try:
                    done_future.exception()
                except (asyncio.CancelledError, Exception):
                    pass

            future.add_done_callback(consume_future_exception)
            futures_dict[p] = future
            pages_under_setup.add(p)

            def on_page_close():
                logger.debug("CDP page-close cleanup triggered")
                if not future.done():
                    future.cancel()
                profile_page_futures.get(profile_id, {}).pop(p, None)
                pages_under_setup.discard(p)
                # Cleanup page from profile_worker_registry
                worker_reg = profile_worker_registry.get(profile_id, {})
                for w_url, p_set in list(worker_reg.items()):
                    p_set.discard(p)
                    if not p_set:
                        worker_reg.pop(w_url, None)
            p.on("close", on_page_close)

            # Monitor workers on this page
            def on_worker(w):
                profile_worker_registry.setdefault(profile_id, {}).setdefault(w.url, set()).add(p)
                w.on("close", lambda: profile_worker_registry.get(profile_id, {}).get(w.url, set()).discard(p))
            p.on("worker", on_worker)

            try:
                if _simulate_cdp_error:
                    raise RuntimeError("Simulated CDP Session error")
                logger.debug("Creating page CDP session")
                session = await context.new_cdp_session(p)
                logger.debug("Page CDP session created")
                await session.send("Network.enable")

                ua_override = {
                    "userAgent": config["user_agent"],
                    "acceptLanguage": config.get("locale") or "en-US",
                    "platform": config["userAgentMetadata"]["platform"],
                    "userAgentMetadata": config["userAgentMetadata"],
                }
                # Emulation populates navigator.userAgent / userAgentData;
                # Network keeps request headers coherent with the same metadata.
                await session.send("Emulation.setUserAgentOverride", ua_override)
                await session.send("Network.setUserAgentOverride", ua_override)
                if not future.done():
                    future.set_result(True)
                logger.debug("CDP user-agent metadata override applied")
            except Exception as e:
                logger.error("CDP page setup failed: %s", e)
                pages_under_setup.discard(p)
                if future.cancelled() or p.is_closed():
                    return
                if not future.done():
                    future.set_exception(e)
                logger.error(f"FAIL-CLOSED: CDP setup failed for page: {e}")
                asyncio.create_task(fail_closed_profile(profile_id, context, playwright, f"CDP UserAgentOverride failed: {e}"))
                raise RuntimeError(f"FAIL-CLOSED: CDP UserAgentOverride failed: {e}")

        # Listen for context close to cancel any remaining futures
        def on_context_close():
            for fut in profile_page_futures.get(profile_id, {}).values():
                if not fut.done():
                    fut.cancel()
        context.on("close", on_context_close)


        # Install context-wide routing barrier before any page navigation
        async def global_routing_barrier(route, request):
            if config["block_trackers"]:
                url = request.url.lower()
                trackers = ["google-analytics.com", "doubleclick.net", "facebook.com/tr", "hotjar.com", "pixel.facebook.com", "analytics", "tracker"]
                if any(t in url for t in trackers):
                    try:
                        await route.abort()
                    except Exception:
                        pass
                    return

            if config["block_service_workers"]:
                url_lower = request.url.lower()
                if request.resource_type == "serviceworker" or "sw.js" in url_lower:
                    try:
                        await route.abort()
                    except Exception:
                        pass
                    return

            page = None
            try:
                frame = request.frame
            except Exception:
                frame = None
            is_worker = (
                request.resource_type in ("worker", "sharedworker", "serviceworker")
                or frame is None
                or (request.headers.get("referer") and "worker.js" in request.headers.get("referer"))
            )

            if is_worker:
                # Map worker request to page future via referrer URL
                ref = request.headers.get("referer")
                owning_page = None
                if ref:
                    for url, p_set in list(profile_worker_registry.get(profile_id, {}).items()):
                        if url == ref or ref.endswith(url):
                            if p_set:
                                owning_page = list(p_set)[0]
                                break
                if not owning_page:
                    futures = list(profile_page_futures.get(profile_id, {}).values())
                    if not futures:
                        try:
                            await route.abort()
                        except Exception:
                            pass
                        return
                    target_future = futures[0]
                else:
                    target_future = profile_page_futures.get(profile_id, {}).get(owning_page)

                # Prepend canvas noise bootstrap to network-loaded classic worker scripts.
                worker_patch = config.get("worker_canvas_patch") or ""
                if worker_patch and request.resource_type in ("worker", "sharedworker"):
                    try:
                        await asyncio.wait_for(target_future, timeout=3.0)
                        response = await route.fetch()
                        content_type = (response.headers.get("content-type") or "").lower()
                        if "javascript" in content_type or "ecmascript" in content_type or request.url.endswith(".js"):
                            body = await response.text()
                            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
                            await route.fulfill(
                                status=response.status,
                                headers=headers,
                                body=worker_patch + "\n" + body,
                            )
                            return
                        await route.fulfill(response=response)
                        return
                    except Exception as worker_inject_err:
                        from backend.logging_config import logger as _wlog
                        _wlog.debug("Worker script inject skipped: %s", worker_inject_err)
            else:
                for i in range(300):
                    try:
                        frame = request.frame
                        if frame:
                            page = frame.page
                            if page:
                                break
                            else:
                                if i % 100 == 0:
                                    from backend.logging_config import logger
                                    logger.debug(
                                        "Routing barrier waiting for page: %s",
                                        _safe_url_for_log(request.url),
                                    )
                        else:
                            if i % 100 == 0:
                                from backend.logging_config import logger
                                logger.debug(
                                    "Routing barrier waiting for frame: %s",
                                    _safe_url_for_log(request.url),
                                )
                    except Exception:
                        try:
                            await route.abort()
                        except Exception:
                            pass
                        return
                    await asyncio.sleep(0.01)

                if not page:
                    try:
                        await route.abort()
                    except Exception:
                        pass
                    return

                target_future = profile_page_futures.get(profile_id, {}).get(page)

            if not target_future:
                try:
                    await route.abort()
                except Exception:
                    pass
                return

            try:
                # Await the configuration future with asyncio.wait_for
                await asyncio.wait_for(target_future, timeout=3.0)

                # Header override/injection based on profile_opted_origins
                try:
                    parsed_req = urlparse(request.url)
                    req_origin = f"{parsed_req.scheme}://{parsed_req.netloc}"
                except Exception:
                    req_origin = ""

                origins_dict = profile_opted_origins.get(profile_id, {})
                opted_hints = None
                for origin_key, hints in origins_dict.items():
                    if origin_key.lower().rstrip('/') == req_origin.lower().rstrip('/'):
                        opted_hints = hints
                        break

                headers = {k.lower(): v for k, v in request.headers.items()}
                high_entropy_headers = [
                    "sec-ch-ua-arch", "sec-ch-ua-bitness", "sec-ch-ua-model",
                    "sec-ch-ua-platform-version", "sec-ch-ua-full-version",
                    "sec-ch-ua-full-version-list"
                ]

                modified = False
                metadata = config.get("userAgentMetadata", {})

                # Always ensure User-Agent is overridden to profile UA
                if "user-agent" in headers or is_worker:
                    if headers.get("user-agent") != config["user_agent"]:
                        headers["user-agent"] = config["user_agent"]
                        modified = True

                # Chromium intentionally omits UA Client Hint request headers on
                # dedicated-worker script and fetch requests, even after the
                # owning origin sends Accept-CH. Do not manufacture a worker-only
                # header surface. Page and frame requests retain the origin-scoped
                # native handshake behavior below.
                if not is_worker:
                    brands_list = metadata.get("brands", [])
                    brands_str = ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in brands_list)
                    if brands_str and headers.get("sec-ch-ua") != brands_str:
                        headers["sec-ch-ua"] = brands_str
                        modified = True

                    mobile_str = "?1" if metadata.get("mobile") else "?0"
                    if headers.get("sec-ch-ua-mobile") != mobile_str:
                        headers["sec-ch-ua-mobile"] = mobile_str
                        modified = True

                    plat_str = f'"{metadata.get("platform", "")}"'
                    if headers.get("sec-ch-ua-platform") != plat_str:
                        headers["sec-ch-ua-platform"] = plat_str
                        modified = True

                    if opted_hints:
                        hint_to_header = {
                            "sec-ch-ua-arch": f'"{metadata.get("architecture", "")}"',
                            "sec-ch-ua-bitness": f'"{metadata.get("bitness", "")}"',
                            "sec-ch-ua-model": f'"{metadata.get("model", "")}"',
                            "sec-ch-ua-platform-version": f'"{metadata.get("platformVersion", "")}"',
                            "sec-ch-ua-full-version": f'"{metadata.get("fullVersion", "")}"',
                            "sec-ch-ua-full-version-list": ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in metadata.get("fullVersionList", []))
                        }

                        for hint in opted_hints:
                            h_lower = hint.lower()
                            if h_lower in hint_to_header and headers.get(h_lower) != hint_to_header[h_lower]:
                                headers[h_lower] = hint_to_header[h_lower]
                                modified = True
                    else:
                        for h in high_entropy_headers:
                            if h in headers:
                                headers.pop(h)
                                modified = True

                if modified:
                    await route.continue_(headers=headers)
                else:
                    await route.continue_()
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
                from backend.logging_config import logger
                logger.error(
                    "Routing barrier failed for %s: %s",
                    _safe_url_for_log(request.url),
                    e,
                )
                try:
                    await route.abort()
                except Exception:
                    pass
                asyncio.create_task(fail_closed_profile(profile_id, context, playwright, f"CDP routing barrier failure: {e}"))

        await context.route("**/*", global_routing_barrier)
        def on_response(response):
            headers = response.headers
            accept_ch = headers.get("accept-ch") or headers.get("Accept-CH")
            if accept_ch:
                _persist_accept_ch_opt_in(
                    profile_id, profile["path"], response.url, accept_ch
                )
        context.on("response", on_response)
        cleanup_registered = set()
        def register_page_cleanup(p):
            if p in cleanup_registered:
                return
            cleanup_registered.add(p)
            def on_page_close_sync():
                cleanup_registered.discard(p)
                worker_reg = profile_worker_registry.get(profile_id, {})
                for w_url, p_set in list(worker_reg.items()):
                    p_set.discard(p)
                    if not p_set:
                        worker_reg.pop(w_url, None)
            p.on("close", on_page_close_sync)

        # Register future pages/tabs
        def on_page_received(p):
            register_page_cleanup(p)
            register_cdp_task(
                profile_id,
                asyncio.create_task(setup_page_cdp(p)),
                context,
                playwright
            )
        context.on("page", on_page_received)

        for p in context.pages:
            register_page_cleanup(p)

        page = context.pages[0] if context.pages else await context.new_page()
        register_page_cleanup(page)

        def _origin_from_url(value: str) -> str:
            try:
                parsed = urlparse(value)
                return f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                return value

        await page.expose_function(
            "__ghostBrowserLogAccess",
            lambda api: log_api_access(_origin_from_url(page.url), api),
        )

        stealth = playwright_stealth.stealth.Stealth(
            navigator_plugins=False,
            navigator_languages=False,
            navigator_vendor=False,
            navigator_user_agent=False,
            navigator_user_agent_data=False,
            navigator_platform=False,
            navigator_platform_override=None,
            sec_ch_ua=False,
            webgl_vendor=False,
            navigator_hardware_concurrency=False,
            navigator_languages_override=None
        )
        await stealth.apply_stealth_async(page)

        _ad_pid = profile_id or "ffffffff-0000-0000-0000-000000000000"
        _ad_seed = int(_ad_pid.replace("-", "")[:8], 16)
        _ad_advanced = profile.get("advanced", {}) or {}
        _ad_os = _ad_advanced.get("os", "Windows")
        _ad_plugins, _ad_mimes = _build_native_surface(_ad_seed, _ad_os)
        _ad_plugins_json = json.dumps(_ad_plugins)
        _ad_mimes_json = json.dumps(_ad_mimes)

        _ad_viewport = config.get("viewport") or {"width": 1280, "height": 720}
        _ad_vp_w = _ad_viewport.get("width", 1280)
        _ad_vp_h = _ad_viewport.get("height", 720)
        if _ad_os == "Mac":
            _outer_w = _ad_vp_w
            _outer_h = _ad_vp_h + 79
        elif _ad_os == "Windows":
            _outer_w = _ad_vp_w + 16
            _outer_h = _ad_vp_h + 121
        else:
            _outer_w = _ad_vp_w + 8
            _outer_h = _ad_vp_h + 74

        def _to_int(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        _ad_cpu = _to_int(_ad_advanced.get("cpu_cores"), 4)
        _ad_mem = _to_int(_ad_advanced.get("memory_gb"), 8)
        _ad_has_device_values = _ad_advanced.get("cpu_cores") is not None and _ad_advanced.get("memory_gb") is not None
        _ad_webgl_vendor = _ad_advanced.get("webgl_vendor", "Google Inc. (NVIDIA)")
        _ad_webgl_renderer = _ad_advanced.get(
            "webgl_renderer",
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"
        )
        _ad_fingerprint = profile.get("fingerprint", {}) or {}
        _ad_is_mobile = _ad_advanced.get("device_type") == "mobile" or _ad_fingerprint.get("is_mobile_profile") is True
        _ad_max_touch_points = int(_ad_fingerprint.get("max_touch_points") or _ad_advanced.get("max_touch_points") or (5 if _ad_is_mobile else 1))
        _ad_locale = profile.get("locale") or _ad_advanced.get("locale") or "en-US"
        _ad_color_scheme = str(_ad_advanced.get("color_scheme", "light")).lower()
        _ad_reduced_motion = _ad_advanced.get("reduced_motion", "no-preference")

        anti_detect_script = """
            // ponytail: native toString proxy shared by every runtime stub below.
            var __nativeToString = Function.prototype.toString;
            var __spoofedFuncNames = new WeakMap();
            Function.prototype.toString = new Proxy(__nativeToString, {
                apply: function(target, thisArg, args) {
                    if (__spoofedFuncNames.has(thisArg)) {
                        return 'function ' + __spoofedFuncNames.get(thisArg) + '() { [native code] }';
                    }
                    return target.apply(thisArg, args);
                }
            });
            function __makeNative(fn, name) {
                __spoofedFuncNames.set(fn, name || fn.name || '');
                return fn;
            }

            // ponytail: lightweight bridge to report accessed fingerprinting APIs.
            function __logAccess(apiName) {
                try { window.__ghostBrowserLogAccess?.(apiName); } catch (e) {}
            }

            // ponytail: deterministic AudioContext runtime noise derived from profile seed.
            var __profileSeed = %s;
            function _audioFloat(seed, i) {
                var h = Math.imul(seed ^ i, 0x9e3779b1);
                h ^= h >>> 16;
                h = Math.imul(h, 0x85ebca6b);
                h ^= h >>> 13;
                h = Math.imul(h, 0xc2b2ae35);
                h ^= h >>> 16;
                return ((h >>> 0) / 4294967296) * 2 - 1;
            }

            try { delete navigator.webdriver; } catch (e) {}
            Object.defineProperty(navigator, 'webdriver', {
                get: __makeNative(function() { return false; }, 'get webdriver'),
                enumerable: false,
                configurable: true
            });

            // Ensure navigator.userAgentData exists and matches launch metadata.
            // Playwright UA overrides can strip Client Hints; CDP Emulation should
            // restore them, and this is the fail-closed JS fallback.
            (function() {
                var meta = %s;
                if (!meta || typeof meta !== 'object') return;
                var brands = Array.isArray(meta.brands) ? meta.brands.slice() : [];
                var fullVersionList = Array.isArray(meta.fullVersionList) ? meta.fullVersionList.slice() : brands.slice();
                var mobile = !!meta.mobile;
                var platform = String(meta.platform || 'Windows');
                function freezeBrandList(list) {
                    return Object.freeze(list.map(function(b) {
                        return Object.freeze({
                            brand: String(b.brand || ''),
                            version: String(b.version || '')
                        });
                    }));
                }
                var frozenBrands = freezeBrandList(brands);
                var frozenFull = freezeBrandList(fullVersionList);
                var highEntropy = {
                    architecture: String(meta.architecture || 'x86'),
                    bitness: String(meta.bitness || '64'),
                    model: String(meta.model || ''),
                    platform: platform,
                    platformVersion: String(meta.platformVersion || ''),
                    uaFullVersion: String(meta.fullVersion || meta.uaFullVersion || ''),
                    fullVersionList: frozenFull,
                    mobile: mobile,
                    wow64: !!meta.wow64
                };
                function makeUAData() {
                    var data = {
                        brands: frozenBrands,
                        mobile: mobile,
                        platform: platform,
                        getHighEntropyValues: __makeNative(function(hints) {
                            var out = {
                                brands: frozenBrands,
                                mobile: mobile,
                                platform: platform
                            };
                            var wanted = Array.isArray(hints) ? hints : [];
                            for (var i = 0; i < wanted.length; i++) {
                                var key = wanted[i];
                                if (Object.prototype.hasOwnProperty.call(highEntropy, key)) {
                                    out[key] = highEntropy[key];
                                }
                            }
                            return Promise.resolve(out);
                        }, 'getHighEntropyValues'),
                        toJSON: __makeNative(function() {
                            return { brands: frozenBrands, mobile: mobile, platform: platform };
                        }, 'toJSON')
                    };
                    try {
                        if (typeof NavigatorUAData !== 'undefined' && NavigatorUAData.prototype) {
                            Object.setPrototypeOf(data, NavigatorUAData.prototype);
                        }
                    } catch (e) {}
                    return data;
                }
                try {
                    var existing = navigator.userAgentData;
                    if (!existing || !existing.brands || !existing.brands.length) {
                        Object.defineProperty(navigator, 'userAgentData', {
                            get: __makeNative(function() { return makeUAData(); }, 'get userAgentData'),
                            configurable: true,
                            enumerable: true
                        });
                    }
                } catch (e) {
                    try {
                        Object.defineProperty(navigator, 'userAgentData', {
                            get: __makeNative(function() { return makeUAData(); }, 'get userAgentData'),
                            configurable: true,
                            enumerable: true
                        });
                    } catch (e2) {}
                }
            })();

            // ponytail: realistic Chromium loadTimes/csi values, wrapped or created as needed.
            function __chromeLoadTimes() {
                var t = performance.now() / 1000;
                return {
                    commitLoadTime: t,
                    connectionInfo: 'h2',
                    finishLoadTime: t + 0.05,
                    firstPaintTime: t + 0.01,
                    navigationType: 'Other',
                    npnNegotiatedProtocol: 'h2',
                    protocol: 'h2',
                    startLoadTime: t - 0.1,
                    wasAlternateProtocolAvailable: false,
                    wasFetchedViaSpdy: true,
                    wasNpnNegotiated: true
                };
            }
            function __chromeCsi() {
                var now = Date.now();
                return {
                    onloadT: now,
                    pageT: performance.now(),
                    startE: now,
                    navigationType: 'navigate',
                    pageStartTime: now
                };
            }

            if (typeof window.chrome === 'undefined' || !window.chrome) {
                window.chrome = {
                    loadTimes: __makeNative(__chromeLoadTimes, 'loadTimes'),
                    csi: __makeNative(__chromeCsi, 'csi'),
                    app: {
                        isInstalled: false,
                        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
                        getDetails: __makeNative(function() { return null; }, 'getDetails'),
                        getIsInstalled: __makeNative(function() { return false; }, 'getIsInstalled'),
                        runningState: __makeNative(function() { return 'cannot_run'; }, 'runningState')
                    },
                    runtime: {
                        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
                        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                        PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
                        RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
                        getManifest: __makeNative(function() { return {}; }, 'getManifest'),
                        getURL: __makeNative(function(path) { return 'chrome-extension://' + (path || ''); }, 'getURL'),
                        sendMessage: __makeNative(function() { throw new Error('Access to extension API denied'); }, 'sendMessage'),
                        onMessage: { addListener: __makeNative(function() {}, 'addListener'), removeListener: __makeNative(function() {}, 'removeListener') }
                    }
                };
            } else {
                try { window.chrome.loadTimes = __makeNative(__chromeLoadTimes, 'loadTimes'); } catch (e) {}
                try { window.chrome.csi = __makeNative(__chromeCsi, 'csi'); } catch (e) {}
            }

            // ponytail: per-profile permission query responses for realistic diversity.
            (function() {
                var origQuery = navigator.permissions && navigator.permissions.query;
                if (typeof origQuery !== 'function') return;
                function __permHash(seed, name) {
                    var h = seed >>> 0;
                    for (var i = 0; i < name.length; i++) {
                        h = Math.imul((h ^ name.charCodeAt(i)) >>> 0, 0x9e3779b1);
                        h ^= h >>> 16;
                    }
                    return h >>> 0;
                }
                var __permSeed = __profileSeed ^ 0x5a3c1f0d;
                var __permDefaults = { geolocation: 'prompt', camera: 'prompt', microphone: 'prompt', notifications: 'prompt', midi: 'prompt', 'clipboard-read': 'prompt', 'clipboard-write': 'prompt' };
                navigator.permissions.query = __makeNative(function(query) {
                    var self = this;
                    var name = (query && query.name) || '';
                    var defaultState = __permDefaults.hasOwnProperty(name) ? __permDefaults[name] : null;
                    var state = defaultState;
                    if (state === 'prompt') {
                        var h = __permHash(__permSeed, name);
                        var roll = (h %% 100);
                        if (roll < 5) state = 'denied';
                        else if (roll < 10) state = 'granted';
                        else state = 'prompt';
                    }
                    return new Promise(function(resolve) {
                        try {
                            Promise.resolve(origQuery.call(self, query)).then(function(result) {
                                if (state) resolve({ state: state, onchange: null });
                                else if (result && result.state === 'denied') resolve(result);
                                else resolve({ state: 'prompt', onchange: null });
                            }).catch(function() {
                                resolve({ state: state || 'prompt', onchange: null });
                            });
                        } catch (e) {
                            resolve({ state: state || 'prompt', onchange: null });
                        }
                    });
                }, 'query');
            })();

            // ponytail: keep notification permission in the unresolved default state.
            if (typeof Notification !== 'undefined') {
                Object.defineProperty(Notification, 'permission', {
                    get: __makeNative(function() { return 'default'; }, 'get permission'),
                    enumerable: true,
                    configurable: true
                });
                Notification.requestPermission = __makeNative(function(callback) {
                    if (typeof callback === 'function') {
                        try { callback('default'); } catch (e) {}
                    }
                    return Promise.resolve('default');
                }, 'requestPermission');
            }

            // ponytail: stub RTCPeerConnection at page level to stop JS-driven IP discovery.
            (function() {
                var NativeRTCPeerConnection = window.RTCPeerConnection;
                if (typeof NativeRTCPeerConnection === 'undefined') return;
                function RTCPeerConnectionStub(configuration) {
                    if (!(this instanceof RTCPeerConnectionStub)) {
                        throw new TypeError("Failed to construct 'RTCPeerConnection': Please use the 'new' operator, this DOM object constructor cannot be called as a function.");
                    }
                    var listeners = {};
                    this.localDescription = null;
                    this.currentLocalDescription = null;
                    this.pendingLocalDescription = null;
                    this.iceGatheringState = 'complete';
                    this.iceConnectionState = 'closed';
                    this.signalingState = 'stable';
                    this.connectionState = 'closed';
                    this.createDataChannel = __makeNative(function(label) { return { label: label || '', readyState: 'connecting' }; }, 'createDataChannel');
                    this.createOffer = __makeNative(function() { return Promise.resolve({ type: 'offer', sdp: '' }); }, 'createOffer');
                    this.createAnswer = __makeNative(function() { return Promise.resolve({ type: 'answer', sdp: '' }); }, 'createAnswer');
                    this.setLocalDescription = __makeNative(function(desc) { this.localDescription = desc || { type: 'offer', sdp: '' }; return Promise.resolve(); }, 'setLocalDescription');
                    this.setRemoteDescription = __makeNative(function() { return Promise.resolve(); }, 'setRemoteDescription');
                    this.addIceCandidate = __makeNative(function() { return Promise.resolve(true); }, 'addIceCandidate');
                    this.getStats = __makeNative(function() { return Promise.resolve(new Map()); }, 'getStats');
                    this.getTransceivers = __makeNative(function() { return []; }, 'getTransceivers');
                    this.getSenders = __makeNative(function() { return []; }, 'getSenders');
                    this.getReceivers = __makeNative(function() { return []; }, 'getReceivers');
                    this.close = __makeNative(function() {}, 'close');
                    this.addEventListener = __makeNative(function(type, listener) {
                        (listeners[type] = listeners[type] || []).push(listener);
                    }, 'addEventListener');
                    this.removeEventListener = __makeNative(function(type, listener) {
                        var arr = listeners[type] || [];
                        var idx = arr.indexOf(listener);
                        if (idx !== -1) arr.splice(idx, 1);
                    }, 'removeEventListener');
                    this.dispatchEvent = __makeNative(function() { return true; }, 'dispatchEvent');
                }
                RTCPeerConnectionStub.prototype = Object.create(NativeRTCPeerConnection.prototype);
                RTCPeerConnectionStub.prototype.constructor = RTCPeerConnectionStub;
                window.RTCPeerConnection = __makeNative(RTCPeerConnectionStub, 'RTCPeerConnection');
            })();

            // ponytail: enforce advanced-dict hardwareConcurrency / deviceMemory when provided.
            if (%s) {
                try {
                    Object.defineProperty(navigator, 'hardwareConcurrency', {
                        get: __makeNative(function() { return %s; }, 'get hardwareConcurrency'),
                        enumerable: true,
                        configurable: true
                    });
                } catch (e) {}
                try {
                    Object.defineProperty(navigator, 'deviceMemory', {
                        get: __makeNative(function() { return %s; }, 'get deviceMemory'),
                        enumerable: true,
                        configurable: true
                    });
                } catch (e) {}
            }

            // ponytail: keep outer dimensions coherent with viewport plus chrome UI.
            var __outerWidth = %s;
            var __outerHeight = %s;
            try {
                Object.defineProperty(window, 'outerWidth', {
                    get: __makeNative(function() { return __outerWidth; }, 'get outerWidth'),
                    enumerable: true,
                    configurable: true
                });
                Object.defineProperty(window, 'outerHeight', {
                    get: __makeNative(function() { return __outerHeight; }, 'get outerHeight'),
                    enumerable: true,
                    configurable: true
                });
            } catch (e) {}

            // ponytail: pin WebGL debug renderer info and plausible parameter surface to the advanced vendor/renderer pair.
            var __spoofedWebGLVendor = %s;
            var __spoofedWebGLRenderer = %s;
            var __spoofedWebGLOS = %s;
            var __isMobileProfile = %s;
            var __profileMaxTouchPoints = %s;
            (function() {
                if (typeof WebGLRenderingContext === 'undefined') return;
                var GL = WebGLRenderingContext;
                var GL2 = (typeof WebGL2RenderingContext !== 'undefined') ? WebGL2RenderingContext : null;

                var __rendererLower = (__spoofedWebGLRenderer || '').toLowerCase();
                var __isMobileGPU = __rendererLower.indexOf('apple') !== -1 || __rendererLower.indexOf('mali') !== -1 || __rendererLower.indexOf('adreno') !== -1;
                var __isIntel = __rendererLower.indexOf('intel') !== -1;
                var __isLowTier = __isIntel || __isMobileGPU;
                var __texSize = __isLowTier ? 8192 : 16384;
                var __viewportArr = new Float32Array([__texSize, __texSize]);
                var __limits = {};
                __limits[GL.MAX_TEXTURE_SIZE] = __texSize;
                __limits[GL.MAX_VIEWPORT_DIMS] = __viewportArr;
                __limits[GL.MAX_VERTEX_ATTRIBS] = 16;
                __limits[GL.MAX_VERTEX_UNIFORM_VECTORS] = __isLowTier ? 2048 : 4096;
                __limits[GL.MAX_FRAGMENT_UNIFORM_VECTORS] = __isLowTier ? 1024 : 2048;

                var __webglVersion = __spoofedWebGLOS === 'Mac' ? 'WebGL 1.0 (OpenGL)' : (__spoofedWebGLOS === 'Windows' ? 'WebGL 1.0 (OpenGL ES 2.0 Chromium)' : 'WebGL 1.0');
                var __webglShading = __spoofedWebGLOS === 'Mac' ? 'WebGL GLSL ES 1.0 (OpenGL)' : 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
                var __webgl2Version = __spoofedWebGLOS === 'Mac' ? 'WebGL 2.0 (OpenGL)' : (__spoofedWebGLOS === 'Windows' ? 'WebGL 2.0 (OpenGL ES 3.0 Chromium)' : 'WebGL 2.0');
                var __webgl2Shading = __spoofedWebGLOS === 'Mac' ? 'WebGL GLSL ES 3.00 (OpenGL)' : 'WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)';

                var __commonExtensions = [
                    'WEBGL_debug_renderer_info', 'WEBGL_lose_context',
                    'EXT_texture_filter_anisotropic', 'OES_texture_float_linear',
                    'OES_element_index_uint', 'OES_standard_derivatives',
                    'WEBGL_depth_texture', 'WEBGL_draw_buffers',
                    'ANGLE_instanced_arrays', 'OES_vertex_array_object',
                    'WEBGL_compressed_texture_s3tc'
                ];

                function __isWebGL2Context(ctx) {
                    return GL2 && ctx instanceof GL2;
                }

                function __overrideGetParameter(proto) {
                    var orig = proto.getParameter;
                    proto.getParameter = __makeNative(function(parameter) {
                        if (parameter === 37445) return __spoofedWebGLVendor;
                        if (parameter === 37446) return __spoofedWebGLRenderer;
                        if (parameter === GL.VERSION) return __isWebGL2Context(this) ? __webgl2Version : __webglVersion;
                        if (parameter === GL.SHADING_LANGUAGE_VERSION) return __isWebGL2Context(this) ? __webgl2Shading : __webglShading;
                        if (__limits.hasOwnProperty(parameter)) return __limits[parameter];
                        return orig.apply(this, arguments);
                    }, 'getParameter');
                }

                function __overrideGetSupportedExtensions(proto) {
                    var orig = proto.getSupportedExtensions;
                    proto.getSupportedExtensions = __makeNative(function() {
                        var native = orig.apply(this, arguments) || [];
                        var out = [];
                        for (var i = 0; i < __commonExtensions.length; i++) {
                            if (native.indexOf(__commonExtensions[i]) !== -1) out.push(__commonExtensions[i]);
                        }
                        return out.length ? out : __commonExtensions.slice();
                    }, 'getSupportedExtensions');
                }

                function __overrideGetExtension(proto) {
                    var orig = proto.getExtension;
                    proto.getExtension = __makeNative(function(name) {
                        var ext = orig.apply(this, arguments);
                        if (name === 'WEBGL_debug_renderer_info' && ext) {
                            return new Proxy(ext, { get: function(t, p) {
                                if (p === 'UNMASKED_VENDOR_WEBGL') return 37445;
                                if (p === 'UNMASKED_RENDERER_WEBGL') return 37446;
                                return t[p];
                            }});
                        }
                        return ext;
                    }, 'getExtension');
                }

                function __overrideGetShaderPrecisionFormat(proto) {
                    var orig = proto.getShaderPrecisionFormat;
                    var __precisionProfile = {};
                    __precisionProfile[GL.LOW_FLOAT] = { rangeMin: 127, rangeMax: 127, precision: 1 };
                    __precisionProfile[GL.MEDIUM_FLOAT] = { rangeMin: 127, rangeMax: 127, precision: 10 };
                    __precisionProfile[GL.HIGH_FLOAT] = { rangeMin: 127, rangeMax: 127, precision: 23 };
                    __precisionProfile[GL.LOW_INT] = { rangeMin: 31, rangeMax: 31, precision: 0 };
                    __precisionProfile[GL.MEDIUM_INT] = { rangeMin: 31, rangeMax: 31, precision: 0 };
                    __precisionProfile[GL.HIGH_INT] = { rangeMin: 31, rangeMax: 31, precision: 0 };
                    proto.getShaderPrecisionFormat = __makeNative(function(shaderType, precisionType) {
                        if (__precisionProfile[precisionType]) return __precisionProfile[precisionType];
                        return orig.apply(this, arguments);
                    }, 'getShaderPrecisionFormat');
                }

                try { __overrideGetParameter(WebGLRenderingContext.prototype); } catch (e) {}
                try { __overrideGetSupportedExtensions(WebGLRenderingContext.prototype); } catch (e) {}
                try { __overrideGetExtension(WebGLRenderingContext.prototype); } catch (e) {}
                try { __overrideGetShaderPrecisionFormat(WebGLRenderingContext.prototype); } catch (e) {}
                if (GL2) {
                    try { __overrideGetParameter(GL2.prototype); } catch (e) {}
                    try { __overrideGetSupportedExtensions(GL2.prototype); } catch (e) {}
                    try { __overrideGetExtension(GL2.prototype); } catch (e) {}
                    try { __overrideGetShaderPrecisionFormat(GL2.prototype); } catch (e) {}
                }
            })();

            // ponytail: surface mobile touch / pointer expectations for mobile profiles.
            if (__isMobileProfile) {
                try {
                    Object.defineProperty(navigator, 'maxTouchPoints', {
                        get: __makeNative(function() { return __profileMaxTouchPoints; }, 'get maxTouchPoints'),
                        enumerable: true,
                        configurable: true
                    });
                } catch (e) {}
                (function() {
                    var __origMatchMedia = window.matchMedia;
                    window.matchMedia = __makeNative(function(query) {
                        var mql = __origMatchMedia.call(window, query);
                        if (typeof query === 'string' && query.replace(/\\s/g, '').indexOf('(pointer:coarse)') !== -1) {
                            return new Proxy(mql, { get: function(target, prop) { if (prop === 'matches') return true; return target[prop]; } });
                        }
                        return mql;
                    }, 'matchMedia');
                })();
                if (typeof window.ontouchstart === 'undefined') {
                    window.ontouchstart = __makeNative(function() {}, 'ontouchstart');
                }
                if (typeof window.TouchEvent === 'undefined') {
                    window.TouchEvent = __makeNative(function TouchEvent() {}, 'TouchEvent');
                }
            }

            // ponytail: coarse timing APIs to prevent high-resolution timing fingerprints.
            (function() {
                var __origPerformanceNow = performance.now;
                var __origDateNow = Date.now;
                performance.now = __makeNative(function() {
                    return Math.floor(__origPerformanceNow.call(performance) * 100) / 100;
                }, 'now');
                Date.now = __makeNative(function() {
                    return Math.floor(__origDateNow.call(Date) * 100) / 100;
                }, 'now');
            })();

            // ponytail: WebGPU adapter spoofing that mirrors the WebGL vendor/renderer pair.
            (function() {
                if (typeof navigator === 'undefined' || !navigator.gpu || typeof navigator.gpu.requestAdapter !== 'function') return;
                function __inferGPUArchitecture(renderer) {
                    var r = (renderer || '').toLowerCase();
                    if (r.indexOf('nvidia') !== -1 || r.indexOf('geforce') !== -1 || r.indexOf('rtx') !== -1) return 'ampere';
                    if (r.indexOf('intel') !== -1) return 'gen12';
                    if (r.indexOf('apple') !== -1 || r.indexOf('m2') !== -1 || r.indexOf('m1') !== -1 || r.indexOf('metal') !== -1) return 'apple-gx';
                    if (r.indexOf('mali') !== -1) return 'mali-g';
                    if (r.indexOf('adreno') !== -1) return 'adreno';
                    return 'generic';
                }
                var __gpuAdapterInfo = {
                    vendor: __spoofedWebGLVendor,
                    architecture: __inferGPUArchitecture(__spoofedWebGLRenderer),
                    device: __spoofedWebGLRenderer,
                    description: __spoofedWebGLRenderer
                };
                function __makeFakeAdapter() {
                    return {
                        features: [],
                        limits: {},
                        info: __gpuAdapterInfo,
                        requestAdapterInfo: __makeNative(function() { return Promise.resolve(__gpuAdapterInfo); }, 'requestAdapterInfo'),
                        requestDevice: __makeNative(function() { return Promise.reject(new Error('WebGPU device request denied')); }, 'requestDevice')
                    };
                }
                navigator.gpu.requestAdapter = __makeNative(function() {
                    return Promise.resolve(__makeFakeAdapter());
                }, 'requestAdapter');
            })();

            // ponytail: Battery API returns a static, fully-charged manager.
            (function() {
                if (typeof navigator === 'undefined') return;
                try { delete navigator.getBattery; } catch (e) {}
                var __batteryListeners = { levelchange: [], chargingchange: [], chargingtimechange: [], dischargingtimechange: [] };
                var __batteryManager = {
                    charging: true,
                    chargingTime: 0,
                    dischargingTime: Infinity,
                    level: 1.0,
                    addEventListener: __makeNative(function(type, listener) {
                        if (__batteryListeners[type]) __batteryListeners[type].push(listener);
                    }, 'addEventListener'),
                    removeEventListener: __makeNative(function(type, listener) {
                        var arr = __batteryListeners[type] || [];
                        var idx = arr.indexOf(listener);
                        if (idx !== -1) arr.splice(idx, 1);
                    }, 'removeEventListener'),
                    dispatchEvent: __makeNative(function() { return true; }, 'dispatchEvent'),
                    onchargingchange: null,
                    onchargingtimechange: null,
                    ondischargingtimechange: null,
                    onlevelchange: null
                };
                try {
                    Object.defineProperty(navigator, 'getBattery', {
                        value: __makeNative(function() { return Promise.resolve(__batteryManager); }, 'getBattery'),
                        writable: true,
                        enumerable: true,
                        configurable: true
                    });
                } catch (e) {}
            })();

            // ponytail: expose plausible sensor constructors on mobile; hide them on desktop cohorts.
            (function() {
                if (!__isMobileProfile) {
                    ['DeviceMotionEvent', 'DeviceOrientationEvent', 'AbsoluteOrientationEvent', 'AmbientLightEvent', 'ProximityEvent'].forEach(function(name) {
                        try { window[name] = undefined; } catch (e) {}
                    });
                    return;
                }
                function __makeSensorConstructor(name, init) {
                    function SensorEvent(type, eventInit) {
                        Event.call(this, type, eventInit || {});
                        init.call(this, eventInit || {});
                    }
                    SensorEvent.prototype = Object.create(Event.prototype);
                    SensorEvent.prototype.constructor = SensorEvent;
                    return __makeNative(SensorEvent, name);
                }
                function __stableAngle(seed, offset) {
                    return ((Math.imul((seed + offset) >>> 0, 0x9e3779b1) >>> 0) %% 3600) / 10;
                }
                function __stableVal(seed, offset, min, max) {
                    var h = (Math.imul((seed ^ offset) >>> 0, 0x85ebca6b) >>> 0) %% 10000;
                    return min + (h / 10000) * (max - min);
                }
                window.DeviceMotionEvent = __makeSensorConstructor('DeviceMotionEvent', function(init) {
                    this.acceleration = { x: __stableVal(__profileSeed, 1, -0.5, 0.5), y: __stableVal(__profileSeed, 2, -0.5, 0.5), z: __stableVal(__profileSeed, 3, -0.5, 0.5) };
                    this.accelerationIncludingGravity = { x: __stableVal(__profileSeed, 4, -1, 1), y: __stableVal(__profileSeed, 5, -1, 1), z: __stableVal(__profileSeed, 6, 9, 10) };
                    this.rotationRate = { alpha: __stableAngle(__profileSeed, 7), beta: __stableAngle(__profileSeed, 8), gamma: __stableAngle(__profileSeed, 9) };
                    this.interval = 16;
                });
                window.DeviceOrientationEvent = __makeSensorConstructor('DeviceOrientationEvent', function(init) {
                    this.absolute = false;
                    this.alpha = __stableAngle(__profileSeed, 10);
                    this.beta = __stableAngle(__profileSeed, 11) - 180;
                    this.gamma = __stableAngle(__profileSeed, 12) - 180;
                });
                window.AbsoluteOrientationEvent = __makeSensorConstructor('AbsoluteOrientationEvent', function(init) {
                    this.quaternion = [
                        __stableVal(__profileSeed, 13, -1, 1),
                        __stableVal(__profileSeed, 14, -1, 1),
                        __stableVal(__profileSeed, 15, -1, 1),
                        __stableVal(__profileSeed, 16, 0, 1)
                    ];
                });
                window.AmbientLightEvent = __makeSensorConstructor('AmbientLightEvent', function(init) {
                    this.value = __stableVal(__profileSeed, 17, 50, 500);
                });
                window.ProximityEvent = __makeSensorConstructor('ProximityEvent', function(init) {
                    this.value = __stableVal(__profileSeed, 18, 0, 10);
                    this.max = 10;
                });
            })();

            // ponytail: enumerateDevices returns a stable, permission-agnostic device list.
            (function() {
                if (typeof navigator === 'undefined') return;
                function __hashDevice(seed, kind, index) {
                    var h = Math.imul(((seed ^ (kind.charCodeAt(0) + index)) >>> 0), 0x45d9f3b);
                    h ^= h >>> 16;
                    return 'fake-' + (h >>> 0).toString(16).padStart(8, '0') + '-' + kind + '-' + index;
                }
                var __mediaDevicesKinds = ['audioinput', 'audiooutput', 'videoinput'];
                if (__isMobileProfile) __mediaDevicesKinds.push('videoinput');
                var __fakeDeviceList = [];
                for (var i = 0; i < __mediaDevicesKinds.length; i++) {
                    var k = __mediaDevicesKinds[i];
                    var did = __hashDevice(__profileSeed, k, i);
                    var gid = __hashDevice(__profileSeed, k, i + 100);
                    __fakeDeviceList.push({ deviceId: did, kind: k, label: '', groupId: gid });
                }
                if (__isMobileProfile) {
                    var faceId = __hashDevice(__profileSeed, 'videoinput', 999);
                    __fakeDeviceList.push({ deviceId: faceId, kind: 'videoinput', label: '', groupId: __hashDevice(__profileSeed, 'videoinput', 1099) });
                }
                var __fakeEnumerateDevices = __makeNative(function() {
                    var list = [];
                    for (var i = 0; i < __fakeDeviceList.length; i++) list.push(Object.assign({}, __fakeDeviceList[i]));
                    return Promise.resolve(list);
                }, 'enumerateDevices');

                if (!navigator.mediaDevices) {
                    navigator.mediaDevices = {
                        enumerateDevices: __fakeEnumerateDevices,
                        getUserMedia: __makeNative(function() {
                            return Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));
                        }, 'getUserMedia'),
                        addEventListener: __makeNative(function() {}, 'addEventListener'),
                        removeEventListener: __makeNative(function() {}, 'removeEventListener'),
                        dispatchEvent: __makeNative(function() { return true; }, 'dispatchEvent')
                    };
                } else if (typeof navigator.mediaDevices.enumerateDevices === 'function') {
                    navigator.mediaDevices.enumerateDevices = __fakeEnumerateDevices;
                }
            })();

            // ponytail: deterministic AudioContext runtime noise patches.
            (function() {
                if (typeof AudioBuffer === 'undefined') return;

                var origCopyFromChannel = AudioBuffer.prototype.copyFromChannel;
                AudioBuffer.prototype.copyFromChannel = __makeNative(function(destination, channelNumber, startInChannel) {
                    var res = origCopyFromChannel.apply(this, arguments);
                    var channel = channelNumber | 0;
                    var start = startInChannel === undefined ? 0 : (startInChannel | 0);
                    for (var i = 0; i < destination.length; i++) {
                        var noise = _audioFloat(__profileSeed ^ channel, start + i) * 1e-7;
                        var val = destination[i] + noise;
                        destination[i] = Math.fround(val > 1 ? 1 : (val < -1 ? -1 : val));
                    }
                    return res;
                }, 'copyFromChannel');

                var origABGetChannelData = AudioBuffer.prototype.getChannelData;
                AudioBuffer.prototype.getChannelData = __makeNative(function(channel) {
                    var data = origABGetChannelData.call(this, channel);
                    var copy = new Float32Array(data);
                    var ch = channel | 0;
                    for (var i = 0; i < copy.length; i++) {
                        var noise = _audioFloat(__profileSeed ^ ch, i) * 1e-7;
                        var val = copy[i] + noise;
                        copy[i] = Math.fround(val > 1 ? 1 : (val < -1 ? -1 : val));
                    }
                    return copy;
                }, 'getChannelData');
            })();

            (function() {
                if (typeof AnalyserNode === 'undefined') return;

                var origGetFloatFrequencyData = AnalyserNode.prototype.getFloatFrequencyData;
                AnalyserNode.prototype.getFloatFrequencyData = __makeNative(function(array) {
                    var res = origGetFloatFrequencyData.apply(this, arguments);
                    for (var i = 0; i < array.length; i++) {
                        var noise = _audioFloat(__profileSeed ^ 0x12345678, i) * 1e-5;
                        var val = array[i] + noise;
                        array[i] = (val === -Infinity || val < -Infinity) ? -Infinity : Math.fround(val);
                    }
                    return res;
                }, 'getFloatFrequencyData');

                var origGetByteFrequencyData = AnalyserNode.prototype.getByteFrequencyData;
                AnalyserNode.prototype.getByteFrequencyData = __makeNative(function(array) {
                    var res = origGetByteFrequencyData.apply(this, arguments);
                    for (var i = 0; i < array.length; i++) {
                        var noise = _audioFloat(__profileSeed ^ 0x23456789, i) * 2;
                        var val = Math.round(array[i] + noise);
                        array[i] = val > 255 ? 255 : (val < 0 ? 0 : val);
                    }
                    return res;
                }, 'getByteFrequencyData');

                var origGetByteTimeDomainData = AnalyserNode.prototype.getByteTimeDomainData;
                AnalyserNode.prototype.getByteTimeDomainData = __makeNative(function(array) {
                    var res = origGetByteTimeDomainData.apply(this, arguments);
                    for (var i = 0; i < array.length; i++) {
                        var noise = _audioFloat(__profileSeed ^ 0x3456789a, i) * 2;
                        var val = Math.round(array[i] + noise);
                        array[i] = val > 255 ? 255 : (val < 0 ? 0 : val);
                    }
                    return res;
                }, 'getByteTimeDomainData');
            })();

            (function() {
                if (typeof OscillatorNode === 'undefined') return;

                var origOscStart = OscillatorNode.prototype.start;
                var __oscStartCache = new WeakMap();
                OscillatorNode.prototype.start = __makeNative(function(when) {
                    try { __oscStartCache.set(this, when === undefined ? -1 : when); } catch (e) {}
                    return origOscStart.apply(this, arguments);
                }, 'start');
            })();

            var __fakePlugins = %s;
            var __fakeMimeTypes = %s;
            __fakeMimeTypes.forEach(function(m, i) { m.enabledPlugin = __fakePlugins[i] || null; });

            function makeFakePluginArray(plugins) {
                var arr = { length: plugins.length };
                for (var i = 0; i < plugins.length; i++) {
                    var p = plugins[i];
                    arr[i] = p;
                    if (p.name) arr[p.name] = p;
                    if (p.filename) arr[p.filename] = p;
                }
                arr.item = __makeNative(function(index) { return arr[index] || null; }, 'item');
                arr.namedItem = __makeNative(function(name) {
                    for (var j = 0; j < plugins.length; j++) {
                        if (plugins[j].name === name || plugins[j].filename === name) return plugins[j];
                    }
                    return null;
                }, 'namedItem');
                arr.refresh = __makeNative(function() {}, 'refresh');
                return arr;
            }

            function makeFakeMimeTypeArray(mimes) {
                var arr = { length: mimes.length };
                for (var i = 0; i < mimes.length; i++) {
                    var m = mimes[i];
                    arr[i] = m;
                    if (m.type) arr[m.type] = m;
                }
                arr.item = __makeNative(function(index) { return arr[index] || null; }, 'item');
                arr.namedItem = __makeNative(function(name) {
                    for (var j = 0; j < mimes.length; j++) {
                        if (mimes[j].type === name) return mimes[j];
                    }
                    return null;
                }, 'namedItem');
                return arr;
            }

            var needPlugins = !navigator.plugins || navigator.plugins.length === 0 || typeof navigator.plugins.refresh !== 'function';
            if (needPlugins) {
                Object.defineProperty(navigator, 'plugins', {
                    get: __makeNative(function() { return makeFakePluginArray(__fakePlugins); }, 'get plugins'),
                    enumerable: true,
                    configurable: true
                });
            }

            var needMimeTypes = !navigator.mimeTypes || navigator.mimeTypes.length === 0;
            if (needMimeTypes) {
                Object.defineProperty(navigator, 'mimeTypes', {
                    get: __makeNative(function() { return makeFakeMimeTypeArray(__fakeMimeTypes); }, 'get mimeTypes'),
                    enumerable: true,
                    configurable: true
                });
            }

            ['__webdriver_script_fn', 'callPhantom', '_phantom', 'awesomium', 'domAutomation', 'domAutomationController', 'cdc_adoQpoasnfa76pfcZLmcfl_', 'botguard'].forEach(function(key) {
                try { delete window[key]; } catch (e) { try { window[key] = undefined; } catch (e2) {} }
            });
        """ % (
            _ad_seed,
            json.dumps(config.get("userAgentMetadata") or {}),
            str(_ad_has_device_values).lower(),
            _ad_cpu,
            _ad_mem,
            _outer_w,
            _outer_h,
            json.dumps(_ad_webgl_vendor),
            json.dumps(_ad_webgl_renderer),
            json.dumps(_ad_os),
            str(_ad_is_mobile).lower(),
            _ad_max_touch_points,
            _ad_plugins_json,
            _ad_mimes_json,
        )

        anti_detect_script += """
            var __profileColorScheme = %s;
            var __profileReducedMotion = %s;
            var __profileLocale = %s;
            var __profileIsLinux = %s;

            // ponytail: comprehensive CSS media-query spoofing tied to the profile cohort.
            (function() {
                var __origMatchMedia2 = window.matchMedia;
                function __normalizeMediaQuery(query) {
                    if (typeof query !== 'string') return '';
                    return query.toLowerCase().replace(/\\s+/g, '').replace(/"/g, "'");
                }
                var __featureAnswerMap = {
                    'prefers-color-scheme': __profileColorScheme,
                    'prefers-reduced-motion': __profileReducedMotion,
                    'prefers-contrast': 'no-preference',
                    'pointer': __isMobileProfile ? 'coarse' : 'fine',
                    'any-pointer': __isMobileProfile ? 'coarse' : 'fine',
                    'hover': __isMobileProfile ? 'none' : 'hover',
                    'any-hover': __isMobileProfile ? 'none' : 'hover',
                    'color-gamut': 'srgb',
                    'forced-colors': 'none'
                };
                function __answerForQuery(query) {
                    var q = __normalizeMediaQuery(query);
                    if (!q) return null;
                    if (q.indexOf('not') !== -1 || q.indexOf('only') !== -1 || q.indexOf(',') !== -1) return null;
                    var __mediaTypes = ['all', 'screen', 'print', 'speech', 'projection'];
                    for (var i = 0; i < __mediaTypes.length; i++) {
                        var mt = __mediaTypes[i] + 'and';
                        if (q.indexOf(mt) === 0) {
                            q = q.slice(mt.length);
                            break;
                        }
                    }
                    if (q[0] === '(' && q[q.length - 1] === ')') {
                        var inner = q.slice(1, -1);
                        var colonIdx = inner.indexOf(':');
                        if (colonIdx === -1) {
                            if (__featureAnswerMap.hasOwnProperty(inner)) return true;
                            return null;
                        }
                        var name = inner.slice(0, colonIdx);
                        var value = inner.slice(colonIdx + 1).replace(/"/g, '').replace(/'/g, '');
                        if (!__featureAnswerMap.hasOwnProperty(name)) return null;
                        return String(value) === String(__featureAnswerMap[name]);
                    }
                    var parts = q.split(')and(');
                    if (parts.length > 1) {
                        parts[0] = parts[0] + ')';
                        for (var k = 1; k < parts.length - 1; k++) parts[k] = '(' + parts[k] + ')';
                        parts[parts.length - 1] = '(' + parts[parts.length - 1];
                        var allTrue = true;
                        for (var j = 0; j < parts.length; j++) {
                            var partAns = __answerForQuery(parts[j]);
                            if (partAns === null) return null;
                            allTrue = allTrue && partAns;
                        }
                        return allTrue;
                    }
                    return null;
                }
                function __makeFakeMQL(query, matches) {
                    var listeners = [];
                    var mql = {
                        media: query || '',
                        matches: !!matches,
                        addListener: __makeNative(function(cb) { if (typeof cb === 'function') listeners.push(cb); }, 'addListener'),
                        removeListener: __makeNative(function(cb) { var idx = listeners.indexOf(cb); if (idx !== -1) listeners.splice(idx, 1); }, 'removeListener'),
                        addEventListener: __makeNative(function(type, cb) { if (type === 'change' && typeof cb === 'function') listeners.push(cb); }, 'addEventListener'),
                        removeEventListener: __makeNative(function(type, cb) { if (type !== 'change') return; var idx = listeners.indexOf(cb); if (idx !== -1) listeners.splice(idx, 1); }, 'removeEventListener'),
                        dispatchEvent: __makeNative(function() { return true; }, 'dispatchEvent'),
                        onchange: null
                    };
                    return mql;
                }
                window.matchMedia = __makeNative(function(query) {
                    var answer = __answerForQuery(query);
                    if (answer !== null) {
                        return __makeFakeMQL(query, answer);
                    }
                    return __origMatchMedia2.call(window, query);
                }, 'matchMedia');
            })();

            // ponytail: stable OS/locale-consistent speech synthesis voice list.
            (function() {
                if (typeof window.speechSynthesis === 'undefined') return;
                var __locale = __profileLocale || 'en-US';
                var __baseLang = (__locale.split('-')[0] || 'en').toLowerCase();
                var __os = __spoofedWebGLOS || 'Windows';
                var __voiceList = [];
                if (__os === 'Mac') {
                    __voiceList = [
                        { voiceURI: 'com.apple.speech.synthesis.voice.Alex', name: 'Alex', lang: 'en-US', localService: true, default: false },
                        { voiceURI: 'com.apple.speech.synthesis.voice.Samantha', name: 'Samantha', lang: 'en-US', localService: true, default: false },
                        { voiceURI: 'com.apple.speech.synthesis.voice.Victoria', name: 'Victoria', lang: 'en-US', localService: true, default: false },
                        { voiceURI: 'com.apple.speech.synthesis.voice.Daniel', name: 'Daniel', lang: 'en-GB', localService: true, default: false },
                        { voiceURI: 'com.apple.speech.synthesis.voice.Karen', name: 'Karen', lang: 'en-AU', localService: true, default: false }
                    ];
                } else if (__os === 'Windows') {
                    __voiceList = [
                        { voiceURI: 'HKEY_LOCAL_MACHINE/SOFTWARE/Microsoft/Speech/Voices/Tokens/TTS_MS_EN-US_DAVID_11.0', name: 'Microsoft David Desktop', lang: 'en-US', localService: true, default: false },
                        { voiceURI: 'HKEY_LOCAL_MACHINE/SOFTWARE/Microsoft/Speech/Voices/Tokens/TTS_MS_EN-US_ZIRA_11.0', name: 'Microsoft Zira Desktop', lang: 'en-US', localService: true, default: false },
                        { voiceURI: 'HKEY_LOCAL_MACHINE/SOFTWARE/Microsoft/Speech/Voices/Tokens/TTS_MS_EN-GB_HAZEL_11.0', name: 'Microsoft Hazel Desktop', lang: 'en-GB', localService: true, default: false },
                        { voiceURI: 'HKEY_LOCAL_MACHINE/SOFTWARE/Microsoft/Speech/Voices/Tokens/TTS_MS_DE-DE_HEDDA_11.0', name: 'Microsoft Hedda Desktop', lang: 'de-DE', localService: true, default: false }
                    ];
                } else {
                    __voiceList = [
                        { voiceURI: 'org.gnu.speech.voice.Default', name: 'Default', lang: 'en-US', localService: true, default: false },
                        { voiceURI: 'org.gnu.speech.voice.Male', name: 'Male', lang: 'en-US', localService: true, default: false },
                        { voiceURI: 'org.gnu.speech.voice.Female', name: 'Female', lang: 'en-US', localService: true, default: false }
                    ];
                }
                var __defaultIdx = -1;
                if (__os === 'Mac' || __os === 'Windows' || __os === 'Linux') {
                    if (__baseLang === 'en') __defaultIdx = 0;
                }
                if (__defaultIdx !== -1) __voiceList[__defaultIdx].default = true;
                window.speechSynthesis.getVoices = __makeNative(function() { return __voiceList.slice(); }, 'getVoices');
                var __origSynthAddEventListener = window.speechSynthesis.addEventListener;
                if (typeof __origSynthAddEventListener === 'function') {
                    window.speechSynthesis.addEventListener = __makeNative(function(type, listener) {
                        if (type === 'voiceschanged' && typeof listener === 'function') {
                            try { listener.call(window.speechSynthesis); } catch (e) {}
                        }
                        return __origSynthAddEventListener.apply(this, arguments);
                    }, 'addEventListener');
                }
            })();

            // ponytail: always report positive, profile-consistent media decoding capabilities.
            (function() {
                if (typeof navigator === 'undefined' || !navigator.mediaCapabilities || typeof navigator.mediaCapabilities.decodingInfo !== 'function') return;
                navigator.mediaCapabilities.decodingInfo = __makeNative(function() {
                    return Promise.resolve({ supported: true, smooth: true, powerEfficient: !__profileIsLinux });
                }, 'decodingInfo');
            })();

            // ponytail: deterministic NetworkInformation surface chosen per device cohort.
            (function() {
                if (typeof navigator === 'undefined') return;
                function __connRandom(seed) {
                    var h = Math.imul(seed >>> 0, 0x45d9f3b);
                    h ^= h >>> 16;
                    return (h >>> 0) / 4294967296;
                }
                var __connSeed = __profileSeed ^ 0x9e3779b1;
                var __connListeners = [];
                var __connType = __isMobileProfile ? '4g' : 'wifi';
                var __effectiveType = __isMobileProfile ? '4g' : 'wifi';
                var __downlink = __isMobileProfile ? (5 + __connRandom(__connSeed) * 15) : (20 + __connRandom(__connSeed) * 80);
                __downlink = Math.round(__downlink * 10) / 10;
                var __rtt = __isMobileProfile ? Math.round(50 + __connRandom(__connSeed ^ 1) * 100) : Math.round(10 + __connRandom(__connSeed ^ 1) * 40);
                var __connection = {
                    effectiveType: __effectiveType,
                    downlink: __downlink,
                    downlinkMax: __isMobileProfile ? 300 : Infinity,
                    rtt: __rtt,
                    saveData: false,
                    type: __connType,
                    onchange: null,
                    addEventListener: __makeNative(function(type, listener) { if (type === 'change' && typeof listener === 'function') __connListeners.push(listener); }, 'addEventListener'),
                    removeEventListener: __makeNative(function(type, listener) { var idx = __connListeners.indexOf(listener); if (idx !== -1) __connListeners.splice(idx, 1); }, 'removeEventListener'),
                    dispatchEvent: __makeNative(function() { return true; }, 'dispatchEvent')
                };
                try {
                    Object.defineProperty(navigator, 'connection', {
                        get: __makeNative(function() { return __connection; }, 'get connection'),
                        enumerable: true,
                        configurable: true
                    });
                } catch (e) {}
            })();

            // ponytail: lie about availability of common system fonts; leave measureText untouched.
            (function() {
                if (typeof document === 'undefined' || !document.fonts || typeof document.fonts.check !== 'function') return;
                var __commonFontSet = {};
                var __commonFontNames = [
                    'arial', 'times new roman', 'courier new', 'georgia', 'verdana', 'tahoma',
                    'trebuchet ms', 'impact', 'comic sans ms', 'lucida grande', 'helvetica',
                    'microsoft sans serif', 'segoe ui', 'roboto', 'ubuntu', 'dejavu sans',
                    'liberation sans', 'palatino linotype', 'garamond', 'bookman old style',
                    'copperplate gothic', 'franklin gothic', 'arial black', 'calibri', 'cambria',
                    'candara', 'consolas', 'constantia', 'corbel', 'times', 'courier', 'system-ui',
                    '-apple-system', 'blinkmacsystemfont'
                ];
                for (var i = 0; i < __commonFontNames.length; i++) __commonFontSet[__commonFontNames[i]] = true;
                var __origFontCheck = document.fonts.check;
                document.fonts.check = __makeNative(function(font, text) {
                    var normalized = (font || '').toLowerCase().replace(/"/g, '').split(',')[0].trim();
                    if (__commonFontSet.hasOwnProperty(normalized)) return true;
                    return __origFontCheck.apply(this, arguments);
                }, 'check');
            })();

            // ponytail: align navigator.share/canShare with the desktop/mobile cohort.
            (function() {
                if (typeof navigator === 'undefined') return;
                navigator.canShare = __makeNative(function(data) {
                    if (!__isMobileProfile) return false;
                    if (!data || typeof data !== 'object') return false;
                    if (data.url && typeof data.url === 'string') return true;
                    if (data.text && typeof data.text === 'string') return true;
                    if (data.title && typeof data.title === 'string') return true;
                    return false;
                }, 'canShare');
                navigator.share = __makeNative(function(data) {
                    if (!__isMobileProfile) {
                        return Promise.reject(new DOMException('Share canceled', 'AbortError'));
                    }
                    if (!navigator.canShare(data)) {
                        return Promise.reject(new TypeError('Invalid share data'));
                    }
                    return Promise.resolve(undefined);
                }, 'share');
            })();
        """ % (
            json.dumps(_ad_color_scheme),
            json.dumps(_ad_reduced_motion),
            json.dumps(_ad_locale),
            str(_ad_os == 'Linux').lower(),
        )

        _ad_experimental_measuretext = _ad_advanced.get("experimental_measuretext", True)

        anti_detect_script += """
            // ponytail: deterministic per-profile CanvasRenderingContext2D.measureText spoof.
            (function() {
                var __experimentalMeasureText = %s;
                if (!__experimentalMeasureText) return;
                if (typeof CanvasRenderingContext2D === 'undefined' || typeof CanvasRenderingContext2D.prototype.measureText !== 'function') return;
                var __origMeasureText = CanvasRenderingContext2D.prototype.measureText;
                var __measureTextOS = __spoofedWebGLOS || 'Windows';
                var __measureTextDeltaTable = {
                    Windows: [0.12, 0.08, 0.16, 0.22, 0.18, 0.04, 0.14, 0.06, 0.10, 0.02, 0.05],
                    Mac: [0.11, 0.09, 0.15, 0.21, 0.17, 0.05, 0.13, 0.07, 0.09, 0.03, 0.06],
                    Linux: [0.13, 0.07, 0.14, 0.23, 0.19, 0.03, 0.15, 0.05, 0.11, 0.02, 0.05]
                };
                function __measureTextHash(s) {
                    var h = __profileSeed >>> 0;
                    for (var i = 0; i < s.length; i++) {
                        h = Math.imul((h ^ s.charCodeAt(i)) >>> 0, 0x85ebca6b);
                        h ^= h >>> 16;
                    }
                    return h >>> 0;
                }
                function __makeFakeTextMetrics(ctx, text, original) {
                    var hash = __measureTextHash(String(text));
                    var deltas = __measureTextDeltaTable[__measureTextOS] || __measureTextDeltaTable.Windows;
                    var baseWidth = original.width > 0 ? original.width : 0;
                    var fontSize = 10;
                    try {
                        var font = (ctx.font || '10px sans-serif').toLowerCase();
                        var m = font.match(/(\\d+(?:\\.\\d+)?)px/);
                        if (m) fontSize = parseFloat(m[1]);
                    } catch (e) {}
                    var ratio = fontSize / 10;
                    var widthOffset = ((hash & 0xffff) / 0x10000 - 0.5) * 0.04;
                    var metrics = {};
                    var names = ['actualBoundingBoxLeft','actualBoundingBoxRight','actualBoundingBoxAscent','actualBoundingBoxDescent','fontBoundingBoxAscent','fontBoundingBoxDescent','emHeightAscent','emHeightDescent','hangingBaseline','alphabeticBaseline','ideographicBaseline'];
                    for (var i = 0; i < names.length; i++) {
                        var n = names[i];
                        var originalVal = (original && typeof original[n] === 'number') ? original[n] : undefined;
                        var val;
                        if (typeof originalVal === 'number') {
                            val = originalVal;
                        } else {
                            var base = baseWidth * ((hash >>> ((i * 7) %% 32)) & 0xff) / 255;
                            if (n.indexOf('Ascent') !== -1 || n === 'hangingBaseline' || n === 'alphabeticBaseline') val = base * ratio;
                            else if (n.indexOf('Descent') !== -1 || n === 'ideographicBaseline') val = base * ratio * 0.35;
                            else val = base;
                        }
                        var deltaScale = (deltas[i] || 0.1) * ratio;
                        var perturb = deltaScale * (((hash >>> (i * 5)) & 0xff) / 128 - 1);
                        metrics[n] = val + perturb;
                    }
                    metrics.width = baseWidth + widthOffset;
                    return metrics;
                }
                CanvasRenderingContext2D.prototype.measureText = __makeNative(function measureText(text) {
                    var original = __origMeasureText.apply(this, arguments);
                    return __makeFakeTextMetrics(this, text, original);
                }, 'measureText');
            })();
        """ % (str(_ad_experimental_measuretext).lower(),)

        await page.context.add_init_script(anti_detect_script)

        # Existing pages were created before add_init_script; inject the anti-detect
        # script directly into them so scanners can use the already-opened page.
        for p in context.pages:
            try:
                await p.evaluate(anti_detect_script)
            except Exception:
                pass

        # Setup existing pages (called after stealth is applied to override any stealth deletions)
        setup_tasks = []
        for p in context.pages:
            t = asyncio.create_task(setup_page_cdp(p))
            register_cdp_task(profile_id, t, context, playwright)
            setup_tasks.append(t)

        if setup_tasks:
            await asyncio.gather(*setup_tasks)

        from backend.ai_anomaly_detector import anomaly_detector
        await anomaly_detector.attach(profile_id, page)

        await context.add_init_script(config["spoofing_script"])

        try:
            await page.goto("data:text/html,<html><body>Stealth Initialized</body></html>", wait_until="commit", timeout=15000)
        except Exception:
            pass

        procs = find_profile_processes(canonical_profile_path)
        pid = procs[0].pid if procs else None

        active_browsers[profile_id] = {
            "playwright": playwright,
            "context": context,
            "page": page,
            "pid": pid,
            "proxy": config.get("assigned_proxy"),
            "args": config.get("args", []),
        }

        def _on_context_close(ctx):
            bd = active_browsers.pop(profile_id, None)
            if bd:
                ht = bd.get("health_task")
                if ht and not ht.done():
                    ht.cancel()

        context.on("close", _on_context_close)

        if config.get("assigned_proxy"):
            active_browsers[profile_id]["health_task"] = asyncio.create_task(
                _proxy_health_loop(profile_id, config["assigned_proxy"])
            )

        from backend.logging_config import logger
        logger.info(f"Browser launched for profile {profile_id}", extra={"profile_id": profile_id})

        set_profile_state(profile_id, "running")

        res = {"status": "success", "message": "Browser launched successfully"}
        if config["proxy_warning"]:
            res["warning"] = config["proxy_warning"]
            logger.warning(config["proxy_warning"])

        return res
    except Exception as e:
        from backend.logging_config import logger
        logger.error(
            "Profile launch failed safely for %s (%s)",
            profile_id,
            type(e).__name__,
        )
        try:
            assigned_proxy_server = (
                (config.get("assigned_proxy") or {}).get("server")
                if 'config' in locals() and config and config.get("assigned_proxy")
                else None
            )
            if assigned_proxy_server:
                proxy_manager.report_proxy_failure(assigned_proxy_server)
                webhook_notifier.notify("proxy.failure", {
                    "profile_id": profile_id,
                    "proxy_server": assigned_proxy_server,
                    "stage": "launch",
                })
        except Exception:
            pass
        if 'context' in locals() and context:
            try:
                await context.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass
        active_browsers.pop(profile_id, None)
        set_profile_state(profile_id, "error")
        procs = find_profile_processes(canonical_profile_path)
        for proc in procs:
            try:
                kill_process_tree(proc)
            except Exception:
                pass
        if isinstance(e, RuntimeError) and str(e).startswith("FAIL-CLOSED:"):
            message = str(e)
        else:
            message = "Launch failed safely without starting an unprotected browser."
        return {"status": "error", "message": message}
    finally:
        # Cancellation can bypass the normal Exception handler.  Never leave a
        # profile permanently marked as launching after releasing its lock.
        if profile_states.get(profile_id) == "launching":
            set_profile_state(profile_id, "error")
        lock_manager.release(profile_id)

async def launch_profile(profile_id: str, force_headless: bool = False, pin: str = None):
    """Public wrapper that emits profile.launch webhooks after the attempt.

    This wrapper is hardened so it never raises an unhandled exception: any
    failure path returns a dict with ``status`` and ``message`` and any
    partially-started Chromium process is cleaned up.
    """
    try:
        result = await _do_launch_profile(profile_id, force_headless=force_headless, pin=pin)
    except Exception as exc:
        from backend.logging_config import logger
        logger.exception("launch_profile unhandled failure for %s", profile_id)
        _cleanup_orphan_processes(profile_id)
        return {"status": "error", "message": f"Launch failed: {exc}"}

    try:
        profile = profile_manager.get_profile(profile_id)
        payload = {
            "profile_id": profile_id,
            "status": result.get("status"),
            "message": result.get("message"),
        }
        if result.get("status") == "error":
            _cleanup_orphan_processes(profile_id)
        if profile:
            payload["profile_name"] = profile.get("name")
        webhook_notifier.notify("profile.launch", payload)
    except Exception:
        from backend.logging_config import logger
        logger.exception("Failed to emit profile.launch webhook for %s", profile_id)
    return result


def _cleanup_orphan_processes(profile_id: str):
    """Kill any Chrome/Chromium process tied to the profile's user data dir.

    Removes stale ``active_browsers`` and profile-state entries. Safe to call
    even when the profile has never been launched.
    """
    from backend.logging_config import logger
    try:
        profile = profile_manager.get_profile(profile_id)
        profile_path = profile.get("path") if profile else None
        if profile_path:
            try:
                canonical_path = os.path.normcase(os.path.realpath(profile_path))
                procs = find_profile_processes(canonical_path)
                for proc in procs:
                    try:
                        kill_process_tree(proc)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception as exc:
        logger.error("Error locating processes for profile %s cleanup: %s", profile_id, exc)

    active_browsers.pop(profile_id, None)
    profile_states.pop(profile_id, None)


async def safe_launch_profile(profile_id: str, force_headless: bool = False, pin: str = None):
    """Wrap ``launch_profile`` and always return a ``status``/``message`` dict."""
    try:
        return await launch_profile(profile_id, force_headless=force_headless, pin=pin)
    except Exception as exc:
        from backend.logging_config import logger
        logger.exception("safe_launch_profile unhandled failure for %s", profile_id)
        _cleanup_orphan_processes(profile_id)
        return {"status": "error", "message": f"Launch failed: {exc}"}

def _maybe_clear_ephemeral_profile_data(profile_id: str):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        return
    mode = str(profile.get("advanced", {}).get("privacy_mode", "standard")).strip().lower()
    if mode != "ephemeral":
        return
    path = profile.get("path")
    if not path:
        return
    canonical_profiles_dir = os.path.normcase(os.path.realpath(profile_manager.PROFILES_DIR))
    canonical_path = os.path.normcase(os.path.realpath(path))
    if not canonical_path or canonical_path == canonical_profiles_dir:
        return
    if not canonical_path.startswith(canonical_profiles_dir + os.sep):
        return
    shutil.rmtree(canonical_path, ignore_errors=True)


async def _do_close_profile(profile_id: str):
    from backend.lock_manager import lock_manager
    await lock_manager.acquire(profile_id)
    set_profile_state(profile_id, "closing")
    try:
        from backend.logging_config import logger
        import psutil
        profile = profile_manager.get_profile(profile_id)
        profile_path = profile.get("path") if profile else None

        if profile_id in active_browsers:
            # Cancel outstanding CDP tasks to prevent leak on relaunch
            tasks = profile_cdp_tasks.pop(profile_id, set())
            for task in tasks:
                if not task.done():
                    task.cancel()
            health_task = active_browsers[profile_id].get("health_task")
            if health_task and not health_task.done():
                health_task.cancel()
            profile_page_events.pop(profile_id, None)
            profile_opted_origins.pop(profile_id, None)

            browser_data = active_browsers[profile_id]
            stored_pid = browser_data.get("pid")

            try:
                await browser_data["context"].close()
            except Exception:
                pass
            try:
                await browser_data["playwright"].stop()
            except Exception:
                pass

            target_proc = None
            if stored_pid:
                try:
                    target_proc = psutil.Process(stored_pid)
                except Exception:
                    pass

            if target_proc or profile_path:
                for _ in range(50):
                    if target_proc:
                        if not target_proc.is_running():
                            break
                    elif profile_path:
                        if not find_profile_processes(profile_path):
                            break
                    await asyncio.sleep(0.1)
                else:
                    logger.warning(f"Graceful close timed out for profile {profile_id}. Forcing process-tree termination.")
                    if target_proc:
                        kill_process_tree(target_proc)
                    elif profile_path:
                        procs = find_profile_processes(profile_path)
                        for proc in procs:
                            kill_process_tree(proc)

            if target_proc or profile_path:
                for _ in range(50):
                    if target_proc and not target_proc.is_running():
                        break
                    if profile_path and not find_profile_processes(profile_path):
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise RuntimeError("Failed to terminate browser processes within timeout.")

            active_browsers.pop(profile_id, None)
            profile_client_hints.pop(profile_id, None)
            set_profile_state(profile_id, "stopped")
            _maybe_clear_ephemeral_profile_data(profile_id)
            logger.info(f"Browser closed for profile {profile_id}", extra={"profile_id": profile_id})
            return {"status": "success", "message": "Browser closed"}

        if profile_path:
            procs = find_profile_processes(profile_path)
            if procs:
                for proc in procs:
                    kill_process_tree(proc)

                for _ in range(50):
                    if not find_profile_processes(profile_path):
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise RuntimeError("Failed to terminate lingering browser processes within timeout.")

                profile_client_hints.pop(profile_id, None)
                set_profile_state(profile_id, "stopped")
                _maybe_clear_ephemeral_profile_data(profile_id)
                return {"status": "success", "message": "Lingering browser processes terminated"}

        profile_client_hints.pop(profile_id, None)
        set_profile_state(profile_id, "stopped")
        return {"status": "error", "message": "Browser is not running"}
    except Exception as e:
        logger.error(f"Error closing profile {profile_id}: {e}", exc_info=True)
        set_profile_state(profile_id, "error")
        return {"status": "error", "message": f"Close failed: {str(e)}"}
    finally:
        lock_manager.release(profile_id)

async def close_profile(profile_id: str):
    """Public wrapper that emits profile.close webhooks after the attempt."""
    result = await _do_close_profile(profile_id)
    try:
        profile = profile_manager.get_profile(profile_id)
        payload = {
            "profile_id": profile_id,
            "status": result.get("status"),
            "message": result.get("message"),
        }
        if profile:
            payload["profile_name"] = profile.get("name")
        webhook_notifier.notify("profile.close", payload)
    except Exception:
        from backend.logging_config import logger
        logger.exception("Failed to emit profile.close webhook for %s", profile_id)
    return result

def is_profile_running(profile_id: str):
    return profile_id in active_browsers

async def get_profile_cookies(profile_id: str):
    is_running = profile_id in active_browsers
    if not is_running:
        res = await launch_profile(profile_id, force_headless=True)
        if res.get("status") == "error":
            return {"status": "error", "message": res.get("message", "Launch failed")}

    try:
        context = active_browsers[profile_id]["context"]
        cookies = await context.cookies()
        return {"status": "success", "cookies": cookies}
    finally:
        if not is_running:
            await close_profile(profile_id)

async def set_profile_cookies(profile_id: str, cookies: list):
    is_running = profile_id in active_browsers
    if not is_running:
        res = await launch_profile(profile_id, force_headless=True)
        if res.get("status") == "error":
            return {"status": "error", "message": res.get("message", "Launch failed")}

    try:
        context = active_browsers[profile_id]["context"]
        await context.add_cookies(cookies)
        return {"status": "success"}
    finally:
        if not is_running:
            await close_profile(profile_id)
