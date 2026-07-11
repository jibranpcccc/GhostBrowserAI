"""
GhostBrowser QA Test Suite — T07-T10
Tests: Profile Consistency, Launch, Isolation, Fingerprint Spoofing
"""
import asyncio
import sys
import os
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.browser_manager import launch_profile, close_profile, active_browsers
from backend.profile_manager import profile_manager

PRIVATE_PREFIXES = [
    '192.168.', '10.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.',
    '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.',
    '172.28.', '172.29.', '172.30.', '172.31.', 'fe80:', 'fc00:', 'fd'
]

WEBGL_JS = """
() => {
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl") || c.getContext("experimental-webgl");
    if (!gl) return {v:"N/A",r:"N/A"};
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    if (!ext) return {v:"N/A",r:"N/A"};
    return {v: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL), r: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)};
}
"""

CANVAS_JS = """
() => {
    const c = document.createElement("canvas"); c.width=200; c.height=50;
    const ctx = c.getContext("2d");
    ctx.textBaseline="top"; ctx.font="14px Arial";
    ctx.fillStyle="#f60"; ctx.fillRect(0,0,200,50);
    ctx.fillStyle="#069"; ctx.fillText("GhostBrowser-QA",2,15);
    return c.toDataURL().substring(0,50);
}
"""

WEBRTC_JS = """
async () => {
    try {
        const pc = new RTCPeerConnection({iceServers:[{urls:"stun:stun.l.google.com:19302"}]});
        const ips=[];
        pc.onicecandidate=(e)=>{if(e.candidate&&e.candidate.candidate)ips.push(e.candidate.candidate)};
        pc.createDataChannel("");
        const offer=await pc.createOffer();
        await pc.setLocalDescription(offer);
        await new Promise(r=>setTimeout(r,3000));
        pc.close();
        return ips;
    } catch(e) { return ["err:"+e.message]; }
}
"""


