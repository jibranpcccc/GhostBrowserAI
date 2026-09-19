# Chromium Engine Lifecycle & Compatibility Policy

GhostBrowser AI integrates directly with modern Chromium engines. To ensure strict coherence between JavaScript Client Hints, HTTP headers, and graphics subsystems, browser engine versions are detected dynamically at runtime using the authoritative `EngineIdentity` resolver.

## Lifecycle Tier Separation (Current as of September 2026)

The project separates engine versions into explicit lifecycle categories defined in `backend/chromium_compat.json`:

1. **Minimum Supported Production Major**:
   - Minimum production baseline: `140`.
   - Production releases require modern Client Hints full-version list specifications, partitioned cookies (`CHIPS`), and WebGPU APIs.

2. **Explicitly Tested Production Majors**:
   - Major `149` (Chromium `149.0.7827.55`, bundled via Playwright `1.61.0`).
   - Each production release candidate is verified against an exact executable SHA-256 and prototype descriptor clean baseline.

3. **Supported Production Majors**:
   - Explicitly listed in policy: `[140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151]`.
   - Each major must pass the full regression and compatibility gate. Support is never inferred merely because a number lies between bounds.

4. **Legacy Test Majors**:
   - Majors `[130, 131, 132, 133, 134, 135]`.
   - Permitted exclusively in isolated test harnesses via `GHOSTBROWSER_TEST_ENV=1` or `GHOSTBROWSER_ALLOW_LEGACY_CHROMIUM=1`.
   - Strictly forbidden from passing production release compatibility gates.

5. **Unsupported / Obsolete Releases**:
   - Any engine version `< 130` or any unapproved major not in policy.
   - Fails closed at launch and build time.

## Authoritative Engine Identity & Resolution

GhostBrowser AI resolves the exact browser binary once through `backend.engine_resolver.get_engine_identity()`:
1. `GHOSTBROWSER_CHROMIUM_BINARY` environment override (if pointing to a custom engine).
2. Frozen distribution bundle (`dist/GhostBrowser/playwright-browsers/chrome-win64/chrome.exe`).
3. Managed Playwright browser runtime (`ms-playwright/chromium-*`).
4. Local system browser fallback (if no bundled or Playwright binary exists).

The resulting `EngineIdentity` records:
- `executable_path`: absolute filesystem path.
- `sha256`: cryptographic checksum of the executable.
- `exact_version`: full `major.minor.build.patch` string (e.g. `149.0.7827.55`).
- `major_version`: integer major release.
- `source`: provenance tier.
- `resolution_timestamp`: ISO 8601 UTC timestamp.

All downstream systems—including browser launching, clean baseline capture, brutal audits, PyInstaller packaging, and release auditing—consume this identical identity object.
