#!/usr/bin/env python3
"""CI-ready storage/origin isolation check between two throwaway profiles."""
import argparse
import asyncio
import http.server
import json
import os
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
import traceback


WRITER_HTML = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<script>
(async () => {
  const PROFILE = new URLSearchParams(location.search).get("p") || "?";
  const out = {};
  try { document.cookie = "iso=" + PROFILE + "; path=/; expires=Fri, 31 Dec 9999 23:59:59 GMT"; out.cookie = document.cookie || ""; } catch (e) { out.cookie = "err:" + e.name; }
  try { localStorage.setItem("iso", PROFILE); out.local = localStorage.getItem("iso"); } catch (e) { out.local = "err:" + e.name; }
  try { sessionStorage.setItem("iso", PROFILE); out.session = sessionStorage.getItem("iso"); } catch (e) { out.session = "err:" + e.name; }
  try {
    const db = await new Promise((res, rej) => {
      const req = indexedDB.open("iso-db", 1);
      req.onupgradeneeded = () => { if (!req.result.objectStoreNames.contains("kv")) req.result.createObjectStore("kv", { keyPath: "id" }); };
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });
    await new Promise((res, rej) => {
      const putReq = db.transaction(["kv"], "readwrite").objectStore("kv").put({ id: "rec", profile: PROFILE });
      putReq.onsuccess = () => res();
      putReq.onerror = () => rej(putReq.error);
    });
    const rec = await new Promise((res, rej) => {
      const getReq = db.transaction(["kv"], "readonly").objectStore("kv").get("rec");
      getReq.onsuccess = () => res(getReq.result);
      getReq.onerror = () => rej(getReq.error);
    });
    out.idb = rec && rec.profile ? rec.profile : null;
  } catch (e) { out.idb = "err:" + e.name; }
  try { window.name = "profile" + PROFILE + "-window"; out.windowName = window.name; } catch (e) { out.windowName = "err:" + e.name; }
  window.__isoResult__ = out;
})();
</script>
</body></html>
"""

READER_HTML = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<script>
(async () => {
  const out = {};
  try { out.cookie = document.cookie || ""; } catch (e) { out.cookie = "err:" + e.name; }
  try { out.local = localStorage.getItem("iso"); } catch (e) { out.local = "err:" + e.name; }
  try { out.session = sessionStorage.getItem("iso"); } catch (e) { out.session = "err:" + e.name; }
  try {
    const db = await new Promise((res, rej) => {
      const req = indexedDB.open("iso-db", 1);
      req.onupgradeneeded = () => { if (!req.result.objectStoreNames.contains("kv")) req.result.createObjectStore("kv", { keyPath: "id" }); };
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });
    if (db.objectStoreNames.contains("kv")) {
      const rec = await new Promise((res, rej) => {
        const getReq = db.transaction(["kv"], "readonly").objectStore("kv").get("rec");
        getReq.onsuccess = () => res(getReq.result);
        getReq.onerror = () => rej(getReq.error);
      });
      out.idb = rec && rec.profile ? rec.profile : null;
    } else { out.idb = null; }
  } catch (e) { out.idb = "err:" + e.name; }
  try { out.windowName = window.name || null; } catch (e) { out.windowName = "err:" + e.name; }
  window.__isoResult__ = out;
})();
</script>
</body></html>
"""


class IsoHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/writer.html":
            body = WRITER_HTML
            status = 200
        elif path == "/reader.html":
            body = READER_HTML
            status = 200
        else:
            body = b"Not Found"
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def _start_http_server():
    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), IsoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def parse_args():
    parser = argparse.ArgumentParser(
        description="Verify two browser profiles do not share cookies, storage, or origin state."
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        default=True,
        help="Skip network-dependent probes (always true for CI).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browsers headless (default true).",
    )
    parser.add_argument(
        "--privacy-mode",
        action="store_true",
        help="Create high-privacy profiles and verify their privacy restrictions.",
    )
    return parser.parse_args()


