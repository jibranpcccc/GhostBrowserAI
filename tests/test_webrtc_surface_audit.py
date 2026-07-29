import os
import sys
import asyncio
import tempfile
import shutil
import threading
import http.server
import socketserver
from typing import Any
import traceback

PROCESS_SYS_PATH = list(sys.path)
PROCESS_ENVIRON = dict(os.environ)

FAILURES = []
def check(name: str, condition: bool):
    if condition: print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name}")
        FAILURES.append(name)

# Ensure backend can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from backend.browser_manager import build_browser_launch_config
from playwright.async_api import async_playwright

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args): pass

def start_server(port, directory):
    class Handler(QuietHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

    server = socketserver.TCPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread

JS_SCRIPT = '''
async () => {
    let results = {
        api: {},
        media: {},
        events: {},
        gathering: {},
        stats: [],
        sdp_sources: {
            offer: "",
            localDescription: "",
            currentLocalDescription: "",
            pendingLocalDescription: ""
        }
    };

    // API Parity
    results.api["RTCPeerConnection"] = {
        type: typeof RTCPeerConnection,
        length: typeof RTCPeerConnection === "function" ? RTCPeerConnection.length : -1,
        protoConstructor: typeof RTCPeerConnection === "function" ? RTCPeerConnection.prototype.constructor === RTCPeerConnection : false,
        toString: typeof RTCPeerConnection === "function" ? Function.prototype.toString.call(RTCPeerConnection) : ""
    };

    let desc = Object.getOwnPropertyDescriptor(window, "RTCPeerConnection");
    if (desc) {
        results.api.descriptor = {
            writable: desc.writable,
            enumerable: desc.enumerable,
            configurable: desc.configurable,
            valueEquals: desc.value === RTCPeerConnection
        };
    }

    let pc;
    try {
        RTCPeerConnection();
        results.api.callWithoutNew = "Did not throw";
    } catch(e) { results.api.callWithoutNew = e.name; }

    try {
        pc = new RTCPeerConnection({iceServers: []});
    } catch(e) {
        results.api.instantiation = e.message;
        return results;
    }

    results.api["createOffer"] = {
        length: pc.createOffer.length,
        toString: Function.prototype.toString.call(pc.createOffer),
        proto: pc.createOffer === RTCPeerConnection.prototype.createOffer,
        descriptors: Object.keys(Object.getOwnPropertyDescriptors(pc.createOffer)).sort()
    };

    results.api["setLocalDescription"] = { toString: Function.prototype.toString.call(pc.setLocalDescription) };
    results.api["getStats"] = { toString: Function.prototype.toString.call(pc.getStats) };
    results.api["addEventListener"] = { toString: Function.prototype.toString.call(pc.addEventListener) };

    try {
        RTCPeerConnection.prototype.createOffer.call({});
        results.api.illegalReceiver = "Did not throw";
    } catch(e) { results.api.illegalReceiver = e.name; }

    results.api.ownProps = Object.getOwnPropertyNames(pc).sort();
    let enumProps = [];
    for (let k in pc) enumProps.push(k);
    results.api.enumProps = enumProps.sort();

    results.media["mediaDevicesType"] = typeof navigator.mediaDevices;
    if (navigator.mediaDevices) {
        results.media["getUserMediaToString"] = Function.prototype.toString.call(navigator.mediaDevices.getUserMedia);
    }

    // Events
    let eventRes = results.events;
    eventRes.thisCorrect = false;
    let candFromEvent = [];
    let candFromListener = [];

    let onceFired = 0;
    pc.addEventListener("icecandidate", () => { onceFired++; }, {once: true});

    pc.addEventListener("icecandidate", e => {
        if (e.candidate) candFromListener.push(e.candidate.candidate);
    });

    let removedFired = false;
    let removedFn = () => { removedFired = true; };
    pc.addEventListener("icecandidate", removedFn);
    pc.removeEventListener("icecandidate", removedFn);

    let handleEventFired = false;
    pc.addEventListener("icecandidate", { handleEvent: () => { handleEventFired = true; }});

    let gatherRes = { candidates: [], endCount: 0 };

    pc.onicecandidate = function(e) {
        if (this === pc) eventRes.thisCorrect = true;
        if (e.candidate) {
            candFromEvent.push(e.candidate.candidate);
            gatherRes.candidates.push(e.candidate.candidate);
        } else {
            gatherRes.endCount++;
        }
    };

    pc.createDataChannel("test");
    let offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    // Wait for gathering complete
    let waitStart = Date.now();
    while (pc.iceGatheringState !== "complete" && (Date.now() - waitStart < 5000)) {
        await new Promise(r => setTimeout(r, 50));
    }

    results.gathering.completed = pc.iceGatheringState === "complete";
    results.gathering.stateComplete = pc.iceGatheringState === "complete";
    results.gathering.endCount = gatherRes.endCount;
    results.gathering.candidates = gatherRes.candidates;

    eventRes.onceCount = onceFired;
    eventRes.removedFired = removedFired;
    eventRes.handleEventFired = handleEventFired;
    eventRes.candFromEventMatch = JSON.stringify(candFromEvent) === JSON.stringify(candFromListener);

    results.sdp_sources.offer = offer.sdp;
    if (pc.localDescription) results.sdp_sources.localDescription = pc.localDescription.sdp;
    if (pc.currentLocalDescription) results.sdp_sources.currentLocalDescription = pc.currentLocalDescription.sdp;
    if (pc.pendingLocalDescription) results.sdp_sources.pendingLocalDescription = pc.pendingLocalDescription.sdp;

    let stats = await pc.getStats();
    stats.forEach(stat => {
        results.stats.push(stat);
    });

    pc.close();
    return results;
}
'''

def classify_ip(ip):
    ip = str(ip).strip()
    if not ip or ip == "None" or ip.endswith(".local"): return "mDNS"
    if ':' in ip:
        if ip == '::': return "unspecified IPv6"
        if ip == '::1': return "loopback IPv6"
        if ip.lower().startswith('fe80:'): return "link-local IPv6"
        if ip.lower().startswith(('fc', 'fd')): return "unique-local IPv6"
        return "public IPv6"
    else:
        parts = ip.split('.')
        if len(parts) != 4: return "unknown"
        if ip == '0.0.0.0': return "unspecified IPv4"
        if ip.startswith('127.'): return "loopback IPv4"
        if ip.startswith('169.254.'): return "link-local IPv4"
        if ip.startswith('10.') or ip.startswith('192.168.') or (ip.startswith('172.') and 16 <= int(parts[1]) <= 31):
            return "private IPv4"
        return "public IPv4"

def extract_ips(sdp):
    ips = []
    import re
    for line in sdp.split('\n'):
        line = line.strip()
        if line.startswith('c=IN '):
            ips.append(line.split(' ')[2])
        elif line.startswith('a=candidate:'):
            parts = line.split(' ')
            if len(parts) > 4:
                ips.append(parts[4])
    return ips

async def run_mode(p, mode, port1, port2):
    import uuid
    prof = {
        "id": str(uuid.uuid4()),
        "advanced": {
            "webrtc_mode": mode if mode != "NATIVE" else "protected",
            "block_service_workers": False,
        }
    }

    config = await build_browser_launch_config(prof)
    if mode == "NATIVE":
        config["args"] = [a for a in config.get("args", []) if "webrtc" not in a.lower()]
        config["spoofing_script"] = ""

    browser = None
    context = None
    page = None
    try:
        browser = await p.chromium.launch(
            headless=True,
            args=config.get("args", [])
        )
        context = await browser.new_context()

        if config.get("spoofing_script"):
            await context.add_init_script(config["spoofing_script"])

        page = await context.new_page()
        await page.goto(f"http://127.0.0.1:{port1}/")

        main_results = await page.evaluate(JS_SCRIPT)

        await page.evaluate(f'''() => {{
            let f1 = document.createElement("iframe");
            f1.src = "http://127.0.0.1:{port1}/?frame=same";
            f1.name = "same-origin-frame";
            document.body.appendChild(f1);

            let f2 = document.createElement("iframe");
            f2.src = "http://127.0.0.1:{port2}/?frame=cross";
            f2.name = "cross-origin-frame";
            document.body.appendChild(f2);
        }}''')

        same_origin_frame = None
        cross_origin_frame = None
        for _ in range(100):
            for frame in page.frames:
                if frame.name == "same-origin-frame":
                    same_origin_frame = frame
                elif frame.name == "cross-origin-frame":
                    cross_origin_frame = frame
            if same_origin_frame and cross_origin_frame:
                break
            await page.wait_for_timeout(50)

        check(f"{mode} same-origin iframe attached", same_origin_frame is not None)
        check(f"{mode} cross-origin iframe attached", cross_origin_frame is not None)
        if same_origin_frame is None or cross_origin_frame is None:
            raise RuntimeError("Required WebRTC audit iframe did not attach")

        await same_origin_frame.wait_for_load_state("load")
        await cross_origin_frame.wait_for_load_state("load")
        check(f"{mode} iframe same-origin URL contains port1", str(port1) in same_origin_frame.url)
        check(f"{mode} iframe cross-origin URL contains port2", str(port2) in cross_origin_frame.url)

        iframe_results = {
            "same_origin": await same_origin_frame.evaluate(JS_SCRIPT),
            "cross_origin": await cross_origin_frame.evaluate(JS_SCRIPT),
        }
        return {"main": main_results, "iframes": iframe_results}
    finally:
        if page is not None:
            await page.close()
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()


def collect_privacy_observations(result, mode, surface_name):
    raw_ips = []
    mdns = []
    unresolved_pairs = []

    def process_ip(ip):
        cls = classify_ip(ip)
        if cls == "mDNS":
            mdns.append(str(ip))
        elif cls not in ("unspecified IPv4", "unspecified IPv6"):
            raw_ips.append(str(ip))

    gathering = result["gathering"]
    for candidate in gathering["candidates"]:
        parts = candidate.split(" ")
        if len(parts) > 4:
            process_ip(parts[4])

    for sdp in result["sdp_sources"].values():
        for ip in extract_ips(sdp):
            process_ip(ip)

    stats_map = {stat["id"]: stat for stat in result["stats"] if stat.get("id")}
    for stat in result["stats"]:
        if stat.get("type") == "local-candidate":
            for key in ("address", "ip"):
                if stat.get(key):
                    process_ip(stat[key])

        if stat.get("type") == "candidate-pair" and stat.get("localCandidateId"):
            local_id = stat["localCandidateId"]
            local_candidate = stats_map.get(local_id)
            if local_candidate is None:
                unresolved_pairs.append(local_id)
                continue
            for key in ("address", "ip"):
                if local_candidate.get(key):
                    process_ip(local_candidate[key])

    check(f"{mode} {surface_name} candidate-pair references resolved", not unresolved_pairs)
    check(f"{mode} {surface_name} privacy union has no raw IPs", not raw_ips)
    if raw_ips:
        print(f"{mode} {surface_name} leaked classifications: {raw_ips}")
    print(f"{mode} {surface_name} mDNS separately: {mdns}")
    return raw_ips

def verify_webrtc_results(mode, results, is_native=False, native_ref=None):
    if not is_native and native_ref:
        m = results["main"]
        n = native_ref["main"]
        check(f"{mode} constructor typeof matches NATIVE", m["api"]["RTCPeerConnection"]["type"] == n["api"]["RTCPeerConnection"]["type"])
        check(f"{mode} constructor length matches NATIVE", m["api"]["RTCPeerConnection"]["length"] == n["api"]["RTCPeerConnection"]["length"])
        check(f"{mode} constructor prototype.constructor matches NATIVE", m["api"]["RTCPeerConnection"]["protoConstructor"] == n["api"]["RTCPeerConnection"]["protoConstructor"])
        check(f"{mode} Function.prototype.toString matches NATIVE", m["api"]["RTCPeerConnection"]["toString"] == n["api"]["RTCPeerConnection"]["toString"])

        check(f"{mode} createOffer length matches NATIVE", m["api"]["createOffer"]["length"] == n["api"]["createOffer"]["length"])
        check(f"{mode} call without new matches NATIVE", m["api"]["callWithoutNew"] == n["api"]["callWithoutNew"])
        check(f"{mode} illegal receiver matches NATIVE", m["api"]["illegalReceiver"] == n["api"]["illegalReceiver"])

        check(f"{mode} own props parity", m["api"]["ownProps"] == n["api"]["ownProps"])
        check(f"{mode} enum props parity", m["api"]["enumProps"] == n["api"]["enumProps"])
        check(f"{mode} createOffer descriptors parity", m["api"]["createOffer"]["descriptors"] == n["api"]["createOffer"]["descriptors"])

        check(f"{mode} createOffer toString parity", m["api"]["createOffer"]["toString"] == n["api"]["createOffer"]["toString"])
        check(f"{mode} setLocalDescription toString parity", m["api"]["setLocalDescription"]["toString"] == n["api"]["setLocalDescription"]["toString"])
        check(f"{mode} getStats toString parity", m["api"]["getStats"]["toString"] == n["api"]["getStats"]["toString"])
        check(f"{mode} addEventListener toString parity", m["api"]["addEventListener"]["toString"] == n["api"]["addEventListener"]["toString"])

        check(f"{mode} media devices parity", m["media"] == n["media"])

        pe = m["events"]
        ne = n["events"]
        check(f"{mode} event listener sequence parity", pe["candFromEventMatch"])
        check(f"{mode} handleEvent object parity", pe["handleEventFired"] == ne["handleEventFired"])
        check(f"{mode} once listener count", pe["onceCount"] == 1)
        check(f"{mode} removed listener does not fire", not pe["removedFired"])
        check(f"{mode} correct 'this'", pe["thisCorrect"])

        g = m["gathering"]
        check(f"{mode} gathering completed", g["completed"])
        check(f"{mode} gatheringState complete", g["stateComplete"])
        check(f"{mode} endCount > 0", g["endCount"] > 0)

        collect_privacy_observations(m, mode, "main frame")

        for frame_name, frame_res in results.get("iframes", {}).items():
            pi = frame_res
            ni = native_ref.get("iframes", {}).get(frame_name, native_ref["main"])

            check(f"{mode} {frame_name} Iframe API parity", pi["api"] == ni["api"])
            check(f"{mode} {frame_name} Iframe media parity", pi["media"] == ni["media"])

            g_i = pi["gathering"]
            check(f"{mode} {frame_name} Iframe gathering completed", g_i["completed"])
            check(f"{mode} {frame_name} Iframe gathering state complete", g_i["stateComplete"])
            check(f"{mode} {frame_name} Iframe endCount > 0", g_i["endCount"] > 0)
            collect_privacy_observations(pi, mode, f"{frame_name} iframe")

async def main():
    temp_owner = tempfile.TemporaryDirectory()
    temp_dir = temp_owner.name
    temp_existed = os.path.isdir(temp_dir)
    loop = asyncio.get_running_loop()
    original_exception_handler = loop.get_exception_handler()
    unhandled_loop_errors = []

    def capture_loop_error(_loop, context):
        unhandled_loop_errors.append(context)

    loop.set_exception_handler(capture_loop_error)

    # Explicit isolated test environment: production correctly requires a
    # verified proxy before build_browser_launch_config can run.
    import backend.browser_manager as browser_manager_module
    original_profiles_dir = browser_manager_module.profile_manager.PROFILES_DIR
    os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
    os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir
    browser_manager_module.profile_manager.PROFILES_DIR = temp_dir

    server1, thread1 = start_server(0, temp_dir)
    port1 = server1.server_address[1]

    server2, thread2 = start_server(0, temp_dir)
    port2 = server2.server_address[1]

    try:
        print("\n--- Testing Unsafe Modes ---")
        for umode in ["real", "disabled"]:
            failed_closed = False
            try:
                import uuid
                await build_browser_launch_config({"id": str(uuid.uuid4()), "advanced": {"webrtc_mode": umode}})
            except RuntimeError:
                failed_closed = True
            check(f"Unsafe {umode} fail closed", failed_closed)

        print("\n--- Testing Modes ---")
        async with async_playwright() as p:
            native = await run_mode(p, "NATIVE", port1, port2)
            protected = await run_mode(p, "protected", port1, port2)

            verify_webrtc_results("PROTECTED", protected, native_ref=native)

    finally:
        server1.shutdown()
        server1.server_close()
        server2.shutdown()
        server2.server_close()

        thread1.join(timeout=5)
        thread2.join(timeout=5)

        check("Server 1 thread joined cleanly", not thread1.is_alive())
        check("Server 2 thread joined cleanly", not thread2.is_alive())

        browser_manager_module.profile_manager.PROFILES_DIR = original_profiles_dir
        loop.set_exception_handler(original_exception_handler)
        temp_owner.cleanup()

        sys.path[:] = PROCESS_SYS_PATH
        os.environ.clear()
        os.environ.update(PROCESS_ENVIRON)

        check("TemporaryDirectory existed during audit", temp_existed)
        check("sys.path is exactly equal to its process-start list", sys.path == PROCESS_SYS_PATH)
        check("os.environ is exactly equal to its process-start dictionary", dict(os.environ) == PROCESS_ENVIRON)
        check("TemporaryDirectory does not exist after cleanup", not os.path.exists(temp_dir))
        check("no unhandled asyncio exceptions occurred", not unhandled_loop_errors)

    if FAILURES:
        print("\nSUMMARY OF FAILURES:")
        for f in FAILURES:
            print(f" - {f}")
        sys.exit(1)
    else:
        print("\nALL TESTS PASSED")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