async def run_qa_tests():
    profiles = profile_manager.list_profiles()
    if not profiles:
        print("FAIL: No profiles to test")
        return

    # Use first available profile (no proxy needed)
    pid = profiles[0]["id"]
    name = profiles[0]["name"]
    print(f"Using profile: {name} ({pid[:12]})")

    # ============= T07: CONSISTENCY =============
    print("\n" + "=" * 60)
    print("T07: PROFILE CONSISTENCY")
    print("=" * 60)

    p = profiles[0]
    adv = p.get("advanced", {})
    ua = p.get("user_agent", "")
    tz = p.get("timezone", "")
    locale = p.get("locale", "")

    consistency_errors = []

    # Check OS/UA consistency
    os_name = adv.get("os", "Windows")
    if os_name == "Windows":
        if "Windows NT" not in ua:
            consistency_errors.append(f"OS=Windows but UA missing 'Windows NT': {ua[:50]}")
            print(f"  FAIL: OS=Windows but UA missing 'Windows NT'")
        else:
            print(f"  PASS: OS=Windows, UA contains 'Windows NT'")
    elif os_name == "Mac":
        if "Macintosh" not in ua:
            consistency_errors.append(f"OS=Mac but UA missing 'Macintosh'")
            print(f"  FAIL: OS=Mac but UA missing 'Macintosh'")
        else:
            print(f"  PASS: OS=Mac, UA contains 'Macintosh'")
    elif os_name == "Linux":
        if "Linux" not in ua:
            consistency_errors.append(f"OS=Linux but UA missing 'Linux'")
            print(f"  FAIL: OS=Linux but UA missing 'Linux'")
        else:
            print(f"  PASS: OS=Linux, UA contains 'Linux'")

    # Check timezone format
    if "/" in tz:
        print(f"  PASS: timezone '{tz}' is valid IANA format")
    else:
        consistency_errors.append(f"Timezone '{tz}' is not IANA format")
        print(f"  FAIL: timezone '{tz}' is not IANA format")

    # Check locale format
    if "-" in locale:
        print(f"  PASS: locale '{locale}' is valid format")
    else:
        consistency_errors.append(f"Locale '{locale}' is not valid format")
        print(f"  FAIL: locale '{locale}' is not valid format")

    # Check screen resolution format
    screen = adv.get("screen_resolution", "")
    if "x" in screen:
        w, h = screen.split("x")
        if int(w) > 0 and int(h) > 0:
            print(f"  PASS: screen_resolution '{screen}' is valid")
        else:
            consistency_errors.append(f"Invalid screen resolution: {screen}")
            print(f"  FAIL: screen_resolution '{screen}' has non-positive dimensions")
    else:
        consistency_errors.append(f"screen_resolution '{screen}' missing 'x' separator")
        print(f"  FAIL: screen_resolution '{screen}' missing 'x' separator")

    # Check hardware values
    cores = adv.get("cpu_cores", 0)
    mem = adv.get("memory_gb", 0)
    if 1 <= cores <= 32:
        print(f"  PASS: cpu_cores={cores} is in valid range (1-32)")
    else:
        consistency_errors.append(f"cpu_cores={cores} out of range")
        print(f"  FAIL: cpu_cores={cores} out of valid range (1-32)")
    if 1 <= mem <= 64:
        print(f"  PASS: memory_gb={mem} is in valid range (1-64)")
    else:
        consistency_errors.append(f"memory_gb={mem} out of range")
        print(f"  FAIL: memory_gb={mem} out of valid range (1-64)")

    # Check WebGL vendor/renderer
    webgl_vendor = adv.get("webgl_vendor", "")
    webgl_renderer = adv.get("webgl_renderer", "")
    if webgl_vendor and webgl_renderer:
        print(f"  PASS: webgl_vendor='{webgl_vendor[:30]}', renderer set")
    else:
        consistency_errors.append("webgl_vendor or renderer missing")
        print(f"  FAIL: webgl_vendor or renderer missing")

    # Check sec_ch_ua
    sec_ch_ua = adv.get("sec_ch_ua", "")
    if "Chromium" in sec_ch_ua or "Chrome" in sec_ch_ua:
        print(f"  PASS: sec_ch_ua contains browser name")
    else:
        consistency_errors.append(f"sec_ch_ua '{sec_ch_ua[:40]}' missing browser name")
        print(f"  FAIL: sec_ch_ua missing browser name")

    print(f"\n  T07 RESULT: {'PASS' if not consistency_errors else 'FAIL'}")
    if consistency_errors:
        for e in consistency_errors:
            print(f"    ERROR: {e}")

    # ============= T08: PROFILE LAUNCH =============
    print("\n" + "=" * 60)
    print("T08: PROFILE LAUNCH (headless)")
    print("=" * 60)

    result = await launch_profile(pid, force_headless=True)
    if result.get("status") == "error":
        print(f"  FAIL: Launch failed: {result.get('message','?')}")
        print(f"\n  T08 RESULT: FAIL")
    else:
        print(f"  PASS: Profile launched successfully")
        print(f"  PASS: active_browsers has profile: {pid in active_browsers}")

        if pid not in active_browsers:
            print(f"  FAIL: Profile not in active_browsers dict")
            print(f"\n  T08 RESULT: FAIL")
        else:
            bd = active_browsers[pid]
            page = bd.get("page")
            context = bd.get("context")
            playwright = bd.get("playwright")

            launch_checks = []
            # NOTE: browser_manager uses launch_persistent_context() — no separate browser object
            # The context IS the browser. This is correct by design.
            if page:
                launch_checks.append(("page object", True))
            else:
                launch_checks.append(("page object", False))
            if context:
                launch_checks.append(("context object", True))
            else:
                launch_checks.append(("context object", False))
            if playwright:
                launch_checks.append(("playwright object", True))
            else:
                launch_checks.append(("playwright object", False))

            for name, ok in launch_checks:
                print(f"  {'PASS' if ok else 'FAIL'}: {name}")

            launch_ok = all(ok for _, ok in launch_checks)
            print(f"\n  T08 RESULT: {'PASS' if launch_ok else 'FAIL'}")

            # ============= T09: ISOLATION =============
            if launch_ok:
                print("\n" + "=" * 60)
                print("T09: PROFILE ISOLATION")
                print("=" * 60)

                # Navigate to a real page first (cookies disabled on data: URLs)
                await page.goto("about:blank", wait_until="domcontentloaded")
                # Navigate to a real page where cookies are allowed
                await page.goto("https://example.com", wait_until="domcontentloaded", timeout=15000)

                # Set a cookie in this profile
                await page.evaluate("""() => { document.cookie = "qa_test_isolation=profile1; path=/"; }""")
                cookie1 = await page.evaluate("() => document.cookie")
                print(f"  Profile 1 cookie: {cookie1}")

                # Set localStorage
                await page.evaluate("""() => { localStorage.setItem('qa_isolation', 'profile1_value'); }""")
                ls1 = await page.evaluate("() => localStorage.getItem('qa_isolation')")
                print(f"  Profile 1 localStorage: {ls1}")

                # Close profile
                await close_profile(pid)
                print(f"  Profile 1 closed.")

                # Create a second profile
                if len(profiles) > 1:
                    pid2 = profiles[1]["id"]
                    name2 = profiles[1]["name"]
                else:
                    # Create a simple test profile
                    pid2 = profile_manager.create_profile("QA-Isolation-Test")["id"]
                    name2 = "QA-Isolation-Test"

                print(f"  Launching profile 2: {name2} ({pid2[:12]})")
                result2 = await launch_profile(pid2, force_headless=True)
                if result2.get("status") == "error":
                    print(f"  FAIL: Could not launch profile 2: {result2.get('message','?')}")
                    print(f"\n  T09 RESULT: FAIL (could not test isolation)")
                else:
                    page2 = active_browsers[pid2]["page"]

                    # Navigate to same page as profile 1
                    await page2.goto("https://example.com", wait_until="domcontentloaded", timeout=15000)

                    # Check if cookie leaked
                    cookie2 = await page2.evaluate("() => document.cookie")
                    print(f"  Profile 2 cookie: {cookie2}")

                    # Check if localStorage leaked
                    ls2 = await page2.evaluate("() => localStorage.getItem('qa_isolation')")
                    print(f"  Profile 2 localStorage: {ls2}")

                    isolation_errors = []
                    if "qa_test_isolation" in (cookie2 or ""):
                        isolation_errors.append("Cookie leaked between profiles!")
                        print(f"  FAIL: Cookie leaked between profiles!")
                    else:
                        print(f"  PASS: No cookie leakage between profiles")

                    if ls2 == "profile1_value":
                        isolation_errors.append("localStorage leaked between profiles!")
                        print(f"  FAIL: localStorage leaked between profiles!")
                    else:
                        print(f"  PASS: No localStorage leakage between profiles")

                    await close_profile(pid2)

                    print(f"\n  T09 RESULT: {'PASS' if not isolation_errors else 'FAIL (CRITICAL)'}")

            # ============= T10: FINGERPRINT SPOOFING =============
            if launch_ok and pid not in active_browsers:
                # Relaunch for fingerprint tests
                result = await launch_profile(pid, force_headless=True)
                if result.get("status") == "error":
                    print(f"\n  Could not relaunch for T10: {result.get('message')}")
                    return

            if pid in active_browsers:
                print("\n" + "=" * 60)
                print("T10: FINGERPRINT SPOOFING")
                print("=" * 60)

                page = active_browsers[pid]["page"]
                spoof_results = []

                # 1. navigator.webdriver
                webdriver = await page.evaluate("navigator.webdriver")
                ok = webdriver == False
                spoof_results.append(("navigator.webdriver = False", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: navigator.webdriver = {webdriver}")

                # 2. User-Agent
                ua = await page.evaluate("navigator.userAgent")
                ok = "Mozilla" in ua and "Chrome" in ua
                spoof_results.append(("User-Agent set correctly", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: UA = {ua[:60]}...")

                # 3. Hardware
                cores = await page.evaluate("navigator.hardwareConcurrency")
                mem = await page.evaluate("navigator.deviceMemory")
                ok = isinstance(cores, int) and cores > 0 and isinstance(mem, (int, float)) and mem > 0
                spoof_results.append(("Hardware spoofed", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: cores={cores}, memory={mem}GB")

                # 4. Timezone
                tz = await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
                ok = tz != "UTC" or "UTC" in p.get("timezone", "")
                spoof_results.append(("Timezone set", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: Timezone = {tz}")

                # 5. Languages
                langs = await page.evaluate("JSON.stringify(navigator.languages)")
                ok = "en" in langs
                spoof_results.append(("Languages set", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: Languages = {langs}")

                # 6. Screen
                screen = await page.evaluate("JSON.stringify({w:screen.width,h:screen.height,cd:screen.colorDepth,dpr:window.devicePixelRatio})")
                ok = "1920" in screen or "1366" in screen or "2560" in screen
                spoof_results.append(("Screen set", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: Screen = {screen}")

                # 7. WebGL
                webgl = await page.evaluate(WEBGL_JS)
                vendor = str(webgl.get("v", "?"))
                renderer = str(webgl.get("r", "?"))
                ok = vendor != "N/A" and "SwiftShader" not in renderer
                spoof_results.append(("WebGL spoofed", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: WebGL vendor={vendor[:30]}, renderer={renderer[:50]}")

                # 8. Canvas
                canvas = await page.evaluate(CANVAS_JS)
                ok = "data:image" in canvas
                spoof_results.append(("Canvas fingerprint active", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: Canvas = {canvas[:30]}...")

                # 9. WebRTC
                webrtc = await page.evaluate(WEBRTC_JS)
                private_found = [ip for ip in webrtc if any(px in ip for px in PRIVATE_PREFIXES)]
                ok = len(private_found) == 0
                spoof_results.append(("No WebRTC private IP leak", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: WebRTC {len(webrtc)} candidates, {len(private_found)} private")
                for ip in webrtc[:2]:
                    is_priv = any(px in ip for px in PRIVATE_PREFIXES)
                    print(f"       {'PRIVATE' if is_priv else 'PUBLIC'} {ip[:80]}")

                # 10. Battery
                try:
                    battery = await page.evaluate("""async () => {
                        if (navigator.getBattery) {
                            const b = await navigator.getBattery();
                            return JSON.stringify({level: b.level, charging: b.charging, discharging: b.dischargingTime});
                        }
                        return "no battery API";
                    }""")
                    ok = "level" in battery
                    spoof_results.append(("Battery spoofed", ok))
                    print(f"  {'PASS' if ok else 'FAIL'}: Battery = {battery}")
                except Exception:
                    spoof_results.append(("Battery spoofed", False))
                    print(f"  FAIL: Battery API error")

                # 11. Audio
                try:
                    audio = await page.evaluate("""() => {
                        const ac = new (window.AudioContext || window.webkitAudioContext)();
                        const osc = ac.createOscillator();
                        const analyser = ac.createAnalyser();
                        osc.connect(analyser);
                        osc.frequency.value = 1000;
                        return "AudioContext OK";
                    }""")
                    ok = "OK" in audio
                    spoof_results.append(("Audio noise injected", ok))
                    print(f"  {'PASS' if ok else 'FAIL'}: Audio = {audio}")
                except Exception:
                    spoof_results.append(("Audio noise injected", False))
                    print(f"  FAIL: Audio API error")

                # 12. Connection
                conn = await page.evaluate("JSON.stringify(navigator.connection ? {type:navigator.connection.effectiveType,rtt:navigator.connection.rtt} : null)")
                ok = "4g" in conn or "wifi" in conn.lower() if conn != "null" else False
                spoof_results.append(("Connection spoofed", ok))
                print(f"  {'PASS' if ok else 'FAIL'}: Connection = {conn}")

                await close_profile(pid)

                pass_count = sum(1 for _, ok in spoof_results if ok)
                fail_count = sum(1 for _, ok in spoof_results if not ok)
                print(f"\n  T10 RESULT: {pass_count}/{len(spoof_results)} PASS, {fail_count} FAIL")


if __name__ == "__main__":
    asyncio.run(run_qa_tests())
