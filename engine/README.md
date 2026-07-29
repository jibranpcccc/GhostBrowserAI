# GhostBrowser Custom Chromium Engine Build

This directory contains the repository-side orchestration for building a custom Chromium engine fork tailored for GhostBrowser. No prebuilt Chromium binary is committed here; only scripts, build configuration, and example patches live in the repo.

## Why a custom build is desirable

GhostBrowser aims to present a coherent, human-like browser surface to websites and anti-automation systems. A stock Chromium binary (and even the Playwright-managed Chromium) includes several signals that are hard to remove completely at runtime:

- **Automation markers** — `navigator.webdriver`, `window.chrome.loadTimes`/`csi` stubs, and the `--enable-automation` flag leave detectable fingerprints.
- **WebRTC path exposure** — Default Chromium allows UDP traffic outside the configured proxy, which can leak local IP and network topology information during WebRTC negotiation.
- **Fingerprint surfaces** — Bundled components such as NaCl, Widevine, and internal PDF plugins differ across Chromium builds and can be used as cohorting signals.

By maintaining our own source-level fork we can:

1. Remove or mask automation markers at the renderer level.
2. Harden WebRTC so proxy-routed UDP is the default; non-proxied UDP is disabled.
3. Own the fingerprint surfaces by controlling enabled components, branding, and compile-time defaults.
4. Reconcile the reported Chromium version with the exact binary we ship, eliminating version-mismatch leaks.

The build is deterministic enough for CI and is packaged in a Docker container so anyone can reproduce it.

## High-level steps

1. **Install `depot_tools`** — Chromium's build tooling (fetch, gclient, gn, ninja, autoninja). `engine/build.py` will download a local copy automatically if `depot_tools` is not already on `PATH`.
2. **Fetch Chromium source** — Use `fetch chromium` (managed by `depot_tools`).
3. **Checkout a pinned version** — Controlled by the `GHOSTBROWSER_CHROMIUM_VERSION` environment variable.
4. **Apply GhostBrowser patches** — Series files under `engine/patches/` (e.g. disable automation marker, harden WebRTC UDP policy).
5. **Generate build args** — `build.py` writes `out/Release/args.gn` from `engine/gn/args.gn`.
6. **Generate build files and compile** — `gn gen out/Release` and `autoninja chrome`.
7. **Integrate the binary** — Set `GHOSTBROWSER_CHROMIUM_BINARY` to the resulting executable; GhostBrowser will prefer it over the Playwright-managed browser.

Example manual run:

```bash
# On Linux / inside the Docker container
python3 engine/build.py --fetch --sync --apply-patches --build
# The final path is printed at the end of the build.
```

## Docker usage

A pre-configured Ubuntu build container is provided:

```bash
docker build -t ghostbrowser/engine:latest engine/
docker run --rm \
  -v "$(pwd)/engine/src:/engine/src" \
  -e GHOSTBROWSER_CHROMIUM_VERSION=132.0.6834.110 \
  ghostbrowser/engine:latest \
  python3 /engine/build.py --sync --apply-patches --build
```

The container clones `depot_tools` and installs all native build dependencies; the Chromium source tree is expected to be mounted at `/engine/src` so the result persists on the host.

## Disk and time estimates

| Step | Disk | Time (modern workstation) |
|---|---|---|
| `depot_tools` only | ~1 GB | < 5 minutes |
| Fetch + sync Chromium source and dependencies | ~25–35 GB | 20–60 minutes |
| Full Release build (`autoninja chrome`) | ~100 GB total including intermediates | 1–4 hours |
| Incremental rebuild after patch change | + a few GB | 5–30 minutes |

Actual times vary heavily with core count, RAM, and I/O speed. Building inside WSL2 or a native Linux host is strongly recommended; Windows filesystem builds are significantly slower.

## Useful links

- [Chromium Build Instructions](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md)
- [depot_tools tutorial](https://commondatastorage.googleapis.com/chrome-infra-docs/flat/depot_tools/docs/html/depot_tools_tutorial.html)
- [GN build configuration](https://gn.googlesource.com/gn/+/main/docs/quick_start.md)
- [Chromium version/branches](https://chromiumdash.appspot.com/branches)

## `GHOSTBROWSER_CHROMIUM_BINARY` integration

After a successful build, set the environment variable to the platform-specific executable:

- Linux: `out/Release/chrome`
- macOS: `out/Release/Chromium.app/Contents/MacOS/Chromium`
- Windows: `out/Release/chrome.exe`

GhostBrowser's resolver uses the variable first and falls back to the Playwright-managed Chromium if the variable is unset or the file does not exist. The path should be absolute and the binary must be executable.
