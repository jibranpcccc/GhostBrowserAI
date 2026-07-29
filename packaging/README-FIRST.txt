GHOSTBROWSER PORTABLE PACKAGE

1. Extract the entire ZIP to a normal writable folder.
2. Double-click Install-GhostBrowser.bat.
3. Optionally select your own Cloudflare account text file when prompted.
4. Keep the extracted folder together; Chromium is bundled inside it.

Account file format, one account per line:
ACCOUNT_ID,API_TOKEN

No Python installation is required.

SECURITY
- Private Cloudflare tokens are not shipped in this package.
- Imported tokens are encrypted with Windows DPAPI for the current Windows user.
- Never distribute a ZIP containing your plaintext account file.
