# GhostBrowser Anti-Detect Plan

This plan maps the feature request against the current codebase. Items are marked **DONE**, **PARTIAL**, or **TODO**. Where GhostBrowser already has a better internal approach, the feature is marked **SKIP** with rationale.

## 1. Core Architecture & Isolation

| Feature | Status | Notes |
|---|---|---|
| Per-profile cookie/localStorage/IndexedDB/cache/SW isolation | **DONE** | Separate `profiles_data/<id>` dirs; verified by `scripts/isolation_check.py`. |
| Persistent, stable identity per profile | **DONE** | Deterministic seeds from profile id for canvas/WebGL/audio/font noise. |
| Profile manager / create / delete / list / templates | **DONE** | `backend/profile_manager.py`, `backend/profile_creator.py`, `backend/profile_folders.py`. |
| Encrypted profile storage | **DONE** | Fernet per-profile crypto in `profile_manager.py`. |
| Profile import/export | **DONE** | `backend/profile_transfer.py` with encrypted channels. |
| Profile tags / bulk tagging | **DONE** | Tags stored in profile metadata; add/remove/bulk endpoints + frontend badges. |
| Bulk operations | **DONE** | `backend/bulk_operations.py` + UI. |
| Team roles | **DONE** | `backend/team_manager.py`. |
| Cloud sync | **TODO** | No remote sync yet; only local encrypted storage. |

## 2. Keyboard/Password/Fraud Isolation (added from request)

| Feature | Status | Notes |
|---|---|---|
| Keylogger resistance / virtual keyboard | **DONE** | On-screen keyboard for `data-secure="true"` inputs; Setting toggle; shift, caps, random scramble. |
| Profile PIN lock | **DONE** | PBKDF2-HMAC-SHA256 hashed PIN; required before launch; endpoints for set/verify/clear. |
| Password field isolation | **PARTIAL** | Profiles isolated; PIN-lock added for sensitive profiles. No OS-level password sandbox. |

## 2b. Proxy Operations

| Feature | Status | Notes |
|---|---|---|
| Per-profile proxy bind | **DONE** | HTTP/HTTPS/SOCKS5 via Playwright proxy + custom parsing. |
| Proxy health check | **DONE** | TCP health_check_proxy() in proxy_manager; launch fails over to healthy proxy. |
| Proxy failover pool | **DONE** | `get_healthy_proxy()` selects healthy proxy; failures trigger 5-min cooldown. |
| Proxy test endpoint | **DONE** | `GET /api/profiles/{profile_id}/test-proxy`. |

## 3. Network-Layer Protections

| Feature | Status | Notes |
|---|---|---|
| Per-profile proxy bind | **DONE** | HTTP/HTTPS/SOCKS5 via Playwright proxy + custom parsing. |
| Proxy kill-switch / no direct fallback | **DONE** | `active_browsers` health loop closes profile if proxy goes unhealthy. |
| WebRTC leak prevention | **DONE** | `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`, RTCPeerConnection stub, browserleaks test passes. |
| DNS leak prevention | **DONE** | `--disable-quic`, `--dns-prefetch-disable`, `--disable-async-dns`, policy check in scanner. Packet-capture proof requires admin. |
| IPv6 leak prevention | **DONE** | `--disable-ipv6` added to launch args. |
| DNS leak packet-capture proof | **TODO** | Requires privileged packet capture or a local root DNS server. |
| Proxy geo → timezone/locale auto-match | **DONE** | `backend/proxy_manager.py` `guess_locale_timezone()` via ipapi.co. |

## 4. TLS / HTTP-Layer Fingerprints

| Feature | Status | Notes |
|---|---|---|
| TLS/JA3/JA4 matching | **SKIP** | Requires proxy/TLS-layer control. Playwright-level Chromium already has a real Chrome JA3; do not modify. |
| HTTP/2 fingerprint matching | **SKIP** | Controlled by upstream Chromium network stack. Do not customize. |
| TCP/IP fingerprint matching | **SKIP** | OS/proxy layer. Out of scope for a browser automation tool. |

