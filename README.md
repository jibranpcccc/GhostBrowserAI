# GhostBrowser

A privacy-focused, anti-detect Chromium browser built on Playwright and FastAPI. Each profile runs in an isolated browser context with its own storage, fingerprint, proxy binding, and privacy settings, making it suitable for multi-account automation, research, and protected browsing.

---

## What is GhostBrowser

GhostBrowser launches isolated Chromium windows through a Playwright backend controlled by a FastAPI server. Every profile gets its own cookie jar, localStorage, IndexedDB, cache, service workers, and deterministic fingerprint seeds, so sessions cannot be linked by canvas, WebGL, audio, or storage fingerprints. The browser intentionally runs on upstream Chromium with the sandbox enabled rather than disabling security protections for stealth.

---

## Key features

- **Profile isolation** — separate `profiles_data/<id>` directories for cookies, localStorage, IndexedDB, cache, and service workers.
- **Fingerprint spoofing** — canvas, WebGL vendor/renderer, audio context, fonts, screen resolution, DPR, `hardwareConcurrency`, `deviceMemory`, touch/pointer, media devices, battery, sensors, WebGPU, speech voices, and network information.
- **Proxy management** — per-profile HTTP/HTTPS/SOCKS5 proxy binding, health checks, failover pool, geo-based timezone/locale auto-match, and kill-switch.
- **Privacy modes** — Standard, Strict, and Ephemeral browsing modes with configurable storage persistence.
- **PIN lock** — PBKDF2-hashed PIN required before launching sensitive profiles.
- **Virtual keyboard** — on-screen keyboard for `data-secure="true"` inputs with shift, caps, and randomized layouts.
- **Detection scanners** — offline anti-detect scanner plus live checks against browserleaks, whoer, creepjs, fingerprintjs, pixelscan, iphey, and sannysoft.
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
- Profile metadata is encrypted at rest with one file-based Fernet master key (`profiles_data/.master.key`) shared by local profiles; backup archives use passphrase-derived AES-256-GCM. Cloudflare credentials use the Windows DPAPI credential store when available.
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

## Testing

Run the scanner suite:

```bash
python scripts/run_scanners.py
```

Reports are written to `logs/scanner_reports/`. For per-profile live detection checks:

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
