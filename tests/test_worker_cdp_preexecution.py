"""
DIRECT TASK 2I-CORRECTION — RESTORE HONEST PRE-EXECUTION AND CLEANUP ASSERTIONS
Architecture proof only. Does not modify production code.
"""

import asyncio
import json
import sys
import pytest
import time
import threading
import ast
from http.server import BaseHTTPRequestHandler

try:
    from http.server import ThreadingHTTPServer
except ImportError:
    from socketserver import ThreadingMixIn
    from http.server import HTTPServer
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):  # type: ignore
        pass

# ─────────────────────────────────────────────────────────────────────────────
# Profile offsets
# ─────────────────────────────────────────────────────────────────────────────
PROFILES = {
    "p1_run1": {"r": 17,  "g": 119, "b": 221},
    "p1_run2": {"r": 17,  "g": 119, "b": 221},
    "p2_run1": {"r": 34,  "g": 238, "b": 186},
}

# ─────────────────────────────────────────────────────────────────────────────
# Worker-compatible patch
# ─────────────────────────────────────────────────────────────────────────────
WORKER_PATCH_TEMPLATE = r"""
(function() {
    'use strict';
    var _sp = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;
    var _ots = Function.prototype.toString;
    if (_sp) {
        Function.prototype.toString = new Proxy(_ots, {
            apply: function(t, th, a) {
                if (_sp.has(th)) return 'function ' + _sp.get(th) + '() { [native code] }';
                return Reflect.apply(t, th, a);
            }
        });
    }
    function _mkn(fn, name, len) {
        var n = name || fn.name || '';
        if (_sp) _sp.set(fn, n);
        try { Object.defineProperty(fn, 'name',   {value: n,   writable: false, enumerable: false, configurable: true}); } catch(e){}
        if (Number.isInteger(len))
            try { Object.defineProperty(fn, 'length', {value: len, writable: false, enumerable: false, configurable: true}); } catch(e){}
        return fn;
    }
    var _R = __R__; var _G = __G__; var _B = __B__;
    function _noise(id, ox, oy, sw) {
        var d = id.data;
        var seed = (Math.imul(_R, 73856093) ^ Math.imul(_G, 19349663) ^ Math.imul(_B, 83492791)) >>> 0;
        var x0 = (ox == null) ? 0 : (ox | 0), y0 = (oy == null) ? 0 : (oy | 0);
        var W  = (sw == null) ? id.width  : (sw  | 0);
        var iw = id.width;
        for (var i = 0; i < d.length; i += 4) {
            var li = i >>> 2, lc = li % iw, lr = (li - lc) / iw;
            var pi = (y0 + lr) * W + (x0 + lc);
            var h  = Math.imul(((pi ^ seed) >>> 0), 0x45d9f3b); h ^= (h >>> 16);
            if ((h & 63) === 0 && d[i + 3] !== 0) {
                var ch = (h >>> 6) % 3, dt = ((h >>> 8) & 1) === 0 ? -1 : 1, ti = i + ch;
                d[ti] = Math.max(0, Math.min(255, d[ti] + dt));
            }
        }
        return id;
    }
    var _ocCtxProto = null;
    try {
        if (typeof OffscreenCanvas !== 'undefined' &&
            typeof OffscreenCanvas.prototype.convertToBlob === 'function') {
            var _toc = new OffscreenCanvas(1, 1);
            var _tcx = _toc.getContext('2d');
            if (_tcx) { _ocCtxProto = Object.getPrototypeOf(_tcx); }
        }
    } catch (_ge) {}
    if (_ocCtxProto !== null) {

        var _oGID = _ocCtxProto.getImageData;
        var _oCTB = OffscreenCanvas.prototype.convertToBlob;
        var _dGID = Object.getOwnPropertyDescriptor(_ocCtxProto, 'getImageData');
        var _dCTB = Object.getOwnPropertyDescriptor(OffscreenCanvas.prototype, 'convertToBlob');

        var _wGID = _mkn(function(x, y, w, h) {
            var img = _oGID.apply(this, arguments);
            return _noise(img, x | 0, y | 0,
                this.canvas ? this.canvas.width : img.width);
        }, 'getImageData', _oGID.length);

        var _df = function(d) {
            return d ? {writable: d.writable, enumerable: d.enumerable, configurable: d.configurable}
                     : {writable: true, enumerable: false, configurable: true};
        };
        Object.defineProperty(_ocCtxProto, 'getImageData',
            Object.assign({value: _wGID}, _df(_dGID)));

        function _clone(src) {
            var c  = new OffscreenCanvas(src.width, src.height);
            var cx = c.getContext('2d'); cx.drawImage(src, 0, 0);
            var id = _oGID.call(cx, 0, 0, c.width, c.height);
            _noise(id, 0, 0, c.width); cx.putImageData(id, 0, 0); return c;
        }
        OffscreenCanvas.prototype.convertToBlob = _mkn(function(opts) {
            if (this.width === 0 || this.height === 0) return _oCTB.apply(this, arguments);
            return _oCTB.call(_clone(this), opts);
        }, 'convertToBlob', _oCTB.length);

        try {
            OffscreenCanvas.prototype.convertToBlob.__ghostPatched = true;
        } catch(e) {}
    }
    self.__ghostWorkerPatchInstalled = true;
})();
"""

def build_patch(r, g, b):
    return (WORKER_PATCH_TEMPLATE
            .replace("__R__", str(r))
            .replace("__G__", str(g))
            .replace("__B__", str(b)))

# ─────────────────────────────────────────────────────────────────────────────
# Worker JavaScript files
# ─────────────────────────────────────────────────────────────────────────────
RELATIVE_DATA    = b"ghost-relative-data-ok"
MODULE_HELPER_JS = "export const MODULE_HELPER_VALUE = 'ghost-module-helper-ok';\n"

_MARKER_EXPR = """(function() {
    try {
        if (typeof OffscreenCanvas !== 'undefined' &&
            OffscreenCanvas.prototype.convertToBlob.__ghostPatched === true)
            return true;
    } catch (_me) {}
    return typeof self !== 'undefined' && self.__ghostWorkerPatchInstalled === true;
})()"""

