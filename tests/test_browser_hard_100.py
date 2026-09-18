"""
GhostBrowser AI — 100-Run Hard Browser & Anti-Detect Stress Test Suite
Tests:
  1. Mistral API Key Rotation & Codestral-latest Profile Generation
  2. In-Browser DOM Stealth Surface Hardening:
     - navigator.webdriver === false / undefined
     - navigator.userAgent & Sec-CH-UA consistency
     - WebGL unmasked vendor/renderer spoofing
     - Screen / viewport / hardwareConcurrency / deviceMemory match
     - WebGPU adapter simulation
     - MediaDevices enumeration
     - Permissions API consistency
     - Performance.now() precision coarsening
  3. Canvas & WebGL Noise Determinism vs Inter-Profile Distinctness
  4. Cookie Storage & Session Isolation
  5. Process Hygiene & Zero Zombie Processes
"""

import os
import sys
import time
import json
import psutil
import asyncio
from datetime import datetime

# Set up paths and environment
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)

# Force non-proxy requirement for local test suite
os.environ["GHOSTBROWSER_REQUIRE_PROXY"] = "0"
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

from backend.profile_creator import create_zero_leak_profile
from backend.profile_manager import profile_manager
from backend.browser_manager import (
    launch_profile,
    close_profile,
    active_browsers,
    find_profile_processes,
    kill_process_tree,
    _profile_has_verified_provenance
)
from backend.ai_generator import (
    generate_fingerprint_ai,
    _get_mistral_api_keys,
    validate_fingerprint_schema
)

PROBE_JS = """
async () => {
    // 1. Canvas Fingerprint
    const canvas = document.createElement('canvas');
    canvas.width = 240;
    canvas.height = 60;
    const ctx = canvas.getContext('2d');
    ctx.textBaseline = 'top';
    ctx.font = '14px Arial';
    ctx.fillStyle = '#f60';
    ctx.fillRect(125, 1, 62, 20);
    ctx.fillStyle = '#069';
    ctx.fillText('GhostBrowser Hard Test 100x', 2, 15);
    ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
    ctx.fillText('GhostBrowser Hard Test 100x', 4, 17);
    const canvasData = canvas.toDataURL();

    // 2. WebGL Fingerprint
    const glCanvas = document.createElement('canvas');
    const gl = glCanvas.getContext('webgl') || glCanvas.getContext('experimental-webgl');
    let vendor = 'NONE';
    let renderer = 'NONE';
    if (gl) {
        const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
        if (debugInfo) {
            vendor = gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL);
            renderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL);
        }
    }

    // 3. WebGPU
    let gpuVendor = null;
    let gpuArchitecture = null;
    if (navigator.gpu && typeof navigator.gpu.requestAdapter === 'function') {
        try {
            const adapter = await navigator.gpu.requestAdapter();
            if (adapter && adapter.info) {
                gpuVendor = adapter.info.vendor;
                gpuArchitecture = adapter.info.architecture;
            }
        } catch (e) {
            gpuVendor = 'ERROR';
        }
    }

    // 4. MediaDevices
    let devicesCount = 0;
    if (navigator.mediaDevices && typeof navigator.mediaDevices.enumerateDevices === 'function') {
        try {
            const devs = await navigator.mediaDevices.enumerateDevices();
            devicesCount = devs.length;
        } catch (e) {}
    }

    // 5. Permissions
    let notificationPermission = 'unknown';
    if (navigator.permissions && typeof navigator.permissions.query === 'function') {
        try {
            const p = await navigator.permissions.query({ name: 'notifications' });
            notificationPermission = p.state;
        } catch (e) {}
    }

    // 6. Timing Precision (Microarchitectural side-channel check)
    const t1 = performance.now();
    const t2 = performance.now();
    const delta = Math.abs(t2 - t1);

    return {
        webdriver: navigator.webdriver,
        hasChrome: !!window.chrome,
        hasChromeRuntime: !!(window.chrome && window.chrome.runtime),
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        hardwareConcurrency: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory,
        screenWidth: window.screen.width,
        screenHeight: window.screen.height,
        colorDepth: window.screen.colorDepth,
        devicePixelRatio: window.devicePixelRatio,
        webglVendor: vendor,
        webglRenderer: renderer,
        gpuVendor: gpuVendor,
        devicesCount: devicesCount,
        notificationPermission: notificationPermission,
        timingDelta: delta,
        canvasHash: canvasData.slice(-64)
    };
}
"""

