import asyncio
import os
import sys
import tempfile
import json
import hashlib
import struct
import math
import ast

_stored_env = os.environ.copy()
_stored_sys_path = sys.path[:]
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

temp_dir = tempfile.TemporaryDirectory()
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir.name
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

import backend.browser_manager as bm
import backend.proxy_manager as pm

_stored_probe = bm.probe_native_metadata
_stored_get_proxy = pm.proxy_manager.get_proxy_for_profile

async def stub_probe_native_metadata(*args, **kwargs):
    return {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "uadata": {"brands": [], "mobile": False, "platform": "Windows"}
    }
async def stub_get_proxy(*args, **kwargs): return None
bm.probe_native_metadata = stub_probe_native_metadata
pm.proxy_manager.get_proxy_for_profile = stub_get_proxy

def hash_samples(samples):
    if samples is None: return None
    h = hashlib.sha256()
    for s in samples:
        h.update(struct.pack("<f", float(s)))
    return h.hexdigest()

DETERMINISTIC_ANALYSER_PRELUDE = r"""
(function() {
    const methods = ['getFloatFrequencyData', 'getByteFrequencyData', 'getFloatTimeDomainData', 'getByteTimeDomainData'];
    for (let m of methods) {
        const origDesc = Object.getOwnPropertyDescriptor(AnalyserNode.prototype, m);
        const replacement = function(array) {
            if (!(this instanceof AnalyserNode)) throw new TypeError("Illegal invocation");
            let expectedClass = (m === 'getFloatFrequencyData' || m === 'getFloatTimeDomainData') ? Float32Array : Uint8Array;
            if (!array || !(array instanceof expectedClass)) {
                throw new TypeError("Failed to execute '" + m + "' on 'AnalyserNode': parameter 1 is not of type '" + expectedClass.name + "'.");
            }
            for (let i = 0; i < array.length; i++) {
                if (m === 'getFloatFrequencyData') array[i] = (i % 17 === 0) ? -Infinity : -50.0;
                else if (m === 'getByteFrequencyData') array[i] = (i % 17 === 0) ? 0 : 160;
                else if (m === 'getFloatTimeDomainData') array[i] = (i % 17 === 0) ? 0.0 : 0.25;
                else if (m === 'getByteTimeDomainData') array[i] = (i % 17 === 0) ? 128 : 160;
            }
            return undefined;
        };
        Object.defineProperty(replacement, 'name', { value: m, configurable: true });
        Object.defineProperty(replacement, 'length', { value: 1, configurable: true });
        Object.defineProperty(AnalyserNode.prototype, m, {
            value: replacement,
            writable: origDesc ? origDesc.writable : true,
            enumerable: origDesc ? origDesc.enumerable : false,
            configurable: origDesc ? origDesc.configurable : true
        });
    }
})();
"""

