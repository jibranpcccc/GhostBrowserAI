# Chromium Engine Lifecycle & Compatibility Policy

GhostBrowser AI integrates directly with modern Chromium engines. To ensure strict coherence between JavaScript Client Hints, HTTP headers, and graphics subsystems, browser engine versions are detected dynamically at runtime.

## Supported Engine Releases

1. **Current Production Releases**: Chromium `131` through `151`+
   - Fully compatible with modern Client Hints, Partitioned Cookies, and WebGPU standards.
2. **Legacy / Development Targets**: Chromium `130`
   - Permitted in automated test suites and development environments via `GHOSTBROWSER_TEST_ENV=1` or `GHOSTBROWSER_ALLOW_LEGACY_CHROMIUM=1`.
   - Obsolete for live commercial production without the explicit development flag.
3. **Obsolete / Unsupported Releases**: Chromium `< 130`
   - Strictly rejected at launch to prevent version-spoofing and security regressions.

## Dynamic Engine Resolution

GhostBrowser AI resolves the exact browser binary dynamically:
1. `GHOSTBROWSER_CHROMIUM_BINARY` environment variable (if user points to a specific hardened or custom binary).
2. Packaged bundle directory (in frozen PyInstaller builds).
3. Playwright browser cache (`ms-playwright/chromium-*`).

The full version (`major.minor.build.patch`) is extracted from the executable's metadata and used by `BrowserVersion` to ensure 100% build-level coherence across DOM and network headers.
