# GhostBrowser Roadmap

This document lists large infrastructure features that are intentionally **not yet implemented**. They are tracked here so users and contributors know what is planned without mistaking any placeholder code for a complete feature.

## Planned Infrastructure Features

### 1. Encrypted Cloud Sync
- Client-side AES-256 encryption of profile data before it leaves the machine.
- Per-device asymmetric key pairs used to exchange profile encryption keys securely.
- Key revocation flow for lost or retired devices.
- Conflict resolution for profile data updated on multiple devices.

### 2. Signed / TUF-Style Update System
- The framework (The Update Framework) inspired metadata roles: root, targets, snapshot, timestamp.
- Offline root key rotation and threshold signatures.
- Binary signature verification before installation.
- Rollback protection via versioned metadata.

### 3. Reproducible Builds + Software Bill of Materials (SBOM)
- Deterministic build environment pinning all toolchain versions.
- Per-release SBOM listing dependencies, licenses, and provenance.
- Integration with the signed update system so only reproducible artifacts are shipped.

### 4. Font Metric Spoofing and Emoji Rendering Consistency
- Override `CanvasRenderingContext2D.measureText()` to return consistent metrics across platforms.
- Spoof `document.fonts.check()` consistently per profile.
- Emoji glyph alignment controls to reduce cross-platform entropy (limited by OS-level font rendering).

### 5. Keylogger-Resistance On-Screen Keyboard
- Optional virtual keyboard UI for sensitive input fields.
- Input delivered to Chromium without relying on low-level OS keyboard events exposed to potential keyloggers.
- Designed as an opt-in accessibility/security feature, not a complete anti-malware guarantee.

## See Also

- `PLAN.md` — current sprint status and smaller TODO items.
- `docs/THREAT_MODEL.md` — trust boundaries and threat scenarios, including future cloud sync.
- `backend/cloud_sync.py` (remote sync client + reference server), `backend/update_manager.py` (GitHub checks, signed downloads, staged apply/rollback), and `backend/sbom.py` (requirements-based CycloneDX-ish SBOM) are implemented repository-side; production deployment of the remote server and update distribution is an external operator task.
