import ast
import sys
import os
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import threading
import asyncio
import pytest
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


@pytest.mark.asyncio
async def test_worker_proxy_transport():
    global origin_port, proxy_port, proxy_records, origin_received_transit_header

    proxy_records = []
    origin_received_transit_header = False

    origin_server = HTTPServer(("127.0.0.1", 0), OriginServerHandler)
    origin_port = origin_server.server_port
    origin_thread = threading.Thread(target=origin_server.serve_forever, daemon=True)
    origin_thread.start()

    proxy_server = HTTPServer(("127.0.0.1", 0), RecordingProxyHandler)
    proxy_port = proxy_server.server_port
    proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_thread.start()

    playwright = None
    browser = None
    context = None
    page = None
    all_passed = True

    try:
        playwright = await async_playwright().start()

        browser = await playwright.chromium.launch(
            headless=True,
            proxy={"server": f"http://127.0.0.1:{proxy_port}"}
        )
        context = await browser.new_context()

        await context.route("**/*", lambda route: route.continue_())

        page = await context.new_page()

        target_url = f"http://worker-target.invalid:{origin_port}/"
        await page.goto(target_url)

        result = None
        for _ in range(50):
            result = await page.evaluate("window.workerResult")
            if result is not None:
                break
            await asyncio.sleep(0.1)

        assert "/" in proxy_records, "Main document request did not traverse the proxy"
        assert "/worker.js" in proxy_records, "/worker.js request did not traverse the proxy"
        assert "/worker-fetch" in proxy_records, "/worker-fetch request did not traverse the proxy"
        assert origin_received_transit_header, "Origin server did not receive X-Test-Proxy-Transit: yes"
        assert (
            result is not None
            and result.get("status") == "success"
            and result.get("data") == "worker response data"
        ), "Worker did not complete successfully or returned wrong data"
        assert len([p for p in proxy_records if p not in ("/", "/worker.js", "/worker-fetch")]) == 0, "Unexpected requests traversed the proxy"
    except Exception as e:
        raise AssertionError(f"Integration test exception: {e}")
    finally:
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


if __name__ == "__main__":
    asyncio.run(test_worker_proxy_transport())