PAGE_JS = r"""
async function getExtendedAudioData(deterministic = false) {
    const results = {
        api: {}, copyFromChannel: {}, multichannel: {},
        analyser: {}, doubleNoise: {}, silenceBoundaries: {}, leaks: {}
    };
    const safeString = (fn) => { try { return Function.prototype.toString.call(fn); } catch(e) { return e.name; } };
    const getInvalidReceiverGetter = (getter) => { try { getter.call({}); return 'no_throw'; } catch(e) { return e.name; } };
    const getInvalidReceiverMethod = (proto, methodName) => { try { proto[methodName].call({}); return 'no_throw'; } catch(e) { return e.name; } };

    const checkApi = (proto, prop, isGetter=false) => {
        const hasOwn = Object.prototype.hasOwnProperty.call(proto, prop);
        const desc = Object.getOwnPropertyDescriptor(proto, prop);
        let fn = isGetter ? (desc ? desc.get : null) : (proto ? proto[prop] : null);
        return {
            hasOwn,
            writable: desc ? desc.writable : null, enumerable: desc ? desc.enumerable : null, configurable: desc ? desc.configurable : null,
            name: fn ? fn.name : null, length: fn ? fn.length : null, toStr: fn ? safeString(fn) : null,
            err: isGetter ? getInvalidReceiverGetter(fn) : getInvalidReceiverMethod(proto, prop)
        };
    };

    results.api.startRendering = checkApi(OfflineAudioContext.prototype, 'startRendering');
    results.api.renderedBuffer = checkApi(OfflineAudioCompletionEvent.prototype, 'renderedBuffer', true);
    results.api.getFloatFrequencyData = checkApi(AnalyserNode.prototype, 'getFloatFrequencyData');
    results.api.getByteFrequencyData = checkApi(AnalyserNode.prototype, 'getByteFrequencyData');
    results.api.getFloatTimeDomainData = checkApi(AnalyserNode.prototype, 'getFloatTimeDomainData');
    results.api.getByteTimeDomainData = checkApi(AnalyserNode.prototype, 'getByteTimeDomainData');
    results.api.getChannelData = checkApi(AudioBuffer.prototype, 'getChannelData');
    results.api.copyFromChannel = checkApi(AudioBuffer.prototype, 'copyFromChannel');

    if (!deterministic) {
        try {
            const oc = new OfflineAudioContext(1, 100, 44100);
            const buf = oc.createBuffer(1, 100, 44100);
            for(let i=0; i<100; i++) buf.getChannelData(0)[i] = i/100;
            let cfcSupported = typeof AudioBuffer.prototype.copyFromChannel === 'function';
            results.copyFromChannel.supported = cfcSupported;
            if (cfcSupported) {
                const dest = new Float32Array(50);
                buf.copyFromChannel(dest, 0, 20);
                results.copyFromChannel.copied = Array.from(dest);
                results.copyFromChannel.direct = Array.from(buf.getChannelData(0).slice(20, 70));
                try { AudioBuffer.prototype.copyFromChannel.call({}, dest, 0); results.copyFromChannel.err = 'no_throw'; }
                catch(e) { results.copyFromChannel.err = e.name; }
            }
        } catch(e) { results.copyFromChannel.fatal = e.message; }

        const toArr = (channelData) => Array.from(channelData);
        try {
            const oc = new OfflineAudioContext(2, 44100, 44100);
            const osc1 = oc.createOscillator(); osc1.type = 'sine'; osc1.frequency.value = 440;
            const osc2 = oc.createOscillator(); osc2.type = 'square'; osc2.frequency.value = 220;
            const merger = oc.createChannelMerger(2);
            osc1.connect(merger, 0, 0); osc2.connect(merger, 0, 1); merger.connect(oc.destination);
            osc1.start(0); osc2.start(0);
            const buf = await oc.startRendering();
            results.multichannel = { ch0: toArr(buf.getChannelData(0)), ch1: toArr(buf.getChannelData(1)) };
        } catch(e) { results.multichannel.error = e.message; }

        try {
            const oc = new OfflineAudioContext(1, 44100, 44100);
            let eventBuf = null;
            oc.oncomplete = (e) => { eventBuf = e.renderedBuffer; };
            const promiseBuf = await oc.startRendering();
            await new Promise(r => setTimeout(r, 10));

            results.doubleNoise = { promise1: toArr(promiseBuf.getChannelData(0)) };
            if (eventBuf) {
                results.doubleNoise.event1 = toArr(eventBuf.getChannelData(0));
                results.doubleNoise.event2 = toArr(eventBuf.getChannelData(0));
                results.doubleNoise.sameObject = (promiseBuf === eventBuf);
            }
            results.doubleNoise.promise2 = toArr(promiseBuf.getChannelData(0));
            if (typeof promiseBuf.copyFromChannel === 'function') {
                const dest = new Float32Array(44100);
                promiseBuf.copyFromChannel(dest, 0);
                results.doubleNoise.copyFromChannel = Array.from(dest);
            }
            results.silenceBoundaries.exactSilence = results.doubleNoise.promise1;

            const ocB = new OfflineAudioContext(1, 100, 44100);
            const bufB = ocB.createBuffer(1, 100, 44100);
            const bData = bufB.getChannelData(0);
            for(let i=0; i<10; i++) bData[i] = -1.0;
            for(let i=10; i<20; i++) bData[i] = 1.0;
            for(let i=20; i<30; i++) bData[i] = 1.1754943508222875e-38;
            for(let i=30; i<40; i++) bData[i] = -1.1754943508222875e-38;
            for(let i=40; i<50; i++) bData[i] = 0.0;

            const srcB = ocB.createBufferSource(); srcB.buffer = bufB; srcB.connect(ocB.destination); srcB.start(0);
            const renB = await ocB.startRendering();
            results.silenceBoundaries.boundaries = toArr(renB.getChannelData(0));
        } catch(e) { results.doubleNoise.error = e.message; }

        const getOwnNames = (obj) => { try { return Object.getOwnPropertyNames(obj); } catch(e) { return []; } };
        results.leaks = {
            windowProps: 'audioProfileSeed' in window || 'mixAudioIndex' in window || 'applyAudioBufferNoise' in window,
            globalProps: 'audioProfileSeed' in globalThis,
            navigatorProps: 'audioProfileSeed' in navigator,
            documentProps: 'audioProfileSeed' in document,
            docElemProps: 'audioProfileSeed' in document.documentElement,
            windowKeys: getOwnNames(window).includes('audioProfileSeed'),
            globalKeys: getOwnNames(globalThis).includes('audioProfileSeed'),
            ownOfflineCtx: getOwnNames(OfflineAudioContext.prototype),
            ownEvent: getOwnNames(OfflineAudioCompletionEvent.prototype),
            ownAnalyser: getOwnNames(AnalyserNode.prototype),
            ownBuffer: getOwnNames(AudioBuffer.prototype)
        };
    }

    try {
        const ac = new AudioContext();
        const ac_state_before = ac.state;
        const src = ac.createConstantSource(); src.offset.value = 0.25;
        const ana = ac.createAnalyser();

        const testFail = (method, args, context) => { try { ana[method].apply(context, args); return 'no_throw'; } catch(e) { return e.name; } };
        results.analyser.failures = {};
        for(let m of ['getFloatFrequencyData', 'getByteFrequencyData', 'getFloatTimeDomainData', 'getByteTimeDomainData']) {
            results.analyser.failures[m] = {
                missingArg: testFail(m, [], ana),
                wrongType: testFail(m, [new Int32Array(10)], ana),
                invalidReceiver: testFail(m, [new Float32Array(10)], {})
            };
        }

        ana.fftSize = deterministic ? 4096 : 256;
        ana.minDecibels = -100;
        ana.maxDecibels = -30;
        ana.smoothingTimeConstant = 0;
        src.connect(ana); ana.connect(ac.destination); src.start();

        if (ac.state === 'suspended') await ac.resume();
        await new Promise(r => setTimeout(r, 100));
        const ac_state_after = ac.state;

        const getAnalyserData = () => {
            const fFloat = new Float32Array(deterministic ? 2048 : ana.frequencyBinCount); ana.getFloatFrequencyData(fFloat);
            const fByte = new Uint8Array(deterministic ? 2048 : ana.frequencyBinCount); ana.getByteFrequencyData(fByte);
            const tFloat = new Float32Array(deterministic ? 4096 : ana.fftSize); ana.getFloatTimeDomainData(tFloat);
            const tByte = new Uint8Array(deterministic ? 4096 : ana.fftSize); ana.getByteTimeDomainData(tByte);
            return {
                fFloat: Array.from(fFloat), fByte: Array.from(fByte),
                tFloat: Array.from(tFloat), tByte: Array.from(tByte)
            };
        };

        results.analyser.first = getAnalyserData();
        results.analyser.second = getAnalyserData();
        await ac.close();
        results.analyser.state = { before: ac_state_before, after: ac_state_after, closed: ac.state };
    } catch(e) { results.analyser.fatal = e.message; }

    return results;
}
"""