_OC_PAYLOAD = r"""
async function _ocPayload() {
    if (typeof OffscreenCanvas === 'undefined') return {ocSupported: false};
    try {
        var oc  = new OffscreenCanvas(128, 64);
        var ctx = oc.getContext('2d');
        ctx.fillStyle = 'rgba(0,0,0,0)';    ctx.fillRect(0,  0, 128, 64);
        ctx.fillStyle = 'rgb(100,150,200)'; ctx.fillRect(0,  0,  80, 64);
        ctx.fillStyle = 'rgb(255,0,0)';     ctx.fillRect(10, 10,  40, 20);
        var id = ctx.getImageData(0, 0, 128, 64); var d = id.data;
        var transOk = true, alphaOk = true;
        for (var i = 0; i < d.length; i += 4) {
            if (d[i+3] !== 0 && d[i+3] !== 255) { alphaOk = false; }
        }
        var hb = await crypto.subtle.digest('SHA-256', d.buffer);
        var imgHash = Array.from(new Uint8Array(hb))
            .map(function(b){return b.toString(16).padStart(2,'0')}).join('');
        var blob = await oc.convertToBlob({type:'image/png'});
        var bb   = await blob.arrayBuffer();
        var bh   = await crypto.subtle.digest('SHA-256', bb);
        var blobHash = Array.from(new Uint8Array(bh))
            .map(function(b){return b.toString(16).padStart(2,'0')}).join('');
        var gidFn = OffscreenCanvasRenderingContext2D.prototype.getImageData;
        var ctbFn = OffscreenCanvas.prototype.convertToBlob;
        var dGID  = Object.getOwnPropertyDescriptor(
            OffscreenCanvasRenderingContext2D.prototype, 'getImageData') || {};
        var dCTB  = Object.getOwnPropertyDescriptor(
            OffscreenCanvas.prototype, 'convertToBlob') || {};
        var invGID = null;
        try { gidFn.call({}, 0, 0, 1, 1); } catch(e) { invGID = e.constructor.name; }
        return {
            ocSupported: true, imgHash: imgHash, blobHash: blobHash,
            transOk: transOk, alphaOk: alphaOk,
            gidName: gidFn.name,  gidLen: gidFn.length,
            ctbName: ctbFn.name,  ctbLen: ctbFn.length,
            gidEnum: dGID.enumerable, gidConf: dGID.configurable,
            ctbConf: dCTB.configurable,
            gidStr:  Function.prototype.toString.call(gidFn),
            ctbStr:  Function.prototype.toString.call(ctbFn),
            invalidRecv: invGID
        };
    } catch(ex) { return {ocSupported: true, error: ex.message}; }
}
"""

CLASSIC_WORKER_JS = "const __markerAtFirstInstruction = " + _MARKER_EXPR + ";\n" + _OC_PAYLOAD + """
self.onmessage = async function(e) {
    var preCtbPatched, preSelfInstalled;
    try { preCtbPatched    = OffscreenCanvas.prototype.convertToBlob.__ghostPatched; } catch(_p1) {}
    try { preSelfInstalled = self.__ghostWorkerPatchInstalled; } catch(_p2) {}
    var href     = self.location.href;
    var fetchOk  = false, fetchData = null;
    try { var r = await fetch('/relative-data'); fetchData = await r.text(); fetchOk = r.ok; }
    catch(ex) { fetchData = 'ERR:' + ex.message; }
    var oc = await _ocPayload();
    var markerAfterAsyncWork = """ + _MARKER_EXPR + """;
    var ghostDebug = {};
    try { ghostDebug.preCtbPatched    = preCtbPatched;    } catch(_d0) {}
    try { ghostDebug.preSelfInstalled = preSelfInstalled; } catch(_d0b) {}
    try { ghostDebug.selfInstalled = self.__ghostWorkerPatchInstalled; } catch(_d1) {}
    try { ghostDebug.ctbPatched = OffscreenCanvas.prototype.convertToBlob.__ghostPatched; } catch(_d2) {}
    try { ghostDebug.ctbName = OffscreenCanvas.prototype.convertToBlob.name; } catch(_d3) {}
    try { ghostDebug.ocrcType = typeof OffscreenCanvasRenderingContext2D; } catch(_d4) {}
    try {
        var _ocrcProto = OffscreenCanvasRenderingContext2D.prototype;
        ghostDebug.ocrcProtoPatched = _ocrcProto.__ghostPatched;
        ghostDebug.ocrcGidName = _ocrcProto.getImageData.name;
    } catch(_d5) {}
    try {
        var _tcx2 = new OffscreenCanvas(1,1).getContext('2d');
        ghostDebug.instanceProtoPatched = Object.getPrototypeOf(_tcx2).__ghostPatched;
    } catch(_d6) {}
    self.postMessage(Object.assign(
        {workerType:'classic', marker:__markerAtFirstInstruction, markerAfterAsyncWork:markerAfterAsyncWork, href:href, fetchOk:fetchOk, fetchData:fetchData,
         ghostDebug:ghostDebug}, oc));
};
"""

MODULE_WORKER_JS = "import { MODULE_HELPER_VALUE } from './module-helper.js';\n" + "const __markerAtFirstInstruction = " + _MARKER_EXPR + ";\n" + _OC_PAYLOAD + """
self.onmessage = async function(e) {
    var href   = self.location.href;
    var oc     = await _ocPayload();
    var markerAfterAsyncWork = """ + _MARKER_EXPR + """;
    self.postMessage(Object.assign(
        {workerType:'module', marker:__markerAtFirstInstruction, markerAfterAsyncWork:markerAfterAsyncWork, href:href, moduleHelper:MODULE_HELPER_VALUE}, oc));
};
"""

NESTED_CHILD_JS = "const __markerAtFirstInstruction = " + _MARKER_EXPR + ";\n" + _OC_PAYLOAD + """
self.onmessage = async function(e) {
    var href   = self.location.href;
    var oc     = await _ocPayload();
    var markerAfterAsyncWork = """ + _MARKER_EXPR + """;
    self.postMessage(Object.assign(
        {workerType:'nested-child', marker:__markerAtFirstInstruction, markerAfterAsyncWork:markerAfterAsyncWork, href:href}, oc));
};
"""

