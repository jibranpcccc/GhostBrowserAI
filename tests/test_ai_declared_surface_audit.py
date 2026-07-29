import asyncio
import os
import sys
import tempfile
import ast
import json
import hashlib
import struct
import math

# =============================================================================
# PHASE 1 — ISOLATION
# =============================================================================
_stored_env = os.environ.copy()
_stored_sys_path = sys.path[:]

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

temp_dir = tempfile.TemporaryDirectory()
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir.name
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

# Import production code AFTER setting env vars
import backend.browser_manager as bm
import backend.proxy_manager as pm

_stored_probe = bm.probe_native_metadata
_stored_get_proxy = pm.proxy_manager.get_proxy_for_profile

async def stub_probe_native_metadata(*args, **kwargs):
    return {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "uadata": {
            "brands": [{"brand": "Chromium", "version": "149"}],
            "mobile": False,
            "platform": "Windows",
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "platformVersion": "19.0.0",
            "uaFullVersion": "149.0.0.0",
            "fullVersionList": [{"brand": "Chromium", "version": "149.0.0.0"}]
        }
    }

async def stub_get_proxy(*args, **kwargs):
    return None

bm.probe_native_metadata = stub_probe_native_metadata
pm.proxy_manager.get_proxy_for_profile = stub_get_proxy

