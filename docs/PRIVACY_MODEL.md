# GhostBrowser Privacy Model

## Overview

GhostBrowser provides browser profile isolation for legitimate use cases such as multi-account management, QA testing, and privacy-conscious browsing. This document describes the privacy properties GhostBrowser **can and cannot guarantee**.

## What GhostBrowser Guarantees

| Property | Guarantee | Mechanism |
|----------|-----------|-----------|
| Profile storage isolation | Separate cookie jars, localStorage, IndexedDB, sessionStorage, cache, service workers, and permissions per profile | Dedicated browser data directory per profile via `--user-data-dir` |
| Network isolation | Per-profile proxy assignment; WebRTC forced to proxy/disabled; DNS prefetch disabled | Browser launch flags + CDP policy enforcement |
| Fingerprint coherence | Timezone, locale, user agent, screen resolution, GPU vendor, and client hints are internally consistent and match the profile's declared OS/device | AI-generated fingerprint validation via coherence validator |
| Automation disclosure | `navigator.webdriver` is spoofed to `false` only when the profile is configured for stealth mode | Context creation args + CDP `Page.addScriptToEvaluateOnNewDocument` |
| Credential redaction | Proxy credentials, Cloudflare tokens, and database secrets are redacted from logs, audit trails, and API responses | `_redact_sensitive_api_data` middleware; credential store uses encrypted storage (DPAPI on Windows) |
| Release integrity | CI runs credential scan + release audit + full test suite before any build is released | `.github/workflows/test.yml`, `scripts/release_audit.py`, `scripts/check_credentials.py` |

## What GhostBrowser Does NOT Guarantee

| Claim | Why it cannot be guaranteed |
|-------|---------------------------|
| "Fully untraceable" | IP address, TLS handshake characteristics, TCP/IP stack fingerprinting, and server-side behavior analysis are outside browser control. No client-side browser automation can mask these. |
| "Never-before-seen device" | Generating globally unique fingerprints per session increases fingerprint entropy, which itself is a detection signal to advanced anti-bot services. Coherent, realistic fingerprints that match real device populations are more privacy-preserving. |
| "Same IP = different device" | IP-based correlation is a server-side capability. The browser cannot control what a remote server observes at the network layer. |
| "Bypass anti-bot services" | Services like Cloudflare Turnstile, Akamai, DataDome, PerimeterX, and Shape Security use server-side ML models, behavioral analysis, and threat intelligence that cannot be evaded by client-side configuration alone. |
| "No fingerprint correlation" | Even with perfect spoofing, account activity patterns, login timestamps, and site-specific behavior can link sessions. |

## Threat Model

GhostBrowser is designed to protect against:

- **Cookie-based tracking** — Profile isolation prevents cross-profile cookie leaks
- **Canvas/WebGL fingerprint correlation** — Per-profile noise seeds prevent canvas fingerprint correlation across profiles
- **WebRTC IP leaks** — WebRTC is forced to proxy-only or disabled
- **DNS leaks** — DNS prefetch and async DNS are disabled; DNS resolution goes through the configured proxy
- **Automation detection via basic probes** — `navigator.webdriver`, CDP artifacts, `chrome.runtime`, and common DOM automation properties are hidden
- **Data persistence leaks** — Separate IndexedDB, localStorage, sessionStorage, cache, and service worker scopes per profile

GhostBrowser is NOT designed to protect against:

- **Network-level fingerprinting** (TCP/IP stack, TLS cipher suites, HTTP/2 settings)
- **Server-side behavioral analysis** (mouse movement patterns, scroll behavior, time-on-page)
- **Account-based correlation** (login email, device registration, payment methods)
- **IP-based geolocation and network attribution** (IP address reveals approximate location and ISP regardless of browser configuration)

## Privacy Configuration

Users can configure the following privacy settings in their profile:

### Available Settings

| Setting | Effect | Default |
|---------|--------|---------|
| `webrtc_mode` | Controls WebRTC IP leak protection (`disabled`, `protected`, `unprotected`) | `protected` |
| `block_service_workers` | Blocks service workers for proxy profiles | `true` (when proxy set) |
| `canvas_noise_seed` | Adds deterministic noise to canvas fingerprint | Random |
| `audio_noise_seed` | Adds deterministic noise to audio fingerprint | Random |
| `webgl_vendor` / `webgl_renderer` | Spoofs WebGL GPU identification | AI-generated |
| `device_scale_factor` | Sets device pixel ratio | 1.0 (desktop) / 2.0 (mobile) |
| `screen_resolution` | Sets viewport resolution | 1920x1080 (desktop) |

### Privacy Mode

When `privacy_mode = "high"` is set on a profile, GhostBrowser will:

1. Disable all high-entropy APIs (WebGL, Canvas, AudioContext, Battery, Sensors, WebGPU) via content setting policies
2. Block all third-party cookies
3. Disable service workers entirely
4. Set WebRTC to `disabled`
5. Strip `Accept-CH` headers for non-local origins

## Reporting Privacy Issues

If you discover a privacy leak or unexpected data persistence across profiles, please open an issue in the repository with steps to reproduce.