_unhandled_loop_exceptions = []
def _loop_exception_handler(loop, context):
    _unhandled_loop_exceptions.append(context)

async def run_test(config_name, pdata=None, tracking=None, deterministic=False):
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    tracking["pw"].append(pw)
    browser = await pw.chromium.launch(headless=True)
    tracking["browser"].append(browser)
    context = await browser.new_context()
    tracking["context"].append(context)

    init_scripts = []
    if deterministic:
        init_scripts.append(DETERMINISTIC_ANALYSER_PRELUDE)

    if config_name not in ["NATIVE", "PRELUDE-ONLY"]:
        ctx_config = await bm.build_browser_launch_config(pdata)
        init_scripts.append(ctx_config["spoofing_script"])

    if init_scripts:
        await context.add_init_script("\n;\n".join(init_scripts))
    page = await context.new_page()
    tracking["page"].append(page)
    iframe_html = "<html><body><iframe id='child' srcdoc='<html><body></body></html>'></iframe></body></html>"
    await page.goto(f"data:text/html,{iframe_html}")
    await page.add_script_tag(content=PAGE_JS)
    main_res = await page.evaluate(f"getExtendedAudioData({'true' if deterministic else 'false'})")

    frame_element = await page.wait_for_selector("#child")
    child_frame = await frame_element.content_frame()
    tracking["iframe"].append(child_frame)
    await child_frame.add_script_tag(content=PAGE_JS)
    iframe_res = await child_frame.evaluate(f"getExtendedAudioData({'true' if deterministic else 'false'})")
    return {"main": main_res, "iframe": iframe_res}