# =============================================================================
# BROWSER JS
# =============================================================================
PAGE_EVAL_JS = r"""
async function getGhostData() {
    const results = { audio: {}, fonts: {}, plugins: {} };

    // --- PHASE 3: AUDIO ---
    let stage = 'init';
    try {
        stage = 'context creation';
        const oc = new OfflineAudioContext(1, 44100, 44100);
        const osc = oc.createOscillator();
        const comp = oc.createDynamicsCompressor();
        const gain = oc.createGain();

        stage = 'routing';
        osc.type = 'sine';
        osc.frequency.value = 440;
        comp.threshold.value = -24;
        comp.knee.value = 30;
        comp.ratio.value = 12;
        comp.reduction.value = 0;
        comp.attack.value = 0.003;
        comp.release.value = 0.25;
        gain.gain.value = 0.5;

        osc.connect(comp);
        comp.connect(gain);
        gain.connect(oc.destination);
        osc.start(0);
        osc.stop(1);

        stage = 'rendering';
        const renderedBuffer = await oc.startRendering();

        stage = 'data extraction';
        const channelData = renderedBuffer.getChannelData(0);

        stage = 'validation';
        const samples = [];
        const selectionLen = Math.min(channelData.length, 44100);
        let allFinite = true;
        for(let i=0; i<selectionLen; i++) {
            const val = channelData[i];
            samples.push(val);
            if (!Number.isFinite(val) || Number.isNaN(val)) {
                allFinite = false;
            }
        }

        results.audio.sampleCount = selectionLen;
        results.audio.samples = samples;
        results.audio.allFinite = allFinite;

        results.audio.properties = {
            sampleRate: oc.sampleRate,
            baseLatency: oc.baseLatency,
            outputLatency: typeof oc.outputLatency !== 'undefined' ? oc.outputLatency : null,
            maxChannelCount: oc.destination.maxChannelCount,
            state: oc.state
        };

        const getDesc = (obj, prop) => {
            const d = Object.getOwnPropertyDescriptor(obj, prop);
            if (!d) return null;
            return { writable: d.writable, enumerable: d.enumerable, configurable: d.configurable };
        };
        const safeString = (fn) => {
            try { return Function.prototype.toString.call(fn); } catch(e) { return e.name; }
        };
        const safeLenName = (fn) => {
            if(!fn) return null;
            return { name: fn.name, length: fn.length };
        };
        const safeCall = (fn, ctx) => {
            try { fn.call(ctx); return 'ok'; } catch(e) { return e.constructor.name; }
        };

        results.audio.api = {
            startRendering: {
                ...safeLenName(OfflineAudioContext.prototype.startRendering),
                desc: getDesc(OfflineAudioContext.prototype, 'startRendering'),
                str: safeString(OfflineAudioContext.prototype.startRendering),
                inv: safeCall(OfflineAudioContext.prototype.startRendering, {})
            },
            getChannelData: {
                ...safeLenName(AudioBuffer.prototype.getChannelData),
                desc: getDesc(AudioBuffer.prototype, 'getChannelData'),
                str: safeString(AudioBuffer.prototype.getChannelData),
                inv: safeCall(AudioBuffer.prototype.getChannelData, {})
            },
            getFloatFrequencyData: {
                ...safeLenName(AnalyserNode.prototype.getFloatFrequencyData),
                desc: getDesc(AnalyserNode.prototype, 'getFloatFrequencyData'),
                str: safeString(AnalyserNode.prototype.getFloatFrequencyData),
                inv: safeCall(AnalyserNode.prototype.getFloatFrequencyData, {})
            },
            getByteTimeDomainData: {
                ...safeLenName(AnalyserNode.prototype.getByteTimeDomainData),
                desc: getDesc(AnalyserNode.prototype, 'getByteTimeDomainData'),
                str: safeString(AnalyserNode.prototype.getByteTimeDomainData),
                inv: safeCall(AnalyserNode.prototype.getByteTimeDomainData, {})
            }
        };
    } catch(e) {
        results.audio.error = e.message;
        results.audio.errorName = e.name;
        results.audio.errorStage = stage;
    }

    // --- PHASE 5: FONTS ---
    try {
        const fontProbes = [
            'Segoe UI', 'Arial', 'Times New Roman', 'Courier New',
            'Calibri', 'Consolas', 'GhostProfileOneFont', 'GhostProfileTwoFont'
        ];
        results.fonts.check = {};
        results.fonts.metrics = {};

        const cvs = document.createElement('canvas');
        const ctx = cvs.getContext('2d');

        for (const f of fontProbes) {
            const fontStr = `16px "${f}"`;
            let chk = false;
            try { chk = document.fonts.check(fontStr); } catch(e){}
            results.fonts.check[f] = chk;

            ctx.font = `${fontStr}, "fallback-font-xyz"`;
            const m = ctx.measureText("GhostBrowser Deterministic Text 123!@#");
            results.fonts.metrics[f] = {
                width: m.width,
                asc: m.actualBoundingBoxAscent,
                desc: m.actualBoundingBoxDescent,
                left: m.actualBoundingBoxLeft,
                right: m.actualBoundingBoxRight
            };
        }

        const getDesc = (obj, prop) => {
            const d = Object.getOwnPropertyDescriptor(obj, prop);
            if (!d) return null;
            return { writable: d.writable, enumerable: d.enumerable, configurable: d.configurable };
        };
        results.fonts.api = {
            fontFaceSet: Object.prototype.toString.call(document.fonts),
            checkName: document.fonts.check.name,
            checkLen: document.fonts.check.length,
            ready: typeof document.fonts.ready.then === 'function',
            desc: getDesc(Object.getPrototypeOf(document.fonts), 'check'),
            checkStr: Function.prototype.toString.call(document.fonts.check)
        };
    } catch(e) { results.fonts.error = e.message; }

    // --- PHASE 7: PLUGINS ---
    try {
        results.plugins.list = [];
        for(let i=0; i<navigator.plugins.length; i++) {
            const p = navigator.plugins[i];
            const mt = [];
            for(let j=0; j<p.length; j++) { mt.push(p[j].type); }
            results.plugins.list.push({
                name: p.name,
                filename: p.filename,
                description: p.description,
                length: p.length,
                mimeTypes: mt
            });
        }
        results.plugins.mimeTypes = [];
        for(let i=0; i<navigator.mimeTypes.length; i++) {
            const m = navigator.mimeTypes[i];
            results.plugins.mimeTypes.push({
                type: m.type,
                suffixes: m.suffixes,
                description: m.description,
                enabledPlugin: m.enabledPlugin ? m.enabledPlugin.name : null
            });
        }

        const safeString = (fn) => {
            try { return Function.prototype.toString.call(fn); } catch(e) { return e.name; }
        };
        const safeCall = (fn, ctx) => {
            try { fn.call(ctx); return 'ok'; } catch(e) { return e.constructor.name; }
        };
        const getDesc = (obj, prop) => {
            const d = Object.getOwnPropertyDescriptor(obj, prop);
            if (!d) return null;
            return { writable: d.writable, enumerable: d.enumerable, configurable: d.configurable };
        };
        const pApi = (obj, prop, proto) => {
            if(!proto[prop]) return null;
            return {
                name: proto[prop].name,
                len: proto[prop].length,
                desc: getDesc(proto, prop),
                str: safeString(proto[prop]),
                inv: safeCall(proto[prop], {})
            };
        };

        results.plugins.api = {
            navPlugins: getDesc(Navigator.prototype, 'plugins'),
            navMimes: getDesc(Navigator.prototype, 'mimeTypes'),
            PluginArray_item: pApi(navigator.plugins, 'item', PluginArray.prototype),
            PluginArray_namedItem: pApi(navigator.plugins, 'namedItem', PluginArray.prototype),
            PluginArray_refresh: pApi(navigator.plugins, 'refresh', PluginArray.prototype),
            MimeTypeArray_item: pApi(navigator.mimeTypes, 'item', MimeTypeArray.prototype),
            MimeTypeArray_namedItem: pApi(navigator.mimeTypes, 'namedItem', MimeTypeArray.prototype),
            tagPlugins: navigator.plugins[Symbol.toStringTag],
            tagMimes: navigator.mimeTypes[Symbol.toStringTag]
        };
    } catch(e) { results.plugins.error = e.message; }

    return results;
}
"""

