#!/usr/bin/env python3
"""Best-effort DNS leak detection.

Privileged mode attempts to capture outbound UDP/53 traffic with a raw socket.
Unprivileged mode (default) runs resolver comparisons via nslookup and a random
domain check. Results are printed as JSON.
"""

import argparse
import datetime
import json
import os
import random
import re
import socket
import string
import subprocess
import sys
import time


def is_admin():
    """Best-effort admin check on Windows."""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def log(message):
    print(message, file=sys.stderr)


def parse_proxy(proxy):
    """Parse host:port into (host, port). Port may be None."""
    if not proxy:
        return None, None
    if proxy.startswith("http://") or proxy.startswith("https://"):
        proxy = proxy.split("://", 1)[1]
    if proxy.count(":") == 1:
        host, port = proxy.rsplit(":", 1)
        return host, port
    return proxy, None


def resolve_host(host):
    """Resolve host to an IPv4 address; return original if already numeric."""
    if not host:
        return None
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except Exception:
        return host


def raw_capture(proxy_ip, timeout=5.0):
    """Try to capture outbound DNS traffic with a raw socket.

    This is best-effort on Windows; raw sockets generally cannot see all outbound
    traffic without a packet driver such as Npcap.
    """
    leaks = []
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_UDP)
        s.settimeout(timeout)
        # Bind is not required for raw capture, but helps on some platforms.
        try:
            s.bind(("0.0.0.0", 0))
        except OSError:
            pass
    except Exception as exc:
        raise RuntimeError(f"Raw socket creation failed: {exc}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        s.settimeout(remaining)
        try:
            data, addr = s.recvfrom(65535)
        except socket.timeout:
            break
        except Exception:
            continue

        if len(data) < 20:
            continue

        # Parse minimal IPv4 header.
        version_ihl = data[0]
        ihl = (version_ihl & 0x0F) * 4
        protocol = data[9]
        if protocol != socket.IPPROTO_UDP or len(data) < ihl + 8:
            continue

        dst_port = (data[ihl + 2] << 8) | data[ihl + 3]
        if dst_port != 53:
            continue

        dst_ip = ".".join(str(b) for b in data[16:20])
        if proxy_ip and dst_ip != proxy_ip:
            leaks.append(
                {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "dst_ip": dst_ip,
                    "dst_port": 53,
                    "expected_proxy_ip": proxy_ip,
                    "reason": "DNS packet destination does not match proxy IP",
                }
            )

    try:
        s.close()
    except Exception:
        pass
    return leaks


def run_nslookup(host, server=None):
    """Run nslookup for host, optionally via a specific server."""
    cmd = ["nslookup", host]
    if server:
        cmd.append(server)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        return proc.stdout + proc.stderr
    except FileNotFoundError:
        return ""
    except Exception as exc:
        return f"nslookup error: {exc}"


def extract_nslookup_ips(output):
    """Extract IPv4 addresses from nslookup output."""
    if not output:
        return []
    return re.findall(r"(?:Address|Adresse)\s*:\s+(\d+\.\d+\.\d+\.\d+)", output, re.IGNORECASE)


def unprivileged_check(proxy_ip, host="google.com"):
    """Compare system resolver with proxy resolver and run a random-domain check."""
    leaks = []
    notes = {}

    system_out = run_nslookup(host)
    proxy_out = run_nslookup(host, proxy_ip) if proxy_ip else ""

    notes["nslookup_system"] = system_out.splitlines()
    notes["nslookup_proxy"] = proxy_out.splitlines() if proxy_ip else None

    system_ips = extract_nslookup_ips(system_out)
    proxy_ips = extract_nslookup_ips(proxy_out)

    if system_ips and proxy_ips and set(system_ips) != set(proxy_ips):
        leaks.append(
            {
                "reason": "System DNS answer differs from proxy DNS answer",
                "host": host,
                "system_ips": system_ips,
                "proxy_ips": proxy_ips,
            }
        )

    token = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    domain = f"{token}.dnsleak.example"
    try:
        resolved = socket.gethostbyname(domain)
        notes["random_domain_check"] = {
            "domain": domain,
            "resolved_ip": resolved,
            "note": "domain unexpectedly resolved",
        }
    except socket.gaierror:
        notes["random_domain_check"] = {
            "domain": domain,
            "resolved_ip": None,
            "note": "domain did not resolve as expected for a non-existent subdomain",
        }
    except Exception as exc:
        notes["random_domain_check"] = {
            "domain": domain,
            "resolved_ip": None,
            "note": f"resolution error: {exc}",
        }

    return leaks, notes


def main():
    parser = argparse.ArgumentParser(description="Best-effort DNS leak detection.")
    parser.add_argument("--proxy", default=None, help="Proxy host:port (optional).")
    parser.add_argument(
        "--timeout", type=float, default=5.0, help="Capture timeout in seconds."
    )
    args = parser.parse_args()

    proxy_host, proxy_port = parse_proxy(args.proxy)
    proxy_ip = resolve_host(proxy_host)

    mode = "privileged" if is_admin() else "unprivileged"
    leaks = []
    notes = {}
    errors = []

    if mode == "privileged":
        try:
            cap_leaks = raw_capture(proxy_ip, timeout=args.timeout)
            leaks.extend(cap_leaks)
        except Exception as exc:
            mode = "unprivileged"
            errors.append(f"Privileged raw socket failed; falling back: {exc}")

    if mode == "unprivileged":
        check_leaks, check_notes = unprivileged_check(proxy_ip)
        leaks.extend(check_leaks)
        notes.update(check_notes)

    # Unprivileged checks are informational only => warning.
    if leaks:
        status = "failed"
    elif mode == "unprivileged":
        status = "warning"
    else:
        status = "passed"

    result = {
        "status": status,
        "mode": mode,
        "proxy_host": proxy_host,
        "proxy_port": proxy_port,
        "proxy_ip": proxy_ip,
        "timeout": args.timeout,
        "leaks": leaks,
        "notes": notes,
        "errors": errors,
    }

    print(json.dumps(result, indent=2))
    sys.exit(1 if status == "failed" else 0)


if __name__ == "__main__":
    main()
