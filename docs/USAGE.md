# GhostBrowser Usage Guide

A practical guide for running the anti-detect browser automation stack, creating protected profiles, and managing proxies and scans.

---

## 1. Quick Start

### Run the backend

Requires Python and the project virtual environment:

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Or run the backend as a module directly:

```bash
python -m backend --port 8000
```

For a stable background server:

```bash
python run_server.py
```

or on Windows use the launcher:

```powershell
.\Start-GhostBrowser.bat
```

The management UI and API will be available at `http://127.0.0.1:8000`. The
interactive API schema is available at `http://127.0.0.1:8000/docs`.

### Open the frontend

Open `http://127.0.0.1:8000/` after starting the backend. The backend serves
the bundled `frontend/` files; opening `frontend/index.html` directly is not a
supported configuration.

---

## 2. Creating a Profile

A profile stores a browser fingerprint and identity configuration.

### Create a profile

1. Go to **Profiles → New Profile** in the web UI.
2. Enter a name and optional metadata.
3. Select the browser engine (Chromium/WebKit-based).
4. Click **Generate Fingerprint** to randomize a realistic device fingerprint.

### AI-generated profile

Click **AI Generate** to produce a complete, coherent identity:

- OS, screen resolution, fonts, timezone, locale
- Browser version and vendor strings
- WebGL renderer, canvas noise seed
- Audio context fingerprint, media device labels
- Battery, motion, and sensor values when applicable

### Advanced options

- **Custom headers**: Add fixed HTTP headers.
- **Viewport override**: Force a specific window size.
- **Geolocation**: Set exact lat/long or auto-match to proxy.
- **Do-not-track headers**: Toggle privacy signals.

### Privacy modes

| Mode        | Behavior |
|-------------|----------|
| Standard    | Normal browsing, persistent cookies and storage. |
| Strict      | Strict ephemeral storage; cookies and local data are discarded when the session ends. |
| Ephemeral   | Fully disposable session; nothing persists locally after close. |

### PIN lock

Enable **PIN Lock** to require a 4-6 digit PIN before launching the profile. The PIN is stored as a hash; it is used only to unlock the profile and does not encrypt stored browser data.

---

## 3. Proxy Setup

Proxies are stored per-profile or globally in the proxy pool.

### Add a proxy

Use the **Proxies** page or the API:

```bash
curl -X POST http://127.0.0.1:8000/api/proxies \
  -H "X-Admin-Token: <admin-token>" \
  -H "X-XSRF-Token: <csrf-token>" \
  -b "XSRF-TOKEN=<csrf-token>" \
  -H "Content-Type: application/json" \
  -d '{"proxies":[{"server":"http://proxy.example.com:8080","username":"user","password":"pass"}]}'
```

### Health check

Each proxy has a **Test Health** button that checks connectivity and response time. Unhealthy proxies are marked red and are not used for new launches until they recover.

### Geo / timezone auto-match

When attaching a proxy to a profile, enable **Match Location**. The profile timezone, geolocation, and locale are automatically aligned with the proxy's egress IP.

### Kill-switch

Enable **Kill Switch** on a profile to abort the browser session immediately if the proxy disconnects or fails a mid-session health check. Use this for sensitive automation work.

---

## 4. Launching Profiles

### Launch a profile

In the UI:

1. Go to **Profiles** and click **Launch** on the desired profile.
2. Select **Headed** or **Headless** mode.
3. If PIN lock is enabled, enter the PIN.
4. The browser opens with the configured fingerprint and proxy.

### From the command line

`backend/launch_profile.py` is no longer provided. Launch through the UI or
the supported API instead:

```bash
curl -X POST http://127.0.0.1:8000/api/profiles/<uuid>/launch \
  -H "X-Admin-Token: <admin-token>" \
  -H "X-XSRF-Token: <csrf-token>" \
  -b "XSRF-TOKEN=<csrf-token>"
```

Obtain a CSRF token and cookie first from `GET /api/system/csrf-token`. All
protected API endpoints require `X-Admin-Token`; unsafe API requests also
require the matching CSRF cookie and `X-XSRF-Token` header.

### PIN prompt

PIN-locked profiles are managed through the UI; there is no standalone profile
launch CLI.

### Virtual keyboard

Enable **Virtual Keyboard** during launch for text input when the host system keyboard layout should be hidden from web sensors.

---

## 5. Anti-Detect Features

GhostBrowser spoofs or normalizes many browser surfaces to reduce fingerprint linking.

### Spoofed surfaces

- **Canvas**: Per-profile noise hash applied to canvas readbacks.
- **WebGL**: Vendor, renderer, parameters, and extensions normalized or randomized.
- **Audio**: AudioContext oscillator values are masked consistently per profile.
- **Media devices**: Device count, labels, and IDs are spoofed.
- **Sensors**: Accelerometer, gyroscope, and motion event values are constrained.
- **Battery**: Battery status is reported as a static or slowly-changing value.
- **Fonts**: Installed font list is filtered to a realistic subset.
- **Navigator**: User-agent, platform, language, cores, memory, and touch support.
- **Permissions**: Query overrides for notifications, camera, microphone, clipboard.
- **Screen / viewport**: Screen size, color depth, pixel ratio.
- **WebRTC**: Public IP leak prevention via disabled or proxy-routed modes.
- **Plugins / MIME types**: Minimal, consistent plugin list.