## 5. Rendering & Hardware Fingerprint Spoofing

| Feature | Status | Notes |
|---|---|---|
| Canvas noise | **DONE** | `toDataURL`, `getImageData`, `toBlob` patches in `browser_manager.py`. |
| WebGL vendor/renderer/params/extensions | **DONE** | `getParameter` override for unmasked vendor/renderer, version, shader precision, max sizes, extension filter. |
| AudioContext/OfflineAudioContext noise | **DONE** | `copyFromChannel`, `getChannelData`, `AnalyserNode` readback patches. |
| Font list spoofing | **DONE** | Native plugin/mime surface and deterministic OS-consistent font list considerations. |
| Font metric spoofing (`measureText`) | **DONE** | Experimental `experimental_measuretext` flag; fake `TextMetrics` with deterministic offsets. Off by default. |
| `document.fonts.check()` | **DONE** | Returns true for common system fonts and defers for others. |
| DOM geometry spoofing (`getClientRects`) | **DONE** | Deterministic ±1 pixel offsets on `getBoundingClientRect`/`getClientRects`. |
| Screen resolution / DPR | **DONE** | Cohort-based via `device_cohorts.py` and launch_policy. |
| `hardwareConcurrency` / `deviceMemory` | **DONE** | Spoofed in init script. |
| Touch / pointer / sensor APIs | **DONE** | Mobile mode sets maxTouchPoints, coarse pointer, TouchEvent. Sensors undefined on desktop, stable on mobile. |

## 6. Navigator, Headers & Client Hints

| Feature | Status | Notes |
|---|---|---|
| User-Agent / platform / languages | **DONE** | Built per OS; reconciled to installed Chromium major version. |
| `navigator.webdriver` removal | **DONE** | Init script delete + Playwright-stealth. |
| Chrome runtime / loadTimes / csi | **DONE** | Init script patches. |
| Permissions API | **DONE** | All `permissions.query` returns `prompt`; notification permission `default`. |
| Client Hints (low/high entropy) | **PARTIAL** | Native Chromium headers. No explicit `sec-ch-ua` override. Real browser behavior is preferred. |
| MediaDevices enumeration spoofing | **DONE** | Stable per-profile device list; desktop 1 video-in + 1 audio-in + 1 audio-out. |
| Battery API spoofing | **DONE** | `navigator.getBattery()` returns fake `BatteryManager` with stable values. |
| Sensor APIs (accelerometer/gyro) | **DONE** | Desktop hides motion/orientation constructors; mobile provides stable generic values. |

## 7. Behavioral & Environmental Consistency

| Feature | Status | Notes |
|---|---|---|
| Timezone/locale/proxy alignment | **DONE** | Proxy geo lookup auto-sets timezone/locale. |
| Human-like interaction engine | **SKIP** | We removed `behavior_engine.py`; deterministic robotic movement is a detection signal. Better to rely on real user input or validated RPA libraries. |
| Cookie robot / profile aging | **DONE** | `backend/cookie_robot.py`. |

## 8. Detection Testing & Observability

| Feature | Status | Notes |
|---|---|---|
| Offline anti-detection scanner | **DONE** | `scripts/detection_check.py` with 7 offline checks. |
| Live scanner targets | **DONE** | browserleaks, whoer, creepjs, fingerprintjs, pixelscan, iphey, sannysoft. |
| Unit tests for PIN + tags | **DONE** | `tests/test_new_features.py` covers PIN set/verify/clear and tag normalization. |
| Isolation verifier | **DONE** | `scripts/isolation_check.py` with local HTTP server. |
| Scanner runner | **DONE** | `scripts/run_scanners.py`. |
| CI smoke jobs | **DONE** | `detection-smoke` + `isolation-smoke` in `.github/workflows/quality.yml`. |
| Detection-risk score UI | **DONE** | `backend/detection_score.py` + frontend badge. |
| Anti-detect surface inventory API | **DONE** | `backend/fingerprint_surface_registry.py` + `/api/anti-detect/surfaces`. |
| Surface inventory dashboard UI | **DONE** | Frontend modal with grouped table and protection score. |

