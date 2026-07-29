# Font & Plugin Privacy Policy

## Overview

This document describes how GhostBrowser handles font enumeration, plugin/MIME-type exposure, and extension isolation.

## Font Enumeration

### Current Behavior

GhostBrowser does **not** fabricate or modify the font list returned by:

- `document.fonts` (FontFaceSet)
- CSS `@font-face` fallback enumeration
- Flash-based font detection (Flash is not supported)

The browser returns the host OS's installed fonts. This is a conscious design choice:

1. **Fabricated font lists are detectable** — Inconsistencies between the declared OS, user agent, and available fonts are a strong automation signal to anti-fingerprinting services
2. **Font enumeration has legitimate uses** — Website rendering, accessibility features, and design tools require accurate font information
3. **Font removal/modification is OS-dependent** — There is no cross-platform API to selectively expose a subset of installed fonts

### Privacy Mode

When `privacy_mode = "high"` is enabled on a profile, GhostBrowser:

1. Adds `--disable-font-subpixel-positioning` launch flag
2. Enables font-cache isolation per profile directory (avoids cross-profile font hinting correlation)
3. Does NOT modify the enumerated font list

## Plugin & MIME-Type Exposure

### Current Behavior

GhostBrowser does **not** fabricate or modify:

- `navigator.plugins` — Returns real installed plugins
- `navigator.mimeTypes` — Returns real registered MIME types
- Plugin enumeration order is determined by Chromium's built-in plugin list

### Privacy Mode

When `privacy_mode = "high"` is enabled:

1. Only Chromium's built-in plugins (PDF Viewer, Chrome PDF Plugin, Native Client) are exposed
2. Third-party plugins registered with the system are filtered out
3. This is achieved through profile-level content settings rather than JavaScript overrides

## Extension Isolation

### Current Behavior

GhostBrowser validates extensions at profile launch:

1. Extensions are loaded from the `backend/extensions/` directory
2. Each extension's `manifest.json` is checked for valid JSON and broad permissions (`<all_urls>`, `*://*/*`)
3. Extensions with broad permissions trigger a security warning in the log
4. The `dummy_extension` test fixture is excluded in production environments

### Extension Permissions

| Permission Level | Behavior | Warning |
|-----------------|----------|---------|
| `host_permissions` scoped to specific origins | Allowed | None |
| `<all_urls>` or `*://*/*` | Allowed with log warning | Security warning logged |
| `nativeMessaging` | Depends on specific native host | Logged at info level |

## Recommended Configuration

For maximum privacy:

1. Use only the extensions bundled with GhostBrowser (no third-party extensions)
2. Set `privacy_mode = "high"` on profiles that visit untrusted websites
3. Avoid extensions that request `<all_urls>` permissions unless absolutely necessary
4. Periodically audit the `backend/extensions/` directory for unused extensions
