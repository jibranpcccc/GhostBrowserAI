# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-10
### Added
- **AI Zero-Leak Profile Generation:** Full integration with Kimi AI via Cloudflare to generate 100% unique hardware fingerprints (Canvas, Audio, WebGL, WebRTC, Fonts).
- **Racing Proxy Infrastructure:** Load balancing across Cloudflare accounts to bypass AI rate limits.
- **Automated Validation (`ai_leak_scanner.py`):** Internal JS evaluation mimicking CreepJS & Sannysoft to vet profiles before saving.
- **Headless Cookie Warming:** Newly created profiles automatically visit Google, Reddit, Quora, Wikipedia, and Amazon headlessly to build cookies and natural history.
- **Proxy Support & DNS Leak Protection:** Native Playwright proxy routing with forced timezone/geolocation matching.
- **Local API Backend (FastAPI):** Complete asynchronous endpoint system for profile CRUD and bulk generation.
- **Frontend Dashboard:** Dark-mode, glassmorphism UI to manage profiles.

### Fixed
- **Playwright Window Positioning Bug:** Fixed issue where Chrome launched off-screen (-32000, -32000). Browser windows now open centered on the primary monitor.
- **UI Lag:** Instant UI updates when deleting profiles to prevent the frontend from hanging while waiting for backend operations.
- **State Synchronization:** The dashboard automatically resets a profile's state to "Stopped" when the browser window is manually closed by the user.

### Removed
- Extraneous test scripts, debug screenshots, and scratch files to ensure a production-ready repository.