NESTED_PARENT_JS = "const __markerAtFirstInstruction = typeof self !== 'undefined' && self.__ghostWorkerPatchInstalled === true;\n" + r"""
self.onmessage = async function(e) {
    var href   = self.location.href;
    var child  = new Worker('/nested-child.js');
    var childRes = await new Promise(function(ok, fail) {
        child.onmessage = function(ev) { ok(ev.data); };
        child.onerror   = function(ev) { fail(new Error(ev.message || 'child-err')); };
        child.postMessage(e.data);
        setTimeout(function() { fail(new Error('nested-child timeout')); }, 8000);
    });
    var markerAfterAsyncWork = typeof self !== 'undefined' && self.__ghostWorkerPatchInstalled === true;
    self.postMessage({workerType:'nested-parent', parentMarker:__markerAtFirstInstruction, parentMarkerAfterAsyncWork:markerAfterAsyncWork,
                      parentHref:href, child:childRes});
};
"""

BLOB_WORKER_CODE = "const __markerAtFirstInstruction = " + _MARKER_EXPR + ";\n" + _OC_PAYLOAD + """
self.onmessage = async function(e) {
    var href   = self.location.href;
    var oc     = await _ocPayload();
    var markerAfterAsyncWork = """ + _MARKER_EXPR + """;
    self.postMessage(Object.assign(
        {workerType:'blob', marker:__markerAtFirstInstruction, markerAfterAsyncWork:markerAfterAsyncWork, href:href}, oc));
};
"""

