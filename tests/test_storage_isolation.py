import os
import sys
import tempfile
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

def main():

    # 1. Initialize variables safely for finally block
    orig_sys_path = list(sys.path)
    orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
    orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")

    orig_probe_native_metadata = None
    orig_get_proxy_for_profile = None

    bm_module = None
    pm_module = None

    temp_dir = tempfile.TemporaryDirectory()
    test_success = False
    cleanup_success = True

    playwright_instance = None
    browser_instance = None
    http_server = None


    try:
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir.name
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

        sys.path.append(os.getcwd())

        # Import modules
        import backend.browser_manager as bm
        import backend.proxy_manager as pm

        bm_module = bm
        pm_module = pm

        # Store originals
        orig_probe_native_metadata = bm.probe_native_metadata
        orig_get_proxy_for_profile = pm.proxy_manager.get_proxy_for_profile

        # Monkeypatch
        async def mock_probe_native_metadata(force_headless=True):
            return {
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                "uadata": {
                    "brands": [
                        {"brand": "Chromium", "version": "149"},
                        {"brand": "Not)A;Brand", "version": "24"}
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
                        {"brand": "Not)A;Brand", "version": "24.0.0.0"}
                    ]
                }
            }

        bm.probe_native_metadata = mock_probe_native_metadata

        async def mock_get_proxy_for_profile(profile_id, force_new=False):
            return None

        pm.proxy_manager.get_proxy_for_profile = mock_get_proxy_for_profile

        class _TestHTTPHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/sw.js":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript")
                    self.end_headers()
                    self.wfile.write(b"""
                        self.addEventListener('install', e => self.skipWaiting());
                        self.addEventListener('activate', e => e.waitUntil(clients.claim()));
                    """.strip())
                elif self.path.startswith("/cached/"):
                    # CacheStorage PUT path placeholder
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"placeholder")
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body></body></html>")

            def log_message(self, format, *args):
                pass

        def _start_http_server():
            global http_server
            http_server = HTTPServer(("127.0.0.1", 0), _TestHTTPHandler)
            t = threading.Thread(target=http_server.serve_forever, daemon=True)
            t.start()
            return http_server.server_address[1]

        async def _write_storage(page, label):
            return await page.evaluate(
                """
                async (label) => {
                    const id = "ghostBrowserStorageProbe";
                    localStorage.setItem(id, label + "-local");
                    sessionStorage.setItem(id, label + "-session");

                    const db = await new Promise((resolve, reject) => {
                        const req = indexedDB.open("GhostBrowserTestDB", 1);
                        req.onupgradeneeded = () => {
                            const db = req.result;
                            if (!db.objectStoreNames.contains("store")) {
                                db.createObjectStore("store", { keyPath: "id" });
                            }
                        };
                        req.onsuccess = () => resolve(req.result);
                        req.onerror = () => reject(req.error);
                    });

                    await new Promise((resolve, reject) => {
                        const tx = db.transaction(["store"], "readwrite");
                        const store = tx.objectStore("store");
                        const putReq = store.put({ id: id, value: label + "-idb" });
                        putReq.onsuccess = () => resolve();
                        putReq.onerror = () => reject(putReq.error);
                    });

                    const cache = await caches.open("GhostBrowserTestCache");
                    const url = "/cached/" + id;
                    const response = new Response(label + "-cache", {
                        status: 200,
                        headers: { "Content-Type": "text/plain" }
                    });
                    await cache.put(url, response.clone());
                    return true;
                }
                """,
                label,
            )

        async def _read_storage(page):
            return await page.evaluate(
                """
                async () => {
                    const id = "ghostBrowserStorageProbe";
                    const result = {
                        local: null,
                        session: null,
                        idb: null,
                        cache: null
                    };

                    result.local = localStorage.getItem(id);
                    result.session = sessionStorage.getItem(id);

                    try {
                        const idb = await new Promise((resolve, reject) => {
                            const req = indexedDB.open("GhostBrowserTestDB", 1);
                            req.onupgradeneeded = () => {
                                const db = req.result;
                                if (!db.objectStoreNames.contains("store")) {
                                    db.createObjectStore("store", { keyPath: "id" });
                                }
                            };
                            req.onsuccess = () => resolve(req.result);
                            req.onerror = () => reject(req.error);
                        });
                        if (idb.objectStoreNames.contains("store")) {
                            result.idb = await new Promise((resolve, reject) => {
                                const tx = idb.transaction(["store"], "readonly");
                                const store = tx.objectStore("store");
                                const getReq = store.get(id);
                                getReq.onsuccess = () => {
                                    const record = getReq.result;
                                    resolve(record ? record.value : null);
                                };
                                getReq.onerror = () => reject(getReq.error);
                            });
                        }
                    } catch (e) {
                        result.idb = "error: " + e.name;
                    }

                    try {
                        const cache = await caches.open("GhostBrowserTestCache");
                        const match = await cache.match("/cached/" + id);
                        if (match) {
                            result.cache = await match.text();
                        }
                    } catch (e) {
                        result.cache = "error: " + e.name;
                    }

                    return result;
                }
                """
            )

        async def _test_service_worker(page):
            return await page.evaluate(
                """
                async () => {
                    if (!navigator.serviceWorker) {
                        return { supported: false, reason: "no serviceWorker container" };
                    }
                    try {
                        const reg = await navigator.serviceWorker.register('/sw.js');
                        const unreg = await reg.unregister();
                        return { supported: true, registerResolved: true, unregistered: unreg };
                    } catch (e) {
                        return { supported: true, registerResolved: false, error: e.name + ": " + e.message };
                    }
                }
                """
            )

        async def _make_context(browser, cfg):
            """Build a profile launch config and return a fresh isolated context."""
            ctx_config = await bm.build_browser_launch_config(cfg, force_headless=True)
            script = ctx_config["spoofing_script"]
            sw_setting = "block" if ctx_config["block_service_workers"] else "allow"
            context = await browser.new_context(service_workers=sw_setting)
            await context.add_init_script(script)
            page = await context.new_page()
            # All storage APIs (and service workers) require a real origin.
            await page.goto(f"http://127.0.0.1:{http_server.server_address[1]}/")
            return context, page

        async def run_test():
            global playwright_instance, browser_instance, http_server
            all_passed = True

            from playwright.async_api import async_playwright

            port = _start_http_server()
            print(f"[INFO] local test server running on port {port}")

            playwright_instance = await async_playwright().start()
            try:
                browser_instance = await playwright_instance.chromium.launch(headless=True)
                try:
                    profile_a = {
                        "id": "a0000000-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "profile-a"),
                        "advanced": {
                            "os": "Windows",
                            "screen_resolution": "1920x1080",
                            "canvas_noise": True,
                            "webgl_noise": False,
                            "block_service_workers": False,
                        },
                    }

                    profile_b = {
                        "id": "b0000000-0000-0000-0000-000000000000",
                        "path": os.path.join(temp_dir.name, "profile-b"),
                        "advanced": {
                            "os": "Windows",
                            "screen_resolution": "1366x768",
                            "canvas_noise": True,
                            "webgl_noise": True,
                            "audio_noise": True,
                            "block_service_workers": True,
                        },
                    }

                    # Profile A: write and read back
                    ctx_a, page_a = await _make_context(browser_instance, profile_a)
                    await _write_storage(page_a, "A")
                    read_a = await _read_storage(page_a)

                    a_local_ok = read_a.get("local") == "A-local"
                    a_session_ok = read_a.get("session") == "A-session"
                    a_idb_ok = read_a.get("idb") == "A-idb"
                    a_cache_ok = read_a.get("cache") == "A-cache"
                    if a_local_ok and a_session_ok and a_idb_ok and a_cache_ok:
                        print("[PASS] Profile A can read back its own storage")
                    else:
                        print(f"[FAIL] Profile A read-back: {read_a}")
                        all_passed = False

                    # Profile A service worker policy: allowed
                    sw_a = await _test_service_worker(page_a)
                    if sw_a.get("supported") and sw_a.get("registerResolved"):
                        print("[PASS] Profile A service worker registration allowed")
                    else:
                        print(f"[FAIL] Profile A service worker expected allowed but got: {sw_a}")
                        all_passed = False

                    # Profile B: must not see Profile A data (cross-profile isolation)
                    ctx_b, page_b = await _make_context(browser_instance, profile_b)
                    read_b_before = await _read_storage(page_b)
                    b_isolated = (
                        not read_b_before.get("local")
                        and not read_b_before.get("session")
                        and not read_b_before.get("idb")
                        and not read_b_before.get("cache")
                    )
                    if b_isolated:
                        print("[PASS] Profile B cannot read Profile A storage")
                    else:
                        print(f"[FAIL] Profile B leaked Profile A storage: {read_b_before}")
                        all_passed = False

                    # Profile B service worker policy: blocked
                    sw_b = await _test_service_worker(page_b)
                    if not sw_b.get("registerResolved"):
                        print("[PASS] Profile B service worker registration blocked")
                    else:
                        print(f"[FAIL] Profile B service worker expected blocked but got: {sw_b}")
                        all_passed = False

                    # Profile B: write and read back
                    await _write_storage(page_b, "B")
                    read_b = await _read_storage(page_b)
                    b_local_ok = read_b.get("local") == "B-local"
                    b_session_ok = read_b.get("session") == "B-session"
                    b_idb_ok = read_b.get("idb") == "B-idb"
                    b_cache_ok = read_b.get("cache") == "B-cache"
                    if b_local_ok and b_session_ok and b_idb_ok and b_cache_ok:
                        print("[PASS] Profile B can read back its own storage")
                    else:
                        print(f"[FAIL] Profile B read-back: {read_b}")
                        all_passed = False

                    await ctx_a.close()
                    await ctx_b.close()

                    # Reopen Profile A: non-persistent contexts must be clean
                    ctx_a2, page_a2 = await _make_context(browser_instance, profile_a)
                    read_a2 = await _read_storage(page_a2)
                    a2_clean = (
                        not read_a2.get("local")
                        and not read_a2.get("session")
                        and not read_a2.get("idb")
                        and not read_a2.get("cache")
                    )
                    if a2_clean:
                        print("[PASS] Profile A fresh context has clean storage")
                    else:
                        print(f"[FAIL] Profile A fresh context leaked closed storage: {read_a2}")
                        all_passed = False

                    # Verify re-opened Profile A service worker still allowed
                    sw_a2 = await _test_service_worker(page_a2)
                    if sw_a2.get("supported") and sw_a2.get("registerResolved"):
                        print("[PASS] Profile A reopened context service worker registration allowed")
                    else:
                        print(f"[FAIL] Profile A reopened service worker expected allowed but got: {sw_a2}")
                        all_passed = False

                    await ctx_a2.close()

                    return all_passed
                finally:
                    if browser_instance:
                        await browser_instance.close()
                        browser_instance = None
            finally:
                if playwright_instance:
                    await playwright_instance.stop()
                    playwright_instance = None
                if http_server:
                    try:
                        http_server.shutdown()
                        http_server.server_close()
                    except Exception:
                        pass
                    http_server = None

        test_success = asyncio.run(run_test())
    except Exception as e:
        print(f"[FAIL] Test execution encountered error: {e}")
        import traceback
        traceback.print_exc()
        test_success = False

    finally:
        # browser cleanup
        browser_cleanup_ok = (playwright_instance is None and browser_instance is None)
        if browser_cleanup_ok:
            print("[PASS] browser cleanup")
        else:
            print("[FAIL] browser cleanup")
            cleanup_success = False

        # Restore monkeypatches
        monkeypatch_ok = True
        if bm_module is not None and orig_probe_native_metadata is not None:
            bm_module.probe_native_metadata = orig_probe_native_metadata
            if bm_module.probe_native_metadata is not orig_probe_native_metadata:
                monkeypatch_ok = False
        else:
            monkeypatch_ok = False

        if pm_module is not None and orig_get_proxy_for_profile is not None:
            pm_module.proxy_manager.get_proxy_for_profile = orig_get_proxy_for_profile
            if pm_module.proxy_manager.get_proxy_for_profile is not orig_get_proxy_for_profile:
                monkeypatch_ok = False
        else:
            monkeypatch_ok = False

        if monkeypatch_ok:
            print("[PASS] monkeypatch restoration")
        else:
            print("[FAIL] monkeypatch restoration")
            cleanup_success = False

        # Restore environment variables
        env_ok = True
        sys.path = list(orig_sys_path)
        if sys.path != orig_sys_path:
            env_ok = False

        if orig_dir is not None:
            os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = orig_dir
        else:
            os.environ.pop("GHOSTBROWSER_TEST_PROFILES_DIR", None)

        if orig_env is not None:
            os.environ["GHOSTBROWSER_TEST_ENV"] = orig_env
        else:
            os.environ.pop("GHOSTBROWSER_TEST_ENV", None)

        if os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR") != orig_dir or os.environ.get("GHOSTBROWSER_TEST_ENV") != orig_env:
            env_ok = False

        if env_ok:
            print("[PASS] environment restoration")
        else:
            print("[FAIL] environment restoration")
            cleanup_success = False

        # Clean up TemporaryDirectory
        try:
            temp_dir.cleanup()
        except Exception:
            pass

        if test_success and cleanup_success:
            return True

        else:
            return False


if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
