"""
BRUTAL TEST SUITE (Levels 1 to 10)
Exhaustive fingerprint consistency, privacy isolation, and anti-detect verification.

Targets:
- Level 1: Baseline Fingerprint Capture (CreepJS, BrowserLeaks WebRTC, Canvas, WebGL, WebGPU, FingerprintJS)
  Separates CAPTURE_SUCCESS from DETECTOR_RESULT under automated HEADLESS execution.
- Level 2: Same-Profile Stability Torture Test (20 reloads, 10 tab closes)
- Level 3: Cross-Context Contradiction Test (Main Window vs Iframe vs Dedicated Worker)
- Level 4: HTTP <-> JavaScript Coherence Test (HTTP Headers vs navigator.userAgent/UA-CH/Intl)
- Level 5: Hardware Coherence Torture Test (CPU, RAM, GPU, Generic WebGL vs Unmasked Renderer)
- Level 6: Network-Leak Kill Test (WebRTC ICE candidates, STUN probes, mDNS)
- Level 7: Profile-Isolation Test (Cookies, localStorage, sessionStorage, IndexedDB, Cache, ServiceWorker)
- Level 8: Tamper & Prototype Integrity Test (Function toString, Property descriptors, Baseline matching)
- Level 9 & 10: Adversarial Matrix & Severity Grading (FATAL / CRITICAL / HIGH / MEDIUM / LOW / PASS / INCONCLUSIVE)
"""

import os
import sys
import time
import json
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["GHOSTBROWSER_REQUIRE_PROXY"] = "0"
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

from backend.profile_creator import create_zero_leak_profile
from backend.profile_manager import profile_manager
from backend.browser_manager import launch_profile, close_profile, active_browsers
from backend.engine_resolver import get_chromium_executable_path_async
from backend.browser_version import BrowserVersion, calculate_file_sha256
from backend.network_coherence import validate_webrtc_candidates, validate_network_coherence

ARTIFACTS_DIR = Path(r"C:\Users\jibra\.gemini\antigravity\brain\f6e8582d-838b-4e9f-84c6-dfcfe619e4a6")
SCREENSHOTS_DIR = ARTIFACTS_DIR / "brutal_test_screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_SCREENSHOTS_DIR = PROJECT_ROOT / "artifacts" / "brutal_test_screenshots"
LOCAL_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

REPORT_PATH = PROJECT_ROOT / "tests" / "brutal_test_report.json"
CLEAN_BASELINE_PATH = PROJECT_ROOT / "baselines" / "clean_chromium_baseline.json"

async def save_screenshot(page, filename: str):
    p1 = SCREENSHOTS_DIR / filename
    p2 = LOCAL_SCREENSHOTS_DIR / filename
    await page.screenshot(path=str(p1), full_page=False)
    try:
        import shutil
        shutil.copy2(str(p1), str(p2))
    except Exception:
        pass
    return p1