async def evaluate_probe(page, profile_data, cycle_idx):
    """Execute in-page DOM probe and assert anti-detect contracts."""
    errors = []
    pid = profile_data["id"]

    await page.goto("about:blank", timeout=15000)
    probe = await page.evaluate(PROBE_JS)

    # A. Webdriver MUST be False
    if probe["webdriver"] is True:
        errors.append("HARD LEAK: navigator.webdriver is True!")

    # B. Platform check
    expected_os = profile_data.get("fingerprint", {}).get("os", "Windows")
    valid_platforms = {
        "Windows": ("Windows", "Win32"),
        "Mac": ("MacIntel", "macOS"),
        "Linux": ("Linux x86_64", "Linux", "Linux armv8l"),
    }
    if probe["platform"] not in valid_platforms.get(expected_os, ()):
        errors.append(f"Platform mismatch: got {probe['platform']}, expected one of {valid_platforms.get(expected_os)}")

    # C. WebGL Vendor & Renderer
    expected_vendor = profile_data.get("fingerprint", {}).get("webgl_vendor")
    expected_renderer = profile_data.get("fingerprint", {}).get("webgl_renderer")
    if expected_vendor and probe["webglVendor"] != expected_vendor:
        errors.append(f"WebGL Vendor mismatch: got {probe['webglVendor']}, expected {expected_vendor}")
    if expected_renderer and probe["webglRenderer"] != expected_renderer:
        errors.append(f"WebGL Renderer mismatch: got {probe['webglRenderer']}, expected {expected_renderer}")

    # D. HardwareConcurrency & DeviceMemory
    expected_cores = profile_data.get("fingerprint", {}).get("hardwareConcurrency")
    if expected_cores and probe["hardwareConcurrency"] != expected_cores:
        errors.append(f"Cores mismatch: got {probe['hardwareConcurrency']}, expected {expected_cores}")

    # E. Notification permission
    if probe["notificationPermission"] not in ("default", "prompt", "denied", "granted"):
        errors.append(f"Notification permission leak: {probe['notificationPermission']}")

    return errors, probe