# =============================================================================
# PYTHON HASHING & NORMALIZATION
# =============================================================================

def hash_audio_samples(audio_result):
    if "error" in audio_result:
        return None, f"Error: {audio_result['error']} at {audio_result.get('errorStage')}"
    if "samples" not in audio_result or not audio_result["samples"]:
        return None, "Samples are missing"
    samples = audio_result["samples"]
    if len(samples) < 8192:
        return None, f"Fewer than 8192 samples: {len(samples)}"

    h = hashlib.sha256()
    for s in samples:
        if not math.isfinite(s):
            return None, f"Non-finite sample found: {s}"
        h.update(struct.pack("<f", float(s)))
    return h.hexdigest(), None

def canonicalize_plugins(plugins_data):
    if not plugins_data or "list" not in plugins_data:
        return {"plugins": [], "mimes": []}

    c_plugins = []
    for p in plugins_data.get("list", []):
        c_plugins.append({
            "name": p.get("name") or "",
            "filename": p.get("filename") or "",
            "description": p.get("description") or "",
            "length": int(p.get("length", 0)) if p.get("length") is not None else 0,
            "mimeTypes": sorted([m or "" for m in p.get("mimeTypes", [])])
        })
    c_plugins.sort(key=lambda x: (x["name"], x["filename"], x["description"]))

    c_mimes = []
    for m in plugins_data.get("mimeTypes", []):
        c_mimes.append({
            "type": m.get("type") or "",
            "suffixes": m.get("suffixes") or "",
            "description": m.get("description") or "",
            "enabledPlugin": m.get("enabledPlugin") or ""
        })
    c_mimes.sort(key=lambda x: (x["type"], x["suffixes"], x["description"], x["enabledPlugin"]))

    return {"plugins": c_plugins, "mimes": c_mimes}

def canonicalize_fonts(fonts_data):
    if not fonts_data:
        return {"check": {}, "metrics": {}}

    c_check = {}
    for k in sorted(fonts_data.get("check", {}).keys()):
        c_check[k] = fonts_data["check"][k]

    c_metrics = {}
    for k in sorted(fonts_data.get("metrics", {}).keys()):
        m = fonts_data["metrics"][k]
        cm = {}
        for prop in ["width", "asc", "desc", "left", "right"]:
            v = m.get(prop, 0.0)
            if not math.isfinite(v):
                v = 0.0
            if v == -0.0:
                v = 0.0
            cm[prop] = v
        c_metrics[k] = cm

    return {"check": c_check, "metrics": c_metrics}


# =============================================================================
# TASKS
# =============================================================================
_unhandled_loop_exceptions = []
def _loop_exception_handler(loop, context):
    _unhandled_loop_exceptions.append(context)