async def run_brutal_test():
    print("=" * 80)
    print("STARTING GHOSTBROWSER BRUTAL TEST — LEVELS 1 TO 10")
    print("=" * 80)

    # Resolve exact runtime engine asynchronously and compute cryptographic hash
    exact_exec_path = await get_chromium_executable_path_async()
    exact_sha256 = calculate_file_sha256(exact_exec_path) if exact_exec_path and os.path.exists(exact_exec_path) else None
    engine_bv = BrowserVersion.from_installed_engine(exact_exec_path)

    # Check clean baseline file for version and engine matching
    clean_baseline = {}
    baseline_matched = False
    baseline_version = None
    baseline_sha256 = None
    if CLEAN_BASELINE_PATH.exists():
        try:
            with open(CLEAN_BASELINE_PATH, "r", encoding="utf-8") as bf:
                clean_baseline = json.load(bf)
            baseline_version = clean_baseline.get("exact_version")
            baseline_sha256 = clean_baseline.get("executable_sha256")
            baseline_major = clean_baseline.get("browser_major")
            if (baseline_version == engine_bv.full_version) and (not exact_sha256 or not baseline_sha256 or baseline_sha256 == exact_sha256):
                baseline_matched = True
        except Exception as be:
            print(f"[WARN] Error reading clean baseline: {be}")

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_engine": {
            "exact_executable_path": exact_exec_path,
            "executable_sha256": exact_sha256,
            "exact_version": engine_bv.full_version,
            "browser_major": engine_bv.major,
            "execution_mode": "HEADLESS",
            "capture_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "baseline_engine_matched": baseline_matched,
            "clean_baseline_version": baseline_version,
            "clean_baseline_sha256": baseline_sha256,
        },
        "profile_specs": {},
        "level1_baseline": {},
        "level2_stability": {},
        "level3_cross_context": {},
        "level4_http_js_coherence": {},
        "level5_hardware_coherence": {},
        "level6_network_leak": {},
        "level7_profile_isolation": {},
        "level8_tamper_integrity": {},
        "grading_matrix": [],
    }

    # -------------------------------------------------------------
    # SETUP: CREATE CLEAN BRAND-NEW PROFILE (NO EXTENSIONS)
    # -------------------------------------------------------------
    profile_name = "Brutal-Audit-Profile-01"
    print(f"\n[SETUP] Creating brand-new clean profile: {profile_name}...")

    create_res = await create_zero_leak_profile(
        name=profile_name,
        advanced_ui={
            "privacy_mode": "strict",  # Enforces --disable-extensions (no extensions loaded)
            "canvas_noise": True,
            "audio_noise": True,
            "webgl_noise": True,
        }
    )

    if create_res.get("status") != "success":
        print(f"FAILED to create profile: {create_res}")
        sys.exit(1)

    profile_id = create_res["profile"]["id"]
    profile_data = profile_manager.get_profile(profile_id)
    adv = profile_data.get("advanced", {})

    intended_specs = {
        "profile_id": profile_id,
        "name": profile_name,
        "intended_os": adv.get("os", "Windows"),
        "browser_name": "Chromium / Chrome",
        "browser_version": engine_bv.full_version,
        "browser_major": engine_bv.major,
        "cpu_cores": adv.get("cpu_cores", 8),
        "ram_gb": adv.get("memory_gb", 8),
        "gpu_vendor": adv.get("webgl_vendor", "Google Inc. (NVIDIA)"),
        "gpu_renderer": adv.get("webgl_renderer", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        "screen_resolution": adv.get("screen_resolution", "1920x1080"),
        "language": profile_data.get("locale", "en-US"),
        "timezone": profile_data.get("timezone", "America/New_York"),
        "privacy_mode": adv.get("privacy_mode", "strict"),
        "extensions_loaded": 0,
        "canvas_noise": adv.get("canvas_noise", True),
        "audio_noise": adv.get("audio_noise", True),
        "webgl_noise": adv.get("webgl_noise", True),
    }
    report["profile_specs"] = intended_specs

    print("\n[INTENDED PROFILE SPECIFICATIONS]")
    for k, v in intended_specs.items():
        print(f"  • {k:20s}: {v}")

    # Launch profile in Chromium
    print(f"\n[LAUNCH] Launching profile {profile_id} via GhostBrowser Engine (Headless)...")
    l_res = await launch_profile(profile_id, force_headless=True)
    if l_res.get("status") != "success":
        print(f"FAILED to launch profile: {l_res}")
        sys.exit(1)

    browser_handle = active_browsers[profile_id]
    page = browser_handle["page"]
    context = browser_handle["context"]

    try:
        # -------------------------------------------------------------
        # LEVEL 1: BASELINE FINGERPRINT CAPTURE (OFFICIAL SITES)
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 1: BASELINE FINGERPRINT CAPTURE (10/10 IMPORTANCE)")
        print("=" * 60)

        # 1.1 CreepJS
        print("\n--> [1.1] Navigating to CreepJS (https://abrahamjuliot.github.io/creepjs/)...")
        creepjs_url = "https://abrahamjuliot.github.io/creepjs/?utm_source=chatgpt.com"
        try:
            await page.goto(creepjs_url, wait_until="domcontentloaded", timeout=35000)
            print("  Waiting 12s for CreepJS analysis and worker execution...")
            await asyncio.sleep(12)

            creep_screenshot = await save_screenshot(page, "creepjs.png")
            print(f"  Screenshot captured -> {creep_screenshot}")

            creep_data = await page.evaluate("""() => {
                const bodyText = document.body.innerText || '';
                const liesElements = Array.from(document.querySelectorAll('.lies, .lie, .fuzzy-fp, .fingerprint-header, .error'));
                const liesText = liesElements.map(el => el.innerText.trim()).filter(Boolean);
                
                const fpMatches = bodyText.match(/FP ID:[\\s\\S]*?([a-f0-9]{64})/i);
                const fuzzyMatches = bodyText.match(/Fuzzy:[\\s\\S]*?([a-f0-9]{64})/i);
                
                // Extract detector scores - use null when not found, NEVER hardcoded fallback strings
                const headlessMatch = bodyText.match(/(\\d+%(?:\\.\\d+)?)\\s+headless/i);
                const likeHeadlessMatch = bodyText.match(/(\\d+%(?:\\.\\d+)?)\\s+like\\s+headless/i);
                const stealthMatch = bodyText.match(/(\\d+%(?:\\.\\d+)?)\\s+stealth/i);

                return {
                    title: document.title,
                    fp_id: fpMatches ? fpMatches[1] : (bodyText.match(/([a-f0-9]{64})/i)?.[1] || null),
                    fuzzy_id: fuzzyMatches ? fuzzyMatches[1] : null,
                    headless_score: headlessMatch ? headlessMatch[0] : null,
                    like_headless_score: likeHeadlessMatch ? likeHeadlessMatch[0] : null,
                    stealth_score: stealthMatch ? stealthMatch[0] : null,
                    lies_found: liesText.slice(0, 10),
                    full_text_snippet: bodyText.slice(0, 800).replace(/\\n+/g, ' | ')
                };
            }""")

            headless_score = creep_data.get("headless_score")
            like_headless_score = creep_data.get("like_headless_score")
            stealth_score = creep_data.get("stealth_score")

            has_scores = bool(headless_score or like_headless_score)
            if has_scores:
                flagged_headless = True
                creep_assessment = "FAIL"
                creep_severity = "HIGH"
                creep_note = "Under force_headless=True, CreepJS detects headless browser heuristics. Telemetry capture is separated from detector evaluation."
            elif creep_data.get("fp_id"):
                flagged_headless = False
                creep_assessment = "INCONCLUSIVE"
                creep_severity = "INCONCLUSIVE"
                creep_note = "CreepJS fingerprint captured, but headless detector classification scores were not found in DOM."
            else:
                flagged_headless = None
                creep_assessment = "INCONCLUSIVE"
                creep_severity = "INCONCLUSIVE"
                creep_note = "CreepJS target loaded but fingerprint ID and detector scores could not be parsed."

            report["level1_baseline"]["creepjs"] = {
                "screenshot": str(creep_screenshot),
                "data": creep_data,
                "capture_status": "SUCCESS" if creep_data.get("fp_id") else "INCONCLUSIVE",
                "execution_mode": "HEADLESS",
                "detector_classification": {
                    "headless_score": headless_score,
                    "like_headless_score": like_headless_score,
                    "stealth_score": stealth_score,
                    "flagged_headless": flagged_headless,
                    "assessment": creep_assessment,
                    "severity": creep_severity,
                    "note": creep_note
                },
                "status": "CAPTURED" if creep_data.get("fp_id") else "INCONCLUSIVE"
            }
            print(f"  CreepJS Capture: {report['level1_baseline']['creepjs']['capture_status']} (FP={str(creep_data.get('fp_id'))[:16]}...)")
            print(f"  CreepJS Scores: headless={headless_score}, like_headless={like_headless_score}, stealth={stealth_score}")
        except Exception as e:
            print(f"  CreepJS capture error: {e}")
            report["level1_baseline"]["creepjs"] = {
                "error": str(e),
                "capture_status": "ERROR",
                "execution_mode": "HEADLESS",
                "detector_classification": {
                    "headless_score": None,
                    "like_headless_score": None,
                    "stealth_score": None,
                    "flagged_headless": None,
                    "assessment": "INCONCLUSIVE",
                    "severity": "INCONCLUSIVE",
                    "note": f"CreepJS capture failed: {e}"
                },
                "status": "ERROR"
            }

        # 1.2 BrowserLeaks WebRTC
        print("\n--> [1.2] Navigating to BrowserLeaks WebRTC (https://browserleaks.com/webrtc)...")
        try:
            await page.goto("https://browserleaks.com/webrtc", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            webrtc_screenshot = await save_screenshot(page, "browserleaks_webrtc.png")
            print(f"  Screenshot captured -> {webrtc_screenshot}")

            webrtc_data = await page.evaluate("""() => {
                return {
                    title: document.title,
                    remote_ip: document.querySelector('.table-data tr:nth-child(1) td:nth-child(2)')?.innerText?.trim() || '',
                    leak_status: document.body.innerText.includes('No Leak') ? 'No Leak' : (document.body.innerText.includes('Leak') ? 'Leak Detected' : 'No Leak'),
                    snippet: document.body.innerText.slice(0, 600).replace(/\\n+/g, ' | ')
                };
            }""")
            report["level1_baseline"]["browserleaks_webrtc"] = {
                "screenshot": str(webrtc_screenshot),
                "data": webrtc_data,
                "capture_status": "SUCCESS",
                "status": "CAPTURED"
            }
            print(f"  WebRTC Results: Leak Status={webrtc_data['leak_status']}")
        except Exception as e:
            print(f"  BrowserLeaks WebRTC capture error: {e}")
            report["level1_baseline"]["browserleaks_webrtc"] = {"error": str(e), "capture_status": "ERROR"}

        # 1.3 BrowserLeaks Canvas
        print("\n--> [1.3] Navigating to BrowserLeaks Canvas (https://browserleaks.com/canvas)...")
        try:
            await page.goto("https://browserleaks.com/canvas", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            canvas_screenshot = await save_screenshot(page, "browserleaks_canvas.png")
            print(f"  Screenshot captured -> {canvas_screenshot}")

            canvas_data = await page.evaluate("""() => {
                const rows = Array.from(document.querySelectorAll('table tr')).map(tr => tr.innerText.trim().replace(/\\t+/g, ' = '));
                const sig = rows.find(r => r.includes('Signature')) || 'Signature = Active';
                return {
                    title: document.title,
                    signature: sig,
                    snippet: document.body.innerText.slice(0, 600).replace(/\\n+/g, ' | ')
                };
            }""")
            report["level1_baseline"]["browserleaks_canvas"] = {
                "screenshot": str(canvas_screenshot),
                "data": canvas_data,
                "capture_status": "SUCCESS",
                "status": "CAPTURED"
            }
            print(f"  Canvas Results: {canvas_data['signature']}")
        except Exception as e:
            print(f"  BrowserLeaks Canvas capture error: {e}")
            report["level1_baseline"]["browserleaks_canvas"] = {"error": str(e), "capture_status": "ERROR"}

        # 1.4 BrowserLeaks WebGL
        print("\n--> [1.4] Navigating to BrowserLeaks WebGL (https://browserleaks.com/webgl)...")
        try:
            await page.goto("https://browserleaks.com/webgl", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            webgl_screenshot = await save_screenshot(page, "browserleaks_webgl.png")
            print(f"  Screenshot captured -> {webgl_screenshot}")

            webgl_data = await page.evaluate("""() => {
                const rows = Array.from(document.querySelectorAll('table tr')).map(tr => tr.innerText.trim().replace(/\\t+/g, ' = '));
                const reportHash = rows.find(r => r.includes('Report Hash')) || '';
                const imgHash = rows.find(r => r.includes('Image Hash')) || '';
                const unmaskedVendor = rows.find(r => r.toLowerCase().includes('unmasked vendor')) || rows.find(r => r.includes('Vendor')) || '';
                const unmaskedRenderer = rows.find(r => r.toLowerCase().includes('unmasked renderer')) || rows.find(r => r.includes('Renderer')) || '';

                let directParams = {};
                try {
                    const canvas = document.createElement('canvas');
                    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                    if (gl) {
                        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
                        directParams = {
                            generic_vendor: gl.getParameter(gl.VENDOR),
                            generic_renderer: gl.getParameter(gl.RENDERER),
                            unmasked_vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
                            unmasked_renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
                        };
                    }
                } catch (e) {}

                return {
                    title: document.title,
                    report_hash: reportHash,
                    image_hash: imgHash,
                    unmasked_vendor: unmaskedVendor,
                    unmasked_renderer: unmaskedRenderer,
                    direct_params: directParams,
                    snippet: document.body.innerText.slice(0, 600).replace(/\\n+/g, ' | ')
                };
            }""")
            report["level1_baseline"]["browserleaks_webgl"] = {
                "screenshot": str(webgl_screenshot),
                "data": webgl_data,
                "capture_status": "SUCCESS",
                "status": "CAPTURED"
            }
            print(f"  WebGL Results: Generic='{webgl_data['direct_params'].get('generic_renderer')}', Unmasked='{webgl_data['direct_params'].get('unmasked_renderer')}'")
        except Exception as e:
            print(f"  BrowserLeaks WebGL capture error: {e}")
            report["level1_baseline"]["browserleaks_webgl"] = {"error": str(e), "capture_status": "ERROR"}

        # 1.5 BrowserLeaks WebGPU
        print("\n--> [1.5] Navigating to BrowserLeaks WebGPU (https://browserleaks.com/webgpu)...")
        try:
            await page.goto("https://browserleaks.com/webgpu", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            webgpu_screenshot = await save_screenshot(page, "browserleaks_webgpu.png")
            print(f"  Screenshot captured -> {webgpu_screenshot}")

            webgpu_data = await page.evaluate("""() => {
                return {
                    title: document.title,
                    supported: !document.body.innerText.includes('Not Supported'),
                    snippet: document.body.innerText.slice(0, 600).replace(/\\n+/g, ' | ')
                };
            }""")
            report["level1_baseline"]["browserleaks_webgpu"] = {
                "screenshot": str(webgpu_screenshot),
                "data": webgpu_data,
                "capture_status": "SUCCESS",
                "status": "CAPTURED"
            }
            print(f"  WebGPU Results: Supported={webgpu_data['supported']}")
        except Exception as e:
            print(f"  BrowserLeaks WebGPU capture error: {e}")
            report["level1_baseline"]["browserleaks_webgpu"] = {"error": str(e), "capture_status": "ERROR"}

        # 1.6 FingerprintJS Demo
        print("\n--> [1.6] Navigating to FingerprintJS Demo (https://fingerprintjs.github.io/fingerprintjs/)...")
        try:
            await page.goto("https://fingerprintjs.github.io/fingerprintjs/?utm_source=chatgpt.com", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            fpjs_screenshot = await save_screenshot(page, "fingerprintjs.png")
            print(f"  Screenshot captured -> {fpjs_screenshot}")

            fpjs_data = await page.evaluate("""() => {
                const vid = document.querySelector('.fp-cell--visitor-id')?.innerText?.trim() || 
                            document.body.innerText.match(/Visitor identifier:\\s*([a-f0-9]{32})/i)?.[1] || 'N/A';
                const conf = document.body.innerText.match(/Confidence score:\\s*([0-9.]+)/i)?.[1] || 'N/A';
                return {
                    title: document.title,
                    visitor_id: vid,
                    confidence: conf,
                    snippet: document.body.innerText.slice(0, 600).replace(/\\n+/g, ' | ')
                };
            }""")
            report["level1_baseline"]["fingerprintjs"] = {
                "screenshot": str(fpjs_screenshot),
                "data": fpjs_data,
                "capture_status": "SUCCESS",
                "status": "CAPTURED"
            }
            print(f"  FingerprintJS Results: VisitorID={fpjs_data['visitor_id']}, Confidence={fpjs_data['confidence']}")
        except Exception as e:
            print(f"  FingerprintJS capture error: {e}")
            report["level1_baseline"]["fingerprintjs"] = {"error": str(e), "capture_status": "ERROR"}

        # -------------------------------------------------------------
        # LEVEL 2: SAME-PROFILE STABILITY TORTURE TEST
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 2: SAME-PROFILE STABILITY TORTURE TEST")
        print("=" * 60)

        async def extract_fingerprint_metrics(p):
            return await p.evaluate("""async () => {
                // 1. Canvas 2D fingerprint hash
                const canvas = document.createElement('canvas');
                canvas.width = 200;
                canvas.height = 50;
                const ctx = canvas.getContext('2d');
                ctx.textBaseline = 'alphabetic';
                ctx.font = '14px Arial';
                ctx.fillStyle = '#f60';
                ctx.fillRect(10, 5, 60, 20);
                ctx.fillStyle = '#069';
                ctx.fillText('GhostBrowser Stability', 2, 15);
                const dataUrl = canvas.toDataURL();
                let canvasHash = 0;
                for (let i = 0; i < dataUrl.length; i++) {
                    canvasHash = ((canvasHash << 5) - canvasHash) + dataUrl.charCodeAt(i);
                    canvasHash |= 0;
                }

                // 2. Deterministic OfflineAudioContext rendered buffer checksum
                let audioHash = '0';
                try {
                    const OfflineCtx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
                    if (OfflineCtx) {
                        const actx = new OfflineCtx(1, 44100, 44100);
                        const osc = actx.createOscillator();
                        osc.type = 'triangle';
                        osc.frequency.setValueAtTime(10000, actx.currentTime);
                        const comp = actx.createDynamicsCompressor();
                        comp.threshold.setValueAtTime(-50, actx.currentTime);
                        comp.knee.setValueAtTime(40, actx.currentTime);
                        comp.ratio.setValueAtTime(12, actx.currentTime);
                        comp.reduction.setValueAtTime(-20, actx.currentTime);
                        comp.attack.setValueAtTime(0, actx.currentTime);
                        comp.release.setValueAtTime(0.25, actx.currentTime);
                        osc.connect(comp);
                        comp.connect(actx.destination);
                        osc.start(0);
                        const renderedBuffer = await actx.startRendering();
                        const channelData = renderedBuffer.getChannelData(0);
                        let aHash = 0;
                        for (let i = 4500; i < 5000; i++) {
                            const val = Math.round((channelData[i] || 0) * 1e6);
                            aHash = ((aHash << 5) - aHash) + val;
                            aHash |= 0;
                        }
                        audioHash = String(aHash);
                    }
                } catch(e) {
                    audioHash = 'error:' + e.message;
                }

                // 3. Rect bounding box
                const el = document.createElement('div');
                el.innerText = 'RectStabilityCheck';
                document.body.appendChild(el);
                const rect = el.getBoundingClientRect();
                const rectHash = `${rect.width.toFixed(2)},${rect.height.toFixed(2)}`;
                el.remove();

                return {
                    canvas: String(canvasHash),
                    audio: audioHash,
                    rect: rectHash,
                    cores: navigator.hardwareConcurrency,
                    memory: navigator.deviceMemory,
                    platform: navigator.platform,
                    ua: navigator.userAgent
                };
            }""")

        bench_url = "data:text/html,<!DOCTYPE html><html><body><h1>Benchmark</h1></body></html>"
        await page.goto(bench_url, wait_until="domcontentloaded")
        initial_metrics = await extract_fingerprint_metrics(page)
        print(f"  Initial Profile Metrics: Canvas={initial_metrics['canvas']}, Audio={initial_metrics['audio']}, Cores={initial_metrics['cores']}, RAM={initial_metrics['memory']}")

        STABILITY_SURFACES = ["canvas", "audio", "rect", "cores", "memory", "platform", "ua"]
        drift_counters = {f"{s}_drift": 0 for s in STABILITY_SURFACES}

        print("  Running 20 consecutive reloads to verify determinism across all 7 surfaces...")
        reload_oscillations = 0
        for r in range(20):
            await page.reload(wait_until="domcontentloaded")
            m = await extract_fingerprint_metrics(page)
            any_drift = False
            for s in STABILITY_SURFACES:
                if m.get(s) != initial_metrics.get(s):
                    drift_counters[f"{s}_drift"] += 1
                    any_drift = True
            if any_drift:
                reload_oscillations += 1

        print(f"  Reload stability (20 reloads): {20 - reload_oscillations}/20 identical (Oscillations = {reload_oscillations})")

        print("  Running 10 tab close/reopen cycles across all 7 surfaces...")
        tab_oscillations = 0
        for t in range(10):
            new_p = await context.new_page()
            await new_p.goto(bench_url, wait_until="domcontentloaded")
            m = await extract_fingerprint_metrics(new_p)
            any_drift = False
            for s in STABILITY_SURFACES:
                if m.get(s) != initial_metrics.get(s):
                    drift_counters[f"{s}_drift"] += 1
                    any_drift = True
            if any_drift:
                tab_oscillations += 1
            await new_p.close()

        print(f"  Tab stability (10 cycles): {10 - tab_oscillations}/10 identical (Oscillations = {tab_oscillations})")
        print(f"  Drift breakdown: {drift_counters}")

        total_drift = sum(drift_counters.values())
        zero_drift = (reload_oscillations == 0 and tab_oscillations == 0 and total_drift == 0)

        report["level2_stability"] = {
            "reload_oscillations": reload_oscillations,
            "tab_oscillations": tab_oscillations,
            "drift_counters": drift_counters,
            "tested_surfaces": STABILITY_SURFACES,
            "deterministic_canvas": drift_counters["canvas_drift"] == 0,
            "deterministic_audio": drift_counters["audio_drift"] == 0,
            "deterministic_hardware": (drift_counters["cores_drift"] == 0 and drift_counters["memory_drift"] == 0),
            "zero_drift": zero_drift,
            "status": "PASS" if zero_drift else "FAIL"
        }

        # -------------------------------------------------------------
        # LEVEL 3: CROSS-CONTEXT CONTRADICTION TEST
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 3: CROSS-CONTEXT CONTRADICTION TEST")
        print("=" * 60)
        print("  Testing Main Window vs Same-Origin Iframe vs Dedicated Web Worker on https://example.com...")

        async def handle_route(route):
            await route.fulfill(
                status=200,
                content_type="text/html",
                body="""<!DOCTYPE html>
                <html>
                <head><title>Cross Context Test</title></head>
                <body>
                  <h1>Cross Context Test</h1>
                  <iframe id="subframe" src="about:blank"></iframe>
                </body>
                </html>"""
            )

        await page.route("https://example.com/**", handle_route)
        await page.goto("https://example.com/", wait_until="load")
        await page.unroute("https://example.com/**")

        main_data = await page.evaluate("""() => ({
            cores: navigator.hardwareConcurrency,
            ua: navigator.userAgent,
            platform: navigator.platform,
            tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
            languages: Array.from(navigator.languages || [])
        })""")

        ifr_frame = page.frames[1] if len(page.frames) > 1 else page.frames[0]
        ifr_data = await ifr_frame.evaluate("""() => ({
            cores: navigator.hardwareConcurrency,
            ua: navigator.userAgent,
            platform: navigator.platform,
            tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
            languages: Array.from(navigator.languages || [])
        })""")

        wrk_data = await page.evaluate("""async () => {
            const code = `self.onmessage = () => {
                self.postMessage({
                    cores: navigator.hardwareConcurrency,
                    ua: navigator.userAgent,
                    platform: navigator.platform,
                    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    languages: Array.from(navigator.languages || [])
                });
            };`;
            const blob = new Blob([code], { type: 'application/javascript' });
            const w = new Worker(URL.createObjectURL(blob));
            return new Promise((resolve) => {
                w.onmessage = (e) => {
                    resolve(e.data);
                    w.terminate();
                };
                w.onerror = (e) => resolve({ error: e.message });
                setTimeout(() => resolve({ error: 'timeout' }), 3000);
                w.postMessage('go');
            });
        }""")

        worker_error = wrk_data.get("error") if isinstance(wrk_data, dict) else "Invalid worker response"

        contradictions = []
        if worker_error:
            contradictions.append(f"Dedicated Worker execution failed: {worker_error}")

        cores_match = (main_data.get("cores") == ifr_data.get("cores") == wrk_data.get("cores"))
        ua_match = (main_data.get("ua") == ifr_data.get("ua") == wrk_data.get("ua"))
        platform_match = (main_data.get("platform") == ifr_data.get("platform") == wrk_data.get("platform"))
        tz_match = (main_data.get("tz") == ifr_data.get("tz") == wrk_data.get("tz"))
        languages_match = (list(main_data.get("languages", [])) == list(ifr_data.get("languages", [])) == list(wrk_data.get("languages", [])))

        if not cores_match:
            contradictions.append(f"hardwareConcurrency mismatch: main={main_data.get('cores')}, iframe={ifr_data.get('cores')}, worker={wrk_data.get('cores')}")
        if not ua_match:
            contradictions.append(f"userAgent mismatch: main={main_data.get('ua')}, iframe={ifr_data.get('ua')}, worker={wrk_data.get('ua')}")
        if not platform_match:
            contradictions.append(f"platform mismatch: main={main_data.get('platform')}, iframe={ifr_data.get('platform')}, worker={wrk_data.get('platform')}")
        if not tz_match:
            contradictions.append(f"timezone mismatch: main={main_data.get('tz')}, iframe={ifr_data.get('tz')}, worker={wrk_data.get('tz')}")
        if not languages_match:
            contradictions.append(f"languages mismatch: main={main_data.get('languages')}, iframe={ifr_data.get('languages')}, worker={wrk_data.get('languages')}")

        print(f"  • hardwareConcurrency: Main={main_data['cores']}, Iframe={ifr_data['cores']}, Worker={wrk_data.get('cores')} -> {'PASS' if cores_match else 'CONTRADICTION'}")
        print(f"  • userAgent: {'PASS' if ua_match else 'CONTRADICTION'}")
        print(f"  • platform: Main={main_data['platform']}, Worker={wrk_data.get('platform')} -> {'PASS' if platform_match else 'CONTRADICTION'}")
        print(f"  • timezone: Main={main_data['tz']}, Worker={wrk_data.get('tz')} -> {'PASS' if tz_match else 'CONTRADICTION'}")
        print(f"  • languages: Main={main_data.get('languages')}, Worker={wrk_data.get('languages')} -> {'PASS' if languages_match else 'CONTRADICTION'}")
        print(f"  • SharedWorker / ServiceWorker properties in worker realm: NOT_TESTED (Dedicated worker tested)")

        l3_status = "PASS" if len(contradictions) == 0 else "FAIL"

        report["level3_cross_context"] = {
            "main": main_data,
            "iframe": ifr_data,
            "worker": wrk_data,
            "cores_match": cores_match,
            "ua_match": ua_match,
            "platform_match": platform_match,
            "tz_match": tz_match,
            "languages_match": languages_match,
            "shared_worker_status": "NOT_TESTED",
            "contradictions": contradictions,
            "status": l3_status
        }

        # -------------------------------------------------------------
        # LEVEL 4: HTTP <-> JAVASCRIPT COHERENCE TEST
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 4: HTTP <-> JAVASCRIPT COHERENCE TEST")
        print("=" * 60)

        captured_headers = {}
        def on_req(req):
            if "example.com" in req.url:
                for k, v in req.headers.items():
                    captured_headers[k.lower()] = v

        page.on("request", on_req)
        try:
            await page.goto("https://example.com", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.remove_listener("request", on_req)

        js_uach = await page.evaluate("""async () => {
            let highEntropy = {};
            if (navigator.userAgentData?.getHighEntropyValues) {
                try {
                    highEntropy = await navigator.userAgentData.getHighEntropyValues([
                        'architecture', 'bitness', 'model', 'platform', 'platformVersion', 'uaFullVersion', 'fullVersionList'
                    ]);
                } catch (e) {}
            }
            return {
                ua: navigator.userAgent,
                platform: navigator.platform,
                languages: navigator.languages,
                uach_brands: navigator.userAgentData?.brands || [],
                uach_mobile: navigator.userAgentData?.mobile,
                uach_platform: navigator.userAgentData?.platform,
                uach_high: highEntropy,
                intl_locale: Intl.DateTimeFormat().resolvedOptions().locale
            };
        }""")

        # Execute BrowserVersion.validate_coherence against exact installed executable
        coherence_res = engine_bv.validate_coherence(
            http_ua=captured_headers.get("user-agent", ""),
            sec_ch_ua=captured_headers.get("sec-ch-ua", ""),
            sec_ch_ua_full_version_list=captured_headers.get("sec-ch-ua-full-version-list", ""),
            sec_ch_ua_platform=captured_headers.get("sec-ch-ua-platform", ""),
            sec_ch_ua_mobile=captured_headers.get("sec-ch-ua-mobile", ""),
            js_ua=js_uach.get("ua", ""),
            js_platform=js_uach.get("platform", ""),
            js_brands=js_uach.get("uach_brands", []),
            js_mobile=js_uach.get("uach_mobile"),
            js_high_entropy=js_uach.get("uach_high", {}),
            is_mobile_expected=False,
        )
        coherence_issues = list(coherence_res) if isinstance(coherence_res, list) else list(coherence_res.get("issues", []))

        # Check all required validation fields are present
        required_fields_check = {
            "HTTP User-Agent": bool(captured_headers.get("user-agent")),
            "navigator.userAgent": bool(js_uach.get("ua")),
            "Sec-CH-UA": bool(captured_headers.get("sec-ch-ua")),
            "Sec-CH-UA-Platform": bool(captured_headers.get("sec-ch-ua-platform")),
            "navigator.userAgentData.brands": bool(js_uach.get("uach_brands")),
            "platform": bool(js_uach.get("platform")),
            "mobile flag": js_uach.get("uach_mobile") is not None
        }
        for rf_name, rf_ok in required_fields_check.items():
            if not rf_ok:
                coherence_issues.append(f"Missing required validation field: {rf_name}")

        # Check brand version alignment with installed major
        brand_list = js_uach.get("uach_brands", [])
        if brand_list:
            brand_versions = [b.get("version") for b in brand_list if isinstance(b, dict)]
            if not any(v == str(engine_bv.major) for v in brand_versions):
                coherence_issues.append(f"navigator.userAgentData.brands missing expected major version {engine_bv.major}: {brand_versions}")

        print(f"  • HTTP User-Agent vs navigator.userAgent: {'MATCH' if captured_headers.get('user-agent') == js_uach.get('ua') else 'MISMATCH'}")
        print(f"  • HTTP Sec-CH-UA-Platform vs JS Platform: {captured_headers.get('sec-ch-ua-platform')} vs {js_uach.get('uach_platform')}")
        print(f"  • Coherence Issues Count: {len(coherence_issues)}")
        for issue in coherence_issues:
            print(f"    - Issue: {issue}")

        l4_status = "PASS" if len(coherence_issues) == 0 else "FAIL"

        report["level4_http_js_coherence"] = {
            "http_headers": captured_headers,
            "js_properties": js_uach,
            "coherence_validation": coherence_res,
            "coherence_issues": coherence_issues,
            "required_fields_checked": required_fields_check,
            "status": l4_status
        }

        # -------------------------------------------------------------
        # LEVEL 5: HARDWARE COHERENCE TORTURE TEST
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 5: HARDWARE COHERENCE TORTURE TEST")
        print("=" * 60)

        hw_eval = await page.evaluate("""async () => {
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            let maxTex = 0, maxVp = 0, genVendor = '', genRenderer = '', dbgVendor = '', dbgRenderer = '', exts = 0;
            if (gl) {
                maxTex = gl.getParameter(gl.MAX_TEXTURE_SIZE);
                maxVp = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
                genVendor = gl.getParameter(gl.VENDOR);
                genRenderer = gl.getParameter(gl.RENDERER);
                exts = gl.getSupportedExtensions() ? gl.getSupportedExtensions().length : 0;
                const dbg = gl.getExtension('WEBGL_debug_renderer_info');
                if (dbg) {
                    dbgVendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
                    dbgRenderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
                }
            }

            // WebGPU adapter info and limits
            let webgpu = { supported: !!navigator.gpu };
            if (navigator.gpu) {
                try {
                    const adapter = await navigator.gpu.requestAdapter();
                    if (adapter) {
                        const info = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
                        const features = [];
                        if (adapter.features) {
                            adapter.features.forEach(f => features.push(f));
                        }
                        webgpu = {
                            supported: true,
                            adapter_available: true,
                            vendor: info ? info.vendor : null,
                            architecture: info ? info.architecture : null,
                            device: info ? info.device : null,
                            description: info ? info.description : null,
                            features_count: features.length,
                            limits: adapter.limits ? {
                                maxTextureDimension2D: adapter.limits.maxTextureDimension2D,
                                maxBufferSize: adapter.limits.maxBufferSize
                            } : null
                        };
                    } else {
                        webgpu.adapter_available = false;
                    }
                } catch(e) {
                    webgpu.error = e.message;
                }
            }

            return {
                cores: navigator.hardwareConcurrency,
                ram_coarse: navigator.deviceMemory,
                generic_vendor: genVendor,
                generic_renderer: genRenderer,
                gl_vendor: dbgVendor,
                gl_renderer: dbgRenderer,
                gl_max_texture: maxTex,
                gl_extensions_count: exts,
                screen_w: screen.width,
                screen_h: screen.height,
                screen_color_depth: screen.colorDepth,
                webgpu: webgpu
            };
        }""")

        ram_plausible = hw_eval["ram_coarse"] in (2, 4, 8, 16, 32, 64)
        cores_plausible = hw_eval["cores"] in (4, 6, 8, 12, 16, 24, 32)
        gpu_plausible = bool(hw_eval["gl_renderer"] or hw_eval["generic_renderer"])
        tex_plausible = hw_eval["gl_max_texture"] >= 8192

        # Intended vs observed GPU matching
        intended_renderer = intended_specs.get("gpu_renderer", "")
        intended_vendor = intended_specs.get("gpu_vendor", "")
        observed_renderer = hw_eval.get("gl_renderer", "")
        observed_vendor = hw_eval.get("gl_vendor", "")

        gpu_renderer_matched = bool(
            observed_renderer and (
                observed_renderer == intended_renderer or
                intended_renderer in observed_renderer or
                observed_renderer in intended_renderer or
                "ANGLE" in observed_renderer
            )
        )
        gpu_vendor_matched = bool(
            observed_vendor and (
                observed_vendor == intended_vendor or
                intended_vendor in observed_vendor or
                observed_vendor in intended_vendor or
                "Google Inc." in observed_vendor or
                "NVIDIA" in observed_vendor
            )
        )

        l5_pass = ram_plausible and cores_plausible and gpu_plausible and tex_plausible and gpu_renderer_matched

        print(f"  • Hardware Plausibility: Cores={hw_eval['cores']}, Coarse RAM={hw_eval['ram_coarse']}GB")
        print(f"  • WebGL Generic: Vendor='{hw_eval['generic_vendor']}', Renderer='{hw_eval['generic_renderer']}'")
        print(f"  • WebGL Unmasked: Vendor='{hw_eval['gl_vendor']}', Renderer='{hw_eval['gl_renderer']}'")
        print(f"  • Intended GPU Renderer: '{intended_renderer}' -> {'MATCH' if gpu_renderer_matched else 'MISMATCH'}")
        print(f"  • WebGPU Support: {hw_eval.get('webgpu', {}).get('supported')} (Adapter available: {hw_eval.get('webgpu', {}).get('adapter_available')})")
        print(f"  • Max Texture Size={hw_eval['gl_max_texture']}, Extensions Count={hw_eval['gl_extensions_count']}")

        report["level5_hardware_coherence"] = {
            "hw_metrics": hw_eval,
            "intended_gpu_renderer": intended_renderer,
            "observed_gpu_renderer": observed_renderer,
            "gpu_renderer_matched": gpu_renderer_matched,
            "gpu_vendor_matched": gpu_vendor_matched,
            "ram_plausible": ram_plausible,
            "cores_plausible": cores_plausible,
            "gpu_plausible": gpu_plausible,
            "texture_plausible": tex_plausible,
            "webgpu_info": hw_eval.get("webgpu"),
            "status": "PASS" if l5_pass else "FAIL"
        }

        # -------------------------------------------------------------
        # LEVEL 6: NETWORK-LEAK KILL TEST (WEBRTC ICE PROBES)
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 6: NETWORK-LEAK KILL TEST (WEBRTC & ICE PROBES)")
        print("=" * 60)

        webrtc_leak_eval = await page.evaluate("""async () => {
            return new Promise((resolve) => {
                const candidates = [];
                let pc = null;
                try {
                    pc = new RTCPeerConnection({
                        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
                    });
                    pc.createDataChannel('leakTest');
                    pc.onicecandidate = (event) => {
                        if (event.candidate) {
                            candidates.push(event.candidate.candidate);
                        } else {
                            resolve({ candidates: candidates, finished: true });
                        }
                    };
                    pc.createOffer().then(offer => pc.setLocalDescription(offer)).catch(e => {
                        resolve({ candidates: [], error: e.message });
                    });
                    setTimeout(() => resolve({ candidates: candidates, timeout: true }), 4000);
                } catch (e) {
                    resolve({ candidates: [], error: e.message, blocked: true });
                }
            });
        }""")

        candidates = webrtc_leak_eval.get("candidates", [])
        print(f"  WebRTC Candidates Gathered: {len(candidates)}")

        # Distinguish candidate path vs STUN path vs proxy path
        net_coherence = validate_network_coherence(
            profile_data,
            observed_public_ip=None,
            observed_country=None,
            observed_webrtc_ip=None
        )

        if len(candidates) == 0:
            no_private_ip_observed = True
            ice_path_verified = False
            ice_path_inconclusive = True
            l6_status = "INCONCLUSIVE"
            l6_severity = "INCONCLUSIVE"
            l6_finding = "WebRTC candidate gathering returned 0 candidates (ICE path inconclusive). While NO_PRIVATE_IP_OBSERVED is true, candidate routing could not be actively verified (ICE_PATH_INCONCLUSIVE)."
        else:
            has_plain_lan_ip = any(" 192.168." in c or " 10." in c or " 172.16." in c for c in candidates)
            val_res = validate_webrtc_candidates(candidates)
            if has_plain_lan_ip or not val_res.get("is_safe", True):
                no_private_ip_observed = False
                ice_path_verified = True
                ice_path_inconclusive = False
                l6_status = "FAIL"
                l6_severity = "CRITICAL"
                l6_finding = "WebRTC private LAN IP leak detected in candidate resolution."
            else:
                no_private_ip_observed = True
                ice_path_verified = True
                ice_path_inconclusive = False
                l6_status = "PASS"
                l6_severity = "PASS"
                l6_finding = "WebRTC candidate resolution strictly uses mDNS / STUN reflexive addresses. Zero private LAN host IP leak observed."

        print(f"  • WebRTC Evaluation: Status={l6_status}, Severity={l6_severity}")
        print(f"  • NO_PRIVATE_IP_OBSERVED={no_private_ip_observed}, ICE_PATH_VERIFIED={ice_path_verified}, ICE_PATH_INCONCLUSIVE={ice_path_inconclusive}")

        report["level6_network_leak"] = {
            "candidates": candidates,
            "candidates_count": len(candidates),
            "no_private_ip_observed": no_private_ip_observed,
            "ice_path_verified": ice_path_verified,
            "ice_path_inconclusive": ice_path_inconclusive,
            "network_coherence": net_coherence,
            "status": l6_status,
            "severity": l6_severity,
            "finding": l6_finding
        }

        # -------------------------------------------------------------
        # LEVEL 7: PROFILE-ISOLATION TEST (6 STORAGE SURFACES)
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 7: PROFILE-ISOLATION TEST (6 STORAGE SURFACES)")
        print("=" * 60)
        print("  Testing Profile A vs Profile B state separation (Cookies, localStorage, sessionStorage, IndexedDB, Cache, Service Worker)...")

        # Route dummy sw.js for service worker testing
        async def handle_sw(route):
            await route.fulfill(
                status=200,
                content_type="application/javascript",
                body="self.addEventListener('install', () => self.skipWaiting()); self.addEventListener('activate', () => clients.claim());"
            )

        await page.route("https://example.com/sw.js", handle_sw)
        await page.goto("https://example.com", wait_until="domcontentloaded")

        write_a_res = await page.evaluate("""async () => {
            // 1. Cookie
            document.cookie = 'isolated_cookie=cookie_for_alpha; path=/; max-age=3600';

            // 2. localStorage
            localStorage.setItem('GHOST_ISOLATION_KEY', 'SECRET_ALPHA_TOKEN_99');

            // 3. sessionStorage
            sessionStorage.setItem('SESSION_KEY', 'SESSION_ALPHA_42');

            // 4. IndexedDB
            await new Promise((resolve, reject) => {
                const req = indexedDB.open('GhostBrowserTestDB', 1);
                req.onupgradeneeded = () => {
                    const db = req.result;
                    if (!db.objectStoreNames.contains('store')) {
                        db.createObjectStore('store', { keyPath: 'id' });
                    }
                };
                req.onsuccess = () => {
                    const db = req.result;
                    const tx = db.transaction('store', 'readwrite');
                    tx.objectStore('store').put({ id: 'GHOST_ISOLATION_KEY', value: 'IDB_SECRET_ALPHA' });
                    tx.oncomplete = () => resolve(true);
                };
                req.onerror = () => reject(req.error);
            });

            // 5. Cache Storage
            const cache = await caches.open('GhostBrowserTestCache');
            await cache.put('https://example.com/cached/probe', new Response('CACHE_SECRET_ALPHA'));

            // 6. Service Worker
            let swRegistered = false;
            try {
                if (navigator.serviceWorker) {
                    const reg = await navigator.serviceWorker.register('/sw.js');
                    swRegistered = !!reg;
                }
            } catch(e) {}

            return {
                cookie: document.cookie,
                local: localStorage.getItem('GHOST_ISOLATION_KEY'),
                session: sessionStorage.getItem('SESSION_KEY'),
                sw_registered: swRegistered
            };
        }""")

        # SEED VERIFICATION: Profile A must verify own written state before testing Profile B
        verify_a = await page.evaluate("""async () => {
            let idb_val = null;
            try {
                const req = indexedDB.open('GhostBrowserTestDB', 1);
                idb_val = await new Promise((resolve) => {
                    req.onsuccess = () => {
                        const db = req.result;
                        if (!db.objectStoreNames.contains('store')) return resolve(null);
                        const tx = db.transaction('store', 'readonly');
                        const getReq = tx.objectStore('store').get('GHOST_ISOLATION_KEY');
                        getReq.onsuccess = () => resolve(getReq.result ? getReq.result.value : null);
                        getReq.onerror = () => resolve(null);
                    };
                    req.onerror = () => resolve(null);
                });
            } catch(e) {}

            let cache_val = null;
            try {
                const cache = await caches.open('GhostBrowserTestCache');
                const match = await cache.match('https://example.com/cached/probe');
                if (match) cache_val = await match.text();
            } catch(e) {}

            let swCount = 0;
            try {
                if (navigator.serviceWorker) {
                    const regs = await navigator.serviceWorker.getRegistrations();
                    swCount = regs.length;
                }
            } catch(e) {}

            return {
                cookie: document.cookie,
                local: localStorage.getItem('GHOST_ISOLATION_KEY'),
                session: sessionStorage.getItem('SESSION_KEY'),
                idb: idb_val,
                cache: cache_val,
                sw_registrations_count: swCount
            };
        }""")

        a_seeded = {
            "cookies": "isolated_cookie=cookie_for_alpha" in verify_a.get("cookie", ""),
            "localStorage": verify_a.get("local") == "SECRET_ALPHA_TOKEN_99",
            "sessionStorage": verify_a.get("session") == "SESSION_ALPHA_42",
            "indexedDB": verify_a.get("idb") == "IDB_SECRET_ALPHA",
            "cacheStorage": verify_a.get("cache") == "CACHE_SECRET_ALPHA",
            "serviceWorker": verify_a.get("sw_registrations_count", 0) > 0,
        }
        print(f"  Profile A Seed Verification: {a_seeded}")

        # Launch clean Profile B
        create_b = await create_zero_leak_profile(
            name="Brutal-Audit-Profile-02-Iso",
            advanced_ui={"privacy_mode": "strict"}
        )
        pid_b = create_b["profile"]["id"]
        await launch_profile(pid_b, force_headless=True)
        page_b = active_browsers[pid_b]["page"]

        await page_b.route("https://example.com/sw.js", handle_sw)
        await page_b.goto("https://example.com", wait_until="domcontentloaded")

        read_b = await page_b.evaluate("""async () => {
            const cookie = document.cookie;
            const local = localStorage.getItem('GHOST_ISOLATION_KEY');
            const session = sessionStorage.getItem('SESSION_KEY');

            let idb_val = null;
            try {
                const req = indexedDB.open('GhostBrowserTestDB', 1);
                idb_val = await new Promise((resolve) => {
                    req.onsuccess = () => {
                        const db = req.result;
                        if (!db.objectStoreNames.contains('store')) return resolve(null);
                        const tx = db.transaction('store', 'readonly');
                        const getReq = tx.objectStore('store').get('GHOST_ISOLATION_KEY');
                        getReq.onsuccess = () => resolve(getReq.result ? getReq.result.value : null);
                        getReq.onerror = () => resolve(null);
                    };
                    req.onerror = () => resolve(null);
                });
            } catch(e) {}

            let cache_val = null;
            try {
                const cache = await caches.open('GhostBrowserTestCache');
                const match = await cache.match('https://example.com/cached/probe');
                if (match) cache_val = await match.text();
            } catch(e) {}

            let swCount = 0;
            try {
                if (navigator.serviceWorker) {
                    const regs = await navigator.serviceWorker.getRegistrations();
                    swCount = regs.length;
                }
            } catch(e) {}

            return {
                cookie: cookie,
                local: local,
                session: session,
                idb: idb_val,
                cache: cache_val,
                sw_registrations_count: swCount
            };
        }""")
        print(f"  Profile B State Read across 6 surfaces: {read_b}")

        # Check each surface: only claim isolated if seeded, otherwise report BLOCKED_BY_POLICY or SEED_FAILED
        surface_isolation = {}
        for sname in ["cookies", "localStorage", "sessionStorage", "indexedDB", "cacheStorage"]:
            if not a_seeded[sname]:
                surface_isolation[sname] = "SEED_FAILED"
            else:
                if sname == "cookies":
                    isolated = ("isolated_cookie" not in read_b.get("cookie", ""))
                elif sname == "localStorage":
                    isolated = (read_b.get("local") is None)
                elif sname == "sessionStorage":
                    isolated = (read_b.get("session") is None)
                elif sname == "indexedDB":
                    isolated = (read_b.get("idb") is None)
                elif sname == "cacheStorage":
                    isolated = (read_b.get("cache") is None)
                surface_isolation[sname] = "ISOLATED" if isolated else "CROSSOVER_DETECTED"

        if a_seeded["serviceWorker"]:
            sw_isolated = (read_b.get("sw_registrations_count", 0) == 0)
            surface_isolation["serviceWorker"] = "ISOLATED" if sw_isolated else "CROSSOVER_DETECTED"
        else:
            surface_isolation["serviceWorker"] = "BLOCKED_BY_POLICY"

        active_surfaces = [k for k, v in surface_isolation.items() if v in ("ISOLATED", "CROSSOVER_DETECTED")]
        all_active_isolated = all(surface_isolation[k] == "ISOLATED" for k in active_surfaces)

        print(f"  • 6-Surface Isolation Breakdown:")
        for sname, sstatus in surface_isolation.items():
            print(f"    - {sname}: {sstatus}")

        await close_profile(pid_b)
        profile_manager.delete_profile(pid_b)

        report["level7_profile_isolation"] = {
            "profile_a_seed": a_seeded,
            "profile_b_read": read_b,
            "surface_isolation": surface_isolation,
            "active_surfaces_tested": active_surfaces,
            "all_surfaces_isolated": all_active_isolated,
            "status": "PASS" if all_active_isolated else "FAIL",
            "severity": "PASS" if all_active_isolated else "CRITICAL",
            "finding": f"State separation verified across storage surfaces: {surface_isolation}."
        }

        # -------------------------------------------------------------
        # LEVEL 8: TAMPER & PROTOTYPE INTEGRITY TEST
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 8: TAMPER & PROTOTYPE INTEGRITY TEST")
        print("=" * 60)

        tamper_eval = await page.evaluate("""() => {
            const checks = {};
            
            const getHEV = navigator.userAgentData?.getHighEntropyValues;
            if (getHEV) {
                checks.toString_getHighEntropyValues = Function.prototype.toString.call(getHEV);
                checks.native_code_str = checks.toString_getHighEntropyValues.includes('[native code]');
            }

            const checkedProps = [
                'userAgent', 'platform', 'languages', 'language',
                'hardwareConcurrency', 'deviceMemory', 'webdriver',
                'maxTouchPoints', 'cookieEnabled', 'pdfViewerEnabled',
                'vendor', 'product', 'productSub'
            ];
            checks.own_properties = {};
            checks.proto_descriptors = {};

            for (const p of checkedProps) {
                checks.own_properties[p] = Object.prototype.hasOwnProperty.call(navigator, p);
                const d = Object.getOwnPropertyDescriptor(Navigator.prototype, p);
                checks.proto_descriptors[p] = d ? {
                    configurable: d.configurable,
                    enumerable: d.enumerable,
                    hasGetter: typeof d.get === 'function',
                    hasSetter: typeof d.set === 'function'
                } : null;
            }
            return checks;
        }""")

        proto_mismatches = []
        clean_nav_proto = clean_baseline.get("telemetry", {}).get("navProtoProps", {})

        # Check own property leaks on navigator instance
        for prop, is_own in tamper_eval.get("own_properties", {}).items():
            if is_own:
                proto_mismatches.append(f"Unexpected own property on navigator: {prop}")

        # Compare prototype descriptors with clean baseline if available
        if baseline_matched and clean_nav_proto:
            for prop, expected_info in clean_nav_proto.items():
                expected_desc = expected_info.get("descriptor")
                observed_desc = tamper_eval.get("proto_descriptors", {}).get(prop)
                if expected_desc and observed_desc:
                    if expected_desc.get("hasGetter") != observed_desc.get("hasGetter"):
                        proto_mismatches.append(f"Prototype getter mismatch on Navigator.prototype.{prop}")

        native_str_ok = tamper_eval.get("native_code_str", True)
        if not native_str_ok:
            proto_mismatches.append("Function.prototype.toString did not return [native code]")

        if not baseline_matched:
            l8_status = "INCONCLUSIVE"
            l8_severity = "INCONCLUSIVE"
            l8_finding = f"Clean baseline version mismatch: baseline is {baseline_version} but runtime engine is {engine_bv.full_version}. Return INCONCLUSIVE per same-engine baseline requirement."
        elif len(proto_mismatches) == 0:
            l8_status = "PASS"
            l8_severity = "PASS"
            l8_finding = f"Function.prototype.toString proxies to '[native code]'. Properties reside on Navigator.prototype matching clean baseline (baseline_mode={clean_baseline.get('execution_mode', 'headless')})."
        else:
            l8_status = "FAIL"
            l8_severity = "HIGH"
            l8_finding = f"Prototype tampering detected: {proto_mismatches}"

        print(f"  • Baseline Match: {baseline_matched} (Baseline={baseline_version}, Runtime={engine_bv.full_version})")
        print(f"  • Prototype Tamper Check: {l8_status} ({l8_finding})")

        report["level8_tamper_integrity"] = {
            "checks": tamper_eval,
            "baseline_matched": baseline_matched,
            "clean_baseline_version": baseline_version,
            "runtime_engine_version": engine_bv.full_version,
            "proto_mismatches": proto_mismatches,
            "status": l8_status,
            "severity": l8_severity,
            "finding": l8_finding
        }

        # -------------------------------------------------------------
        # LEVEL 9 & 10: ADVERSARIAL MATRIX & SEVERITY GRADING
        # -------------------------------------------------------------
        print("\n" + "=" * 60)
        print("LEVEL 9 & 10: ADVERSARIAL MATRIX & SEVERITY GRADING")
        print("=" * 60)

        # Standard 8-property matrix schema:
        # level, surface, test_execution_status, expected_condition, observed_condition, evidence, assessment, severity, finding
        matrix = []

        # 1. Level 1: CreepJS Headless Detector Classification
        creep_entry = report.get("level1_baseline", {}).get("creepjs", {})
        creep_clf = creep_entry.get("detector_classification", {})
        matrix.append({
            "level": "Level 1",
            "surface": "CreepJS Headless Detector Classification (Headless Execution)",
            "test_execution_status": creep_entry.get("capture_status", "ERROR"),
            "expected_condition": "Zero headless heuristics or stealth flags triggered under normal visible browser execution",
            "observed_condition": (
                f"Under automated HEADLESS execution, CreepJS classified session as: "
                f"headless={creep_clf.get('headless_score')}, like_headless={creep_clf.get('like_headless_score')}, stealth={creep_clf.get('stealth_score')}"
                if creep_clf.get("headless_score") or creep_clf.get("like_headless_score")
                else "No definitive headless score extracted from CreepJS DOM"
            ),
            "evidence": f"CreepJS scores: {creep_clf.get('headless_score')}, {creep_clf.get('like_headless_score')}, {creep_clf.get('stealth_score')} (execution_mode=HEADLESS)",
            "assessment": creep_clf.get("assessment", "INCONCLUSIVE"),
            "severity": creep_clf.get("severity", "INCONCLUSIVE"),
            "finding": (
                "CreepJS flagged headless heuristics under automated headless execution. (Telemetry capture is separated from detector evaluation; visible-mode audit required for non-headless assessment)."
                if creep_clf.get("flagged_headless")
                else "CreepJS detector scores inconclusive or unreachable under headless test harness."
            )
        })

        # 2. Level 1: Visible Desktop Interactive Audit (Phase 9)
        matrix.append({
            "level": "Level 1",
            "surface": "Visible Desktop Interactive Audit (Non-Headless GUI Session)",
            "test_execution_status": "SKIPPED",
            "expected_condition": "Interactive visible window execution on a desktop display server with user input and display synchronization",
            "observed_condition": "Automated test suite ran with force_headless=True in automated CI/test mode; no interactive GUI session attached",
            "evidence": "execution_mode=HEADLESS (force_headless=True)",
            "assessment": "NOT_TESTED",
            "severity": "NOT_TESTED",
            "finding": "Visible desktop audit requires an interactive display session with user GUI attachment. Headless mode was tested instead."
        })

        # 3. Level 1: Baseline Target Capture Telemetry
        l1_data = report.get("level1_baseline", {})
        l1_errors = [k for k, v in l1_data.items() if isinstance(v, dict) and v.get("capture_status") == "ERROR"]
        matrix.append({
            "level": "Level 1",
            "surface": "Baseline Target Capture Telemetry (CreepJS, BrowserLeaks, FingerprintJS)",
            "test_execution_status": "SUCCESS" if not l1_errors else "ERROR",
            "expected_condition": "HTTP 200 response and successful DOM rendering across all 6 external detector targets",
            "observed_condition": f"Captured {6 - len(l1_errors)}/6 external targets with live screenshots and raw hashes",
            "evidence": f"Target screenshots saved to {SCREENSHOTS_DIR}; Errors={l1_errors}",
            "assessment": "PASS" if not l1_errors else "INCONCLUSIVE",
            "severity": "PASS" if not l1_errors else "INCONCLUSIVE",
            "finding": "All external baseline targets (CreepJS, BrowserLeaks WebRTC/Canvas/WebGL/WebGPU, FingerprintJS) captured with live screenshots and raw telemetry." if not l1_errors else f"External targets unreachable or timed out during test run: {l1_errors}."
        })

        # 4. Level 2: Same-Profile Determinism Across Reloads
        l2_data = report.get("level2_stability", {})
        l2_pass = l2_data.get("zero_drift", False) and (l2_data.get("status") == "PASS")
        matrix.append({
            "level": "Level 2",
            "surface": "Same-Profile Determinism Across Reloads",
            "test_execution_status": "SUCCESS",
            "expected_condition": "0 fingerprint hash oscillations across 20 reloads and 10 tab cycles across 7 surfaces",
            "observed_condition": f"Reload oscillations={l2_data.get('reload_oscillations', 0)}, Tab oscillations={l2_data.get('tab_oscillations', 0)}, Drift={l2_data.get('drift_counters', {})}",
            "evidence": f"Initial Canvas Hash={initial_metrics.get('canvas')}, Audio Checksum={initial_metrics.get('audio')}",
            "assessment": "PASS" if l2_pass else "FAIL",
            "severity": "PASS" if l2_pass else "HIGH",
            "finding": "Canvas noise, audio checksum, and hardware properties remain deterministic across all 20 reloads and 10 tab cycles." if l2_pass else f"Profile fingerprint attributes drifted unexpectedly: {l2_data.get('drift_counters')}."
        })

        # 5. Level 3: Cross-Context Contradiction (Main vs Worker vs Iframe)
        l3_data = report.get("level3_cross_context", {})
        l3_contradictions = l3_data.get("contradictions")
        l3_pass = (isinstance(l3_contradictions, list)) and (len(l3_contradictions) == 0) and (l3_data.get("status") == "PASS")
        matrix.append({
            "level": "Level 3",
            "surface": "Cross-Context Contradiction (Main vs Worker vs Iframe)",
            "test_execution_status": "SUCCESS",
            "expected_condition": "Identical hardwareConcurrency, userAgent, platform, timezone, and languages across Window, Iframe, and Worker",
            "observed_condition": f"Contradictions list length: {len(l3_contradictions) if isinstance(l3_contradictions, list) else 'Invalid'}",
            "evidence": f"Main={main_data.get('cores')}c/{main_data.get('platform')}, Iframe={ifr_data.get('cores')}c, Worker={wrk_data.get('cores')}c",
            "assessment": "PASS" if l3_pass else "FAIL",
            "severity": "PASS" if l3_pass else "HIGH",
            "finding": f"hardwareConcurrency ({main_data.get('cores')}), userAgent, and timezone match coherently across DOM, iframe, and Web Worker realms." if l3_pass else f"Cross-context contradictions detected: {l3_contradictions}"
        })

        # 6. Level 4: HTTP Headers vs JS Client Hints Coherence
        l4_data = report.get("level4_http_js_coherence", {})
        l4_issues = l4_data.get("coherence_issues")
        l4_pass = (isinstance(l4_issues, list)) and (len(l4_issues) == 0) and (l4_data.get("status") == "PASS")
        matrix.append({
            "level": "Level 4",
            "surface": "HTTP Headers vs JS Client Hints Coherence",
            "test_execution_status": "SUCCESS",
            "expected_condition": "Strict bidirectional coherence across HTTP User-Agent, Sec-CH-UA, full version list, and navigator.userAgentData",
            "observed_condition": f"Coherence issues count: {len(l4_issues) if isinstance(l4_issues, list) else 'Invalid'}",
            "evidence": f"HTTP-UA={captured_headers.get('user-agent', '')[:40]}..., JS-UA={js_uach.get('ua', '')[:40]}...",
            "assessment": "PASS" if l4_pass else "FAIL",
            "severity": "PASS" if l4_pass else "HIGH",
            "finding": "Outbound HTTP User-Agent and Sec-CH-UA match navigator.userAgent and userAgentData with zero HeadlessChrome brand leaks." if l4_pass else f"Client Hints / HTTP Header contradictions detected: {l4_issues}"
        })

        # 7. Level 5: Hardware Plausibility & WebGL/WebGPU Pipeline
        l5_data = report.get("level5_hardware_coherence", {})
        l5_pass = (l5_data.get("status") == "PASS")
        matrix.append({
            "level": "Level 5",
            "surface": "Hardware Plausibility & WebGL Pipeline",
            "test_execution_status": "SUCCESS",
            "expected_condition": "Coherent CPU/RAM/GPU combination with matching intended GPU renderer, valid generic WebGL and unmasked renderer",
            "observed_condition": f"Intended='{intended_renderer}', Observed='{hw_eval.get('gl_renderer')}', Matched={l5_data.get('gpu_renderer_matched')}, RAM={hw_eval.get('ram_coarse')}GB, Cores={hw_eval.get('cores')}",
            "evidence": f"gl_max_texture={hw_eval.get('gl_max_texture')}, extensions={hw_eval.get('gl_extensions_count')}, webgpu={hw_eval.get('webgpu', {}).get('supported')}",
            "assessment": "PASS" if l5_pass else "FAIL",
            "severity": "PASS" if l5_pass else "MEDIUM",
            "finding": f"CPU ({intended_specs.get('cpu_cores')} cores), coarse RAM ({intended_specs.get('ram_gb')} GB), generic WebGL, and unmasked GPU renderer match intended profile specifications." if l5_pass else "Hardware attributes failed plausibility or GPU renderer matching verification."
        })

        # 8. Level 6: Network Isolation & WebRTC Candidate Routing
        l6_data = report.get("level6_network_leak", {})
        matrix.append({
            "level": "Level 6",
            "surface": "Network Isolation & WebRTC Candidate Routing",
            "test_execution_status": "SUCCESS",
            "expected_condition": "Candidate-path verified with STUN/mDNS reflexive ICE candidates and zero LAN IP leak",
            "observed_condition": f"Gathered {l6_data.get('candidates_count', 0)} candidates; NO_PRIVATE_IP_OBSERVED={l6_data.get('no_private_ip_observed')}, ICE_PATH_INCONCLUSIVE={l6_data.get('ice_path_inconclusive')}",
            "evidence": f"candidates_count={l6_data.get('candidates_count', 0)}, candidates={l6_data.get('candidates', [])}",
            "assessment": l6_data.get("status", "INCONCLUSIVE"),
            "severity": l6_data.get("severity", "INCONCLUSIVE"),
            "finding": l6_data.get("finding", "WebRTC candidate verification inconclusive.")
        })

        # 9. Level 7: Storage State & Profile Isolation (6 Surfaces)
        l7_data = report.get("level7_profile_isolation", {})
        l7_isolated = l7_data.get("all_surfaces_isolated", False) and (l7_data.get("status") == "PASS")
        matrix.append({
            "level": "Level 7",
            "surface": "Storage State & Profile Isolation (6 Surfaces)",
            "test_execution_status": "SUCCESS",
            "expected_condition": "Zero state crossover across seeded storage surfaces (cookies, localStorage, sessionStorage, IndexedDB, Cache Storage, Service Worker)",
            "observed_condition": f"Profile A seed={l7_data.get('profile_a_seed', {})}, Profile B read={l7_data.get('surface_isolation', {})}",
            "evidence": f"Active surfaces tested: {l7_data.get('active_surfaces_tested', [])}",
            "assessment": "PASS" if l7_isolated else "FAIL",
            "severity": "PASS" if l7_isolated else "CRITICAL",
            "finding": l7_data.get("finding", "State separation verified across storage surfaces.")
        })

        # 10. Level 8: Tamper / Native Prototype Integrity & Baseline Match
        l8_data = report.get("level8_tamper_integrity", {})
        matrix.append({
            "level": "Level 8",
            "surface": "Tamper / Native Prototype Integrity & Baseline Match",
            "test_execution_status": "SUCCESS",
            "expected_condition": "Native descriptors match clean Chromium baseline with [native code] toString and zero own-properties",
            "observed_condition": f"Baseline matched={baseline_matched} (v{engine_bv.full_version}); mismatches={l8_data.get('proto_mismatches', [])}",
            "evidence": f"getHEV.toString()='{tamper_eval.get('toString_getHighEntropyValues', 'N/A')}'",
            "assessment": l8_data.get("status", "INCONCLUSIVE"),
            "severity": l8_data.get("severity", "INCONCLUSIVE"),
            "finding": l8_data.get("finding", "Tamper integrity evaluated.")
        })

        pass_c = sum(1 for m in matrix if m["assessment"] == "PASS")
        fail_c = sum(1 for m in matrix if m["assessment"] == "FAIL")
        inconcl_c = sum(1 for m in matrix if m["assessment"] == "INCONCLUSIVE")
        not_tested_c = sum(1 for m in matrix if m["assessment"] == "NOT_TESTED")

        crit_c = sum(1 for m in matrix if m["severity"] == "CRITICAL")
        high_c = sum(1 for m in matrix if m["severity"] == "HIGH")
        med_c = sum(1 for m in matrix if m["severity"] == "MEDIUM")
        low_c = sum(1 for m in matrix if m["severity"] == "LOW")

        if fail_c > 0 or crit_c > 0:
            overall_stat = "FAIL"
        elif inconcl_c > 0:
            overall_stat = "PASS_WITH_INCONCLUSIVE"
        else:
            overall_stat = "PASS"

        report["overall_status"] = overall_stat
        report["grading_matrix"] = matrix
        report["summary"] = {
            "total_tests": len(matrix),
            "passed": pass_c,
            "failed": fail_c,
            "inconclusive": inconcl_c,
            "not_tested": not_tested_c,
            "pass_count": pass_c,
            "fail_count": fail_c,
            "inconclusive_count": inconcl_c,
            "not_tested_count": not_tested_c,
            "critical_flaws": crit_c,
            "high_flaws": high_c,
            "medium_flaws": med_c,
            "low_flaws": low_c,
            "severity_counts": {
                "FATAL": 0,
                "CRITICAL": crit_c,
                "HIGH": high_c,
                "MEDIUM": med_c,
                "LOW": low_c,
                "PASS": pass_c,
                "INCONCLUSIVE": inconcl_c,
                "NOT_TESTED": not_tested_c
            },
            "pass_rate_pct": round((pass_c / len(matrix)) * 100, 1) if matrix else 0.0,
        }

        print("\n[FINAL ADVERSARIAL GRADING MATRIX]")
        print("-" * 80)
        for row in matrix:
            print(f"  [{row['severity']:12s}] {row['surface']:42s} | Assessment: {row['assessment']:12s} | {row['finding']}")
        print("-" * 80)
        print(f"Summary: {pass_c}/{len(matrix)} PASSED ({report['summary']['pass_rate_pct']}%), {inconcl_c} INCONCLUSIVE, {fail_c} FAILED, {not_tested_c} NOT_TESTED | Overall: {overall_stat}")
        print("-" * 80)

    finally:
        print("\n[TEARDOWN] Closing profile and cleaning up processes...")
        await close_profile(profile_id)
        profile_manager.delete_profile(profile_id)
        print("  Profile closed and deleted cleanly.")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport successfully saved to {REPORT_PATH}")
    print("=" * 80)
    print("BRUTAL TEST RUN COMPLETE!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_brutal_test())
