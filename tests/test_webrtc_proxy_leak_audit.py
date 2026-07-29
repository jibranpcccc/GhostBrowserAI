r"""Opt-in authenticated proxy/WebRTC audit with zero credential logging.

Credentials are read only after the opt-in guard from:
    %LOCALAPPDATA%\GhostBrowser\proxy_audit_secrets.json

The file must contain ``{"proxies": [...]}`` with exactly five entries. Each
entry contains server, username, and password. The file is deliberately outside
the repository and must not be readable by broad Windows principals.
"""

import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time


RUN_SALT = secrets.token_bytes(32)
IP_ENDPOINTS = ("https://api.ipify.org", "https://icanhazip.com")


def _salted_ip_hash(value: str) -> str:
    try:
        normalized = str(ipaddress.ip_address(value))
    except ValueError:
        return "invalid"
    return hashlib.sha256(RUN_SALT + normalized.encode("ascii")).hexdigest()[:16]


def _secret_file() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("secret_location_unavailable")
    return Path(local_app_data) / "GhostBrowser" / "proxy_audit_secrets.json"


def _assert_secret_file_is_private(path: Path) -> None:
    resolved = path.resolve(strict=True)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise RuntimeError("secret_file_inside_repository")

    if os.name == "nt":
        completed = subprocess.run(
            ["icacls", str(resolved)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise RuntimeError("secret_acl_unverifiable")
        acl = completed.stdout.casefold()
        broad_principals = (
            "everyone:",
            "authenticated users:",
            "builtin\\users:",
            "all application packages:",
        )
        if any(principal in acl for principal in broad_principals):
            raise RuntimeError("secret_acl_is_too_broad")
    else:
        if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
            raise RuntimeError("secret_mode_is_too_broad")


def _load_five_proxies() -> list[dict]:
    path = _secret_file()
    _assert_secret_file_is_private(path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    proxies = payload.get("proxies") if isinstance(payload, dict) else None
    if not isinstance(proxies, list) or len(proxies) != 5:
        raise RuntimeError("exactly_five_proxies_required")
    normalized = []
    for entry in proxies:
        if not isinstance(entry, dict):
            raise RuntimeError("invalid_proxy_record")
        server = entry.get("server")
        username = entry.get("username")
        password = entry.get("password")
        if not all(isinstance(v, str) and v for v in (server, username, password)):
            raise RuntimeError("incomplete_proxy_record")
        if "@" in server:
            raise RuntimeError("credentials_must_not_be_embedded_in_server")
        normalized.append(
            {"server": server, "username": username, "password": password}
        )
    return normalized


async def _browser_observations(page) -> tuple[str, set[str]]:
    observed = []
    for endpoint in IP_ENDPOINTS:
        response = await page.goto(endpoint, timeout=25_000, wait_until="commit")
        if response is None or response.status != 200:
            raise RuntimeError("ip_endpoint_failed")
        body = (await page.locator("body").inner_text()).strip()
        observed.append(str(ipaddress.ip_address(body)))
    if observed[0] != observed[1]:
        raise RuntimeError("ip_endpoints_disagree")

    candidate_values = await page.evaluate(
        r"""async () => {
            const found = new Set();
            const collect = (candidate) => {
                if (!candidate) return;
                for (const match of String(candidate).matchAll(
                    /(?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]{3,}/g
                )) found.add(match[0]);
            };
            const pc = new RTCPeerConnection({
                iceServers: [{urls: 'stun:stun.l.google.com:19302'}]
            });
            try {
                pc.createDataChannel('audit');
                pc.addEventListener('icecandidate', event => {
                    if (event.candidate) {
                        collect(event.candidate.candidate);
                        collect(event.candidate.address);
                    }
                });
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                await new Promise(resolve => setTimeout(resolve, 3500));
                collect(pc.localDescription && pc.localDescription.sdp);
                const stats = await pc.getStats();
                stats.forEach(report => {
                    if (report.type === 'local-candidate') {
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
    valid_candidates = set()
    for value in candidate_values:
        try:
            valid_candidates.add(str(ipaddress.ip_address(value)))
        except ValueError:
            pass
    return observed[0], valid_candidates


async def _launch_raw(playwright, directory: str, proxy: dict | None):
    started = time.perf_counter()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=directory,
        headless=True,
        args=[
            "--disable-quic",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--enforce-webrtc-ip-permission-check",
        ],
        proxy=proxy,
    )
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        public_ip, candidates = await _browser_observations(page)
        return public_ip, candidates, round((time.perf_counter() - started) * 1000)
    finally:
        await context.close()


async def _launch_production(playwright, directory: str, proxy: dict, index: int):
    from unittest.mock import AsyncMock, patch
    from backend.browser_manager import build_browser_launch_config
    from backend.proxy_manager import proxy_manager

    profile = {
        "id": f"33333333-3333-3333-3333-{index:012d}",
        "name": "proxy-audit",
        "path": directory,
        "proxy": proxy,
        "advanced": {"webrtc_mode": "protected", "block_service_workers": True},
    }
    with patch.object(
        proxy_manager,
        "get_proxy_for_profile",
        new=AsyncMock(side_effect=AssertionError("pool_selection_forbidden")),
    ) as pool_mock, patch.object(
        proxy_manager, "check_proxy_health", new=AsyncMock(return_value=True)
    ) as health_mock:
        config = await build_browser_launch_config(profile, force_headless=True)
    if pool_mock.await_count != 0 or health_mock.await_count != 1:
        raise RuntimeError("proxy_precedence_failed")
    if config["proxy"] != proxy:
        raise RuntimeError("production_proxy_mismatch")
    required_args = {
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--enforce-webrtc-ip-permission-check",
    }
    if not required_args.issubset(set(config["args"])):
        raise RuntimeError("network_protection_missing")

    started = time.perf_counter()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=directory,
        headless=True,
        args=config["args"],
        proxy=config["proxy"],
        service_workers="block",
    )
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        public_ip, candidates = await _browser_observations(page)
        return public_ip, candidates, round((time.perf_counter() - started) * 1000)
    finally:
        await context.close()


async def _run_live_audit() -> int:
    original_env = os.environ.copy()
    original_path = list(sys.path)
    temp_root = tempfile.TemporaryDirectory()
    errors = []
    try:
        proxies = _load_five_proxies()
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_root.name
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            direct_dir = str(Path(temp_root.name) / "direct")
            direct_ip, direct_candidates, direct_ms = await _launch_raw(
                playwright, direct_dir, None
            )
            direct_candidate_hashes = sorted(
                _salted_ip_hash(value) for value in direct_candidates
            )
            print(
                f"direct_ms={direct_ms} direct_hash={_salted_ip_hash(direct_ip)} "
                f"webrtc_hashes={','.join(direct_candidate_hashes) or 'none'}"
            )

            for index, proxy in enumerate(proxies, 1):
                raw_dir = str(Path(temp_root.name) / f"raw-{index}")
                prod_dir = str(Path(temp_root.name) / f"production-{index}")
                try:
                    raw_ip, raw_candidates, raw_ms = await _launch_raw(
                        playwright, raw_dir, proxy
                    )
                    prod_ip, prod_candidates, prod_ms = await _launch_production(
                        playwright, prod_dir, proxy, index
                    )
                    candidate_union = raw_candidates | prod_candidates
                    leaked_direct = direct_ip in candidate_union or (
                        raw_ip == direct_ip or prod_ip == direct_ip
                    )
                    mismatch = raw_ip != prod_ip
                    candidate_hashes = sorted(
                        _salted_ip_hash(value) for value in candidate_union
                    )
                    status = "PASS" if not leaked_direct and not mismatch else "FAIL"
                    print(
                        f"proxy_index={index} status={status} raw_ms={raw_ms} "
                        f"production_ms={prod_ms} ip_hash={_salted_ip_hash(prod_ip)} "
                        f"webrtc_hashes={','.join(candidate_hashes) or 'none'}"
                    )
                    if status != "PASS":
                        errors.append(index)
                except Exception as exc:
                    print(
                        f"proxy_index={index} status=FAIL error_type={type(exc).__name__}"
                    )
                    errors.append(index)
    except Exception as exc:
        print(f"audit_status=FAIL error_type={type(exc).__name__}")
        return 1
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        sys.path[:] = original_path
        temp_root.cleanup()

    if errors:
        print(f"audit_status=FAIL failed_count={len(errors)}")
        return 1
    print("audit_status=PASS proxy_count=5")
    return 0


def main() -> int:
    # This must remain before production imports and before the secret file is
    # located, ACL-checked, or read.
    if os.environ.get("GHOSTBROWSER_RUN_PROXY_WEBRTC_TEST") != "1":
        print("PROXY WEBRTC LEAK TEST: NOT RUN")
        return 0
    return asyncio.run(_run_live_audit())


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(main())
