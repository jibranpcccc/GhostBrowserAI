# Browser Extensions Directory

This folder is dynamically scanned by the Browser Manager when a Playwright profile is launched. 

## How to add Extensions
1. Download the `.crx` file or zip of the extension (e.g., a Captcha Solver).
2. Unzip the file so that you have a folder containing the `manifest.json`.
3. Drop that folder directly into this directory!

**Example:**
- `backend/extensions/captcha_solver/manifest.json`
- `backend/extensions/cloudflare_bypass/manifest.json`

The system will automatically load them globally into every browser profile that boots up.
