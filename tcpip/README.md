# TCP/IP Fingerprint Control Harness

GhostBrowser can spoof JavaScript-visible fingerprints (canvas, WebGL, audio, etc.), but some anti-bot systems also measure lower-level TCP/IP properties such as:

- IP TTL (Time-To-Live)
- TCP window size and scaling
- TCP options ordering
- MTU / MSS values
- IPv6 leakage paths
- DNS resolver behavior

These properties are controlled by the operating system network stack and by the upstream proxy, not by the browser itself. This folder provides hooks, scripts, and a proxy harness that let a user or administrator normalize these signals where practical.

## What is implemented here

1. **Windows TCP/IP OS tweaks** (`windows_tcpip_tweaks.py`)
   Read/write Windows network parameters via `netsh`/`reg.exe`. Requires admin privileges to change values; reads are safe for non-admin users.

2. **Local DNS proxy** (`dns_resolver.py`)
   A small UDP DNS forwarder that binds to localhost and sends all queries to a configurable upstream resolver. Can be used by Chromium with `--host-resolver-rules` to keep DNS inside the proxy tunnel and to drop AAAA records if desired.

3. **TCP normalizer (user-space)** (`normalizer.py`)
   A local SOCKS-like relay. Because raw SYN control is unavailable from Python sockets, this cannot modify TTL or options, but it can ensure connections exit through a controlled upstream proxy and log outbound flows.

4. **WinDivert / WFP skeleton** (`windivert_adapter.py`)
   Documentation and function stubs for a lightweight Windows Filter Platform driver that could modify outbound packets. A full implementation requires installing the WinDivert driver and is deferred to operator choice.

5. **Backend manager** (`backend/tcpip_manager.py`)
   Optional singleton that starts the local DNS and TCP relays based on environment variables.

## Limitations

- True TCP SYN option normalization requires kernel-level or driver-level packet filtering.
- This harness does not remove TLS/JA3 or IP geolocation signals; those must be handled by the proxy.
- The browser remains ordinary Chromium unless the administrator also configures the OS/proxy layer.

## Quick start

Set in `.env` or environment:

```bash
GHOSTBROWSER_ENABLE_TCPIP_MANAGER=1
GHOSTBROWSER_DNS_SERVER=1.1.1.1
GHOSTBROWSER_DNS_PORT=53
```

The backend will start a local DNS proxy on startup and log its bound port.