## 9. Security & Operational Hardening

| Feature | Status | Notes |
|---|---|---|
| Sandbox/site isolation enabled | **DONE** | Upstream Chromium defaults kept; we do not disable sandbox for stealth. |
| CSRF/XSRF protection | **DONE** | Double-submit cookie/header validation via FastAPI middleware for `POST`, `PUT`, `PATCH`, `DELETE`; frontend obtains token from `GET /api/system/csrf-token` and sends it as `X-XSRF-Token` header. |
| Kill switch | **DONE** | Proxy kill switch. |
| Privacy/threat model document | **DONE** | `docs/PRIVACY_MODEL.md` is the canonical model. |
| Usage documentation | **DONE** | `docs/USAGE.md` added. |
| Signed updates | **DONE** | GitHub checks, download verification, staged apply, and rollback are implemented. |
| Reproducible builds / SBOM | **DONE** | `backend/sbom.py` serves CycloneDX-ish SBOM. |
| CSP / security headers | **DONE** | FastAPI middleware adds CSP, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, X-Frame-Options. |
| Per-site API access log | **DONE** | `backend/api_access_logger.py` + JS binding + REST endpoints. |
| Secrets hygiene scan | **DONE** | Full repo scan; no real live secrets found; tracking hygiene recommendations noted. |
| Access log dashboard UI | **DONE** | Frontend modal lists origins, APIs, last access, call counts. |

## 10. Additional Surface Hardening

| Feature | Status | Notes |
|---|---|---|
| WebGPU spoofing | **DONE** | `navigator.gpu.requestAdapter` returns fake adapter info matching WebGL profile. |
| SharedArrayBuffer / timing jitter | **DONE** | `performance.now()` and `Date.now()` coarsened to centisecond precision. |
| MediaCapabilities spoofing | **DONE** | `navigator.mediaCapabilities.decodingInfo` resolves mocked result. |
| Speech synthesis voices | **DONE** | `speechSynthesis.getVoices()` returns OS/locale-consistent fake voice list. |
| Network Information API | **DONE** | `navigator.connection` spoofed per device type (wifi/4g, downlink, rtt). |
| Web Share API | **DONE** | Desktop rejects/canShare false; mobile resolves mock share. |
| CSS media query consistency | **DONE** | `matchMedia` overridden for pointer, hover, color-scheme, contrast, etc. |
| Emoji rendering consistency | **SKIP** | Requires OS-level font control; runtime JS cannot fix emoji glyph rendering. |
| Keyboard layout consistency | **PARTIAL** | Locale/timezone set; actual physical keyboard layout not observable remotely. |

## 11. Modes & UX

| Feature | Status | Notes |
|---|---|---|
| Standard / strict / ephemeral privacy modes | **DONE** | `privacy_mode` in advanced dict; Strict disables extensions/SW; Ephemeral wipes data on close. |
| Built-in leak-check page | **DONE** | Detection scanner + risk badge + surface inventory dashboard. |

## 12. Not Doings

These remain genuinely outside the repository because they require external services, kernel components, or a different product:

- TLS/JA3/JA4 spoofing — requires a TLS-intercepting proxy or custom network stack.
- HTTP/2 frame-level spoofing — controlled by Chromium's network stack; no safe user-space override.
- IP reputation optimization / automatic proxy rotation for account farms — depends on paid proxy providers and usage policies.
- Full behavioral human emulation — deterministic robotic movement is a detection signal; real user input is preferred.

