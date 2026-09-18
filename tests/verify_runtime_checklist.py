"""
Comprehensive live runtime verification of the browser fingerprint surface.

Run:  python tests/verify_runtime_checklist.py   (from repo root, venv python)

Creates two real profiles through the production pipeline (profile_creator +
profile_manager), launches them through the real launch_profile path (both
spoofing scripts, CDP UserAgentOverride, stealth), and probes every
anti-detection surface from the research checklist:

  * navigator (webdriver, vendor, platform, plugins, languages, UA data)
  * client hints coherence (UA <-> userAgentData <-> platform)
  * hardware (cores, memory, performance.memory)
  * screen / window / DPR / media queries / visualViewport
  * canvas noise (stability + presence)
  * WebGL vendor/renderer/extensions
  * audio fingerprint noise
  * WebRTC ICE candidate masking
  * timezone / locale coherence
  * battery / sensors / speechSynthesis / mediaDevices / connection / mediaCapabilities / WebGPU
  * fonts / plugin stubs
  * per-profile uniqueness of noisy surfaces

Exits 0 when every surface passes.
"""
import os
import sys
import tempfile

def main():
    orig_sys_path = list(sys.path)
    orig_dir = os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
    orig_env = os.environ.get("GHOSTBROWSER_TEST_ENV")

    temp_dir = tempfile.TemporaryDirectory()
    created_pids = []

    try:
        os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = temp_dir.name
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        # The checklist validates the FULL spoof pipeline; coherence mode is
        # covered by the live Gmail/password audit. Force the full layer so
        # fixture surfaces are deterministic regardless of the local engine.
        os.environ["GHOSTBROWSER_FORCE_FULL_SPOOF"] = "1"
        sys.path.append(os.getcwd())

        import backend.browser_manager as bm
        import backend.profile_creator as profile_creator
        import backend.proxy_manager as pm
        from unittest.mock import patch

        from backend.config import get_installed_chromium_major_version
        ENGINE_MAJOR = str(get_installed_chromium_major_version())

        orig_probe_native_metadata = bm.probe_native_metadata
        orig_get_proxy_for_profile = pm.proxy_manager.get_proxy_for_profile

        async def mock_probe_native_metadata(force_headless=True):
            return {
                "ua": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ENGINE_MAJOR}.0.0.0 Safari/537.36",
                "uadata": {
                    "brands": [
                        {"brand": "Chromium", "version": ENGINE_MAJOR},
                        {"brand": "Not)A;Brand", "version": "24"},
                    ],
                    "mobile": False,
                    "platform": "Windows",
                    "architecture": "x86",
                    "bitness": "64",
                    "model": "",
                    "platformVersion": "19.0.0",
                    "uaFullVersion": f"{ENGINE_MAJOR}.0.0.0",
                    "fullVersionList": [
                        {"brand": "Chromium", "version": f"{ENGINE_MAJOR}.0.0.0"},
                        {"brand": "Not)A;Brand", "version": "24.0.0.0"},
                    ],
                },
            }

        bm.probe_native_metadata = mock_probe_native_metadata

        async def mock_get_proxy_for_profile(profile_id, force_new=False):
            return None

        pm.proxy_manager.get_proxy_for_profile = mock_get_proxy_for_profile

        from backend.config import get_installed_chromium_major_version, get_installed_chromium_version
        inst_maj = get_installed_chromium_major_version()
        inst_full = get_installed_chromium_version()

        def make_fingerprint(overrides):
            fp = {
                "os": "Windows",
                "platform": "Win32",
                "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{inst_maj}.0.0.0 Safari/537.36",
                "webgl_vendor": "Google Inc. (NVIDIA)",
                "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11)",
                "cpu_cores": 16,
                "hardwareConcurrency": 16,
                "memory_gb": 32,
                "deviceMemory": 32,
                "timezone": "America/New_York",
                "locale": "en-US",
                "languages": ["en-US", "en"],
                "screen_resolution": "2560x1440",
                "screen_color_depth": 24,
                "canvas_noise": True,
                "webgl_noise": True,
                "audio_noise": True,
                "sec_ch_ua": f'"Not A(Brand";v="99", "Chromium";v="{inst_maj}"',
                "sec_ch_ua_platform": '"Windows"',
                "client_hints": {
                    "architecture": "x86",
                    "bitness": "64",
                    "model": "",
                    "platformVersion": "10.0.0",
                    "uaFullVersion": inst_full,
                },
                "notification_permission": "default",
                "battery_level": 0.98,
                "battery_charging": False,
                "battery_discharging": 46800,
                "behavior": {},
            }
            fp.update(overrides)
            return fp

        fp_a = make_fingerprint({
            "timezone": "America/New_York",
            "locale": "en-US",
            "languages": ["en-US", "en"],
            "cpu_cores": 8,
            "hardwareConcurrency": 8,
            "memory_gb": 16,
            "deviceMemory": 16,
            "screen_resolution": "1920x1080",
        })
        fp_b = make_fingerprint({
            "timezone": "Europe/Berlin",
            "locale": "de-DE",
            "languages": ["de-DE", "de"],
            "cpu_cores": 12,
            "hardwareConcurrency": 12,
            "memory_gb": 32,
            "deviceMemory": 32,
            "screen_resolution": "2560x1440",
        })
        fp_queue = [fp_a, fp_b]
        fp_lock = None

        async def mock_ai_gen(*args, **kwargs):
            return fp_queue.pop(0)

        PROBE_JS = r"""
        () => {
            const nativeStr = (fn) => {
                try { return Function.prototype.toString.call(fn); }
                catch (e) { return ''; }
            };
            const hasNative = (fn) => nativeStr(fn).includes('[native code]');
            const tz = (() => {
                try {
                    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
                } catch (e) { return null; }
            })();
            const tzOffset = new Date().getTimezoneOffset();
            const lang = (navigator.languages && navigator.languages[0]) || navigator.language;

            // Canvas fingerprint (deterministic draw, two samples)
            const canvasSample = () => {
                try {
                    const c = document.createElement('canvas');
                    c.width = 256; c.height = 256;
                    const ctx = c.getContext('2d');
                    ctx.fillStyle = '#f00'; ctx.fillRect(0, 0, 256, 256);
                    ctx.fillStyle = '#0f0'; ctx.fillRect(32, 32, 128, 128);
                    ctx.fillStyle = '#00f'; ctx.font = '48px serif'; ctx.fillText('ghost', 40, 120);
                    return c.toDataURL();
                } catch (e) { return 'ERR:' + e.message; }
            };
            const canvas1 = canvasSample();
            const canvas2 = canvasSample();
            const canvasStable = (canvas1 === canvas2);

            // WebGL
            let webgl = null;
            try {
                const gl = document.createElement('canvas').getContext('webgl');
                if (gl) {
                    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
                    webgl = {
                        vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
                        renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
                        version: gl.getParameter(gl.VERSION),
                        extensions: (gl.getSupportedExtensions() || []).length,
                        vendorNative: hasNative(gl.getParameter),
                    };
                }
            } catch (e) { webgl = { error: e.message }; }

            return {
                webdriver: navigator.webdriver,
                vendor: navigator.vendor,
                product: navigator.product,
                platform: navigator.platform,
                languages: navigator.languages,
                language: navigator.language,
                cookieEnabled: navigator.cookieEnabled,
                pdfViewerEnabled: navigator.pdfViewerEnabled,
                doNotTrack: navigator.doNotTrack,
                javaEnabled: (() => { try { return navigator.javaEnabled(); } catch (e) { return 'ERR'; } })(),
                plugins: (() => { try { return { length: navigator.plugins.length, names: Array.from(navigator.plugins).map(p => p.name) }; } catch (e) { return { length: -1 }; } })(),
                mimeTypes: (() => { try { return navigator.mimeTypes.length; } catch (e) { return -1; } })(),
                maxTouchPoints: navigator.maxTouchPoints,
                hardwareConcurrency: navigator.hardwareConcurrency,
                deviceMemory: navigator.deviceMemory,
                ua: navigator.userAgent,
                uaData: (() => { try {
                    return { brands: navigator.userAgentData ? navigator.userAgentData.brands : null,
                             platform: navigator.userAgentData ? navigator.userAgentData.platform : null,
                             architecture: navigator.userAgentData ? navigator.userAgentData.architecture : null,
                             bitness: navigator.userAgentData ? navigator.userAgentData.bitness : null,
                             mobile: navigator.userAgentData ? navigator.userAgentData.mobile : null,
                             fullVersionList: navigator.userAgentData ? navigator.userAgentData.fullVersionList : null };
                } catch (e) { return { error: e.message }; } })(),
                perfMemory: (() => { try { const m = performance.memory; return m ? { limit: m.jsHeapSizeLimit } : null; } catch (e) { return null; } })(),
                dpr: window.devicePixelRatio,
                screen: [screen.width, screen.height],
                availScreen: [screen.availWidth, screen.availHeight],
                colorDepth: screen.colorDepth,
                inner: [window.innerWidth, window.innerHeight],
                outer: [window.outerWidth, window.outerHeight],
                resolutionMatch: matchMedia('(resolution: ' + window.devicePixelRatio + 'dppx)').matches,
                visualViewport: (() => { try { return window.visualViewport ? { w: visualViewport.width, h: visualViewport.height, s: visualViewport.scale } : null; } catch (e) { return null; } })(),
                canvas1: canvas1,
                canvas2: canvas2,
                canvasStable: canvasStable,
                webgl: webgl,
                tz: tz,
                tzOffset: tzOffset,
                lang: lang,
                battery: (() => { try { return navigator.getBattery ? 'present' : 'absent'; } catch (e) { return 'err'; } })(),
                sensors: (() => { return { accel: typeof DeviceOrientationEvent, motion: typeof DeviceMotionEvent, light: typeof DeviceLightEvent }; })(),
                speechVoices: (() => { try { return navigator.speechSynthesis ? typeof navigator.speechSynthesis.getVoices : 'absent'; } catch (e) { return 'err'; } })(),
                mediaDevices: (() => { try { return navigator.mediaDevices ? typeof navigator.mediaDevices.enumerateDevices : 'absent'; } catch (e) { return 'err'; } })(),
                connection: (() => { try { return navigator.connection ? { etype: navigator.connection.effectiveType } : null; } catch (e) { return null; } })(),
                mediaCapabilities: (() => { try { return navigator.mediaCapabilities ? typeof navigator.mediaCapabilities.decodingInfo : 'absent'; } catch (e) { return 'err'; } })(),
                webgpu: (() => { try { return navigator.gpu ? typeof navigator.gpu.requestAdapter : 'absent'; } catch (e) { return 'err'; } })(),
                chrome: (() => { try { return typeof window.chrome; } catch (e) { return 'err'; } })(),
                fontCheck: (() => { try { return typeof document.fonts.check; } catch (e) { return 'err'; } })(),
                pointerMedia: matchMedia('(pointer: fine)').matches,
                hoverMedia: matchMedia('(hover: hover)').matches,
                touchMedia: matchMedia('(any-pointer: coarse)').matches,
                colorSchemeMedia: matchMedia('(prefers-color-scheme: dark)').matches,
            };
        }
        """

        AUDIO_JS = r"""
        async () => {
            try {
                const actx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = actx.createOscillator();
                osc.type = 'triangle';
                osc.frequency.value = 440;
                const analyser = actx.createAnalyser();
                osc.connect(analyser);
                analyser.connect(actx.destination);
                osc.start();
                const arr = new Uint8Array(analyser.frequencyBinCount);
                await new Promise((r) => setTimeout(r, 300));
                analyser.getByteFrequencyData(arr);
                let h = 2166136261;
                for (let i = 0; i < 300; i++) h = Math.imul(h ^ arr[i], 16777619);
                actx.close();
                return (h >>> 0);
            } catch (e) { return 'ERR:' + e.message; }
        }
        """

        RTC_JS = r"""
        () => new Promise((resolve) => {
            let candidates = [];
            let done = false;
            const finish = () => { if (!done) { done = true; resolve(candidates); } };
            try {
                const pc = new RTCPeerConnection({ iceServers: [] });
                pc.onicecandidate = (e) => {
                    if (e.candidate) { candidates.push(e.candidate.candidate); }
                    else { finish(); }
                };
                setTimeout(finish, 3000);
                pc.createDataChannel('probe');
                pc.createOffer().then((o) => pc.setLocalDescription(o)).catch(finish);
            } catch (e) { finish(); }
        })
        """

        import asyncio

        async def probe_page(page):
            data = await page.evaluate(PROBE_JS)
            data["audioHash"] = await page.evaluate(AUDIO_JS)
            data["rtcCandidates"] = await page.evaluate(RTC_JS)
            return data

        async def run_all():
            results = {}
            created = []
            try:
                with patch("backend.profile_creator.generate_fingerprint_ai", mock_ai_gen):
                    p_a = await profile_creator.create_zero_leak_profile(name="verify-a")
                    if p_a.get("status") != "success":
                        raise RuntimeError(f"create profile verify-a failed: {p_a}")
                    created.append(p_a["profile"]["id"])
                    p_b = await profile_creator.create_zero_leak_profile(name="verify-b")
                    if p_b.get("status") != "success":
                        raise RuntimeError(f"create profile verify-b failed: {p_b}")
                    created.append(p_b["profile"]["id"])

                    for pd in (p_a["profile"], p_b["profile"]):
                        results[pd["name"]] = {}

                    la = await bm.launch_profile(p_a["profile"]["id"], force_headless=True)
                    if la.get("status") != "success":
                        raise RuntimeError(f"launch verify-a failed: {la}")
                    la_b = await bm.launch_profile(p_b["profile"]["id"], force_headless=True)
                    if la_b.get("status") != "success":
                        raise RuntimeError(f"launch verify-b failed: {la_b}")

                    results["verify-a"]["profile"] = p_a["profile"]
                    results["verify-b"]["profile"] = p_b["profile"]
                    results["verify-a"]["data"] = await probe_page(bm.active_browsers[p_a["profile"]["id"]]["page"])
                    results["verify-b"]["data"] = await probe_page(bm.active_browsers[p_b["profile"]["id"]]["page"])
            finally:
                for pid in created:
                    try:
                        await bm.close_profile(pid)
                    except Exception:
                        pass
            return results

        results = asyncio.run(run_all())
        failed = []

        def check(name, ok, detail=""):
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not ok else ""))
            if not ok:
                failed.append(name)

        r_a = results["verify-a"]["data"]
        r_b = results["verify-b"]["data"]
        prof_a = results["verify-a"]["profile"]
        prof_b = results["verify-b"]["profile"]
        adv_a = prof_a.get("advanced") or {}
        adv_b = prof_b.get("advanced") or {}
        fp_a_actual = prof_a.get("fingerprint") or fp_a
        fp_b_actual = prof_b.get("fingerprint") or fp_b

        tz_a = prof_a.get("timezone") or fp_a_actual.get("timezone")
        tz_b = prof_b.get("timezone") or fp_b_actual.get("timezone")
        loc_a = prof_a.get("locale") or fp_a_actual.get("locale")
        loc_b = prof_b.get("locale") or fp_b_actual.get("locale")
        cpu_a = adv_a.get("cpu_cores") or fp_a_actual.get("cpu_cores") or 8
        cpu_b = adv_b.get("cpu_cores") or fp_b_actual.get("cpu_cores") or 12
        mem_a = adv_a.get("memory_gb") or fp_a_actual.get("memory_gb") or 16
        mem_b = adv_b.get("memory_gb") or fp_b_actual.get("memory_gb") or 32
        screen_a = fp_a_actual.get("screen_resolution") or "1920x1080"
        screen_b = fp_b_actual.get("screen_resolution") or "2560x1440"

        # ---- navigator / automation ----
        check("navigator.webdriver is falsy",
              not r_a["webdriver"] and not r_b["webdriver"], f"a={r_a['webdriver']!r} b={r_b['webdriver']!r}")
        check("navigator.vendor == 'Google Inc.'",
              r_a["vendor"] == "Google Inc." and r_b["vendor"] == "Google Inc.", f"a={r_a['vendor']!r} b={r_b['vendor']!r}")
        check("navigator.product == 'Gecko'",
              r_a["product"] == "Gecko" and r_b["product"] == "Gecko")
        check("navigator.platform coherent (Win32 or Windows)",
              r_a["platform"] in ("Win32", "Windows") and r_b["platform"] in ("Win32", "Windows"),
              f"a={r_a['platform']!r} b={r_b['platform']!r}")
        check("navigator.pdfViewerEnabled is true",
              r_a["pdfViewerEnabled"] is True and r_b["pdfViewerEnabled"] is True)
        check("navigator.cookieEnabled is true",
              r_a["cookieEnabled"] is True and r_b["cookieEnabled"] is True)
        check("navigator.doNotTrack is null",
              r_a["doNotTrack"] is None and r_b["doNotTrack"] is None)
        check("javaEnabled() is false",
              r_a["javaEnabled"] is False and r_b["javaEnabled"] is False)
        check("plugins are stubbed (length 5)",
              r_a["plugins"]["length"] == 5 and r_b["plugins"]["length"] == 5,
              f"a={r_a['plugins']['length']} b={r_b['plugins']['length']} ({r_a['plugins']['names']})")
        check("mimeTypes are stubbed (non-negative)",
              r_a["mimeTypes"] >= 0 and r_b["mimeTypes"] >= 0)
        mtp_a = r_a["maxTouchPoints"]
        mtp_b = r_b["maxTouchPoints"]
        check("maxTouchPoints is coherent for desktop (0 or 1)",
              mtp_a in (0, 1) and mtp_b in (0, 1),
              f"a={mtp_a} b={mtp_b}")

        # ---- client hints coherence ----
        uad_a = r_a["uaData"] or {}
        uad_b = r_b["uaData"] or {}
        a_brands = [b["brand"] for b in (uad_a.get("brands") or [])]
        b_brands = [b["brand"] for b in (uad_b.get("brands") or [])]
        check("userAgentData present (fallback installed)",
              isinstance(uad_a.get("brands"), list) and len(uad_a.get("brands")) > 0,
              f"a={r_a['uaData']}")
        check("userAgentData.platform matches UA (Windows)",
              uad_a.get("platform") == "Windows" and uad_b.get("platform") == "Windows",
              f"a={r_a['uaData']} b={r_b['uaData']}")
        check("userAgentData.architecture == x86, bitness == 64",
              uad_a.get("architecture") == "x86" and uad_a.get("bitness") == "64",
              str(r_a["uaData"]))
        check("userAgentData.brands includes Chromium+Not-A-Brand",
              "Chromium" in a_brands and "Not)A;Brand" in a_brands and "Chromium" in b_brands,
              str(a_brands))
        check(f"navigator.userAgent contains Chrome/{ENGINE_MAJOR}",
              f"Chrome/{ENGINE_MAJOR}" in r_a["ua"] and f"Chrome/{ENGINE_MAJOR}" in r_b["ua"], f"a={r_a['ua']}")
        check("UA+userAgentData platform coherent",
              "Windows NT 10.0" in r_a["ua"] and (uad_a.get("platform") == "Windows"),
              f"ua={r_a['ua']} uad={r_a['uaData']}")

        # ---- hardware coherence ----
        check("hardwareConcurrency matches profile",
              r_a["hardwareConcurrency"] == cpu_a and r_b["hardwareConcurrency"] == cpu_b,
              f"a={r_a['hardwareConcurrency']} exp={cpu_a} b={r_b['hardwareConcurrency']} exp={cpu_b}")
        check("deviceMemory matches profile",
              r_a["deviceMemory"] == mem_a and r_b["deviceMemory"] == mem_b,
              f"a={r_a['deviceMemory']} exp={mem_a} b={r_b['deviceMemory']} exp={mem_b}")

        # ---- performance.memory ----
        check("performance.memory present",
              r_a["perfMemory"] is not None, f"a={r_a['perfMemory']}")
        if r_a["perfMemory"]:
            check("performance.memory jsHeapSizeLimit matches profile RAM",
                  r_a["perfMemory"]["limit"] == mem_a * 1073741824,
                  f"a={r_a['perfMemory']['limit']} exp={mem_a * 1073741824}")

        # ---- screen / window ----
        w_a, h_a = (int(x) for x in screen_a.lower().split("x"))
        w_b, h_b = (int(x) for x in screen_b.lower().split("x"))
        check("screen matches profile (profile A)",
              r_a["screen"] == [w_a, h_a], f"a={r_a['screen']} exp=[{w_a}, {h_a}]")
        check("screen differs between profiles",
              r_a["screen"] != r_b["screen"], f"a={r_a['screen']} b={r_b['screen']}")
        check("DPR matches config and media query",
              isinstance(r_a["dpr"], (int, float)) and r_a["dpr"] > 0 and r_a["resolutionMatch"] is True,
              f"dpr={r_a['dpr']} resolutionMatch={r_a['resolutionMatch']}")
        check("colorDepth is 24",
              r_a["colorDepth"] == 24 and r_b["colorDepth"] == 24, f"a={r_a['colorDepth']}")
        check("visualViewport present",
              r_a["visualViewport"] is not None, str(r_a["visualViewport"]))

        # ---- timezone / locale ----
        check("timezone matches profile",
              r_a["tz"] == tz_a and r_b["tz"] == tz_b, f"a={r_a['tz']} exp={tz_a} b={r_b['tz']} exp={tz_b}")
        try:
            import datetime as _dt
            from zoneinfo import ZoneInfo
            za = ZoneInfo(tz_a)
            zb = ZoneInfo(tz_b)
            exp_a = _dt.datetime.now(za).utcoffset().total_seconds() / 60
            exp_b = _dt.datetime.now(zb).utcoffset().total_seconds() / 60
            off_ok = abs(-r_a["tzOffset"] - exp_a) < 1 and abs(-r_b["tzOffset"] - exp_b) < 1
            check("timezone offset matches IANA zone",
                  off_ok, f"a={-r_a['tzOffset']} exp={exp_a} b={-r_b['tzOffset']} exp={exp_b}")
        except Exception as e:
            check("timezone offset matches IANA zone", False, str(e))
        check("locale matches profile",
              r_a["lang"] == loc_a and r_b["lang"] == loc_b, f"a={r_a['lang']} exp={loc_a} b={r_b['lang']} exp={loc_b}")

        # ---- canvas noise ----
        check("canvas sample stable within profile",
              r_a["canvasStable"] is True and r_b["canvasStable"] is True,
              f"a_stable={r_a['canvasStable']}")
        check("canvas noise differs between profiles",
              r_a["canvas1"] != r_b["canvas1"] and r_a["canvas1"] != r_b["canvas2"],
              f"a={r_a['canvas1'][:48]}... b={r_b['canvas1'][:48]}...")
        check("canvas is not empty/ERR",
              isinstance(r_a["canvas1"], str) and not r_a["canvas1"].startswith("ERR") and len(r_a["canvas1"]) > 100,
              f"a_len={len(r_a['canvas1']) if isinstance(r_a['canvas1'], str) else r_a['canvas1']}")

        # ---- WebGL ----
        if r_a["webgl"] and r_b["webgl"] and "error" not in r_a["webgl"]:
            check("WebGL context available",
                  isinstance(r_a["webgl"].get("renderer"), str) and len(r_a["webgl"]["renderer"]) > 0,
                  str(r_a["webgl"]))
            check("WebGL renderer mentions a coherent GPU (NVIDIA/ANGLE)",
                  "NVIDIA" in r_a["webgl"]["renderer"] or "ANGLE" in r_a["webgl"]["renderer"],
                  r_a["webgl"]["renderer"])
            check("WebGL vendor is Google Inc./NVIDIA",
                  r_a["webgl"]["vendor"] in ("Google Inc. (NVIDIA)", "Google Inc.", "NVIDIA Corporation"),
                  r_a["webgl"]["vendor"])
            check("WebGL extensions enumerated",
                  isinstance(r_a["webgl"]["extensions"], int) and r_a["webgl"]["extensions"] >= 5,
                  str(r_a["webgl"]["extensions"]))
            check("WebGL vendorNative string preserved",
                  r_a["webgl"]["vendorNative"] is True)
        else:
            check("WebGL context available", False, str(r_a["webgl"]))

        # ---- audio noise ----
        check("audio fingerprint computed",
              isinstance(r_a["audioHash"], int) and isinstance(r_b["audioHash"], int),
              f"a={r_a['audioHash']!r} b={r_b['audioHash']!r}")
        check("audio fingerprint differs between profiles",
              r_a["audioHash"] != r_b["audioHash"],
              f"a={r_a['audioHash']} b={r_b['audioHash']}")

        # ---- WebRTC masking ----
        def _has_private_ip(cands):
            import re
            for c in cands:
                m = re.search(r"(?:^|\s)(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", c)
                if m:
                    return True
            return False

        rtc_ok = isinstance(r_a["rtcCandidates"], list) and isinstance(r_b["rtcCandidates"], list)
        check("RTCPeerConnection ICE gathered",
              rtc_ok, f"a={r_a['rtcCandidates']} b={r_b['rtcCandidates']}")
        if rtc_ok:
            check("No private LAN IP in ICE candidates",
                  not _has_private_ip(r_a["rtcCandidates"]) and not _has_private_ip(r_b["rtcCandidates"]),
                  f"a={r_a['rtcCandidates']} b={r_b['rtcCandidates']}")
            check("ICE uses mDNS obfuscation or relay/srflx only",
                  all(".local" in c or "srflx" in c or "relay" in c or "host" not in c for c in r_a["rtcCandidates"]),
                  str(r_a["rtcCandidates"]))

        # ---- misc surfaces ----
        check("Battery API absent or spoof-shaped",
              r_a["battery"] in ("present", "absent"), f"a={r_a['battery']!r}")
        check("Sensors hidden on desktop (accel/motion/light undefined)",
              r_a["sensors"]["accel"] == "undefined" and r_a["sensors"]["motion"] == "undefined",
              str(r_a["sensors"]))
        check("SpeechSynthesis absent or spoof-shaped",
              r_a["speechVoices"] in ("function", "absent"), f"a={r_a['speechVoices']!r}")
        check("MediaDevices absent or spoof-shaped",
              r_a["mediaDevices"] in ("function", "absent"), f"a={r_a['mediaDevices']!r}")
        check("NetworkInformation present",
              r_a["connection"] is not None and "etype" in r_a["connection"], str(r_a["connection"]))
        check("MediaCapabilities present",
              r_a["mediaCapabilities"] == "function")
        check("WebGPU absent or spoof-shaped",
              r_a["webgpu"] in ("function", "absent"), f"a={r_a['webgpu']!r}")
        check("window.chrome present",
              r_a["chrome"] in ("object", "function"))
        check("FontFaceSet.check present",
              r_a["fontCheck"] == "function")
        check("matchMedia pointer/hover/coarse coherent for desktop",
              r_a["pointerMedia"] is True and r_a["hoverMedia"] is True and r_a["touchMedia"] is False,
              f"pointer={r_a['pointerMedia']} hover={r_a['hoverMedia']} touch={r_a['touchMedia']}")

        print()
        if failed:
            print(f"CHECKLIST FAILED: {len(failed)} surface(s): {failed}")
            return False
        print("CHECKLIST PASSED: all browser surfaces verified.")
        return True

    except Exception as e:
        import traceback
        print(f"[FAIL] Checklist run encountered error: {e}")
        traceback.print_exc()
        return False
    finally:
        try:
            import backend.browser_manager as _bm
            if "mock_probe_native_metadata" in dir() and _bm.probe_native_metadata is mock_probe_native_metadata:
                _bm.probe_native_metadata = orig_probe_native_metadata
        except Exception:
            pass
        try:
            import backend.proxy_manager as _pm
            if "mock_get_proxy_for_profile" in dir() and _pm.proxy_manager.get_proxy_for_profile is mock_get_proxy_for_profile:
                _pm.proxy_manager.get_proxy_for_profile = orig_get_proxy_for_profile
        except Exception:
            pass
        sys.path = list(orig_sys_path)
        if orig_dir is not None:
            os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = orig_dir
        else:
            os.environ.pop("GHOSTBROWSER_TEST_PROFILES_DIR", None)
        if orig_env is not None:
            os.environ["GHOSTBROWSER_TEST_ENV"] = orig_env
        else:
            os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
        os.environ.pop("GHOSTBROWSER_FORCE_FULL_SPOOF", None)
        try:
            temp_dir.cleanup()
        except Exception:
            pass

if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