def _setup_temp_env():
    temp_dir = tempfile.mkdtemp(prefix="ghostbrowser_isolation_")
    os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
    os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir
    return temp_dir


async def _mock_probe_native_metadata(force_headless=True):
    return {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "uadata": {
            "brands": [
                {"brand": "Chromium", "version": "149"},
                {"brand": "Not)A;Brand", "version": "24"},
            ],
            "mobile": False,
            "platform": "Windows",
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "platformVersion": "19.0.0",
            "uaFullVersion": "149.0.0.0",
            "fullVersionList": [
                {"brand": "Chromium", "version": "149.0.0.0"},
                {"brand": "Not)A;Brand", "version": "24.0.0.0"},
            ],
        },
    }


async def _clean(bm, profile_manager, profile_a_id, profile_b_id, temp_dir):
    for pid in (profile_b_id, profile_a_id):
        if not pid:
            continue
        try:
            await bm.close_profile(pid)
        except Exception:
            pass
        try:
            profile_manager.delete_profile(pid)
        except Exception:
            pass
    if temp_dir and os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _launch(bm, profile_id, headless):
    res = await bm.launch_profile(profile_id, force_headless=headless)
    if res.get("status") != "success":
        raise RuntimeError(f"launch_profile {profile_id} failed: {res.get('message')}")
    return bm.active_browsers[profile_id]["context"], bm.active_browsers[profile_id]["page"]


async def _read_result(page, timeout=10000):
    await page.wait_for_function("() => !!window.__isoResult__", timeout=timeout)
    return await page.evaluate("() => window.__isoResult__")


