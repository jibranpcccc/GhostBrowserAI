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

### Trust boundaries and assets

The operator, Chromium, Playwright, the local OS, proxy providers, extensions, and the optional remote sync service are separate trust boundaries. Profile cookies, tokens, proxy credentials, fingerprint templates, browsing activity, local keys, and encrypted sync archives are sensitive assets. A compromised Playwright process, extension, unlocked host, or malicious proxy can access data within its boundary.

### Mitigated threats

- **Cross-profile storage leakage** — each profile has a separate Chromium data directory.
- **Direct-network fallback** — configured proxies are health-checked through an HTTPS exit-IP endpoint; QUIC, IPv6, DNS prefetch, and non-proxied WebRTC UDP paths are disabled at launch.
- **Basic fingerprint inconsistency and automation probes** — profile values are validated for coherence and runtime patches reduce common automation markers.
- **Profile archive exposure in transit or at rest** — local/remote sync archives are passphrase-derived AES-256-GCM payloads before they are written or uploaded. The remote service receives encrypted bytes, not the passphrase or plaintext profile data.
- **Cloudflare credential exposure** — the normal Windows credential store uses user-scoped DPAPI. Legacy plaintext credential files require an explicit opt-in.

### Storage-key limitation

Live profile metadata is encrypted with a single file-based Fernet master key at `profiles_data/.master.key`, shared by all local profiles. It is not per-profile and is not DPAPI-protected. Protect the profile directory and master-key file with OS account and filesystem permissions; compromise of that key exposes all locally encrypted profile metadata.

### Out of scope

GhostBrowser does not protect against network-level TLS/TCP/HTTP fingerprinting, server-side behavioral or account correlation, a malicious proxy or TLS-intercepting provider, OS-level malware or memory inspection, or a compromised browser extension. It provides no anonymity or undetectability guarantee.

### Implemented and future capabilities

Encrypted cloud sync, a remote-sync reference server, GitHub update checks, signed-download verification, staged update application/rollback, and an SBOM endpoint are implemented repository capabilities. Deployment and operation of a remote sync or update service remain the operator's responsibility. TUF-style metadata, reproducible builds, and fuller font-metric/emoji consistency remain future work.

## Privacy Configuration

Users can configure the following privacy settings in their profile:

### Available Settings

| Setting | Effect | Default |
|---------|--------|---------|
| `webrtc_mode` | Controls WebRTC IP leak protection; only `protected`/legacy `altered` are accepted | `protected` |
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
4. Retain protected WebRTC transport (the unsupported legacy `disabled` value is normalized to protected)
5. Strip `Accept-CH` headers for non-local origins

## Reporting Privacy Issues

If you discover a privacy leak or unexpected data persistence across profiles, please open an issue in the repository with steps to reproduce.
