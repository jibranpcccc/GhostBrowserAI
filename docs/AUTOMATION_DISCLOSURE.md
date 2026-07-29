# Automation Disclosure Policy

## Purpose

GhostBrowser is a browser automation and profile isolation tool. This document describes how the software discloses its automated nature to websites, when it is appropriate to do so, and how users control this behavior.

## Disclosure Philosophy

GhostBrowser follows a **consent-gated, explicit automation** model:

1. Automation is always user-initiated and user-controlled
2. Browser automation signals are hidden only when the user explicitly enables stealth mode
3. The software never attempts to actively bypass anti-automation systems (Cloudflare Turnstile, Akamai, DataDome, PerimeterX, Shape Security, etc.)
4. Users are responsible for complying with each website's Terms of Service

## Automation Signals

### Signals Hidden in Stealth Mode

When stealth mode is enabled (`profile.advanced.stealth = true`), GhostBrowser hides the following automation indicators:

| Signal | How it's hidden |
|--------|----------------|
| `navigator.webdriver` | Set to `false` via CDP `Page.addScriptToEvaluateOnNewDocument` |
| CDP artifacts (`$cdc_*`, `$chrome_*` globals) | Removed via CDP evaluation at page creation |
| `window.domAutomation` | Undefined (no automation controller injected) |
| `window.domAutomationController` | Undefined (no automation controller injected) |
| `window.callPhantom` | Undefined |
| `chrome.runtime` | Hidden when Chrome extension is not active |

### Signals NOT Hidden

GhostBrowser does NOT attempt to hide:

- **TLS fingerprint** — The TLS handshake is determined by the installed Chromium/Playwright build
- **TCP/IP stack** — Network stack properties are OS-level and not scriptable
- **HTTP/2 settings** — Determined by the browser engine
- **Font enumeration** — Installed fonts reflect the host OS
- **WebGL rendering** — Results are noise-seeded but not fabricated
- **Timing precision** — `performance.now()` and `Date.now()` are not reduced below browser default
- **AudioContext fingerprint** — Noise-seeded but not fabricated
- **Battery API** — If supported, returns real battery status
- **Sensor APIs** — If supported, returns real sensor data

## User Consent

GhostBrowser requires:

1. **User initiation** — All profile launches, macro execution, and automated actions require explicit user action via the UI, CLI, or API
2. **Audit trail** — All state-changing API operations are logged with timestamp, user agent, and profile ID
3. **Rate limits** — API operations are rate-limited to prevent abuse
4. **Fail-closed** — If stealth configuration fails to apply, the browser launch is aborted

## Supported Websites

GhostBrowser does **not** maintain or distribute a list of websites where stealth mode is effective. Effectiveness depends on:

- The website's anti-automation technology stack
- The profile's fingerprint coherence and configuration
- The IP address and proxy quality
- The specific browser behavior during the session

## Compliance

Users must:

1. Comply with all applicable laws and website Terms of Service
2. Not use GhostBrowser for fraudulent activities, account abuse, or unauthorized access
3. Not use GhostBrowser to bypass rate limits, content protections, or anti-abuse systems
4. Ensure they have the right to automate interactions with each website they access

GhostBrowser developers assume no liability for misuse of the software.