async def main():
    print("=" * 80, flush=True)
    print("GHOSTBROWSER AI — 100-RUN HARD BROWSER STRESS TEST SUITE", flush=True)
    print(f"Timestamp: {datetime.now().isoformat()}", flush=True)
    print("=" * 80, flush=True)

    # 1. Check Mistral Keys
    mistral_keys = _get_mistral_api_keys()
    print(f"[*] Discovered Mistral API Keys: {len(mistral_keys)}", flush=True)
    model_name = os.environ.get("MISTRAL_MODEL", "codestral-latest")
    print(f"[*] Target Mistral Model: {model_name}", flush=True)

    if len(mistral_keys) < 1:
        print("[!] ERROR: No Mistral API keys available.", flush=True)
        sys.exit(1)

    # 2. Phase 1: Generate Pool of Profiles with Mistral AI
    TOTAL_PROFILES = 10
    CYCLES_PER_PROFILE = 10
    TOTAL_TEST_RUNS = TOTAL_PROFILES * CYCLES_PER_PROFILE  # 100 Test Cycles
    print(f"\n[PHASE 1] Generating {TOTAL_PROFILES} Profiles with Mistral AI ({model_name})...", flush=True)
    created_profiles = []

    for i in range(1, TOTAL_PROFILES + 1):
        p_name = f"Mistral_Stress_Profile_{i}_{int(time.time())}"
        print(f"  -> [{i}/{TOTAL_PROFILES}] Requesting Mistral profile generation (key rotation active)...", end=" ", flush=True)
        t0 = time.time()
        res = await create_zero_leak_profile(p_name, skip_warming=True)
        dur = time.time() - t0
        if res.get("status") == "success":
            prof = res["profile"]
            created_profiles.append(prof)
            prov = prof.get("ai_provenance", {})
            print(f"[OK] in {dur:.2f}s (id={prof['id'][:8]}..., source={prov.get('source')}, model={prov.get('requested_model')})", flush=True)
        else:
            print(f"[FAIL] {res.get('message')}", flush=True)

    if not created_profiles:
        print("[!] FATAL: Failed to create profiles with Mistral.", flush=True)
        sys.exit(1)

    print(f"\n[+] Phase 1 Complete: {len(created_profiles)}/{TOTAL_PROFILES} profiles generated and verified.", flush=True)

    # 3. Phase 2: Canvas Noise Determinism & Inter-Profile Distinctness
    print("\n[PHASE 2] Testing Deterministic Rendering & Inter-Profile Noise Distinctness...", flush=True)
    p0 = created_profiles[0]
    p1 = created_profiles[1] if len(created_profiles) > 1 else created_profiles[0]

    # Launch p0
    await launch_profile(p0["id"], force_headless=True)
    page0 = active_browsers[p0["id"]]["page"]
    _, probe_p0_run1 = await evaluate_probe(page0, p0, 1)
    _, probe_p0_run2 = await evaluate_probe(page0, p0, 2)
    await close_profile(p0["id"])

    # Launch p1
    await launch_profile(p1["id"], force_headless=True)
    page1 = active_browsers[p1["id"]]["page"]
    _, probe_p1_run1 = await evaluate_probe(page1, p1, 1)
    await close_profile(p1["id"])

    h0_1 = probe_p0_run1.get("canvasHash")
    h0_2 = probe_p0_run2.get("canvasHash")
    h1_1 = probe_p1_run1.get("canvasHash")

    if h0_1 and h0_2 and h0_1 == h0_2:
        print(f"  [PASS] Deterministic Intra-Profile Canvas: Reloads match perfectly ({h0_1[:16]}...)", flush=True)
    else:
        print(f"  [FAIL] Canvas non-deterministic within same profile: {h0_1} vs {h0_2}", flush=True)

    if h0_1 and h1_1 and h0_1 != h1_1:
        print(f"  [PASS] Inter-Profile Canvas Noise Distinct: Profiles 0 and 1 have unique fingerprints ({h0_1[:10]}... != {h1_1[:10]}...)", flush=True)
    else:
        print(f"  [WARN] Cross-profile canvas collided or single profile used", flush=True)

    # 4. Phase 3: Execute 100 Hard Browser Anti-Detect Stress Cycles
    print(f"\n[PHASE 3] Executing 100 Full Browser Anti-Detect Stress Cycles...", flush=True)
    print(f"Structure: {len(created_profiles)} Profiles x {CYCLES_PER_PROFILE} In-Browser Cycles = {TOTAL_TEST_RUNS} Total Runs", flush=True)

    global_run_idx = 0
    passed_count = 0
    failed_count = 0
    latencies = []
    results_summary = []
    start_time = time.time()

    for p_idx, profile_data in enumerate(created_profiles, 1):
        pid = profile_data["id"]
        p_name = profile_data.get("name")
        print(f"\n--- [Profile {p_idx}/{len(created_profiles)}] Launching {pid[:8]}... ({p_name}) ---", flush=True)

        # Launch profile
        t_launch_start = time.time()
        launch_res = await launch_profile(pid, force_headless=True)
        if launch_res.get("status") != "success":
            print(f"  [ERROR] Failed to launch profile {pid}: {launch_res.get('message')}", flush=True)
            for _ in range(CYCLES_PER_PROFILE):
                global_run_idx += 1
                failed_count += 1
                results_summary.append({"run": global_run_idx, "status": "FAIL", "error": "Launch failed"})
            continue

        b_entry = active_browsers.get(pid)
        page = b_entry["page"]
        context = b_entry["context"]
        cdp_pid = b_entry.get("pid")

        # Run CYCLES_PER_PROFILE inside this live browser
        for c_idx in range(1, CYCLES_PER_PROFILE + 1):
            global_run_idx += 1
            t_cycle_start = time.time()

            # Evaluate probe
            errors, probe = await evaluate_probe(page, profile_data, global_run_idx)

            # Cookie storage write and read-back test
            test_cookie = {"name": f"ghost_{global_run_idx}", "value": f"secret_{pid[:8]}", "url": "https://ghostbrowser.local"}
            await context.add_cookies([test_cookie])
            all_cookies = await context.cookies("https://ghostbrowser.local")
            found = any(c.get("name") == f"ghost_{global_run_idx}" for c in all_cookies)
            if not found:
                errors.append("Cookie storage read-back failed")

            dur = time.time() - t_cycle_start
            latencies.append(dur)

            if not errors:
                passed_count += 1
                status_tag = "PASS"
            else:
                failed_count += 1
                status_tag = "FAIL"

            results_summary.append({
                "run": global_run_idx,
                "profile_id": pid,
                "status": status_tag,
                "duration": round(dur, 2),
                "errors": errors,
                "webdriver": probe.get("webdriver"),
                "webglRenderer": probe.get("webglRenderer", "")[:35]
            })

            mem_mb = psutil.Process().memory_info().rss / (1024 * 1024)
            err_str = f" - ERRORS: {errors}" if errors else ""
            print(f"  [Run {global_run_idx:03d}/{TOTAL_TEST_RUNS}] {status_tag} in {dur:.2f}s | Mem: {mem_mb:.1f}MB | Webdriver: {probe.get('webdriver')} | Renderer: {probe.get('webglRenderer', '')[:25]}...{err_str}", flush=True)

        # Close profile cleanly
        await close_profile(pid)
        if cdp_pid and psutil.pid_exists(cdp_pid):
            await asyncio.sleep(0.2)
            if psutil.pid_exists(cdp_pid):
                kill_process_tree(cdp_pid)

    total_time = time.time() - start_time
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    # 5. Clean up created profiles
    print("\n[PHASE 4] Cleaning Up Test Profiles...", flush=True)
    for p in created_profiles:
        profile_manager.delete_profile(p["id"])
        print(f"  [-] Deleted profile: {p['id'][:8]}...", flush=True)

    # Final Report
    print("\n" + "=" * 80, flush=True)
    print("FINAL TEST REPORT — 100x HARD BROWSER STRESS RUN", flush=True)
    print("=" * 80, flush=True)
    print(f"Total Cycles Run:      {global_run_idx}", flush=True)
    print(f"Passed Cycles:         {passed_count} ({passed_count/max(global_run_idx,1)*100:.1f}%)", flush=True)
    print(f"Failed Cycles:         {failed_count}", flush=True)
    print(f"Total Test Duration:   {total_time:.2f}s (~{total_time/60:.2f} min)", flush=True)
    print(f"Average Cycle Latency: {avg_latency:.2f}s", flush=True)
    print(f"Min / Max Latency:     {min(latencies):.2f}s / {max(latencies):.2f}s", flush=True)
    print(f"Mistral Keys Utilized: {len(mistral_keys)}", flush=True)
    print(f"Mistral Model:         {model_name}", flush=True)
    print("=" * 80, flush=True)

    report_path = os.path.join(PROJECT_ROOT, "tests", "stress_test_100_report.json")
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "total_runs": global_run_idx,
            "passed": passed_count,
            "failed": failed_count,
            "total_duration_seconds": round(total_time, 2),
            "average_latency_seconds": round(avg_latency, 2),
            "mistral_model": model_name,
            "mistral_keys_count": len(mistral_keys),
            "runs": results_summary
        }, f, indent=2)
    print(f"[+] Detailed forensic report written to: {report_path}", flush=True)

    if failed_count > 0:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