# =============================================================================
# RUNNER
# =============================================================================
async def run_test(config_name, pdata=None, tracking=None):
    from playwright.async_api import async_playwright

    pw = browser = context = page = None
    try:
        pw = await async_playwright().start()
        if tracking is not None: tracking["pw"].append(pw)

        browser = await pw.chromium.launch(headless=True)
        if tracking is not None: tracking["browser"].append(browser)

        context = await browser.new_context()
        if tracking is not None: tracking["context"].append(context)

        if config_name != "NATIVE":
            ctx_config = await bm.build_browser_launch_config(pdata)
            script = ctx_config["spoofing_script"]
            await context.add_init_script(script)

        page = await context.new_page()
        if tracking is not None: tracking["page"].append(page)

        iframe_html = "<html><body><iframe id='child' srcdoc='<html><body></body></html>'></iframe></body></html>"
        await page.goto(f"data:text/html,{iframe_html}")

        await page.add_script_tag(content=PAGE_EVAL_JS)
        main_res = await page.evaluate("getGhostData()")

        frame_element = await page.wait_for_selector("#child")
        child_frame = await frame_element.content_frame()
        if tracking is not None: tracking["iframe"].append(child_frame)

        await child_frame.add_script_tag(content=PAGE_EVAL_JS)
        iframe_res = await child_frame.evaluate("getGhostData()")

        frames_len = len(page.frames)

        return {
            "main": main_res,
            "iframe": iframe_res,
            "frames_len": frames_len
        }
    finally:
        pass

# =============================================================================
# MAIN
# =============================================================================
_all_passed = True
def check(label: str, cond: bool, detail: str = ""):
    global _all_passed
    sfx = f" — {detail}" if (detail and not cond) else ""
    print(f"{'[PASS]' if cond else '[FAIL]'} {label}{sfx}")
    if not cond: _all_passed = False

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
                    if isinstance(cond, ast.Constant) and cond.value is True:
                        print(f"[FAIL] AST Check: {lbl.value} uses literal True")
                        global _all_passed
                        _all_passed = False
                if isinstance(cond, ast.Compare) and len(cond.ops) == 1 and isinstance(cond.ops[0], ast.IsNot):
                    if isinstance(cond.comparators[0], ast.Constant) and cond.comparators[0].value is None:
                        print(f"[FAIL] AST Check: Condition uses only 'is not None'")
                        _all_passed = False
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                if node.args[0].value.startswith("[PASS]"):
                    print(f"[FAIL] AST Check: Found print('[PASS]') outside check function")
                    _all_passed = False

