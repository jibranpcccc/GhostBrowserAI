import ast
import sys
import os
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import threading
import asyncio
from playwright.async_api import async_playwright

proxy_records = []
origin_received_transit_header = False
origin_port = 0
proxy_port = 0

class OriginServerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        global origin_received_transit_header
        transit = self.headers.get("X-Test-Proxy-Transit")
        if self.path == "/worker-fetch" and transit == "yes":
            origin_received_transit_header = True

        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html>
            <body>
            <script>
            window.workerResult = null;
            const worker = new Worker('/worker.js');
            worker.onmessage = (e) => {
                window.workerResult = e.data;
            };
            worker.postMessage('start');
            </script>
            </body>
            </html>
            """)
        elif self.path == "/worker.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.end_headers()
            self.wfile.write(b"""
            self.onmessage = async (e) => {
                try {
                    const resp = await fetch('/worker-fetch');
                    const text = await resp.text();
                    self.postMessage({status: "success", data: text});
                } catch(err) {
                    self.postMessage({status: "error", message: err.message});
                }
            };
            """)
        elif self.path == "/worker-fetch":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"worker response data")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

class RecordingProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        global proxy_records
        url_to_parse = self.path
        if not url_to_parse.startswith("http"):
            host_hdr = self.headers.get("Host", "")
            url_to_parse = f"http://{host_hdr}{self.path}"

        parsed = urlparse(url_to_parse)

        if parsed.hostname != "worker-target.invalid":
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden destination")
            return

        proxy_records.append(parsed.path)

        # Forward request to origin
        origin_url = f"http://127.0.0.1:{origin_port}{parsed.path}"
        if parsed.query:
            origin_url += f"?{parsed.query}"

        req_headers = {k: v for k, v in self.headers.items() if k.lower() != 'host'}
        req_headers["X-Test-Proxy-Transit"] = "yes"
        req_headers["Host"] = f"worker-target.invalid:{origin_port}"

        try:
            req = urllib.request.Request(origin_url, headers=req_headers, method="GET")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

async def main():
    global origin_port, proxy_port

    # Start origin server on random port
    origin_server = HTTPServer(("127.0.0.1", 0), OriginServerHandler)
    origin_port = origin_server.server_port
    origin_thread = threading.Thread(target=origin_server.serve_forever, daemon=True)
    origin_thread.start()

    # Start proxy server on random port
    proxy_server = HTTPServer(("127.0.0.1", 0), RecordingProxyHandler)
    proxy_port = proxy_server.server_port
    proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_thread.start()

    print(f"Origin Server running on 127.0.0.1:{origin_port}")
    print(f"Proxy Server running on 127.0.0.1:{proxy_port}")

    playwright = None
    browser = None
    context = None
    page = None
    all_passed = True

    try:
        playwright = await async_playwright().start()

        # Launch with proxy configuration
        browser = await playwright.chromium.launch(
            headless=True,
            proxy={"server": f"http://127.0.0.1:{proxy_port}"}
        )
        context = await browser.new_context()

        # Install a Playwright context route using only await route.continue_()
        await context.route("**/*", lambda route: route.continue_())

        page = await context.new_page()

        # Navigate using the fake hostname
        target_url = f"http://worker-target.invalid:{origin_port}/"
        await page.goto(target_url)

        # Wait for the worker to complete and post its message
        result = None
        for _ in range(50):
            result = await page.evaluate("window.workerResult")
            if result is not None:
                break
            await asyncio.sleep(0.1)

        print(f"DEBUG: Worker result: {result}")
        print(f"DEBUG: Proxy records: {proxy_records}")
        print(f"DEBUG: Origin received transit header: {origin_received_transit_header}")

        # Assertions
        # 1. Main document passed through the proxy
        main_doc_ok = "/" in proxy_records
        if main_doc_ok:
            print("[PASS] Main document request traversed the proxy.")
        else:
            print("[FAIL] Main document request did not traverse the proxy.")
            all_passed = False

        # 2. /worker.js passed through the proxy
        worker_js_ok = "/worker.js" in proxy_records
        if worker_js_ok:
            print("[PASS] /worker.js request traversed the proxy.")
        else:
            print("[FAIL] /worker.js request did not traverse the proxy.")
            all_passed = False

        # 3. /worker-fetch passed through the proxy
        worker_fetch_ok = "/worker-fetch" in proxy_records
        if worker_fetch_ok:
            print("[PASS] /worker-fetch request traversed the proxy.")
        else:
            print("[FAIL] /worker-fetch request did not traverse the proxy.")
            all_passed = False

        # 4. Origin received transit header on /worker-fetch
        if origin_received_transit_header:
            print("[PASS] Origin server received X-Test-Proxy-Transit: yes on /worker-fetch.")
        else:
            print("[FAIL] Origin server did not receive X-Test-Proxy-Transit: yes on /worker-fetch.")
            all_passed = False

        # 5. Worker completed successfully
        worker_success = result is not None and result.get("status") == "success" and result.get("data") == "worker response data"
        if worker_success:
            print("[PASS] Worker completed successfully and returned correct data.")
        else:
            print("[FAIL] Worker did not complete successfully or returned wrong data.")
            all_passed = False

        # 6. No request was forwarded to an external destination
        external_ok = len([p for p in proxy_records if p not in ("/", "/worker.js", "/worker-fetch")]) == 0
        if external_ok:
            print("[PASS] No request was forwarded to any external or unexpected destination.")
        else:
            print("[FAIL] Unexpected requests traversed the proxy.")
            all_passed = False

    except Exception as e:
        print(f"[FAIL] Integration test encountered exception: {e}")
        all_passed = False
    finally:
        # Gracefully close everything
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass

        # Shutdown HTTP servers
        try:
            proxy_server.shutdown()
            proxy_server.server_close()
        except Exception:
            pass
        try:
            origin_server.shutdown()
            origin_server.server_close()
        except Exception:
            pass

    if all_passed:
        print("ALL PROXY TRANSPORT TESTS PASSED.")
        sys.exit(0)
    else:
        print("SOME PROXY TRANSPORT TESTS FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
