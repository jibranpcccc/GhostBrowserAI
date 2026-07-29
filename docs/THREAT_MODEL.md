# GhostBrowser Threat Model

## 1. Overview

GhostBrowser is a per-profile isolated wrapper around upstream Chromium, controlled through Playwright. Each profile receives its own user data directory, proxy configuration, and fingerprint template. The goal is to prevent cross-profile leakage, reduce browser fingerprint entropy, and make automated browsing sessions coherent while remaining honest about what the tool can and cannot hide.

## 2. Trust Boundaries

| Boundary | Description |
|---|---|
| User | The operator who creates, launches, and owns the profile data. |
| Browser engine | Upstream Chromium provides rendering, networking, sandbox, and storage. GhostBrowser delegates security guarantees to Chromium where it does not explicitly override them. |
| Playwright automation layer | Controls browser launch, context isolation, page interaction, and network interception. Highly privileged; compromise = full session access. |
| Profile manager | Creates and tracks per-profile directories, fingerprint templates, and proxy mappings. |
| Proxy providers | Third-party transport for outgoing traffic. See network observers and MitM. |
| Local OS | Host filesystem, memory, and user accounts are outside browser control. |
| Cloud sync (future) | Optional encrypted backup of profiles and keys to a remote service. Not implemented yet. |

## 3. Threat Actors

- **Website trackers / fingerprinting services**: try to link sessions across profiles using browser attributes, canvas/WebGL, fonts, and behavior.
- **Anti-fraud / bot detection systems**: classify traffic as automated and apply extra challenges or blocks.
- **Malicious websites**: attempt renderer compromise, XSS, or social engineering to extract cookies/credentials.
- **Local shared-device users**: access profile data stored on a shared or unlocked machine.
- **Network observers (ISP, public WiFi)**: observe DNS, SNI, traffic size, and timing.
- **Proxy providers / man-in-the-middle**: terminate or inspect TLS, inject scripts, log destinations, or reveal real IP through side channels.
- **Compromised extension**: runs inside the browser with elevated permissions and may exfiltrate data.

## 4. Assets to Protect

| Asset | Why it matters |
|---|---|
| Per-profile cookies, credentials, tokens | Personal account access; cross-profile leakage breaks isolation. |
| Profile fingerprint template | Defines the synthetic device/identity of the profile; leakage allows correlation. |
| Proxy credentials | Grant network egress; leakage allows abuse or deanonymization. |
| User browsing activity | Reveals intent, accounts, locations, and relationships. |
| Local encryption keys | Protect profile metadata at rest; compromise = data exposure. |

## 5. Threat Scenarios & Mitigations

| Scenario | Mitigation |
|---|---|
| Cross-profile storage leak | Strictly separate profile data directories; never share `userDataDir` between profiles; clear caches on deletion. |
| IP/DNS/WebRTC leak | Force all traffic through configured proxy; block QUIC, UDP fallback, and IPv6 by default; implement a kill switch that aborts launch if proxy connection fails. |
| Fingerprint inconsistency | Use cohort-based coherent profiles where canvas, fonts, WebGL, timezone, geolocation, and viewport values are drawn from one template; validate consistency at launch. |
| Automation detection | Apply anti-detect runtime patches to reduce known automation markers; this is intended to reduce false bot classification, not to defeat legitimate site security testing or terms-of-service enforcement. |
| Renderer exploit | Rely on Chromium sandbox and site isolation; do not run with `--no-sandbox` unless required by a specific environment, and warn when this happens. |
| Profile data theft at rest | Encrypt profile metadata and sensitive configuration with keys tied to the local user context; do not store plaintext proxy credentials. |

## 6. Out-of-Scope / Limitations

- **TLS/HTTP layer cannot be spoofed from Playwright.** The browser performs real TLS handshakes; certificate pinning, JA3, and HTTP/2 fingerprints are properties of Chromium, not masks we apply.
- **Real-time behavioral emulation is not provided.** Mouse paths, scroll timing, and typing cadence remain automation-driven unless the user explicitly adds them.
- **Hardware-backed attestation and DRM cannot be defeated.** We do not forge TPM, SafetyNet, Play Integrity, or Widevine state.
- **OS-level malware is out of scope.** Keyloggers, memory scrapers, or compromised hosts can bypass any browser isolation.
- **No "undetectable" guarantee.** GhostBrowser raises the cost of detection and correlation; it does not make a user anonymous or invisible to a determined, well-resourced adversary.

## 7. Future Work

- Encrypted cloud sync for profiles and keys, with client-side encryption and device authorization.
- Signed update system and reproducible builds to reduce supply-chain risk.
- Fingerprint-access dashboard for reviewing and rotating per-profile templates.