### View the Surface Inventory dashboard

Use the profile fingerprint and scan API endpoints to inspect a profile's
configured surfaces and validation result:

- Expected spoofed value
- Real host value (for comparison)
- Risk flag if the surface is inconsistent with the proxy location

`GET /api/profiles/<id>/fingerprint` returns the configured fingerprint, and
`GET /api/profiles/<id>/scan` runs the validation scan. Both require an admin
token.

---

## 6. Scanning and Testing

Validate profiles before using them for work.

### Run all scanners

```bash
python scripts/run_scanners.py
```

This runs the full baseline scanner suite and writes reports to `logs/scanner_reports/`.

### Live detection check

Test a running browser session:

```bash
python scripts/detection_check.py --live
```

For a specific profile:

```bash
python scripts/detection_check.py --live --profile-id <uuid>
```

### Review results

Reports include a pass/fail summary and risk score per surface. Re-run the relevant scanner or adjust the profile when a surface fails.

---

## 7. Backup, Import, and Export

### Encrypted local backup

1. Go to **Settings → Backup**.
2. Choose **Export Backup**.
3. Enter a backup password. The archive is encrypted using the password you provide.

Use the supported profile-transfer API: `POST /api/profiles/export` exports
profiles, with encryption and safe-mode options in the request body.

### Restore from backup

Use `POST /api/profiles/import` with the export payload. Set `overwrite` only
when replacing matching profiles is intended.

Restoring overwrites matching profiles in the current data directory. Make a fresh backup first.

### Profile transfer

Export a single profile for transfer:

Call `POST /api/profiles/export` with `profile_ids` containing the profile ID.

Import a transferred profile:

Import it through `POST /api/profiles/import`.

Transferred profiles keep fingerprints, proxies, and metadata but do not include session cookies unless **Include session data** was selected.

---

## 8. Team Management

The API supports role-based access control (RBAC) for team accounts.

### Default roles

| Role   | Capabilities |
|--------|--------------|
| viewer | Read profiles, proxies, reports, dashboards. |
| operator | Create and launch profiles, run scans, manage own proxies. |
| admin | Full access including team-member management and global settings. |

### Team endpoints

Team-management endpoints require the configured admin token in the
`X-Admin-Token` header.

- `GET /api/team/members` — list team members (admin)
- `POST /api/team/members` — invite a team member (admin)
- `PUT /api/team/members/{id}/role` — change a member's role (admin)

---

## 9. Packaging as a Standalone Executable

GhostBrowser can be packaged with PyInstaller into a single executable or directory for distribution.

### Requirements

PyInstaller is listed as a dev/build extra in `requirements.txt`:

```bash
pip install pyinstaller
```

### Build

Run the packaging script from the project root:

```bash
python scripts/build_executable.py --onefile --console
```

For a directory bundle instead of a single file:

```bash
python scripts/build_executable.py --onedir --console
```

For a windowed build (no console window):

```bash
python scripts/build_executable.py --onefile --windowed
```

The default output directory is `dist/ghostbrowser`. Change it with `--output-dir`:

```bash
python scripts/build_executable.py --onefile --output-dir build/release
```

### Included files

The build includes the `backend` package and the following extra data files bundled alongside the executable:

- `frontend/` static files
- `.env.example`
- `VERSION`
- `requirements.txt`

### Skipping gracefully

If PyInstaller is not installed, the script prints the install command and exits without error so it can be safely used in environments where the build step is optional.

---

## 10. Troubleshooting

### Proxy failure

**Symptom**: Browser fails to start or pages do not load.

1. In **Proxies**, click **Test Health**.
2. Verify host, port, username, and password.
3. Confirm the proxy scheme matches (`http`, `https`, `socks5`).
4. Try disabling the kill-switch to see if the proxy is timing out.
5. If using geo-match, make sure the proxy IP has a resolvable country/timezone.

### Profile not launching

**Symptom**: Nothing opens, or the launcher exits immediately.

1. Check that the backend is running: `curl http://127.0.0.1:8000/api/system/health`.
2. Verify the profile ID exists in **Profiles**.
3. Ensure the browser executable path is correct in settings.
4. Try launching with `--headed` first; some profiles fail in headless due to media flags.
5. Review `logs/server_stderr.log` for the error trace.

### Scanner failures

**Symptom**: `run_scanners.py` reports inconsistent surfaces.

1. Re-generate the profile fingerprint if the profile is old.
2. Match the proxy geo/timezone to the profile.
3. Run `scripts/detection_check.py --live` with a real session.
4. Compare the output against the Surface Inventory dashboard.
5. Check `logs/scanner_reports/` for the full per-surface breakdown.

### PIN not accepted

- PIN lock compares against the stored hash. If forgotten, reset the PIN from the profile settings. Resetting the PIN does not affect browser data.

### Need more help

Open an issue with:

- Backend and frontend versions (`VERSION` file)
- Relevant log files from `logs/`
 Steps to reproduce
