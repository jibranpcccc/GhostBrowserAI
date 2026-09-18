# GhostBrowser

A privacy-focused, anti-detect Chromium browser built on Playwright and FastAPI. Each profile runs in an isolated browser context with its own storage, fingerprint, proxy binding, and privacy settings, making it suitable for multi-account automation, research, and protected browsing.

---

## What is GhostBrowser

GhostBrowser launches isolated Chromium windows through a Playwright backend controlled by a FastAPI server. Every profile gets its own cookie jar, localStorage, IndexedDB, cache, service workers, and deterministic fingerprint seeds, so sessions cannot be linked by canvas, WebGL, audio, or storage fingerprints. The browser intentionally runs on upstream Chromium with the sandbox enabled rather than disabling security protections for stealth.

---

## Key features

- **Profile isolation** — separate `profiles_data/<id>` directories for cookies, localStorage, IndexedDB, cache, and service workers.
- **Fingerprint consistency** — canvas, WebGL vendor/renderer, audio context, fonts, screen resolution, DPR, `hardwareConcurrency`, `deviceMemory`, touch/pointer, media devices, battery, sensors, WebGPU, speech voices, and network information.
- **Native prototype inheritance** — all navigator overrides reside on `Navigator.prototype` and `Screen.prototype` with native accessor getters, leaving zero own-property footprint on instances.
- **Authoritative browser versioning** — dynamic engine detection via `BrowserVersion` model synchronizing `Sec-CH-UA`, Client Hints, and HTTP headers with zero headless leakage.
- **Deterministic session noise** — 100% stable seeded session noise across dates without day-dependent hardware jitter.
- **Network coherence & WebRTC safety** — strict mDNS and STUN reflexive mapping preventing private LAN host IP leaks.
- **Proxy management** — per-profile HTTP/HTTPS/SOCKS5 proxy binding, health checks, failover pool, geo-based timezone/locale auto-match, and kill-switch.
- **Privacy modes** — Standard, Strict, and Ephemeral browsing modes with configurable storage persistence.
- **PIN lock** — optional per-profile launch lock. New and changed PINs must be 4–6 ASCII digits and are stored as PBKDF2 hashes; existing legacy PINs remain usable for unlock until changed.
- **Virtual keyboard** — on-screen keyboard for `data-secure="true"` inputs with shift, caps, and randomized layouts.
- **Detection scanners & brutal audit** — offline anti-detect scanner, live multi-target checks, and 10-tier automated brutal torture benchmark.
- **CSRF / CSP / security headers** — FastAPI double-submit cookie CSRF protection, CSP, X-Frame-Options, Referrer-Policy, and Permissions-Policy.
- **SBOM** — CycloneDX-style software bill of materials served from `/api/sbom`.
- **Encrypted backup** — AES-256-GCM encrypted local profile export/import and full-profile transfer.

---

## Quick start

1. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. Start the backend:

   ```bash
   uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
   ```

   Or use the validated launcher:

   ```bash
   python run_server.py
   ```

3. Open GhostBrowser:

   Visit `http://127.0.0.1:8000/`. The backend serves the frontend; do not open
   `frontend/index.html` directly.

The API is available at `http://127.0.0.1:8000`; its interactive schema is at
`http://127.0.0.1:8000/docs`.

---

## Architecture

- `backend/` — FastAPI application, profile management, browser automation, proxy handling, fingerprint spoofing, security hardening, scanners, and encrypted storage.
- `frontend/` — static HTML/CSS/JS management UI.
- `scripts/` — CLI utilities for scanners, isolation checks, detection checks, backup/restore, DNS capture, and release audits.
- `docs/` — usage guide (`USAGE.md`) and privacy/threat model (`PRIVACY_MODEL.md`).

---

## Security / API Authentication

- Chromium sandbox and site isolation are left enabled; GhostBrowser does not disable security features for stealth.
- Proxy kill-switch prevents direct-connection fallback when a proxy fails.
- CSRF double-submit cookie/header validation is implemented for unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`). The frontend obtains a CSRF token from `GET /api/system/csrf-token` and sends it as `X-XSRF-Token` header.
- Content Security Policy and additional security headers are set by FastAPI middleware.
- Profile metadata is encrypted at rest with one file-based Fernet master key (`profiles_data/.master.key.dpapi`) shared by local profiles; backup archives use passphrase-derived AES-256-GCM. Cloudflare credentials use the Windows DPAPI credential store when available.
- No OS-level keystroke interception; the virtual keyboard is a UI-layer mitigation.

### Administrative API authentication

Administrative API operations are gated by a server-side token.

- Set `GHOSTBROWSER_ADMIN_TOKEN` on the server to a strong, private random value.
- Send an `X-Admin-Token: <your-secret-token>` header with all mutation requests.
- Authentication responses:
  - `503 Service Unavailable` — no token is configured on the server.
  - `401 Unauthorized` — the `X-Admin-Token` header is missing.
  - `403 Forbidden` — the supplied token is invalid.
- Health checks (`/api/system/health`), CSRF-token endpoints, and static files remain public.

---

## Testing & Adversarial Verification

GhostBrowser AI includes both offline scanners and an adversarial 10-tier Brutal Anti-Detect Torture Test suite:

### 1. Adversarial Brutal Test Suite (Levels 1–10)
Evaluates live runtime integrity across CreepJS, BrowserLeaks (Canvas, WebGL, WebGPU, WebRTC), and FingerprintJS with automated screenshot capture, DOM/Worker coherence, deterministic reload stability, network candidate leakage, and cross-profile state crossover:

```bash
python tests/run_brutal_test_suite.py
```

Outputs:
- Machine-readable telemetry: `tests/brutal_test_report.json`
- Verification screenshots: `artifacts/brutal_test_screenshots/`

### 2. Comprehensive Word Audit Report Generator
Compiles the empirical telemetry, full-fidelity screenshots, and adversarial severity matrix into an executive Microsoft Word document:

```bash
python tests/build_word_report.py
```
Output: `GhostBrowser_Brutal_AntiDetect_Audit_Report.docx`

### 3. Unit & Integration Regression Suite
Run full automated test passes:

```bash
python -m pytest tests/ -v
```

### 4. Detection Scanners & Per-Profile Checks
Run the legacy scanner suite:

```bash
python scripts/run_scanners.py
```

For per-profile live detection checks:

```bash
python scripts/detection_check.py --live --profile-id <uuid>
```

See `docs/USAGE.md` for detailed workflows.

---

## Release capabilities and remaining work

Encrypted local and remote profile sync is implemented in `backend/cloud_sync.py`; archives are encrypted client-side before upload. The repository also includes GitHub update checks, signed-download verification, staged apply/rollback support, and an SBOM endpoint. Operators remain responsible for deploying the remote sync service and update distribution. Reproducible builds, a TUF-style metadata system, and broader font-metric consistency remain future work.

---

## License

MIT License. See LICENSE for details.