async def _goto_result(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    return await _read_result(page)


def _looks_like(profile, result):
    cookie = str(result.get("cookie") or "")
    return (
        f"iso={profile}" in cookie,
        result.get("local") == profile,
        result.get("session") == profile,
        result.get("idb") == profile,
        result.get("windowName") == f"profile{profile}-window",
    )


def _report_failures(label, expected_profile, result, include_transient=True):
    failures = []
    cookie_ok, local_ok, session_ok, idb_ok, name_ok = _looks_like(expected_profile, result)
    if not cookie_ok:
        failures.append(f"{label} cookie expected iso={expected_profile}, got {result.get('cookie')!r}")
    if not local_ok:
        failures.append(f"{label} localStorage expected {expected_profile!r}, got {result.get('local')!r}")
    if include_transient:
        if not session_ok:
            failures.append(f"{label} sessionStorage expected {expected_profile!r}, got {result.get('session')!r}")
        if not name_ok:
            failures.append(f"{label} window.name expected 'profile{expected_profile}-window', got {result.get('windowName')!r}")
    if not idb_ok:
        failures.append(f"{label} indexedDB expected {expected_profile!r}, got {result.get('idb')!r}")
    return failures


def _report_leaks(label, leaked_profile, result):
    leaks = []
    cookie_bad = f"iso={leaked_profile}" in str(result.get("cookie") or "")
    local_bad = result.get("local") == leaked_profile
    session_bad = result.get("session") == leaked_profile
    idb_bad = result.get("idb") == leaked_profile
    name_bad = result.get("windowName") == f"profile{leaked_profile}-window"
    if cookie_bad:
        leaks.append(f"{label} cookie leaked profile {leaked_profile}")
    if local_bad:
        leaks.append(f"{label} localStorage leaked profile {leaked_profile}")
    if session_bad:
        leaks.append(f"{label} sessionStorage leaked profile {leaked_profile}")
    if idb_bad:
        leaks.append(f"{label} indexedDB leaked profile {leaked_profile}")
    if name_bad:
        leaks.append(f"{label} window.name leaked profile {leaked_profile}")
    return leaks


async def main():
    args = parse_args()
    os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
    temp_dir = _setup_temp_env()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    import backend.browser_manager as bm
    from backend.profile_manager import profile_manager

    bm.probe_native_metadata = _mock_probe_native_metadata

    server = None
    leaks = []
    profile_a_id = None
    profile_b_id = None
    summary = {}

    try:
        server, port = _start_http_server()
        base = f"http://127.0.0.1:{port}/"

        advanced = {"headless": args.headless, "webrtc_mode": "protected"}
        if args.privacy_mode:
            advanced.update({"privacy_mode": "high", "block_service_workers": True})
        profile_a = profile_manager.create_profile(name="isolation_check_a", advanced=advanced)
        profile_b = profile_manager.create_profile(name="isolation_check_b", advanced=advanced)
        profile_a_id = profile_a["id"]
        profile_b_id = profile_b["id"]

        # Profile A: write, then read back to prove writes persist in the same profile.
        ctx_a, page_a = await _launch(bm, profile_a_id, args.headless)
        written_a = await _goto_result(page_a, base + "writer.html?p=A")
        await page_a.goto(base + "reader.html", wait_until="domcontentloaded", timeout=15000)
        read_a = await _read_result(page_a)
        leaks.extend(_report_failures("profile A read-after-write", "A", read_a))
        await page_a.close()

        # Profile B: read before writing; must not see profile A values.
        ctx_b, page_b = await _launch(bm, profile_b_id, args.headless)
        if args.privacy_mode:
            from backend.launch_policy import get_privacy_launch_flags
            expected_flags = set(get_privacy_launch_flags(advanced))
            active_args = set(bm.active_browsers[profile_b_id].get("args", []))
            restricted_profiles = (
                profile_a["advanced"].get("privacy_mode") == "high"
                and profile_b["advanced"].get("privacy_mode") == "high"
                and profile_a.get("path") != profile_b.get("path")
            )
            privacy_flags_present = all(
                any(flag.split("=", 1)[1] in arg for arg in active_args if arg.startswith(flag.split("=", 1)[0]))
                for flag in expected_flags
            )
            if not restricted_profiles or not privacy_flags_present:
                leaks.append("high privacy profiles are missing isolated storage/cache or launch restrictions")
            if not any("ServiceWorker" in arg for arg in active_args):
                leaks.append("service worker blocking flag missing")
            if not any("ThirdPartyCookies" in arg for arg in active_args):
                leaks.append("third-party cookie blocking flag missing")
        read_b_before = await _goto_result(page_b, base + "reader.html")
        leaks.extend(_report_leaks("profile B pre-write", "A", read_b_before))

        # Profile B: write its own values.
        written_b = await _goto_result(page_b, base + "writer.html?p=B")
        await page_b.close()

        # Reopen profile A (new page, same context) and read; must not see profile B values.
        page_a2 = await ctx_a.new_page()
        await asyncio.sleep(0.5)  # let browser_manager finish CDP setup for the new page
        read_a_after = await _goto_result(page_a2, base + "reader.html")
        leaks.extend(_report_leaks("profile A after B write", "B", read_a_after))
        # Profile A's persisted state should still be A; sessionStorage/window.name reset on new page.
        leaks.extend(_report_failures("profile A re-read own state", "A", read_a_after, include_transient=False))

        status = "passed" if not leaks else "failed"
        summary = {
            "status": status,
            "leaks": leaks,
            "profile_a_id": profile_a_id,
            "profile_b_id": profile_b_id,
            "port": port,
            "profile_a_write": written_a,
            "profile_a_read_after_write": read_a,
            "profile_b_pre_write": read_b_before,
            "profile_b_write": written_b,
            "profile_a_re_read": read_a_after,
        }
    except Exception as exc:
        summary = {
            "status": "failed",
            "leaks": leaks + [f"exception: {exc}"],
            "profile_a_id": profile_a_id,
            "profile_b_id": profile_b_id,
            "traceback": traceback.format_exc(),
        }

    print(json.dumps(summary, indent=2))

    await _clean(bm, profile_manager, profile_a_id, profile_b_id, temp_dir)

    if server:
        try:
            server.shutdown()
        except Exception:
            pass

    sys.exit(0 if summary.get("status") == "passed" else 1)


if __name__ == "__main__":
    asyncio.run(main())