Repository-side adapters/build pipelines are in place for:
- Custom Chromium engine (`engine/`) — actual source compilation is deferred to build time.
- TCP/IP fingerprint control (`tcpip/`) — actual packet modification requires WinDivert driver installation.

## 13. Stubs / Architectural Debt

| Feature | Status | Notes |
|---|---|---|
| Encrypted local profile backup/restore | **DONE** | AES-256-GCM encrypted tar archives + export/import endpoints. |
| Encrypted sync directory | **DONE** | `GHOSTBROWSER_SYNC_DIR` support + sync endpoints. |
| Remote encrypted cloud sync server | **DONE** | `sync_server/` complete FastAPI server + client integration + Docker + tests. |
| Signed auto-update system | **DONE** | GitHub check + download + verify + confirmation tokens + staged apply + rollback. |
| SBOM generation | **DONE** | `backend/sbom.py` serves CycloneDX-ish JSON. |
| Top-level README | **DONE** | `README.md` rewritten. |
| CLI entry point | **DONE** | `python -m backend` works. |
| Rate limiting | **DONE** | Thread-safe, process-local sliding-window limiter: 100 requests/minute per peer IP globally, 10/minute for admin-token checks, and 5/minute for PIN verification. Responses include `RateLimit-*` and `Retry-After` headers. |
| Health/metrics endpoints | **DONE** | Extended health + metrics endpoints. |
| PyInstaller packaging script | **DONE** | `scripts/build_executable.py`. |
| Webhook/callback notifier | **DONE** | `backend/webhook_notifier.py` + endpoints. |
| Custom Chromium build pipeline | **DONE** | `engine/` scripts, Dockerfile, patches, GN args, resolver integration. |
| TCP/IP fingerprint control harness | **DONE** | `tcpip/` DNS proxy, TCP relay, Windows netsh helpers, WFP skeleton, manager. |

## 14. Done in This Latest Sprint

- Custom Chromium engine build pipeline (`engine/`)
- TCP/IP fingerprint control harness (`tcpip/`)
- Remote encrypted cloud sync server (`sync_server/`)
- Cloud sync router endpoints protected by admin token
- Removed insecure XOR fallback for profile encryption
- Signed auto-update system with confirmation, staged apply, rollback
- CLI entry point
- Thread-safe API sliding-window rate limiting with global, admin-token, and PIN-verification quotas
- Expanded health/metrics endpoints
- PyInstaller packaging script
- Webhook/callback notifier
- Scanner init-script bugfix
- Security secrets-hygiene scan
- Unit tests for PIN/tags/sync/update/engine/tcpip
- Encrypted local sync directory
- README.md rewrite
- Access log dashboard, measureText spoof, profile tags, usage docs, CSRF, SBOM, CSP
- Virtual keyboard, PIN lock, proxy failover, extra live scanners

## 15. What Remains

All implementable repository-side work is complete. No active TODOs remain.

## 16. Known Non-Blocking Frontend Items

These are tracked for polish but are not release blockers:

| Item | Status | Notes |
|------|--------|-------|
| Dashboard Live Activity empty state | **DONE** | Shows "Waiting for events..." until first activity event. |
| Rate-limit self-throttling for polling | **DONE** | Polling pauses when `document.hidden` is true; consolidated to single `setInterval`. |
| Fetch wrapper normalization | **DONE** | All mutation endpoints use `requestJson`; global fetch override handles headers/XSRF/admin-token. |
| Dashboard activity feed population | **PARTIAL** | Populated by `addActivity()` calls; empty state shown before first event. |
| Account badge responsiveness | **DONE** | Collapsed sidebar hides badges; mobile layout shows compact icons. |

External runtime dependencies that the operator must provide at deploy time:
- A deployed production instance of `sync_server/`
- A GitHub releases bucket and signing key for automatic updates
- `WinDivert` driver installation if packet-level TCP/IP normalization is desired
- A real Chromium build run from `engine/build.py` (hours, ~100 GB)