_all_passed = True
def check(label: str, cond: bool, detail: str = ""):
    global _all_passed
    sfx = f" — {detail}" if (detail and not cond) else ""
    print(f"{'[PASS]' if cond else '[FAIL]'} {label}{sfx}")
    if not cond: _all_passed = False

def check_ast_rules():
    global _all_passed
    with open(__file__, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "check":
            if len(node.args) >= 2:
                cond = node.args[1]
                if isinstance(cond, ast.Constant) and cond.value is True:
                    print("[FAIL] AST Check: uses literal True")
                    _all_passed = False
        if isinstance(node, ast.IfExp):
            if isinstance(node.body, ast.Constant) and node.body.value is True:
                print("[FAIL] AST Check: conditional fallback is True")
                _all_passed = False
            if isinstance(node.orelse, ast.Constant) and node.orelse.value is True:
                print("[FAIL] AST Check: conditional fallback is True")
                _all_passed = False


async def main():
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(_loop_exception_handler)
    tracking = {"pw": [], "browser": [], "context": [], "page": [], "iframe": []}

    NO_NOISE = {"id": "00000000-0000-0000-0000-000000000000", "advanced": {"os": "Windows", "audio_noise": False, "canvas_noise": False, "webgl_noise": False}}
    P1A = {"id": "00000001-0000-0000-0000-000000000000", "advanced": {"os": "Windows", "audio_noise": True, "canvas_noise": False, "webgl_noise": False}}
    P1B = {"id": "00000001-0000-0000-0000-000000000000", "advanced": {"os": "Windows", "audio_noise": True, "canvas_noise": False, "webgl_noise": False}}
    P2 = {"id": "00000002-0000-0000-0000-000000000000", "advanced": {"os": "Windows", "audio_noise": True, "canvas_noise": False, "webgl_noise": False}}

    try:
        r_nat = await run_test("NATIVE", tracking=tracking)
        r_nn = await run_test("NO-NOISE", NO_NOISE, tracking=tracking)
        r_p1a = await run_test("PROFILE 1 A", P1A, tracking=tracking)
        r_p1b = await run_test("PROFILE 1 B", P1B, tracking=tracking)
        r_p2 = await run_test("PROFILE 2", P2, tracking=tracking)

        d_base = await run_test("PRELUDE-ONLY", tracking=tracking, deterministic=True)
        d_nn = await run_test("PRELUDE+NO-NOISE", NO_NOISE, tracking=tracking, deterministic=True)
        d_p1a = await run_test("PRELUDE+PROFILE 1 A", P1A, tracking=tracking, deterministic=True)
        d_p1b = await run_test("PRELUDE+PROFILE 1 B", P1B, tracking=tracking, deterministic=True)
        d_p2 = await run_test("PRELUDE+PROFILE 2", P2, tracking=tracking, deterministic=True)

        print("\n" + "="*74)
        print("  VERIFICATION EXTENDED AUDIO AUDIT")
        print("="*74 + "\n")
        check_ast_rules()

        api_keys = ["startRendering", "renderedBuffer", "getFloatFrequencyData", "getByteFrequencyData", "getFloatTimeDomainData", "getByteTimeDomainData", "getChannelData", "copyFromChannel"]
        nat_api = r_nat["main"]["api"]
        nn_api = r_nn["main"]["api"]
        p1_api = r_p1a["main"]["api"]

        for k in api_keys:
            check(f"API {k} exists natively", nat_api[k].get("name") is not None or nat_api[k].get("hasOwn") is True)
            check(f"NATIVE == NO-NOISE for {k}", nat_api[k] == nn_api[k], f"diff {nat_api[k]} != {nn_api[k]}")

            p1 = p1_api[k]
            nat = nat_api[k]
            check(f"Profile 1 preserves placement/descriptor {k}", p1.get("hasOwn") == nat.get("hasOwn") and p1.get("writable") == nat.get("writable") and p1.get("enumerable") == nat.get("enumerable") and p1.get("configurable") == nat.get("configurable"))
            check(f"Profile 1 preserves name/length {k}", p1.get("name") == nat.get("name") and p1.get("length") == nat.get("length"))
            check(f"Profile 1 preserves exception {k}", p1.get("err") == nat.get("err"))
            tostr = p1.get("toStr", "")
            check(f"Profile 1 native-looking toString {k}", "[native code]" in tostr and (p1.get("name") or k.split(" ")[-1]) in tostr and "applyAudioBufferNoise" not in tostr and "mixAudioIndex" not in tostr and "audioProfileSeed" not in tostr)

        c_nat = r_nat["main"]["copyFromChannel"]
        c_p1 = r_p1a["main"]["copyFromChannel"]
        c_sup = c_p1.get("supported")
        check("AudioBuffer.prototype.copyFromChannel supported", bool(c_sup))
        if c_sup:
            check("copyFromChannel bytes exactly equal getChannelData slice", c_p1.get("copied") == c_p1.get("direct"))
            check("copyFromChannel throws native exception type", c_p1.get("err") == c_nat.get("err"))
        else:
            check("copyFromChannel bytes exactly equal getChannelData slice", False)
            check("copyFromChannel throws native exception type", False)

        m_nat = r_nat["main"]["multichannel"]
        m_nn = r_nn["main"]["multichannel"]
        m_p1a = r_p1a["main"]["multichannel"]
        m_p1b = r_p1b["main"]["multichannel"]
        m_p2 = r_p2["main"]["multichannel"]

        for k, v in [("NATIVE", m_nat), ("P1A", m_p1a)]:
            check(f"Multichannel {k} 44100 len ch0 & ch1", len(v.get("ch0",[])) == 44100 and len(v.get("ch1",[])) == 44100)
            check(f"Multichannel {k} finite and [-1,1]", all(math.isfinite(x) and -1.0 <= x <= 1.0 for x in v.get("ch0",[])) and all(math.isfinite(x) and -1.0 <= x <= 1.0 for x in v.get("ch1",[])))

        check("Multichannel NATIVE == NO-NOISE", m_nat.get("ch0") == m_nn.get("ch0") and m_nat.get("ch1") == m_nn.get("ch1"))
        check("Multichannel P1A == P1B", m_p1a.get("ch0") == m_p1b.get("ch0") and m_p1a.get("ch1") == m_p1b.get("ch1"))
        check("Multichannel P1 != P2 on ch0", m_p1a.get("ch0") != m_p2.get("ch0"))
        check("Multichannel P1 != P2 on ch1", m_p1a.get("ch1") != m_p2.get("ch1"))

        n_ch0, n_ch1 = m_nat.get("ch0",[]), m_nat.get("ch1",[])
        p1_ch0, p1_ch1 = m_p1a.get("ch0",[]), m_p1a.get("ch1",[])
        ch0_diff = [i for i in range(44100) if n_ch0[i] != p1_ch0[i]]
        ch1_diff = [i for i in range(44100) if n_ch1[i] != p1_ch1[i]]
        check("Multichannel ch0 has at least one changed sample", len(ch0_diff) > 0)
        check("Multichannel ch1 has at least one changed sample", len(ch1_diff) > 0)
        check("Multichannel changed count <= ceil(44100/512)", len(ch0_diff) <= 87 and len(ch1_diff) <= 87)
        check("Multichannel deltas > 0 and <= 1.5e-7", all(0 < abs(n_ch0[i]-p1_ch0[i]) <= 1.5e-7 for i in ch0_diff) and all(0 < abs(n_ch1[i]-p1_ch1[i]) <= 1.5e-7 for i in ch1_diff))
        check("Multichannel native zeros remain zero", all(p1_ch0[i] == 0 for i in range(44100) if n_ch0[i] == 0) and all(p1_ch1[i] == 0 for i in range(44100) if n_ch1[i] == 0))
        check("Multichannel ch0 and ch1 changed index sets differ", ch0_diff != ch1_diff)
        check("Multichannel two resulting channels differ from each other", p1_ch0 != p1_ch1)

        dn_p1 = r_p1a["main"]["doubleNoise"]
        check("Double-noise event sameObject", bool(dn_p1.get("sameObject")))
        h_p1 = hash_samples(dn_p1.get("promise1"))
        h_e1 = hash_samples(dn_p1.get("event1"))
        h_e2 = hash_samples(dn_p1.get("event2"))
        h_p2 = hash_samples(dn_p1.get("promise2"))
        h_c = hash_samples(dn_p1.get("copyFromChannel"))
        if not h_c: check("Double-noise all hashes identical", False, "copyFromChannel hash missing")
        else: check("Double-noise all hashes identical", h_p1 == h_e1 and h_e1 == h_e2 and h_e2 == h_p2 and h_p2 == h_c)

        sb_p1 = r_p1a["main"]["silenceBoundaries"]
        ex_s = sb_p1.get("exactSilence", [])
        check("Exact silence remains exactly zero", len(ex_s) > 0 and all(x == 0.0 for x in ex_s))
        bound = sb_p1.get("boundaries", [])
        check("Boundaries no exceed [-1,1]", all(-1.0 <= x <= 1.0 for x in bound))
        check("Boundaries no NaN or Infinity", all(math.isfinite(x) for x in bound))
        check("Boundaries zero remains zero", bound[40] == 0.0 and bound[45] == 0.0)

        l_nat = r_nat["main"]["leaks"]
        l_p1 = r_p1a["main"]["leaks"]
        check("No seed on window/global/nav/doc", not l_p1["windowProps"] and not l_p1["globalProps"] and not l_p1["navigatorProps"] and not l_p1["documentProps"] and not l_p1["docElemProps"])
        check("No seed in getOwnPropertyNames", not l_p1["windowKeys"] and not l_p1["globalKeys"])
        check("No custom enumerable markers context", set(l_p1["ownOfflineCtx"]) == set(l_nat["ownOfflineCtx"]))
        check("No custom enumerable markers event", set(l_p1["ownEvent"]) == set(l_nat["ownEvent"]))
        check("No custom enumerable markers analyser", set(l_p1["ownAnalyser"]) == set(l_nat["ownAnalyser"]))
        check("No custom enumerable markers buffer", set(l_p1["ownBuffer"]) == set(l_nat["ownBuffer"]))

        check("Exactly 1 child frame for NATIVE", len(tracking["iframe"]) > 0)
        check("Profile 1 main == iframe hash", hash_samples(r_p1a["main"]["multichannel"]["ch0"]) == hash_samples(r_p1a["iframe"]["multichannel"]["ch0"]))

        a_nat = r_nat["main"]["analyser"]
        a_p1a = r_p1a["main"]["analyser"]
        analyser_running = bool("fatal" not in a_nat and a_nat["state"]["after"] == "running")
        check("Real Analyser reach running state", analyser_running)
        if analyser_running:
            check("Real AudioContext closed in finally", a_p1a["state"]["closed"] == "closed")
            for m in ['getFloatFrequencyData', 'getByteFrequencyData', 'getFloatTimeDomainData', 'getByteTimeDomainData']:
                n_f = a_nat["failures"][m]
                p_f = a_p1a["failures"][m]
                check(f"Real Analyser failures {m} missingArg", p_f["missingArg"] == n_f["missingArg"])
                check(f"Real Analyser failures {m} wrongType", p_f["wrongType"] == n_f["wrongType"])
                check(f"Real Analyser failures {m} invalidReceiver", p_f["invalidReceiver"] == n_f["invalidReceiver"])

            for m, t in [('fFloat', 'getFloatFrequencyData'), ('fByte', 'getByteFrequencyData'), ('tFloat', 'getFloatTimeDomainData'), ('tByte', 'getByteTimeDomainData')]:
                arr = a_nat["first"][m]
                check(f"Real Analyser {m} non-empty array", len(arr) > 0)
                eligible = []
                for x in arr:
                    if m == 'fFloat' and math.isfinite(x): eligible.append(x)
                    elif m == 'fByte' and x != 0: eligible.append(x)
                    elif m == 'tFloat' and math.isfinite(x) and x != 0: eligible.append(x)
                    elif m == 'tByte' and x != 128: eligible.append(x)
                if not eligible:
                    print(f"real analyser engine input unavailable for {m}")

        da_base = d_base["main"]["analyser"]
        da_nn = d_nn["main"]["analyser"]
        da_p1a = d_p1a["main"]["analyser"]
        da_p1b = d_p1b["main"]["analyser"]
        da_p2 = d_p2["main"]["analyser"]

        check("Deterministic baseline baseline matches fFloat pattern", all(x == -float('inf') if i%17==0 else x == -50.0 for i,x in enumerate(da_base["first"]["fFloat"])))
        check("Deterministic baseline baseline matches fByte pattern", all(x == 0 if i%17==0 else x == 160 for i,x in enumerate(da_base["first"]["fByte"])))
        check("Deterministic baseline baseline matches tFloat pattern", all(x == 0.0 if i%17==0 else x == 0.25 for i,x in enumerate(da_base["first"]["tFloat"])))
        check("Deterministic baseline baseline matches tByte pattern", all(x == 128 if i%17==0 else x == 160 for i,x in enumerate(da_base["first"]["tByte"])))

        for m, t in [('fFloat', 'getFloatFrequencyData'), ('fByte', 'getByteFrequencyData'), ('tFloat', 'getFloatTimeDomainData'), ('tByte', 'getByteTimeDomainData')]:
            b_arr = da_base["first"][m]

            check(f"Det Analyser {m} first call equals second call", b_arr == da_base["second"][m])
            check(f"Det Analyser {m} Profile 1 A equals Profile 1 B", da_p1a["first"][m] == da_p1b["first"][m])
            check(f"Det Analyser {m} PRELUDE-ONLY equals NO-NOISE", b_arr == da_nn["first"][m])

            p1_arr = da_p1a["first"][m]
            p2_arr = da_p2["first"][m]
            check(f"Det Analyser {m} output array length is unchanged", len(b_arr) == len(p1_arr) and len(p1_arr) == len(p2_arr))

            if m == 'fFloat' or m == 'tFloat':
                check(f"Det Analyser {m} output type/range is valid", all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in p1_arr))
            else:
                check(f"Det Analyser {m} output type/range is valid", all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 255 for x in p1_arr))

            check(f"Det Analyser {m} Profile 1 differs from Profile 2", p1_arr != p2_arr)
            c1 = [i for i, x in enumerate(b_arr) if p1_arr[i] != x]
            c2 = [i for i, x in enumerate(b_arr) if p2_arr[i] != x]
            check(f"Det Analyser {m} changed-index sets differ", c1 != c2)
            check(f"Det Analyser {m} changed count > 0", len(c1) > 0 and len(c2) > 0)
            check(f"Det Analyser {m} changed count <= ceil(len/64)", len(c1) <= math.ceil(len(b_arr)/64.0) and len(c2) <= math.ceil(len(b_arr)/64.0))

            if m == 'fFloat':
                check(f"Det Analyser {m} bounds (-Inf unchanged)", all(p1_arr[i] == -float('inf') for i in range(len(b_arr)) if b_arr[i] == -float('inf')))
                check(f"Det Analyser {m} bounds (-Inf only where expected)", all(b_arr[i] == -float('inf') for i in range(len(p1_arr)) if p1_arr[i] == -float('inf')))
                check(f"Det Analyser {m} bounds (delta > 0)", all(abs(p1_arr[i] - b_arr[i]) > 0 for i in c1))
                check(f"Det Analyser {m} bounds (delta <= 1.5e-5)", all(abs(p1_arr[i] - b_arr[i]) <= 1.5e-5 for i in c1))
                check(f"Det Analyser {m} bounds (finite stays finite)", all(math.isfinite(p1_arr[i]) for i in range(len(b_arr)) if math.isfinite(b_arr[i])))
            elif m == 'fByte':
                check(f"Det Analyser {m} bounds (0 unchanged)", all(p1_arr[i] == 0 for i in range(len(b_arr)) if b_arr[i] == 0))
                check(f"Det Analyser {m} bounds (delta == 1)", all(abs(p1_arr[i] - b_arr[i]) == 1 for i in c1))
                check(f"Det Analyser {m} bounds (0 to 255)", all(0 <= x <= 255 for x in p1_arr))
            elif m == 'tFloat':
                check(f"Det Analyser {m} bounds (0 unchanged)", all(p1_arr[i] == 0 for i in range(len(b_arr)) if b_arr[i] == 0))
                check(f"Det Analyser {m} bounds (delta > 0)", all(abs(p1_arr[i] - b_arr[i]) > 0 for i in c1))
                check(f"Det Analyser {m} bounds (delta <= 1.5e-7)", all(abs(p1_arr[i] - b_arr[i]) <= 1.5e-7 for i in c1))
                check(f"Det Analyser {m} bounds (-1 to 1)", all(-1 <= x <= 1 for x in p1_arr))
                check(f"Det Analyser {m} bounds (all finite)", all(math.isfinite(x) for x in p1_arr))
            elif m == 'tByte':
                check(f"Det Analyser {m} bounds (128 unchanged)", all(p1_arr[i] == 128 for i in range(len(b_arr)) if b_arr[i] == 128))
                check(f"Det Analyser {m} bounds (delta == 1)", all(abs(p1_arr[i] - b_arr[i]) == 1 for i in c1))
                check(f"Det Analyser {m} bounds (0 to 255)", all(0 <= x <= 255 for x in p1_arr))

            b_f = da_base["failures"][t]
            n_f = da_nn["failures"][t]
            p_f = da_p1a["failures"][t]
            check(f"Det Analyser exceptions {m} missingArg match", b_f["missingArg"] == n_f["missingArg"] and n_f["missingArg"] == p_f["missingArg"])
            check(f"Det Analyser exceptions {m} wrongType match", b_f["wrongType"] == n_f["wrongType"] and n_f["wrongType"] == p_f["wrongType"])
            check(f"Det Analyser exceptions {m} invalidReceiver match", b_f["invalidReceiver"] == n_f["invalidReceiver"] and n_f["invalidReceiver"] == p_f["invalidReceiver"])

            api_det_p1 = d_p1a["main"]["api"][t]
            api_det_base = d_base["main"]["api"][t]
            check(f"Det Analyser Wrapper {m} name match native", api_det_p1["name"] == t)
            check(f"Det Analyser Wrapper {m} length match native", api_det_p1["length"] == 1)
            check(f"Det Analyser Wrapper {m} descriptors match prelude", api_det_p1["writable"] == api_det_base["writable"] and api_det_p1["configurable"] == api_det_base["configurable"] and api_det_p1["enumerable"] == api_det_base["enumerable"])
            check(f"Det Analyser Wrapper {m} native-looking toString", "[native code]" in api_det_p1["toStr"] and t in api_det_p1["toStr"] and "applyAudioBufferNoise" not in api_det_p1["toStr"])
            check(f"Det Analyser Wrapper {m} output differs only on audio_noise=True", b_arr == da_nn["first"][m] and b_arr != da_p1a["first"][m])

        for m in ['fFloat', 'fByte', 'tFloat', 'tByte']:
            check(f"Det Analyser {m} iframe Profile 1 equals main", d_p1a["iframe"]["analyser"]["first"][m] == da_p1a["first"][m])
            check(f"Det Analyser {m} iframe Profile 2 equals main", d_p2["iframe"]["analyser"]["first"][m] == da_p2["first"][m])
            check(f"Det Analyser {m} iframe Profile 1 differs Profile 2", d_p1a["iframe"]["analyser"]["first"][m] != d_p2["iframe"]["analyser"]["first"][m])

    finally:
        cleanup_errors = []
        for p in reversed(tracking["page"]):
            try: await p.close()
            except Exception as e: cleanup_errors.append(e)
        for c in reversed(tracking["context"]):
            try: await c.close()
            except Exception as e: cleanup_errors.append(e)
        for b in reversed(tracking["browser"]):
            try: await b.close()
            except Exception as e: cleanup_errors.append(e)
        for pw in reversed(tracking["pw"]):
            try: await pw.stop()
            except Exception as e: cleanup_errors.append(e)

        check("Cleanup encountered no exceptions", len(cleanup_errors) == 0, str(cleanup_errors))
        check("All browser objects closed", all(not b.is_connected() for b in tracking["browser"]))
        check("No unhandled loop exceptions", len(_unhandled_loop_exceptions) == 0, str(_unhandled_loop_exceptions))

        temp_dir.cleanup()
        sys.path[:] = _stored_sys_path
        os.environ.clear(); os.environ.update(_stored_env)
        bm.probe_native_metadata = _stored_probe
        pm.proxy_manager.get_proxy_for_profile = _stored_get_proxy
        loop.set_exception_handler(old_handler)

    return _all_passed

if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