async def main():
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(_loop_exception_handler)

    tracking = {
        "pw": [],
        "browser": [],
        "context": [],
        "page": [],
        "iframe": []
    }

    try:
        NO_NOISE_DATA = {
            "id": "00000000-0000-0000-0000-000000000000",
            "advanced": {
                "os": "Windows",
                "audio_noise": False,
                "canvas_noise": False,
                "webgl_noise": False
            }
        }

        P1_DATA = {
            "id": "00000001-0000-0000-0000-000000000000",
            "advanced": {
                "os": "Windows",
                "audio_noise": True,
                "canvas_noise": False,
                "webgl_noise": False,
                "fonts": ["Segoe UI", "GhostProfileOneFont"],
                "plugins": ["Ghost PDF Profile One", "application/x-ghost-profile-one"]
            }
        }

        P2_DATA = {
            "id": "00000002-0000-0000-0000-000000000000",
            "advanced": {
                "os": "Windows",
                "audio_noise": True,
                "canvas_noise": False,
                "webgl_noise": False,
                "fonts": ["Times New Roman", "GhostProfileTwoFont"],
                "plugins": ["Ghost PDF Profile Two", "application/x-ghost-profile-two"]
            }
        }

        P1_ID_CONTROL_DATA = {
            "id": "11111111-0000-0000-0000-000000000000",
            "advanced": {
                "os": "Windows",
                "audio_noise": True,
                "canvas_noise": False,
                "webgl_noise": False,
                "fonts": ["Segoe UI", "GhostProfileOneFont"],
                "plugins": ["Ghost PDF Profile One", "application/x-ghost-profile-one"]
            }
        }

        res_native = await run_test("NATIVE", tracking=tracking)
        res_no_noise = await run_test("NO-NOISE", NO_NOISE_DATA, tracking=tracking)
        res_p1_a = await run_test("PROFILE 1 A", P1_DATA, tracking=tracking)
        res_p1_b = await run_test("PROFILE 1 B", P1_DATA, tracking=tracking)
        res_p2 = await run_test("PROFILE 2", P2_DATA, tracking=tracking)
        res_p1_id_control = await run_test("PROFILE 1 ID-CONTROL", P1_ID_CONTROL_DATA, tracking=tracking)

        print("\n" + "="*74)
        print("  ASSERTION REPORT")
        print("="*74 + "\n")

        check_ast_rules()

        all_results = [
            ("NATIVE main", res_native["main"]), ("NATIVE iframe", res_native["iframe"]),
            ("NO-NOISE main", res_no_noise["main"]), ("NO-NOISE iframe", res_no_noise["iframe"]),
            ("PROFILE 1 A main", res_p1_a["main"]), ("PROFILE 1 A iframe", res_p1_a["iframe"]),
            ("PROFILE 1 B main", res_p1_b["main"]), ("PROFILE 1 B iframe", res_p1_b["iframe"]),
            ("PROFILE 2 main", res_p2["main"]), ("PROFILE 2 iframe", res_p2["iframe"]),
            ("PROFILE 1 ID-CONTROL main", res_p1_id_control["main"]), ("PROFILE 1 ID-CONTROL iframe", res_p1_id_control["iframe"])
        ]

        audio_hashes = {}
        collection_ok = True

        for name, res in all_results:
            a = res.get("audio", {})
            ahash, err = hash_audio_samples(a)
            print(f"Audio [{name}]: Samples={a.get('sampleCount', 0)}, Finite={a.get('allFinite')}, Hash={ahash}, Err={err or a.get('error')}")

            check(f"Audio Context [{name}] Valid", err is None and bool(ahash), str(err))
            if err or not ahash: collection_ok = False
            audio_hashes[name] = ahash

        check("Exactly 1 child frame for NATIVE", res_native.get("frames_len") == 2, str(res_native.get("frames_len")))
        check("Exactly 1 child frame for PROFILE 1", res_p1_a.get("frames_len") == 2, str(res_p1_a.get("frames_len")))

        if collection_ok:
            check("Profile 1 A == Profile 1 B audio hash", audio_hashes["PROFILE 1 A main"] == audio_hashes["PROFILE 1 B main"])
            check("Profile 1 main == Profile 1 iframe audio hash", audio_hashes["PROFILE 1 A main"] == audio_hashes["PROFILE 1 A iframe"])
            check("Profile 1 != Profile 2 audio hash", audio_hashes["PROFILE 1 A main"] != audio_hashes["PROFILE 2 main"], "Hashes match")
            check("NATIVE == NO-NOISE audio hash", audio_hashes["NATIVE main"] == audio_hashes["NO-NOISE main"])

            nat_props = json.dumps(res_native["main"].get("audio", {}).get("properties"), sort_keys=True)
            nn_props = json.dumps(res_no_noise["main"].get("audio", {}).get("properties"), sort_keys=True)
            check("NATIVE AudioContext properties == NO-NOISE", nat_props == nn_props, "Mismatch")

            api_ok = True
            api_diff = ""
            a_nat = res_native["main"].get("audio", {})
            a_p1a = res_p1_a["main"].get("audio", {})
            if "api" in a_nat and "api" in a_p1a:
                for method, nat_m in a_nat["api"].items():
                    p1_m = a_p1a["api"].get(method, {})
                    for key in ["name", "length", "inv", "str", "desc"]:
                        if nat_m.get(key) != p1_m.get(key):
                            api_ok = False
                            api_diff += f"{method}.{key} nat={nat_m.get(key)} p1={p1_m.get(key)} "
            check("Audio API metadata matches NATIVE", api_ok, api_diff)

            if audio_hashes["PROFILE 1 A main"] == audio_hashes["PROFILE 2 main"]:
                print("[FAIL] audio_noise=True has no observable profile-specific effect.")

        # FONTS
        f_hashes = {}
        for name, res in all_results:
            cf = canonicalize_fonts(res.get("fonts", {}))
            f_hashes[name] = json.dumps(cf, sort_keys=True)

        check("Profile 1 A == Profile 1 B font results", f_hashes["PROFILE 1 A main"] == f_hashes["PROFILE 1 B main"])
        check("Profile 1 main == Profile 1 iframe font results", f_hashes["PROFILE 1 A main"] == f_hashes["PROFILE 1 A iframe"])
        check("Profile 2 main == Profile 2 iframe font results", f_hashes["PROFILE 2 main"] == f_hashes["PROFILE 2 iframe"])
        check("NATIVE == NO-NOISE font results", f_hashes["NATIVE main"] == f_hashes["NO-NOISE main"])
        check("PROFILE 1 A == NATIVE font results", f_hashes["PROFILE 1 A main"] == f_hashes["NATIVE main"])
        check("PROFILE 2 == NATIVE font results", f_hashes["PROFILE 2 main"] == f_hashes["NATIVE main"])

        check("No fake fonts in PROFILE 1 A", "GhostProfileOneFont" not in f_hashes["PROFILE 1 A main"] or f_hashes["PROFILE 1 A main"] == f_hashes["NATIVE main"])
        check("No fake fonts in PROFILE 2", "GhostProfileTwoFont" not in f_hashes["PROFILE 2 main"] or f_hashes["PROFILE 2 main"] == f_hashes["NATIVE main"])

        # PLUGINS
        pl_hashes = {}
        for name, res in all_results:
            cp = canonicalize_plugins(res.get("plugins", {}))
            pl_hashes[name] = json.dumps(cp, sort_keys=True)
            if name in ["NATIVE main", "NO-NOISE main", "PROFILE 1 A main", "PROFILE 1 B main", "PROFILE 2 main", "PROFILE 1 ID-CONTROL main"]:
                print(f"Plugins [{name}]: {len(cp['plugins'])} plugins, {len(cp['mimes'])} mimes")
                check(f"No fake plugins in {name}",
                      "Ghost PDF Profile One" not in pl_hashes[name] and "application/x-ghost-profile-one" not in pl_hashes[name] and
                      "Ghost PDF Profile Two" not in pl_hashes[name] and "application/x-ghost-profile-two" not in pl_hashes[name])

        check("Profile 1 A == Profile 1 B plugin results", pl_hashes["PROFILE 1 A main"] == pl_hashes["PROFILE 1 B main"])
        check("Profile 1 main == Profile 1 iframe plugin results", pl_hashes["PROFILE 1 A main"] == pl_hashes["PROFILE 1 A iframe"])
        check("Profile 2 main == Profile 2 iframe plugin results", pl_hashes["PROFILE 2 main"] == pl_hashes["PROFILE 2 iframe"])
        check("NATIVE == NO-NOISE plugin results", pl_hashes["NATIVE main"] == pl_hashes["NO-NOISE main"])
        check("PROFILE 1 A == NATIVE plugin results", pl_hashes["PROFILE 1 A main"] == pl_hashes["NATIVE main"])
        check("PROFILE 2 == NATIVE plugin results", pl_hashes["PROFILE 2 main"] == pl_hashes["NATIVE main"])
        check("Audio differentiation from stable profile seed is expected",
              audio_hashes.get("PROFILE 1 A main") != audio_hashes.get("PROFILE 1 ID-CONTROL main"))

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
        check("Restored sys.path exactly", sys.path == _stored_sys_path)

        os.environ.clear()
        os.environ.update(_stored_env)
        check("Restored os.environ exactly", dict(os.environ) == _stored_env)

        bm.probe_native_metadata = _stored_probe
        pm.proxy_manager.get_proxy_for_profile = _stored_get_proxy
        check("Restored probe_native_metadata", bm.probe_native_metadata is _stored_probe)
        check("Restored get_proxy", pm.proxy_manager.get_proxy_for_profile is _stored_get_proxy)

        loop.set_exception_handler(old_handler)

    return _all_passed

if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