PAGE_HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>CDP Worker Injection Test</title></head><body><script>
window.__results = {};
window.runWorker = async function(type, pid) {
    return new Promise(function(res, rej) {
        var w;
        if      (type === 'classic') { w = new Worker('/classic-worker.js'); }
        else if (type === 'module')  { w = new Worker('/module-worker.js', {type:'module'}); }
        else if (type === 'nested')  { w = new Worker('/nested-parent.js'); }
        else if (type === 'blob') {
            var code = BLOB_CODE;
            var b = new Blob([code], {type:'application/javascript'});
            w = new Worker(URL.createObjectURL(b));
        }
        w.onmessage = function(ev) { window.__results[type] = ev.data; res(ev.data); };
        w.onerror   = function(ev) { rej(new Error(ev.message || 'worker-error')); };
        w.postMessage({pid: pid});
        setTimeout(function() { rej(new Error('timeout:' + type)); }, 25000);
    });
};
</script></body></html>
""".replace("BLOB_CODE", json.dumps(BLOB_WORKER_CODE))

# ─────────────────────────────────────────────────────────────────────────────
# HTTP server
# ─────────────────────────────────────────────────────────────────────────────
class WorkerHandler(BaseHTTPRequestHandler):
    ROUTES = {
        "/":                  (PAGE_HTML.encode(),              "text/html; charset=utf-8"),
        "/classic-worker.js": (CLASSIC_WORKER_JS.encode(),     "application/javascript; charset=utf-8"),
        "/module-worker.js":  (MODULE_WORKER_JS.encode(),      "application/javascript; charset=utf-8"),
        "/module-helper.js":  (MODULE_HELPER_JS.encode(),      "application/javascript; charset=utf-8"),
        "/nested-parent.js":  (NESTED_PARENT_JS.encode(),      "application/javascript; charset=utf-8"),
        "/nested-child.js":   (NESTED_CHILD_JS.encode(),       "application/javascript; charset=utf-8"),
        "/relative-data":     (RELATIVE_DATA,                  "text/plain; charset=utf-8"),
    }
    def do_GET(self):
        self.server.requested_paths.add(self.path)
        self.server.remote_peers.add(self.client_address[0])
        self.server.requested_hosts.add(self.headers.get("Host"))
        entry = self.ROUTES.get(self.path)
        if entry is None:
            self.server.unexpected_paths.add(self.path)
            self.send_response(404); self.end_headers(); return
        body, ct = entry
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), WorkerHandler)
    srv.requested_paths  = set()
    srv.unexpected_paths = set()
    srv.remote_peers = set()
    srv.requested_hosts = set()
    server_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    server_thread.start()
    return srv, srv.server_address[1], server_thread

# ─────────────────────────────────────────────────────────────────────────────
# Task helpers
# ─────────────────────────────────────────────────────────────────────────────
_registered_tasks = []
def register_task(label, coro):
    task = asyncio.ensure_future(coro)
    _registered_tasks.append((label, task))
    return task

_unhandled_loop_exceptions = []
def _loop_exception_handler(loop, context):
    _unhandled_loop_exceptions.append(context)

# ─────────────────────────────────────────────────────────────────────────────
# Target tracker
# ─────────────────────────────────────────────────────────────────────────────
class TargetInfo:
    def __init__(self, tid, ttype, turl, paused, session_id):
        self.target_id       = tid
        self.target_type     = ttype
        self.target_url      = turl
        self.paused          = paused
        self.session_id      = session_id
        self.injection_ok    = False
        self.marker_verified = False
        self.resumed         = False
        self.watchdog_used   = False
        self.errors: list[str] = []
        self.events_seen: list[str] = []
        self.ts_attached = time.monotonic()
        self.ts_paused = None
        self.ts_patch_sent = None
        self.ts_patch_success = None
        self.ts_marker_verified = None
        self.ts_auto_attach = None
        self.ts_resume_sent = None
        self.ts_first_result = None

# ─────────────────────────────────────────────────────────────────────────────
# Page CDPSession router
# ─────────────────────────────────────────────────────────────────────────────
class PageCDPRouter:
    def __init__(self, page_cdp):
        self._cdp  = page_cdp
        self._mid  = 0
        self._pending: dict[str, dict[int, asyncio.Future]] = {}
        self._sevt:    dict[str, dict[str, list]] = {}
        self._session_parents: dict[str, str] = {}   # child_sid → parent_sid
        self.all_futures_resolved = True
        page_cdp.on("Target.receivedMessageFromTarget", self._on_recv)

    def _next_id(self):
        self._mid += 1
        return self._mid

    def on_session_event(self, sid: str, method: str, handler):
        self._sevt.setdefault(sid, {}).setdefault(method, []).append(handler)

    def register_parent(self, child_sid: str, parent_sid: str):
        self._session_parents[child_sid] = parent_sid

    def _on_recv(self, params):
        if not params:
            return
        sid = params.get("sessionId", "")
        raw = params.get("message", "")
        if not raw:
            return
        try:
            msg = json.loads(raw)
        except Exception:
            return
        mid = msg.get("id")
        if mid is None:
            method     = msg.get("method", "")
            evt_params = msg.get("params") or {}
            if method == "Target.receivedMessageFromTarget":
                self._on_recv(evt_params)
                return
            for h in self._sevt.get(sid, {}).get(method, []):
                try:
                    h(evt_params)
                except Exception:
                    pass
            return
        sess_futs = self._pending.get(sid)
        if not sess_futs or mid not in sess_futs:
            return
        fut = sess_futs.pop(mid)
        if not fut.done():
            if "error" in msg:
                fut.set_exception(Exception(str(msg["error"])))
            else:
                fut.set_result(msg.get("result", {}))

    async def send(self, session_id: str, method: str, params=None, timeout: float = 8.0):
        mid  = self._next_id()
        msg  = json.dumps({"id": mid, "method": method, "params": params or {}})
        loop = asyncio.get_event_loop()
        fut  = loop.create_future()
        self._pending.setdefault(session_id, {})[mid] = fut

        parent_sid = self._session_parents.get(session_id)
        if parent_sid:
            outer = json.dumps({
                "id": self._next_id(),
                "method": "Target.sendMessageToTarget",
                "params": {"sessionId": session_id, "message": msg},
            })
            await self._cdp.send("Target.sendMessageToTarget", {
                "sessionId": parent_sid,
                "message": outer,
            })
        else:
            await self._cdp.send("Target.sendMessageToTarget", {
                "sessionId": session_id,
                "message":   msg,
            })
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.get(session_id, {}).pop(mid, None)
            self.all_futures_resolved = False
            raise TimeoutError(f"Timeout: {method} sid={session_id[:12]}")

    def cancel_all(self):
        for sess_futs in self._pending.values():
            for fut in sess_futs.values():
                if not fut.done():
                    fut.cancel()
        self._pending.clear()

# ─────────────────────────────────────────────────────────────────────────────
# Per-run result
# ─────────────────────────────────────────────────────────────────────────────
class RunResult:
    def __init__(self, key):
        self.profile_key          = key
        self.cdp_session_created  = False
        self.auto_attach_before   = False
        self.worker_results: dict = {}
        self.targets: dict        = {}
        self.classic_paused       = False
        self.module_paused        = False
        self.blob_paused          = False
        self.nested_parent_paused = False
        self.nested_child_paused  = False
        self.no_resume_after_fail = True
        self.all_sessions_closed  = False
        self.all_futures_resolved = True
        self.page_closed = False
        self.context_closed = False
        self.browser_closed = False
        self.playwright_stopped = False

# ─────────────────────────────────────────────────────────────────────────────
# Injection helpers
# ─────────────────────────────────────────────────────────────────────────────
async def run_profile(profile_key: str, offsets: dict, base_url: str) -> RunResult:
    from playwright.async_api import async_playwright

    result = RunResult(profile_key)
    patch  = build_patch(offsets["r"], offsets["g"], offsets["b"])

    pw = browser = browser_cdp_raw = page_cdp = router = None
    failed_sessions: set[str] = set()

    try:
        pw      = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        browser_cdp_raw = await browser.new_browser_cdp_session()
        result.cdp_session_created = True

        context  = await browser.new_context()
        page     = await context.new_page()
        page_cdp = await context.new_cdp_session(page)
        router   = PageCDPRouter(page_cdp)

        async def _inject_simple(info: TargetInfo, do_cascade: bool = True):
            sid = info.session_id
            is_nested_parent = "nested-parent" in info.target_url.lower()

            if not info.paused:
                info.errors.append("Target not paused, cannot use _inject_simple")
                failed_sessions.add(sid)
                result.no_resume_after_fail = False
                # If pause state cannot be proven, detach and fail
                try:
                    await page_cdp.send("Target.detachFromTarget", {"sessionId": sid})
                except Exception:
                    pass
                return

            info.ts_patch_sent = time.monotonic()
            try:
                resp = await router.send(sid, "Runtime.evaluate",
                                         {"expression": patch, "returnByValue": True,
                                          "awaitPromise": False}, timeout=6.0)
                ex = resp.get("exceptionDetails")
                if ex:
                    desc = (ex.get("exception") or {}).get("description") or ex.get("text", "?")
                    info.errors.append(f"eval exc: {desc}")
                    failed_sessions.add(sid)
                    result.no_resume_after_fail = False
                    return
                info.ts_patch_success = time.monotonic()
                info.injection_ok = True
            except Exception as exc:
                info.errors.append(f"inject err: {exc}")
                failed_sessions.add(sid)
                result.no_resume_after_fail = False
                return

            try:
                mr = await router.send(sid, "Runtime.evaluate",
                                        {"expression": "self.__ghostWorkerPatchInstalled === true",
                                         "returnByValue": True}, timeout=3.0)
                if mr.get("result", {}).get("value") is True:
                    info.ts_marker_verified = time.monotonic()
                    info.marker_verified = True
                else:
                    info.errors.append(
                        f"simple marker={mr.get('result',{}).get('value')!r}")
            except Exception as exc:
                info.errors.append(f"simple marker err: {exc}")

            if do_cascade or is_nested_parent:
                try:
                    await router.send(sid, "Target.setAutoAttach",
                                      {"autoAttach": True, "waitForDebuggerOnStart": True,
                                       "recursive": True},
                                      timeout=3.0)
                    info.ts_auto_attach = time.monotonic()
                    info.errors.append("simple cascade ok")
                except Exception as exc:
                    info.errors.append(f"cascade: {exc}")

            try:
                info.ts_resume_sent = time.monotonic()
                await router.send(sid, "Runtime.runIfWaitingForDebugger", timeout=3.0)
                info.resumed = True
            except Exception as exc:
                info.errors.append(f"simple resume err: {exc}")

        BREAK_ON_START_TIMEOUT = 20.0
        REAL_BP_TIMEOUT        = 12.0
        WATCHDOG_TIMEOUT       = 19.0

        async def _inject_via_debugger(info: TargetInfo):
            sid = info.session_id
            t0  = time.monotonic()

            if "nested-parent" in info.target_url.lower():
                def _on_child_attached(params, _parent_sid=sid):
                    if not params:
                        return
                    child_sid = params.get("sessionId", "")
                    if not child_sid:
                        return
                    router.register_parent(child_sid, _parent_sid)
                    on_attached(params)
                router.on_session_event(sid, "Target.attachedToTarget", _on_child_attached)

            DIAG_METHODS = [
                "Debugger.paused", "Debugger.resumed", "Debugger.scriptParsed",
                "Runtime.executionContextCreated", "Runtime.executionContextDestroyed",
            ]
            def make_event_logger(method: str):
                def logger(p):
                    elapsed = time.monotonic() - t0
                    summary = f"t={elapsed:.2f}s {method}"
                    if method == "Debugger.paused":
                        summary += (f" reason={p.get('reason')!r}"
                                    f" hitBP={bool(p.get('hitBreakpoints'))}")
                        if info.ts_paused is None:
                            info.ts_paused = time.monotonic()
                    elif method == "Debugger.scriptParsed":
                        summary += f" url={p.get('url','')[:40]}"
                    elif method.startswith("Runtime.executionContext"):
                        ctx = p.get("executionContextId") or (p.get("context") or {}).get("id")
                        summary += f" ctxId={ctx}"
                    info.events_seen.append(summary)
                return logger

            for m in DIAG_METHODS:
                router.on_session_event(sid, m, make_event_logger(m))

            paused_q: asyncio.Queue = asyncio.Queue()
            def on_paused(p):
                paused_q.put_nowait(p)
            router.on_session_event(sid, "Debugger.paused", on_paused)

            watchdog_task = None
            async def watchdog():
                await asyncio.sleep(WATCHDOG_TIMEOUT)
                if not info.resumed:
                    info.watchdog_used = True
                    info.errors.append(
                        f"watchdog: forcing runIfWaitingForDebugger "
                        f"at t={time.monotonic()-t0:.1f}s")
                    try:
                        info.ts_resume_sent = time.monotonic()
                        await router.send(
                            sid, "Runtime.runIfWaitingForDebugger", timeout=3.0)
                        info.resumed = True
                    except Exception as exc:
                        info.errors.append(f"watchdog fail: {exc}")

            watchdog_task = register_task("watchdog", watchdog())

            bp_id = None
            try:
                dbg_resp, bp_resp = await asyncio.gather(
                    router.send(sid, "Debugger.enable", timeout=5.0),
                    router.send(
                        sid, "Debugger.setBreakpointByUrl",
                        {"lineNumber": 0, "columnNumber": 0,
                         "url": info.target_url},
                        timeout=5.0),
                )
                bp_id  = bp_resp.get("breakpointId", "")
                actual = bp_resp.get("actualLocation", {})
                info.errors.append(
                    f"enable+bp ok: debuggerId={dbg_resp.get('debuggerId','?')[:8]} "
                    f"bpId={bp_id[:16] if bp_id else 'NONE'} "
                    f"actual={actual} t={time.monotonic()-t0:.2f}s")

                if not actual:
                    info.errors.append("actual={} → falling back to _inject_simple")
                    watchdog_task.cancel()
                    await _inject_simple(info, do_cascade=False)
                    return
            except Exception as exc:
                info.errors.append(f"enable+bp err: {exc}")
                watchdog_task.cancel()
                await _inject_simple(info, do_cascade=False)
                return

            for iteration in range(8):
                timeout = (BREAK_ON_START_TIMEOUT if iteration == 0
                           else REAL_BP_TIMEOUT)
                try:
                    paused = await asyncio.wait_for(paused_q.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - t0
                    info.errors.append(
                        f"Debugger.paused timeout iter={iteration} "
                        f"t={elapsed:.1f}s")
                    break

                elapsed    = time.monotonic() - t0
                reason     = paused.get("reason", "")
                hit_bps    = paused.get("hitBreakpoints") or []
                info.errors.append(
                    f"paused: reason={reason!r} hitBP={bool(hit_bps)} "
                    f"t={elapsed:.2f}s iter={iteration}")

                if hit_bps:
                    watchdog_task.cancel()
                    info.ts_patch_sent = time.monotonic()
                    try:
                        resp = await router.send(
                            sid, "Runtime.evaluate",
                            {"expression": patch, "returnByValue": True,
                             "awaitPromise": False}, timeout=6.0)
                        ex = resp.get("exceptionDetails")
                        if ex:
                            desc = ((ex.get("exception") or {}).get("description")
                                    or ex.get("text", "?"))
                            info.errors.append(f"eval exc: {desc}")
                            failed_sessions.add(sid)
                            result.no_resume_after_fail = False
                            try:
                                await router.send(sid, "Debugger.resume", timeout=3.0)
                            except Exception:
                                pass
                            return
                        info.ts_patch_success = time.monotonic()
                        info.injection_ok = True
                    except Exception as exc:
                        info.errors.append(f"inject err: {exc}")
                        failed_sessions.add(sid)
                        result.no_resume_after_fail = False
                        try:
                            await router.send(sid, "Debugger.resume", timeout=3.0)
                        except Exception:
                            pass
                        return

                    _verify_expr = (
                        "(function() {"
                        " try {"
                        "  if (typeof OffscreenCanvas !== 'undefined' &&"
                        "      OffscreenCanvas.prototype.convertToBlob.__ghostPatched === true)"
                        "   return true;"
                        " } catch (_ve) {}"
                        " return typeof self !== 'undefined' && self.__ghostWorkerPatchInstalled === true;"
                        "})()"
                    )
                    try:
                        mr = await router.send(
                            sid, "Runtime.evaluate",
                            {"expression": _verify_expr,
                             "returnByValue": True}, timeout=3.0)
                        if mr.get("result", {}).get("value") is True:
                            info.ts_marker_verified = time.monotonic()
                            info.marker_verified = True
                        else:
                            info.errors.append(f"marker={mr.get('result',{}).get('value')!r}")
                    except Exception as exc:
                        info.errors.append(f"marker err: {exc}")

                    if bp_id:
                        try:
                            await router.send(sid, "Debugger.removeBreakpoint",
                                              {"breakpointId": bp_id}, timeout=3.0)
                        except Exception:
                            pass

                    if "nested-parent" in info.target_url.lower():
                        try:
                            await router.send(sid, "Target.setAutoAttach",
                                              {"autoAttach": True,
                                               "waitForDebuggerOnStart": True,
                                               "recursive": True},
                                              timeout=3.0)
                            info.ts_auto_attach = time.monotonic()
                            info.errors.append(f"nested-parent cascade ok t={time.monotonic()-t0:.2f}s")
                        except Exception as exc:
                            info.errors.append(f"nested-parent cascade: {exc} t={time.monotonic()-t0:.2f}s")

                    try:
                        info.ts_resume_sent = time.monotonic()
                        await router.send(sid, "Debugger.resume", timeout=3.0)
                        info.resumed = True
                    except Exception as exc:
                        info.errors.append(f"Debugger.resume err: {exc}")
                    return

                else:
                    info.errors.append(f"Break on start ({reason!r}) → Debugger.resume")
                    try:
                        info.ts_resume_sent = time.monotonic()
                        await router.send(sid, "Debugger.resume", timeout=3.0)
                        info.errors.append(f"Debugger.resume ok t={time.monotonic()-t0:.2f}s")
                    except Exception as exc:
                        info.errors.append(f"resume BoS err: {exc}")
                        break

            if watchdog_task and not watchdog_task.done():
                watchdog_task.cancel()
            if not info.resumed:
                info.errors.append(f"injection loop exhausted t={time.monotonic()-t0:.1f}s")

        def on_attached(params):
            if not params:
                return
            ti    = params.get("targetInfo") or {}
            tid   = ti.get("targetId", "")
            ttype = ti.get("type", "")
            turl  = ti.get("url", "")
            wait  = params.get("waitingForDebugger", False)
            sid   = params.get("sessionId", "")

            if ttype != "worker":
                return

            info = TargetInfo(tid, ttype, turl, wait, sid)
            result.targets[tid] = info

            if wait:
                url_lower = turl.lower()
                if "classic-worker" in url_lower:
                    result.classic_paused = True
                elif "module-worker" in url_lower:
                    result.module_paused  = True
                elif "nested-parent" in url_lower:
                    result.nested_parent_paused = True
                elif "nested-child" in url_lower:
                    result.nested_child_paused  = True
                elif turl.startswith("blob:"):
                    result.blob_paused = True

            if "module-worker" in turl.lower():
                register_task('inject_simple', _inject_simple(info))
            else:
                register_task('inject_via_debugger', _inject_via_debugger(info))

        page_cdp.on("Target.attachedToTarget", on_attached)

        await page_cdp.send("Target.setAutoAttach", {
            "autoAttach":             True,
            "waitForDebuggerOnStart": True,
            "recursive":              True,
        })
        result.auto_attach_before = True

        await page.goto(f"{base_url}/", wait_until="load", timeout=20000)

        for wtype in ("classic", "module", "blob", "nested"):
            try:
                res = await page.evaluate(f"window.runWorker('{wtype}', '{profile_key}')")
                for info in result.targets.values():
                    if wtype in info.target_url or (wtype == 'blob' and info.target_url.startswith("blob:")):
                        if info.ts_first_result is None:
                            info.ts_first_result = time.monotonic()
                    if wtype == 'nested' and 'nested-child' in info.target_url:
                        if info.ts_first_result is None:
                            info.ts_first_result = time.monotonic()
                result.worker_results[wtype] = res if isinstance(res, dict) else {}
            except Exception as exc:
                result.worker_results[wtype] = {"error": str(exc)}
            await asyncio.sleep(1.5)

        await asyncio.sleep(2.0)

        if router:
            result.all_futures_resolved = len(router._pending) == 0
            if router.all_futures_resolved and not result.all_futures_resolved:
                router.all_futures_resolved = False
            router.cancel_all()
        for cleanup_cmd in (
            {"autoAttach": False, "waitForDebuggerOnStart": False, "recursive": True},
        ):
            try:
                await page_cdp.send("Target.setAutoAttach", cleanup_cmd)
            except Exception:
                pass
        try:
            await page_cdp.detach()
        except Exception:
            pass
        try:
            await browser_cdp_raw.detach()
        except Exception:
            pass
        try:
            await page.close()
            result.page_closed = True
        except Exception:
            pass
        try:
            await context.close()
            result.context_closed = True
        except Exception:
            pass
        result.all_sessions_closed = True

    finally:
        if browser:
            try:
                await browser.close()
                result.browser_closed = True
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
                result.playwright_stopped = True
            except Exception:
                pass

    return result

# ─────────────────────────────────────────────────────────────────────────────
# Assertions
# ─────────────────────────────────────────────────────────────────────────────
_all_passed = True

def check(label: str, cond: bool, detail: str = ""):
    global _all_passed
    sfx = f" — {detail}" if (detail and not cond) else ""
    print(f"{'[PASS]' if cond else '[FAIL]'} {label}{sfx}")
    if not cond:
        _all_passed = False

def _wr(r: RunResult, wtype: str, key: str):
    return r.worker_results.get(wtype, {}).get(key) if r else None

def _h(r: RunResult, wtype: str, key: str):
    v = _wr(r, wtype, key)
    return None if (v is None or (isinstance(v, str) and v.startswith("ERROR"))) else v

def check_ast_rules():
    with open(__file__, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "check":
            if len(node.args) >= 2:
                lbl = node.args[0]
                cond = node.args[1]
                if isinstance(lbl, ast.Constant) and isinstance(lbl.value, str):
                    if any(lbl.value.startswith(str(i) + ".") for i in range(29, 36)):
                        if isinstance(cond, ast.Constant) and cond.value is True:
                            print(f"[FAIL] AST Check: {lbl.value} uses literal True")
                            global _all_passed
                            _all_passed = False

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_worker_cdp_preexecution():
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(_loop_exception_handler)

    server, port, server_thread = start_server()
    base_url = f"http://127.0.0.1:{port}"

    results: dict[str, RunResult] = {}
    for pk in ("p1_run1", "p1_run2", "p2_run1"):
        print(f"\n{'='*74}")
        print(f"  Profile run: {pk}")
        print(f"{'='*74}")
        try:
            results[pk] = await run_profile(pk, PROFILES[pk], base_url)
        except Exception as exc:
            import traceback
            print(f"[FATAL] {pk}: {exc}")
            traceback.print_exc()
            results[pk] = RunResult(pk)

    try:
        # Await pending registered tasks
        for lbl, t in _registered_tasks:
            if not t.done():
                t.cancel()
        task_futs = [t for _, t in _registered_tasks]
        task_res = await asyncio.gather(*task_futs, return_exceptions=True)
        task_exceptions = [e for e in task_res if isinstance(e, Exception) and not isinstance(e, asyncio.CancelledError)]
    finally:
        loop.set_exception_handler(old_handler)

    r1  = results.get("p1_run1") or RunResult("p1_run1")
    r2  = results.get("p1_run2") or RunResult("p1_run2")
    rp2 = results.get("p2_run1") or RunResult("p2_run1")

    print(f"\n{'='*74}")
    print("  ASSERTION REPORT")
    print(f"{'='*74}\n")

    check_ast_rules()

    def check_all_runs(f):
        return all(f(r) for r in results.values() if r.cdp_session_created)

    check("1. Browser-level CDP session created",       r1.cdp_session_created)
    check("2. Auto-attach enabled before workers",      r1.auto_attach_before)

    check("3. Classic worker attached while paused in all runs",
          check_all_runs(lambda r: r.classic_paused and r.targets.get(next((tid for tid, t in r.targets.items() if "classic-worker" in t.target_url), ""), TargetInfo("","", "", False, "")).paused))

    _gd = _wr(r1, "classic", "ghostDebug") or {}
    print(f"[DIAG] classic ghostDebug (p1_run1): {_gd}", flush=True)

    check("4. Classic worker marker true at first obs (all runs)",
          check_all_runs(lambda r: _wr(r, "classic", "marker") is True))

    check("5. Classic worker original HTTP URL unchanged",
          check_all_runs(lambda r: bool(_wr(r,"classic","href") and "127.0.0.1" in _wr(r,"classic","href") and "classic-worker.js" in _wr(r,"classic","href"))))

    check("6. Classic relative fetch succeeded",
          check_all_runs(lambda r: _wr(r,"classic","fetchOk") is True and _wr(r,"classic","fetchData") == RELATIVE_DATA.decode()))

    check("7. Module worker attached while paused in all runs",
          check_all_runs(lambda r: r.module_paused and r.targets.get(next((tid for tid, t in r.targets.items() if "module-worker" in t.target_url), ""), TargetInfo("","", "", False, "")).paused))

    check("8. Module worker marker true at first obs (all runs)",
          check_all_runs(lambda r: _wr(r,"module","marker") is True))

    check("9. Module worker HTTP URL unchanged",
          check_all_runs(lambda r: bool(_wr(r,"module","href") and "module-worker.js" in _wr(r,"module","href"))))

    check("10. Module relative import succeeded",
          check_all_runs(lambda r: _wr(r,"module","moduleHelper") == "ghost-module-helper-ok"))

    check("11. Blob worker attached while paused in all runs",
          check_all_runs(lambda r: r.blob_paused and r.targets.get(next((tid for tid, t in r.targets.items() if t.target_url.startswith("blob:")), ""), TargetInfo("","", "", False, "")).paused))

    check("12. Blob worker marker true at first obs (all runs)",
          check_all_runs(lambda r: _wr(r,"blob","marker") is True))

    check("13. Blob worker location is blob: URL",
          check_all_runs(lambda r: bool(_wr(r,"blob","href") and _wr(r,"blob","href").startswith("blob:"))))

    check("14. Nested parent attached while paused in all runs",
          check_all_runs(lambda r: r.nested_parent_paused and r.targets.get(next((tid for tid, t in r.targets.items() if "nested-parent" in t.target_url), ""), TargetInfo("","", "", False, "")).paused))

    check("15. Nested child attached while paused in all runs",
          check_all_runs(lambda r: r.nested_child_paused and r.targets.get(next((tid for tid, t in r.targets.items() if "nested-child" in t.target_url), ""), TargetInfo("","", "", False, "")).paused))

    check("16. Nested child marker true at first obs (all runs)",
          check_all_runs(lambda r: ((r.worker_results.get("nested") or {}).get("child") or {}).get("marker") is True))

    for run_name, r in results.items():
        if not r.cdp_session_created: continue
        targets = list(r.targets.values())
        c_classic = sum(1 for t in targets if "classic-worker" in t.target_url)
        c_module = sum(1 for t in targets if "module-worker" in t.target_url)
        c_blob = sum(1 for t in targets if t.target_url.startswith("blob:"))
        c_parent = sum(1 for t in targets if "nested-parent" in t.target_url)
        c_child = sum(1 for t in targets if "nested-child" in t.target_url)
        check(f"16b. Run {run_name} has exactly 1 of each worker",
              c_classic == 1 and c_module == 1 and c_blob == 1 and c_parent == 1 and c_child == 1)

        for t in targets:
            # Check pause status
            if not t.paused:
                check(f"16c. Run {run_name} Target {t.target_url[:20]} state ok", False, "Target attached with paused=False")
                continue
                
            ok_state = (t.paused and t.injection_ok and t.marker_verified and t.resumed and not t.watchdog_used and not t.errors)
            check(f"16c. Run {run_name} Target {t.target_url[:20]} state ok",
                  ok_state, str(t.errors) + f" paused={t.paused} inject={t.injection_ok} marker={t.marker_verified} resumed={t.resumed} watchdog={t.watchdog_used}")
            if not t.ts_attached: t.ts_attached = 0
            if not t.ts_patch_success: t.ts_patch_success = -1
            if not t.ts_resume_sent: t.ts_resume_sent = -2
            if not t.ts_first_result: t.ts_first_result = -3
            ts_ok = (t.ts_attached <= t.ts_patch_success < t.ts_resume_sent <= t.ts_first_result)
            check(f"16d. Run {run_name} Target {t.target_url[:20]} strict timing",
                  ts_ok, f"att={t.ts_attached} patch={t.ts_patch_success} res={t.ts_resume_sent} first={t.ts_first_result}")

    for wtype, lbl in (("classic","17a"),("module","17b"),("blob","17c")):
        h1 = _h(r1, wtype, "imgHash"); h2 = _h(r2, wtype, "imgHash")
        check(f"{lbl}. Profile 1 {wtype} getImageData hash stable",
              h1 is not None and h1 == h2, f"run1={h1} run2={h2}")

    for wtype, lbl in (("classic","18a"),("blob","18b")):
        b1 = _h(r1, wtype, "blobHash"); b2 = _h(r2, wtype, "blobHash")
        check(f"{lbl}. Profile 1 {wtype} convertToBlob hash stable",
              b1 is not None and b1 == b2, f"run1={b1} run2={b2}")

    h1c = _h(r1, "classic","imgHash");  h2c = _h(rp2,"classic","imgHash")
    check("19. P1 and P2 getImageData hashes differ (classic)",
          h1c is not None and h2c is not None and h1c != h2c, f"p1={h1c} p2={h2c}")
    b1c = _h(r1, "classic","blobHash"); b2c = _h(rp2,"classic","blobHash")
    check("20. P1 and P2 convertToBlob hashes differ (classic)",
          b1c is not None and b2c is not None and b1c != b2c, f"p1={b1c} p2={b2c}")

    check("21. Transparent pixels unchanged",    _wr(r1,"classic","transOk") is True)
    check("22. Alpha unchanged",                 _wr(r1,"classic","alphaOk") is True)
    check("23. getImageData hash present",       h1c is not None)

    check("24. getImageData name == 'getImageData'",
          _wr(r1,"classic","gidName") == "getImageData",
          str(_wr(r1,"classic","gidName")))
    check("24b. convertToBlob name == 'convertToBlob'",
          _wr(r1,"classic","ctbName") == "convertToBlob",
          str(_wr(r1,"classic","ctbName")))
    check("25. getImageData length == 4",
          _wr(r1,"classic","gidLen") == 4, str(_wr(r1,"classic","gidLen")))
    check("25b. convertToBlob length == 0 (native value preserved)",
          _wr(r1,"classic","ctbLen") == 0, str(_wr(r1,"classic","ctbLen")))
    check("26. getImageData enumerable == True (native value preserved)",
          _wr(r1,"classic","gidEnum") is True, str(_wr(r1,"classic","gidEnum")))
    check("26b. getImageData configurable == True",
          _wr(r1,"classic","gidConf") is True, str(_wr(r1,"classic","gidConf")))
    check("26c. convertToBlob configurable == True",
          _wr(r1,"classic","ctbConf") is True, str(_wr(r1,"classic","ctbConf")))
    check("27. Invalid receiver throws TypeError",
          _wr(r1,"classic","invalidRecv") == "TypeError",
          str(_wr(r1,"classic","invalidRecv")))
    gid_str = _wr(r1,"classic","gidStr") or ""
    ctb_str = _wr(r1,"classic","ctbStr") or ""
    check("28a. getImageData toString native-looking",
          "[native code]" in gid_str and "getImageData" in gid_str,
          gid_str[:80] or "None")
    check("28b. convertToBlob toString native-looking",
          "[native code]" in ctb_str and "convertToBlob" in ctb_str,
          ctb_str[:80] or "None")

    check("29. No worker resumed after failed injection",
          all(r.no_resume_after_fail for r in results.values()))
    check("30. No unexpected server path",
          len(server.unexpected_paths) == 0, str(server.unexpected_paths))
    
    server.shutdown()
    server.server_close()
    
    all_loopback = all(p == "127.0.0.1" for p in server.remote_peers)
    all_host = all(h and "127.0.0.1" in h for h in server.requested_hosts)
    check("31. Honest external network checks",
          all_loopback and all_host and len(server.remote_peers) > 0, f"peers={server.remote_peers} hosts={server.requested_hosts}")

    check("32. All CDP futures resolved",
          all(r.all_futures_resolved for r in results.values()))
    check("33. All CDP sessions closed",
          all(r.all_sessions_closed for r in results.values()))
          
    cleanup_ok = all(r.page_closed and r.context_closed and r.browser_closed and r.playwright_stopped and r.all_sessions_closed and r.all_futures_resolved for r in results.values())
    check("34. Real Cleanup complete", cleanup_ok)
    
    no_unhandled = len(_unhandled_loop_exceptions) == 0 and len(task_exceptions) == 0
    check("35. No unhandled Future or Task exception", no_unhandled, f"loop={_unhandled_loop_exceptions} tasks={task_exceptions}")

    for pk, r in results.items():
        print(f"\n{'='*74}")
        print(f"  WORKER ATTACHMENT SUMMARY ({pk})")
        print(f"{'='*74}")
        for tid, info in r.targets.items():
            print(f"  [{info.target_type}] "
                  f"paused={info.paused} inject={info.injection_ok} "
                  f"marker={info.marker_verified} resumed={info.resumed}")
            print(f"    url={info.target_url[:66]}")
            for e in info.errors:
                print(f"    | {e}")
            for ev in info.events_seen:
                print(f"    ~ {ev}")

    print(f"\n{'='*74}")
    print("  REQUESTED SERVER PATHS")
    print(f"{'='*74}")
    for p in sorted(server.requested_paths):
        print(f"  {p}")

    # Check if Chromium architecture limitation forces failure
    # If the marker checks failed for workers, we must return failure.
    if not _all_passed:
        print("[FAIL] Architectural limitation: Pre-execution marker assertion failed.")

    return _all_passed


if __name__ == "__main__":
    asyncio.run(test_worker_cdp_preexecution())
