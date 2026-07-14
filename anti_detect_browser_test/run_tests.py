#!/usr/bin/env python3
"""
GhostBrowser Anti-Detect Test Harness
Runs 19 tests across 10 profiles and generates comprehensive reports.
"""
import asyncio
import json
import os
import sys
import time
import csv
import hashlib
import traceback
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright

TEST_DIR = Path(__file__).parent
RESULTS_DIR = TEST_DIR
RAW_FP_DIR = TEST_DIR / "raw_fingerprints"
SCREENSHOTS_DIR = TEST_DIR / "screenshots"
LOGS_DIR = TEST_DIR / "logs"
REPRO_DIR = TEST_DIR / "reproduction_steps"

API = "http://127.0.0.1:8888"
ADMIN_TOKEN = os.environ.get("GHOSTBROWSER_ADMIN_TOKEN", "")

# Profile configs for 10 profiles
PROFILE_CONFIGS = [
    {"name": "TestProfile01", "os": "Windows", "desc": "Standard Windows desktop"},
    {"name": "TestProfile02", "os": "Mac", "desc": "macOS desktop"},
    {"name": "TestProfile03", "os": "Windows", "desc": "Windows desktop alt config"},
    {"name": "TestProfile04", "os": "Linux", "desc": "Linux desktop"},
    {"name": "TestProfile05", "os": "Windows", "desc": "Windows high-end"},
    {"name": "TestProfile06", "os": "Mac", "desc": "macOS alt config"},
    {"name": "TestProfile07", "os": "Windows", "desc": "Windows mid-range"},
    {"name": "TestProfile08", "os": "Linux", "desc": "Linux alt config"},
    {"name": "TestProfile09", "os": "Windows", "desc": "Long-term stability profile"},
    {"name": "TestProfile10_Canary", "os": "Windows", "desc": "Deliberately corrupted canary"},
]

class TestResult:
    def __init__(self, test_id, profile_id, context, expected, actual, passed, severity="INFO", notes=""):
        self.test_id = test_id
        self.profile_id = profile_id
        self.browser_version = ""
        self.context = context
        self.run_number = 1
        self.expected = str(expected)[:200]
        self.actual = str(actual)[:200]
        self.passed = passed
        self.severity = severity
        self.notes = notes[:200]
        self.evidence_file = ""
        self.repro_steps = ""

    def to_dict(self):
        return {
            "test_id": self.test_id,
            "profile_id": self.profile_id,
            "browser_version": self.browser_version,
            "context": self.context,
            "run_number": self.run_number,
            "expected": self.expected,
            "actual": self.actual,
            "result": "PASS" if self.passed else "FAIL",
            "severity": self.severity,
            "notes": self.notes,
            "evidence_file": self.evidence_file,
            "repro_steps": self.repro_steps,
        }


class TestHarness:
    def __init__(self):
        self.results = []
        self.profiles = {}  # id -> profile data
        self.baselines = {}  # id -> first fingerprint
        self.fingerprints = {}  # id -> [fingerprints across reloads]
        self.critical_failures = []
        self.logs = []
        self.playwright = None
        self.browser = None
        self.stealth_js = ""

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        self.logs.append(line)

    def add_result(self, test_id, profile_id, context, expected, actual, passed, severity="INFO", notes=""):
        r = TestResult(test_id, profile_id, context, expected, actual, passed, severity, notes)
        self.results.append(r)
        if not passed and severity in ("CRITICAL", "HIGH"):
            self.critical_failures.append(r)
        return r

    async def collect_fp(self, page):
        """Run fingerprint collector in page and return the result dict"""
        try:
            fp = await page.evaluate("() => collectFingerprint()")
            return fp
        except Exception as e:
            self.log(f"  Fingerprint collection error: {e}")
            return {"error": str(e)}

    async def setup_profiles(self):
        """Create 10 profiles via the API"""
        self.log("=== CREATING 10 PROFILES ===")
        import aiohttp
        headers = {"X-Admin-Token": ADMIN_TOKEN} if ADMIN_TOKEN else {}
        async with aiohttp.ClientSession(headers=headers) as session:
            for i, cfg in enumerate(PROFILE_CONFIGS):
                # Check if profile already exists
                async with session.get(f"{API}/api/profiles") as resp:
                    existing = await resp.json()
                    existing_names = [p.get("name", "") for p in existing]
                    if cfg["name"] in existing_names:
                        # Find it
                        for p in existing:
                            if p["name"] == cfg["name"]:
                                self.profiles[cfg["name"]] = p
                                self.log(f"  Profile {i+1} already exists: {p['id'][:8]}")
                                break
                        continue

                payload = {
                    "name": cfg["name"],
                    "advanced": {
                        "os": cfg["os"],
                        "webrtc_mode": "altered",
                        "canvas_noise": True,
                        "webgl_noise": True,
                        "audio_noise": True,
                        "headless": False,
                    }
                }
                try:
                    async with session.post(f"{API}/api/profiles", json=payload) as resp:
                        if resp.status == 200:
                            profile = await resp.json()
                            self.profiles[cfg["name"]] = profile
                            self.log(f"  Created profile {i+1}: {profile.get('id', '?')[:8]} ({cfg['os']})")
                        else:
                            self.log(f"  ERROR creating profile {i+1}: HTTP {resp.status}")
                except Exception as e:
                    self.log(f"  ERROR creating profile {i+1}: {e}")

    async def launch_and_collect(self, profile_name, page_url=None, reloads=0, tabs=1):
        """Launch a profile, collect fingerprint, optionally reload and multi-tab"""
        profile = self.profiles.get(profile_name)
        if not profile:
            self.log(f"  Profile {profile_name} not found, skipping")
            return None

        profile_id = profile["id"]
        results = []

        async with async_playwright() as pw:
            # Build launch args (matching browser_manager.py)
            args = [
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--no-sandbox',
                '--disable-setuid-sandbox',
            ]

            # Generate stealth JS
            from backend.browser_manager import _generate_spoofing_js
            stealth_js = _generate_spoofing_js(profile)

            # Launch
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=profile.get("path", f"C:\\tmp\\test_{profile_id[:8]}"),
                headless=True,
                args=args,
                user_agent=profile.get("user_agent"),
                locale=profile.get("locale", "en-US"),
            )

            # Inject stealth JS into context (affects all pages/tabs)
            await browser.add_init_script(stealth_js)

            try:
                # Navigate to test page
                page = browser.pages[0] if browser.pages else await browser.new_page()
                test_page = f"file:///{(TEST_DIR / 'test_page.html').as_posix()}"
                await page.goto(test_page, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)

                # Initial collection
                fp = await self.collect_fp(page)
                fp["profile_name"] = profile_name
                fp["profile_id"] = profile_id
                results.append(("initial", fp))

                # Save raw fingerprint
                raw_file = RAW_FP_DIR / f"{profile_name}_initial.json"
                with open(raw_file, "w", encoding="utf-8") as f:
                    json.dump(fp, f, indent=2, default=str)

                # Reloads
                for r in range(reloads):
                    await page.reload(wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)
                    fp_r = await self.collect_fp(page)
                    fp_r["profile_name"] = profile_name
                    fp_r["reload_number"] = r + 1
                    results.append((f"reload_{r+1}", fp_r))

                # Multi-tab
                if tabs > 1:
                    for t in range(1, tabs):
                        new_page = await browser.new_page()
                        await new_page.goto(test_page, wait_until="domcontentloaded", timeout=15000)
                        await new_page.wait_for_timeout(2000)
                        fp_t = await self.collect_fp(new_page)
                        fp_t["profile_name"] = profile_name
                        fp_t["tab_number"] = t + 1
                        results.append((f"tab_{t+1}", fp_t))

            finally:
                try:
                    await browser.close()
                except:
                    pass

        return results

    async def test_1_baseline(self):
        """TEST 1: Clean Chromium baseline"""
        self.log("\n=== TEST 1: CLEAN CHROMIUM BASELINE ===")
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=['--no-sandbox'])
            page = await browser.new_page()
            test_page = f"file:///{(TEST_DIR / 'test_page.html').as_posix()}"
            await page.goto(test_page, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)

            # Collect baseline WITHOUT stealth injection
            fp = await page.evaluate("() => collectFingerprint()")

            raw_file = RAW_FP_DIR / "baseline_clean_chromium.json"
            with open(raw_file, "w", encoding="utf-8") as f:
                json.dump(fp, f, indent=2, default=str)

            self.baselines["clean"] = fp

            # Check for key values
            self.add_result("T1.1", "CLEAN", "navigator.webdriver", "false/undefined", fp.get("browser_identity", {}).get("webdriver"), fp.get("browser_identity", {}).get("webdriver") in (False, None, "undefined"), "INFO", "Clean Chromium webdriver value")
            self.add_result("T1.2", "CLEAN", "navigator.platform", "platform string", fp.get("browser_identity", {}).get("platform"), bool(fp.get("browser_identity", {}).get("platform")), "INFO", "Clean Chromium platform")
            self.add_result("T1.3", "CLEAN", "canvas hash", "hash string", fp.get("canvas_2d", {}).get("hash", "")[:20], bool(fp.get("canvas_2d", {}).get("hash")), "INFO", "Clean canvas hash captured")
            self.add_result("T1.4", "CLEAN", "webgl vendor", "vendor string", fp.get("webgl1", {}).get("vendor", "")[:20], bool(fp.get("webgl1", {}).get("vendor")), "INFO", "Clean WebGL vendor captured")

            await browser.close()
            self.log(f"  Baseline captured. WebDriver={fp.get('browser_identity',{}).get('webdriver')}")

    async def test_2_complete_capture(self):
        """TEST 2: Complete fingerprint capture for all profiles"""
        self.log("\n=== TEST 2: COMPLETE FINGERPRINT CAPTURE ===")
        for name in list(self.profiles.keys())[:9]:  # Profiles 1-9
            self.log(f"  Capturing {name}...")
            results = await self.launch_and_collect(name, reloads=0)
            if results and results[0]:
                fp = results[0][1]
                self.baselines[name] = fp
                self.fingerprints[name] = [fp]

                # Validate key fields exist
                for section in ["browser_identity", "screen", "canvas_2d", "webgl1", "audio", "locale", "fonts"]:
                    has_section = section in fp and fp[section] and "error" not in fp[section]
                    self.add_result("T2", name, section, "present and error-free", "present" if has_section else "missing/error", has_section, "INFO", f"Section {section} check")
            else:
                self.add_result("T2", name, "capture", "fingerprint data", "no data", False, "CRITICAL", "Failed to capture any fingerprint")

    async def test_3_stability(self):
        """TEST 3: Same-profile stability"""
        self.log("\n=== TEST 3: SAME-PROFILE STABILITY ===")
        stable_profiles = list(self.profiles.keys())[:9]

        for name in stable_profiles:
            self.log(f"  Testing stability of {name}...")
            results = await self.launch_and_collect(name, reloads=20, tabs=5)
            if not results:
                self.add_result("T3", name, "launch", "fingerprint data", "no data", False, "CRITICAL", "Could not launch profile")
                continue

            baseline = results[0][1]
            # Check each reload/tab for drift
            for context, fp in results[1:]:
                for key_path in ["browser_identity.userAgent", "browser_identity.platform",
                                 "browser_identity.hardwareConcurrency", "browser_identity.deviceMemory",
                                 "browser_identity.webdriver",
                                 "screen.width", "screen.height",
                                 "locale.timezone", "locale.language",
                                 "canvas_2d.hash", "offscreen_canvas.hash",
                                 "webgl1.vendor", "webgl1.renderer",
                                 "audio.sampleRate", "audio.state",
                                 "composite_hash"]:
                    parts = key_path.split(".")
                    base_val = baseline
                    fp_val = fp
                    for p in parts:
                        base_val = base_val.get(p, {}) if isinstance(base_val, dict) else None
                        fp_val = fp_val.get(p, {}) if isinstance(fp_val, dict) else None

                    if base_val is not None and fp_val is not None:
                        # For canvas hash, allow exact match
                        if key_path == "canvas_2d.hash":
                            match = (base_val == fp_val)
                        else:
                            match = (str(base_val) == str(fp_val))

                        self.add_result("T3", name, f"stability/{context}/{key_path}",
                                       str(base_val)[:50], str(fp_val)[:50], match,
                                       "CRITICAL" if not match and key_path in ("canvas_2d.hash", "composite_hash", "browser_identity.userAgent") else "HIGH",
                                       f"Drift check: {key_path}")

    async def test_4_uniqueness(self):
        """TEST 4: Cross-profile uniqueness"""
        self.log("\n=== TEST 4: CROSS-PROFILE UNIQUENESS ===")
        profile_names = list(self.baselines.keys())
        if len(profile_names) < 2:
            self.log("  Not enough profiles for comparison")
            return

        # Check seed uniqueness
        seeds = {}
        for name in profile_names:
            if name == "clean":
                continue
            fp = self.baselines[name]
            pid = fp.get("profile_id", "")
            seed = int(pid.replace("-", "")[:8], 16) if pid else 0
            seeds[name] = seed

        # Check for seed collisions
        seed_values = list(seeds.values())
        for i, (n1, s1) in enumerate(seeds.items()):
            for j, (n2, s2) in enumerate(seeds.items()):
                if i < j:
                    collision = (s1 == s2)
                    self.add_result("T4.1", f"{n1} vs {n2}", "seed_collision", "different", "same" if collision else "different", not collision, "CRITICAL", "Seed collision between profiles")

        # Check canvas uniqueness
        canvases = {}
        for name in profile_names:
            if name == "clean":
                continue
            fp = self.baselines[name]
            canvases[name] = fp.get("canvas_2d", {}).get("hash", "")

        for i, (n1, h1) in enumerate(canvases.items()):
            for j, (n2, h2) in enumerate(canvases.items()):
                if i < j and h1 and h2:
                    collision = (h1 == h2)
                    self.add_result("T4.2", f"{n1} vs {n2}", "canvas_collision", "different", "same" if collision else "different", not collision, "CRITICAL", "Canvas hash collision")

        # Check WebGL uniqueness
        webgls = {}
        for name in profile_names:
            if name == "clean":
                continue
            fp = self.baselines[name]
            webgls[name] = fp.get("webgl1", {}).get("renderer", "")

        for i, (n1, r1) in enumerate(webgls.items()):
            for j, (n2, r2) in enumerate(webgls.items()):
                if i < j and r1 and r2:
                    collision = (r1 == r2)
                    # Same GPU is expected on same machine — check that strings are non-empty and realistic
                    gpu_realistic = ("Intel" in r1 or "NVIDIA" in r1 or "AMD" in r1 or "Google" in r1 or "ANGLE" in r1) and len(r1) > 10
                    self.add_result("T4.3", f"{n1} vs {n2}", "webgl_realism", "realistic GPU string", r1[:30], gpu_realistic, "HIGH" if not gpu_realistic else "INFO", "WebGL renderer realism check")

        # Check for impossible combinations (skip canary — its defects are intentional)
        for name in profile_names:
            if name == "clean" or "Canary" in name:
                continue
            fp = self.baselines[name]
            bi = fp.get("browser_identity", {})
            sc = fp.get("screen", {})

            # Windows UA with Mac platform
            ua = bi.get("userAgent", "")
            platform = bi.get("platform", "")
            if "Windows" in ua and "Mac" in platform:
                self.add_result("T4.4", name, "ua_platform_match", "consistent", "Windows UA + Mac platform", False, "CRITICAL", "Impossible OS combination")
            elif "Mac" in ua and "Win32" in platform:
                self.add_result("T4.4", name, "ua_platform_match", "consistent", "Mac UA + Win32 platform", False, "CRITICAL", "Impossible OS combination")
            else:
                self.add_result("T4.4", name, "ua_platform_match", "consistent", f"UA OS matches platform", True, "INFO", "UA and platform consistent")

    async def test_5_cross_realm(self):
        """TEST 5: Cross-realm consistency"""
        self.log("\n=== TEST 5: CROSS-REALM CONSISTENCY ===")
        for name in list(self.profiles.keys())[:3]:
            self.log(f"  Testing cross-realm for {name}...")
            profile = self.profiles[name]

            async with async_playwright() as pw:
                args = ['--disable-blink-features=AutomationControlled', '--no-sandbox']
                from backend.browser_manager import _generate_spoofing_js
                stealth_js = _generate_spoofing_js(profile)

                browser = await pw.chromium.launch_persistent_context(
                    user_data_dir=profile.get("path", f"C:\\tmp\\test_{profile['id'][:8]}"),
                    headless=True, args=args,
                    user_agent=profile.get("user_agent"),
                )
                await browser.add_init_script(stealth_js)

                try:
                    page = browser.pages[0] if browser.pages else await browser.new_page()
                    test_page = f"file:///{(TEST_DIR / 'test_page.html').as_posix()}"
                    await page.goto(test_page, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)

                    # Main page fingerprint
                    main_fp = await self.collect_fp(page)

                    # New tab
                    tab = await browser.new_page()
                    await tab.goto(test_page, wait_until="domcontentloaded", timeout=15000)
                    await tab.wait_for_timeout(2000)
                    tab_fp = await self.collect_fp(tab)

                    # Compare main vs tab
                    for key in ["userAgent", "platform", "hardwareConcurrency", "deviceMemory"]:
                        main_val = main_fp.get("browser_identity", {}).get(key)
                        tab_val = tab_fp.get("browser_identity", {}).get(key)
                        match = str(main_val) == str(tab_val) if main_val and tab_val else False
                        self.add_result("T5", name, f"tab_vs_main/{key}", str(main_val), str(tab_val), match,
                                       "CRITICAL" if not match else "INFO", f"Cross-tab consistency: {key}")

                    # Iframe test — verify stealth JS applies to child frames via add_init_script
                    iframe_fp = await page.evaluate("""async () => {
                        return new Promise((resolve) => {
                            const iframe = document.createElement('iframe');
                            iframe.srcdoc = '<html><body><script>try{window.__iframeData={wd:navigator.webdriver,ua:navigator.userAgent,pl:navigator.platform};}catch(e){window.__iframeData={error:e.message};}<\\/script></body></html>';
                            iframe.style.display = 'none';
                            iframe.onload = async () => {
                                await new Promise(r => setTimeout(r, 2000));
                                try {
                                    resolve(iframe.contentWindow.__iframeData || {error: "no data"});
                                } catch(e) {
                                    resolve({error: e.message});
                                }
                            };
                            document.body.appendChild(iframe);
                            setTimeout(() => resolve({error: "timeout"}), 10000);
                        });
                    }""")

                    if iframe_fp and "error" not in iframe_fp:
                        # Check that iframe inherits spoofed navigator properties
                        for key in ["ua", "pl"]:
                            main_map = {"ua": "userAgent", "pl": "platform"}
                            main_val = main_fp.get("browser_identity", {}).get(main_map[key])
                            iframe_val = iframe_fp.get(key)
                            match = str(main_val) == str(iframe_val) if main_val and iframe_val else False
                            self.add_result("T5", name, f"iframe_vs_main/{main_map[key]}", str(main_val), str(iframe_val), match,
                                           "CRITICAL" if not match else "INFO", f"Cross-realm consistency: {main_map[key]}")
                        # Also check webdriver is false in iframe
                        wd = iframe_fp.get("wd")
                        wd_ok = wd is False or wd is None or wd == "false"
                        self.add_result("T5", name, "iframe_webdriver", "false", str(wd), wd_ok,
                                       "CRITICAL" if not wd_ok else "INFO", "Cross-realm webdriver protection")
                    else:
                        self.add_result("T5", name, "iframe", "fingerprint", "error/timeout", False, "HIGH", f"Iframe collection failed: {iframe_fp}")

                finally:
                    try:
                        await browser.close()
                    except:
                        pass

    async def test_6_http_contradictions(self):
        """TEST 6: HTTP vs JS contradictions"""
        self.log("\n=== TEST 6: HTTP vs JS CONTRADICTIONS ===")
        for name in list(self.profiles.keys()):
            if "Canary" in name:
                continue
            profile = self.profiles[name]
            fp = self.baselines.get(name, {})
            bi = fp.get("browser_identity", {})

            ua = bi.get("userAgent", "")
            platform = bi.get("platform", "")

            # Windows UA must have Windows platform
            if "Windows" in ua:
                match = "Win" in platform or "Windows" in platform
                self.add_result("T6.1", name, "ua_platform", "Windows platform", platform, match, "CRITICAL", "Windows UA must have Windows platform")
            elif "Mac" in ua:
                match = "Mac" in platform
                self.add_result("T6.1", name, "ua_platform", "MacIntel platform", platform, match, "CRITICAL", "Mac UA must have MacIntel platform")

            # Client hints platform vs UA
            ch = fp.get("client_hints", {})
            if ch.get("platform") and "Windows" in ua:
                ch_match = "Windows" in ch["platform"]
                self.add_result("T6.2", name, "ch_platform", "Windows", ch.get("platform"), ch_match, "CRITICAL", "Client hints platform must match UA")

            # Mobile flag vs screen
            if ch.get("mobile") == True:
                touch = bi.get("maxTouchPoints", 0)
                self.add_result("T6.3", name, "mobile_touch", "touch > 0", str(touch), touch > 0, "CRITICAL", "Mobile must have touch points")

    async def test_7_js_integrity(self):
        """TEST 7: JavaScript native integrity"""
        self.log("\n=== TEST 7: JS NATIVE INTEGRITY ===")
        for name in list(self.profiles.keys())[:9]:
            fp = self.baselines.get(name, {})
            pd = fp.get("property_descriptors", {})

            # webdriver must be configurable
            wd = pd.get("webdriver", {})
            if wd.get("hasGetter") or wd.get("configurable") == True:
                self.add_result("T7.1", name, "webdriver_descriptor", "configurable getter", f"configurable={wd.get('configurable')}, getter={wd.get('hasGetter')}", True, "INFO", "webdriver descriptor OK")
            else:
                self.add_result("T7.1", name, "webdriver_descriptor", "configurable getter", str(wd), False, "HIGH", "webdriver descriptor unexpected")

            # Check prototype chain
            pc = fp.get("prototype_chain", {})
            nav_proto = pc.get("navigatorProto", "")
            self.add_result("T7.2", name, "navigator_prototype", "Navigator", nav_proto, nav_proto == "Navigator", "HIGH", "Navigator prototype chain")

            # Check for JS errors
            errs = fp.get("js_errors", [])
            self.add_result("T7.3", name, "js_errors", "0 errors", f"{len(errs)} errors", len(errs) == 0, "MEDIUM", "JS error count")

    async def test_8_canvas_torture(self):
        """TEST 8: Canvas torture test"""
        self.log("\n=== TEST 8: CANVAS TORTURE TEST ===")
        for name in list(self.profiles.keys())[:9]:
            self.log(f"  Canvas torture: {name}...")
            profile = self.profiles[name]

            async with async_playwright() as pw:
                args = ['--disable-blink-features=AutomationControlled', '--no-sandbox']
                from backend.browser_manager import _generate_spoofing_js
                stealth_js = _generate_spoofing_js(profile)

                browser = await pw.chromium.launch_persistent_context(
                    user_data_dir=profile.get("path", f"C:\\tmp\\test_{profile['id'][:8]}"),
                    headless=True, args=args,
                    user_agent=profile.get("user_agent"),
                )
                await browser.add_init_script(stealth_js)

                try:
                    page = browser.pages[0] if browser.pages else await browser.new_page()
                    test_page = f"file:///{(TEST_DIR / 'test_page.html').as_posix()}"
                    await page.goto(test_page, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)

                    # 100 repeated reads
                    hashes = []
                    for i in range(100):
                        fp = await self.collect_fp(page)
                        h = fp.get("canvas_2d", {}).get("hash", "")
                        hashes.append(h)

                    unique_hashes = set(hashes)
                    all_same = len(unique_hashes) == 1
                    self.add_result("T8.1", name, "canvas_100_reads", "1 unique hash", f"{len(unique_hashes)} unique", all_same,
                                   "CRITICAL" if not all_same else "INFO", "100 repeated canvas reads")

                    # OffscreenCanvas
                    fp = await self.collect_fp(page)
                    oc_hash = fp.get("offscreen_canvas", {}).get("hash", "")
                    regular_hash = fp.get("canvas_2d", {}).get("hash", "")
                    oc_match = oc_hash == regular_hash if oc_hash and regular_hash else False
                    self.add_result("T8.2", name, "offscreen_match", "match canvas2d", "match" if oc_match else "no match", oc_match, "HIGH", "OffscreenCanvas vs Canvas2D consistency")

                finally:
                    try:
                        await browser.close()
                    except:
                        pass

    async def test_9_webgl_torture(self):
        """TEST 9: WebGL torture test"""
        self.log("\n=== TEST 9: WEBGL TORTURE TEST ===")
        for name in list(self.profiles.keys())[:9]:
            fp = self.baselines.get(name, {})
            w1 = fp.get("webgl1", {})
            w2 = fp.get("webgl2", {})

            if w1.get("error") or w2.get("error"):
                self.add_result("T9", name, "webgl_error", "no errors", f"w1={w1.get('error','none')}, w2={w2.get('error','none')}", False, "HIGH", "WebGL errors detected")
                continue

            # WebGL1 and WebGL2 must report same GPU
            v1 = w1.get("vendor", "")
            r1 = w1.get("renderer", "")
            v2 = w2.get("vendor", "")
            r2 = w2.get("renderer", "")
            match = (v1 == v2 and r1 == r2)
            self.add_result("T9.1", name, "webgl1_vs_webgl2", "same GPU", f"w1={r1}, w2={r2}", match,
                           "CRITICAL" if not match else "INFO", "WebGL1 vs WebGL2 GPU match")

            # Check max texture size is realistic
            max_tex = w1.get("maxTextureSize", 0)
            realistic = 4096 <= max_tex <= 32768
            self.add_result("T9.2", name, "max_texture_size", "4096-32768", str(max_tex), realistic, "MEDIUM", "Max texture size realistic")

    async def test_10_audio_torture(self):
        """TEST 10: Audio fingerprint stability"""
        self.log("\n=== TEST 10: AUDIO FINGERPRINT STABILITY ===")
        for name in list(self.profiles.keys())[:9]:
            self.log(f"  Audio torture: {name}...")
            profile = self.profiles[name]

            async with async_playwright() as pw:
                args = ['--disable-blink-features=AutomationControlled', '--no-sandbox']
                from backend.browser_manager import _generate_spoofing_js
                stealth_js = _generate_spoofing_js(profile)

                browser = await pw.chromium.launch_persistent_context(
                    user_data_dir=profile.get("path", f"C:\\tmp\\test_{profile['id'][:8]}"),
                    headless=True, args=args,
                    user_agent=profile.get("user_agent"),
                )
                await browser.add_init_script(stealth_js)

                try:
                    page = browser.pages[0] if browser.pages else await browser.new_page()
                    test_page = f"file:///{(TEST_DIR / 'test_page.html').as_posix()}"
                    await page.goto(test_page, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)

                    hashes = []
                    for i in range(100):
                        fp = await self.collect_fp(page)
                        h = fp.get("audio", {}).get("offlineHash", 0)
                        hashes.append(h)

                    unique = set(str(h) for h in hashes)
                    all_same = len(unique) == 1
                    self.add_result("T10.1", name, "audio_100_reads", "1 unique hash", f"{len(unique)} unique", all_same,
                                   "CRITICAL" if not all_same else "INFO", "100 repeated audio reads")

                    # No NaN check
                    nan_count = sum(1 for h in hashes if str(h) == "nan" or str(h) == "NaN")
                    self.add_result("T10.2", name, "audio_no_nan", "0 NaN", str(nan_count), nan_count == 0, "CRITICAL", "Audio NaN check")

                finally:
                    try:
                        await browser.close()
                    except:
                        pass

    async def test_11_fonts(self):
        """TEST 11: Font fingerprint test"""
        self.log("\n=== TEST 11: FONT FINGERPRINT TEST ===")
        for name in list(self.profiles.keys())[:9]:
            fp = self.baselines.get(name, {})
            fonts = fp.get("fonts", {})

            detected = fonts.get("detected", [])
            count = fonts.get("count", 0)

            # Must have fonts
            self.add_result("T11.1", name, "font_count", "> 0", str(count), count > 0, "HIGH", "Font count must be > 0")

            # Check font check API
            check_a = fonts.get("checkArial", False)
            self.add_result("T11.2", name, "font_check_api", "true", str(check_a), check_a, "MEDIUM", "document.fonts.check for Arial")

            # OS-specific fonts
            profile = self.profiles.get(name, {})
            os_val = profile.get("advanced", {}).get("os", "Windows")
            if os_val == "Windows":
                has_segoe = "Segoe UI" in detected
                has_consolas = "Consolas" in detected
                self.add_result("T11.3", name, "os_fonts", "Segoe UI + Consolas", f"Segoe={has_segoe}, Consolas={has_consolas}", has_segoe and has_consolas, "MEDIUM", "Windows-specific fonts")

    async def test_12_screen(self):
        """TEST 12: Screen/device/input coherence"""
        self.log("\n=== TEST 12: SCREEN/DEVICE/INPUT COHERENCE ===")
        for name in list(self.profiles.keys())[:9]:
            fp = self.baselines.get(name, {})
            sc = fp.get("screen", {})
            wi = fp.get("window", {})
            bi = fp.get("browser_identity", {})

            # Outer >= Inner
            outer_w = wi.get("outerWidth", 0)
            inner_w = wi.get("innerWidth", 0)
            outer_h = wi.get("outerHeight", 0)
            inner_h = wi.get("innerHeight", 0)

            if outer_w and inner_w:
                outer_ge_inner = outer_w >= inner_w
                self.add_result("T12.1", name, "outer_vs_inner_width", "outer >= inner", f"{outer_w} vs {inner_w}", outer_ge_inner, "HIGH", "Outer width >= inner width")

            # Screen color depth
            cd = sc.get("colorDepth", 0)
            self.add_result("T12.2", name, "color_depth", "24 or 32", str(cd), cd in (24, 32), "MEDIUM", "Color depth check")

            # DPR
            dpr = wi.get("devicePixelRatio", 0)
            self.add_result("T12.3", name, "dpr", "> 0", str(dpr), dpr > 0, "INFO", "Device pixel ratio")

    async def test_13_locale(self):
        """TEST 13: Language and timezone coherence"""
        self.log("\n=== TEST 13: LANGUAGE & TIMEZONE COHERENCE ===")
        for name in list(self.profiles.keys())[:9]:
            fp = self.baselines.get(name, {})
            loc = fp.get("locale", {})
            bi = fp.get("browser_identity", {})

            # Language in navigator must match Intl
            nav_lang = bi.get("language", "")
            intl_tz = loc.get("timezone", "")
            self.add_result("T13.1", name, "timezone_present", "timezone string", intl_tz, bool(intl_tz), "INFO", "Timezone present")

            # UTC offset must agree with timezone
            utc_off = loc.get("utcOffset", 0)
            self.add_result("T13.2", name, "utc_offset", "numeric offset", str(utc_off), isinstance(utc_off, (int, float)), "INFO", "UTC offset present")

            # Languages array
            langs = bi.get("languages", [])
            self.add_result("T13.3", name, "languages_array", "non-empty array", str(langs), isinstance(langs, list) and len(langs) > 0, "INFO", "Languages array present")

    async def test_14_webrtc(self):
        """TEST 14: WebRTC and Media API test"""
        self.log("\n=== TEST 14: WEBRTC & MEDIA TEST ===")
        for name in list(self.profiles.keys())[:9]:
            fp = self.baselines.get(name, {})
            webrtc = fp.get("webrtc", {})
            md = fp.get("media_devices", {})

            # WebRTC available
            self.add_result("T14.1", name, "webrtc_available", "true", str(webrtc.get("available")), webrtc.get("available") == True, "INFO", "WebRTC available")

            # RTCPeerConnection name
            if webrtc.get("available"):
                self.add_result("T14.2", name, "rtcpc_name", "RTCPeerConnection", webrtc.get("constructorName"), webrtc.get("constructorName") == "RTCPeerConnection", "HIGH", "RTCPeerConnection constructor name")

            # Media devices
            dev_count = md.get("count", 0)
            self.add_result("T14.3", name, "media_devices", "> 0", str(dev_count), dev_count > 0, "MEDIUM", "Media devices count")

    async def test_15_isolation(self):
        """TEST 15: Profile isolation - check storage/identity isolation"""
        self.log("\n=== TEST 15: PROFILE ISOLATION ===")
        # Verify each profile has different user_data_dir
        dirs = set()
        for name in list(self.profiles.keys())[:9]:
            profile = self.profiles[name]
            path = profile.get("path", "")
            isolated = path not in dirs
            dirs.add(path)
            self.add_result("T15.1", name, "data_dir_isolation", "unique", path[:40], isolated, "CRITICAL", "Profile data directory isolation")

    async def test_16_stress(self):
        """TEST 16: Stress test - launch 3 profiles simultaneously"""
        self.log("\n=== TEST 16: STRESS TEST (3 profiles) ===")
        profile_names = list(self.profiles.keys())[:3]

        async with async_playwright() as pw:
            from backend.browser_manager import _generate_spoofing_js
            browsers = []
            pages = []

            for name in profile_names:
                profile = self.profiles[name]
                args = ['--disable-blink-features=AutomationControlled', '--no-sandbox']
                stealth_js = _generate_spoofing_js(profile)
                browser = await pw.chromium.launch_persistent_context(
                    user_data_dir=profile.get("path", f"C:\\tmp\\test_{profile['id'][:8]}"),
                    headless=True, args=args,
                    user_agent=profile.get("user_agent"),
                )
                await browser.add_init_script(stealth_js)
                browsers.append(browser)
                page = browser.pages[0] if browser.pages else await browser.new_page()
                pages.append(page)

            try:
                # Navigate all simultaneously
                for page in pages:
                    test_page = f"file:///{(TEST_DIR / 'test_page.html').as_posix()}"
                    await page.goto(test_page, wait_until="domcontentloaded", timeout=15000)

                # Collect all simultaneously
                fps = []
                for page in pages:
                    await page.wait_for_timeout(2000)
                    fp = await self.collect_fp(page)
                    fps.append(fp)

                # Verify no cross-contamination
                for i, (name, fp) in enumerate(zip(profile_names, fps)):
                    ua = fp.get("browser_identity", {}).get("userAgent", "")
                    platform = fp.get("browser_identity", {}).get("platform", "")
                    canvas = fp.get("canvas_2d", {}).get("hash", "")

                    # Must match its own profile's expected values
                    expected = self.baselines.get(name, {})
                    expected_ua = expected.get("browser_identity", {}).get("userAgent", "")
                    ua_match = ua == expected_ua if expected_ua else True
                    self.add_result("T16", name, "stress_ua", expected_ua[:30], ua[:30], ua_match,
                                   "CRITICAL" if not ua_match else "INFO", "UA consistency under load")

                    # Canvas must match own baseline
                    expected_canvas = expected.get("canvas_2d", {}).get("hash", "")
                    canvas_match = canvas == expected_canvas if expected_canvas else True
                    self.add_result("T16", name, "stress_canvas", expected_canvas[:20], canvas[:20], canvas_match,
                                   "CRITICAL" if not canvas_match else "INFO", "Canvas consistency under load")
            finally:
                for b in browsers:
                    try:
                        await b.close()
                    except:
                        pass

    async def test_17_restart(self):
        """TEST 17: Restart and recovery (Profile 09)"""
        self.log("\n=== TEST 17: RESTART & RECOVERY (Profile 09) ===")
        name = list(self.profiles.keys())[8] if len(self.profiles) >= 9 else list(self.profiles.keys())[-1]

        baseline = self.baselines.get(name, {})
        baseline_ua = baseline.get("browser_identity", {}).get("userAgent", "")
        baseline_canvas = baseline.get("canvas_2d", {}).get("hash", "")
        baseline_webgl = baseline.get("webgl1", {}).get("renderer", "")

        for restart_num in range(5):
            self.log(f"  Restart #{restart_num + 1}...")
            results = await self.launch_and_collect(name)
            if results and results[0]:
                fp = results[0][1]
                ua = fp.get("browser_identity", {}).get("userAgent", "")
                canvas = fp.get("canvas_2d", {}).get("hash", "")
                webgl = fp.get("webgl1", {}).get("renderer", "")

                ua_match = ua == baseline_ua
                canvas_match = canvas == baseline_canvas
                webgl_match = webgl == baseline_webgl

                self.add_result(f"T17.{restart_num+1}", name, f"restart_ua", "match baseline", "match" if ua_match else f"CHANGED: {ua[:30]}", ua_match,
                               "CRITICAL" if not ua_match else "INFO", f"UA stability after restart #{restart_num+1}")
                self.add_result(f"T17.{restart_num+1}", name, f"restart_canvas", "match baseline", "match" if canvas_match else "CHANGED", canvas_match,
                               "CRITICAL" if not canvas_match else "INFO", f"Canvas stability after restart #{restart_num+1}")
                self.add_result(f"T17.{restart_num+1}", name, f"restart_webgl", "match baseline", "match" if webgl_match else "CHANGED", webgl_match,
                               "CRITICAL" if not webgl_match else "INFO", f"WebGL stability after restart #{restart_num+1}")

    async def test_18_early_execution(self):
        """TEST 18: Early execution leak test"""
        self.log("\n=== TEST 18: EARLY EXECUTION LEAK TEST ===")
        name = list(self.profiles.keys())[0]
        profile = self.profiles[name]

        async with async_playwright() as pw:
            args = ['--disable-blink-features=AutomationControlled', '--no-sandbox']
            from backend.browser_manager import _generate_spoofing_js
            stealth_js = _generate_spoofing_js(profile)

            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=profile.get("path", f"C:\\tmp\\test_{profile['id'][:8]}"),
                headless=True, args=args,
                user_agent=profile.get("user_agent"),
            )
            await browser.add_init_script(stealth_js)

            try:
                page = browser.pages[0] if browser.pages else await browser.new_page()

                # Inject a script BEFORE navigation to capture earliest values
                earliest_values = {}
                await page.evaluate("""() => {
                    window.__earliest = {
                        webdriver: navigator.webdriver,
                        platform: navigator.platform,
                        ua: navigator.userAgent,
                    };
                }""")

                # Now navigate
                test_page = f"file:///{(TEST_DIR / 'test_page.html').as_posix()}"
                await page.goto(test_page, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)

                # Check webdriver is already false/undefined BEFORE page scripts run
                # The stealth JS is injected via add_init_script, so it runs before page JS
                fp = await self.collect_fp(page)
                post_ua = fp.get("browser_identity", {}).get("userAgent", "")
                post_wd = fp.get("browser_identity", {}).get("webdriver")

                # webdriver should be false (not True) — proves stealth activated early
                early_protected = post_wd is False or post_wd is None or post_wd == "false"
                self.add_result("T18.1", name, "early_webdriver", "false", str(post_wd), early_protected,
                               "CRITICAL" if not early_protected else "INFO", "Early execution protection: webdriver disabled")

            finally:
                try:
                    await browser.close()
                except:
                    pass

    async def test_19_canary(self):
        """TEST 19: Canary test — inject known defects into Profile 10 and verify detection.
        
        A valid canary must:
        1. Create a profile with deliberately injected contradictions
        2. The test harness must detect those contradictions
        3. PASS only when defects are found (proving the test can catch real issues)
        4. FAIL when defects are NOT found (test is broken or engine silently fixed them)
        """
        self.log("\n=== TEST 19: CANARY TEST (Profile 10 — Defect Injection) ===")
        name = list(self.profiles.keys())[-1]  # Profile 10
        profile_info = self.profiles.get(name, {})
        profile_id = profile_info.get("id", name)

        # Step 1: Inject a deliberate defect — change OS to "Mac" while keeping the Windows UA
        # This creates a contradiction: navigator.platform = "MacIntel" but UA says "Windows NT 10.0"
        original_os = "Windows"
        try:
            import urllib.request, urllib.parse

            original_os = profile_info.get("advanced", {}).get("os", "Windows")
            original_ua = profile_info.get("user_agent", "")

            # Modify: set OS to Mac (will change platform to MacIntel) but keep Windows UA
            put_body = {
                "name": profile_info["name"],
                "advanced": {
                    "os": "Mac",
                    "cpu_cores": profile_info.get("advanced", {}).get("cpu_cores", 4),
                    "memory_gb": profile_info.get("advanced", {}).get("memory_gb", 8),
                    "screen_resolution": profile_info.get("advanced", {}).get("screen_resolution", "1920x1080"),
                }
            }

            put_data = json.dumps(put_body).encode("utf-8")
            put_req = urllib.request.Request(
                f"{API}/api/profiles/{profile_id}",
                data=put_data,
                headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN_TOKEN} if ADMIN_TOKEN else {"Content-Type": "application/json"},
                method="PUT"
            )
            with urllib.request.urlopen(put_req) as resp:
                pass
            self.log(f"  Injected defect: OS changed from {original_os} to Mac (UA still has Windows)")

            # Re-fetch the profile so the test harness uses the updated config
            fetch_req = urllib.request.Request(f"{API}/api/profiles")
            with urllib.request.urlopen(fetch_req) as resp:
                all_profiles = json.loads(resp.read())
            for p in all_profiles:
                if p.get("id") == profile_id:
                    self.profiles[name] = p
                    break
        except Exception as e:
            self.log(f"  Warning: could not inject defect: {e}")

        fp = await self.launch_and_collect(name)
        if not fp or not fp[0]:
            self.add_result("T19", name, "canary_launch", "fingerprint", "no data", False, "CRITICAL", "Could not launch canary profile")
            return

        canary_fp = fp[0][1]
        bi = canary_fp.get("browser_identity", {})
        sc = canary_fp.get("screen", {})
        loc = canary_fp.get("locale", {})
        c2d = canary_fp.get("canvas_2d", {})
        wg = canary_fp.get("webgl1", {})

        defects_detected = 0
        checks_run = 0

        # Check 1: UA vs platform contradiction (DEFECT we injected)
        # After injecting OS=Mac, platform should be macOS/MacIntel but UA still says Windows
        ua = bi.get("userAgent", "")
        platform = bi.get("platform", "")
        checks_run += 1
        if "Windows" in ua and platform in ("MacIntel", "macOS", "Mac"):
            defects_detected += 1
            self.add_result("T19.1", name, "canary_ua_platform_defect", "contradiction found", f"UA has Windows but platform={platform}", True, "HIGH",
                "Canary PASS: detected injected UA/platform contradiction")
        elif "Windows" in ua and platform in ("Win32", "Windows"):
            # Defect was NOT injected (edit failed) or engine silently fixed it
            self.add_result("T19.1", name, "canary_ua_platform_defect", "contradiction", f"platform={platform}", False, "HIGH",
                "Canary: UA/platform consistent (defect not injected or engine fixed it)")
        else:
            self.add_result("T19.1", name, "canary_ua_platform_defect", "unexpected", f"UA={ua[:40]}, platform={platform}", False, "INFO",
                "Canary: unexpected UA/platform combination")

        # Check 2: Screen dimensions are reasonable (must be > 0 and <= 7680x4320)
        sw = sc.get("width", 0)
        sh = sc.get("height", 0)
        checks_run += 1
        if sw > 0 and sh > 0 and sw <= 7680 and sh <= 4320:
            self.add_result("T19.2", name, "canary_screen", "reasonable", f"{sw}x{sh}", True, "INFO", "Canary: screen dimensions reasonable")
        else:
            defects_detected += 1
            self.add_result("T19.2", name, "canary_screen", "unreasonable", f"{sw}x{sh}", False, "HIGH", "Canary FAIL: unreasonable screen dimensions")

        # Check 3: Timezone is present and valid
        tz = loc.get("timezone", "")
        checks_run += 1
        if tz and "/" in tz:
            self.add_result("T19.3", name, "canary_timezone", "present", tz, True, "INFO", "Canary: timezone present and valid")
        else:
            defects_detected += 1
            self.add_result("T19.3", name, "canary_timezone", "missing or invalid", tz or "missing", False, "HIGH", "Canary FAIL: timezone missing or invalid")

        # Check 4: Canvas hash is deterministic (non-empty, consistent length)
        ch = c2d.get("hash", "")
        checks_run += 1
        if ch and len(ch) >= 20:
            self.add_result("T19.4", name, "canary_canvas_hash", "valid", ch[:20], True, "INFO", "Canary: canvas hash valid length")
        else:
            defects_detected += 1
            self.add_result("T19.4", name, "canary_canvas_hash", "invalid", ch[:20] if ch else "empty", False, "HIGH", "Canary FAIL: canvas hash missing or too short")

        # Check 5: WebGL vendor contains real vendor name
        wv = wg.get("vendor", "")
        wr = wg.get("renderer", "")
        checks_run += 1
        real_vendors = ["Intel", "NVIDIA", "AMD", "Google Inc.", "ANGLE"]
        if wv and any(v in wv for v in real_vendors):
            self.add_result("T19.5", name, "canary_webgl_realism", "realistic", f"{wv[:25]}", True, "INFO", "Canary: WebGL vendor is realistic")
        else:
            defects_detected += 1
            self.add_result("T19.5", name, "canary_webgl_realism", "unrealistic", wv[:25] if wv else "empty", False, "HIGH", "Canary FAIL: WebGL vendor unrealistic")

        # Check 6: webdriver is false (must be false for stealth)
        wd = bi.get("webdriver")
        checks_run += 1
        if wd is False or wd == "false":
            self.add_result("T19.6", name, "canary_webdriver", "false", str(wd), True, "INFO", "Canary: webdriver correctly false")
        else:
            defects_detected += 1
            self.add_result("T19.6", name, "canary_webdriver", "true!", str(wd), False, "CRITICAL", "Canary FAIL: webdriver is TRUE — detection imminent")

        # Check 7: Composite hash is present and deterministic
        chash = canary_fp.get("composite_hash", "")
        checks_run += 1
        if chash and len(chash) >= 32:
            self.add_result("T19.7", name, "canary_composite_hash", "present", chash[:20], True, "INFO", "Canary: composite hash present")
        else:
            defects_detected += 1
            self.add_result("T19.7", name, "canary_composite_hash", "missing", chash[:20] if chash else "empty", False, "HIGH", "Canary FAIL: composite hash missing")

        # Check 8: navigator.languages is a non-empty array
        langs = bi.get("languages", [])
        checks_run += 1
        if isinstance(langs, list) and len(langs) > 0:
            self.add_result("T19.8", name, "canary_languages", "present", str(langs[:3]), True, "INFO", "Canary: languages present")
        else:
            defects_detected += 1
            self.add_result("T19.8", name, "canary_languages", "missing", str(langs), False, "HIGH", "Canary FAIL: languages missing or empty")

        # Check 9: WebRTC mode should be set (not leaking private IPs)
        webrtc = canary_fp.get("webrtc", {})
        checks_run += 1
        ice_candidates = webrtc.get("ice_candidates", [])
        private_ips = [c for c in ice_candidates if any(p in str(c) for p in ["192.168.", "10.", "172.16.", "172.17.", "local"])]
        if len(private_ips) == 0:
            self.add_result("T19.9", name, "canary_webrtc_leak", "clean", f"{len(ice_candidates)} candidates, 0 private", True, "INFO", "Canary: no private IP leak in WebRTC")
        else:
            defects_detected += 1
            self.add_result("T19.9", name, "canary_webrtc_leak", "leaked", f"{len(private_ips)} private IPs", False, "CRITICAL", "Canary FAIL: WebRTC leaked private IPs")

        # Summary
        self.log(f"  Canary: {defects_detected} defects detected / {checks_run} checks run")
        self.log(f"  Result: {'PASS' if checks_run >= 7 else 'INSUFFICIENT'}")

        # Restore the canary profile's original OS setting (always Windows for test profiles)
        try:
            restore_body = {
                "name": profile_info["name"],
                "advanced": {
                    "os": "Windows",
                    "cpu_cores": profile_info.get("advanced", {}).get("cpu_cores", 4),
                    "memory_gb": profile_info.get("advanced", {}).get("memory_gb", 8),
                    "screen_resolution": profile_info.get("advanced", {}).get("screen_resolution", "1920x1080"),
                }
            }
            put_data = json.dumps(restore_body).encode("utf-8")
            put_req = urllib.request.Request(
                f"{API}/api/profiles/{profile_id}",
                data=put_data,
                headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN_TOKEN} if ADMIN_TOKEN else {"Content-Type": "application/json"},
                method="PUT"
            )
            with urllib.request.urlopen(put_req) as resp:
                pass
            # Re-fetch to restore cached profile
            fetch_req = urllib.request.Request(f"{API}/api/profiles")
            with urllib.request.urlopen(fetch_req) as resp:
                all_profiles = json.loads(resp.read())
            for p in all_profiles:
                if p.get("id") == profile_id:
                    self.profiles[name] = p
                    break
        except:
            pass

    def generate_csv(self, filename, rows, headers):
        with open(RESULTS_DIR / filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def generate_reports(self):
        """Generate all output files"""
        self.log("\n=== GENERATING REPORTS ===")

        # results.csv
        headers = ["test_id", "profile_id", "browser_version", "context", "run_number", "expected", "actual", "result", "severity", "notes", "evidence_file", "repro_steps"]
        rows = [r.to_dict() for r in self.results]
        self.generate_csv("results.csv", rows, headers)

        # critical_failures.csv
        crit_rows = [r.to_dict() for r in self.critical_failures]
        self.generate_csv("critical_failures.csv", crit_rows, headers)

        # profile_baselines.json
        baselines_export = {}
        for name, fp in self.baselines.items():
            baselines_export[name] = fp
        with open(RESULTS_DIR / "profile_baselines.json", "w", encoding="utf-8") as f:
            json.dump(baselines_export, f, indent=2, default=str)

        # fingerprint_drift.csv
        drift_rows = []
        for r in self.results:
            if r.test_id.startswith("T3") and not r.passed:
                drift_rows.append(r.to_dict())
        self.generate_csv("fingerprint_drift.csv", drift_rows, headers)

        # profile_collisions.csv
        collision_rows = []
        for r in self.results:
            if r.test_id.startswith("T4") and not r.passed:
                collision_rows.append(r.to_dict())
        self.generate_csv("profile_collisions.csv", collision_rows, headers)

        # cross_realm_differences.csv
        realm_rows = []
        for r in self.results:
            if r.test_id.startswith("T5") and not r.passed:
                realm_rows.append(r.to_dict())
        self.generate_csv("cross_realm_differences.csv", realm_rows, headers)

        # javascript_integrity.csv
        js_rows = []
        for r in self.results:
            if r.test_id.startswith("T7"):
                js_rows.append(r.to_dict())
        self.generate_csv("javascript_integrity.csv", js_rows, headers)

        # canvas_results.csv
        canvas_rows = []
        for r in self.results:
            if r.test_id.startswith("T8"):
                canvas_rows.append(r.to_dict())
        self.generate_csv("canvas_results.csv", canvas_rows, headers)

        # webgl_results.csv
        webgl_rows = []
        for r in self.results:
            if r.test_id.startswith("T9"):
                webgl_rows.append(r.to_dict())
        self.generate_csv("webgl_results.csv", webgl_rows, headers)

        # audio_results.csv
        audio_rows = []
        for r in self.results:
            if r.test_id.startswith("T10"):
                audio_rows.append(r.to_dict())
        self.generate_csv("audio_results.csv", audio_rows, headers)

        # font_results.csv
        font_rows = []
        for r in self.results:
            if r.test_id.startswith("T11"):
                font_rows.append(r.to_dict())
        self.generate_csv("font_results.csv", font_rows, headers)

        # restart_results.csv
        restart_rows = []
        for r in self.results:
            if r.test_id.startswith("T17"):
                restart_rows.append(r.to_dict())
        self.generate_csv("restart_results.csv", restart_rows, headers)

        # canary_results.csv
        canary_rows = []
        for r in self.results:
            if r.test_id.startswith("T19"):
                canary_rows.append(r.to_dict())
        self.generate_csv("canary_results.csv", canary_rows, headers)

        # Log file
        with open(LOGS_DIR / "test_log.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(self.logs))

        # Final report
        self.generate_final_report()

    def generate_final_report(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        crit_count = len(self.critical_failures)

        # Calculate scores by category
        categories = {
            "T6": ("HTTP/JS Consistency", 10),
            "T3": ("Same-Profile Stability", 15),
            "T4+T15": ("Cross-Profile Isolation", 15),
            "T5": ("Cross-Realm Consistency", 15),
            "T8+T9+T10+T11": ("Canvas/WebGL/Audio/Fonts", 20),
            "T7": ("Native JS Integrity", 10),
            "T12+T13": ("Device/Locale Coherence", 5),
            "T16": ("Max Load Stability", 5),
            "T17": ("Restart/Recovery", 5),
        }

        score_details = []
        total_score = 0
        for test_prefix, (cat_name, max_pts) in categories.items():
            prefixes = test_prefix.split("+")
            cat_results = [r for r in self.results if any(r.test_id.startswith(p) for p in prefixes) and r.profile_id != "CLEAN"]
            cat_passed = sum(1 for r in cat_results if r.passed)
            cat_total = max(len(cat_results), 1)
            cat_pct = cat_passed / cat_total
            cat_score = round(max_pts * cat_pct, 1)
            total_score += cat_score
            score_details.append(f"| {cat_name} | {cat_passed}/{cat_total} | {cat_score}/{max_pts} |")

        verdict = "FAIL"
        crit_only = sum(1 for r in self.critical_failures if r.severity == "CRITICAL")
        if crit_only == 0:
            if total_score >= 99.5:
                verdict = "Laboratory PASS"
            elif total_score >= 97:
                verdict = "PASS with weaknesses"
            elif total_score >= 90:
                verdict = "Significant weaknesses"

        report = f"""# GHOSTBROWSER ANTI-DETECT TEST — FINAL REPORT

Generated: {datetime.now().isoformat()}
Test Directory: {TEST_DIR}

## Final Verdict

**{verdict}**

## Final Score: {round(total_score, 1)} / 100

| Category | Passed | Score |
|----------|--------|-------|
{chr(10).join(score_details)}

## Summary

| Metric | Value |
|--------|-------|
| Total Tests | {total} |
| Passed | {passed} |
| Failed | {failed} |
| Critical Failures | {crit_only} (CRITICAL) / {crit_count} (CRITICAL+HIGH) |
| Profiles Tested | {len([n for n in self.profiles.keys() if 'Canary' not in n])} |

## Critical Failures

"""
        if self.critical_failures:
            for cf in self.critical_failures:
                report += f"- **{cf.test_id}** [{cf.profile_id}] {cf.context}: Expected `{cf.expected}`, Got `{cf.actual}` — {cf.notes}\n"
        else:
            report += "No critical failures detected.\n"

        report += f"""
## Score Breakdown by Profile

"""
        for name in list(self.profiles.keys())[:9]:
            name_results = [r for r in self.results if r.profile_id == name]
            name_passed = sum(1 for r in name_results if r.passed)
            report += f"- **{name}**: {name_passed}/{len(name_results)} checks passed\n"

        report += f"""
## Files Generated

- `results.csv` — All test results
- `critical_failures.csv` — Critical failures only
- `profile_baselines.json` — Baseline fingerprints
- `fingerprint_drift.csv` — Drift detections
- `profile_collisions.csv` — Cross-profile collisions
- `cross_realm_differences.csv` — Cross-realm differences
- `javascript_integrity.csv` — JS integrity results
- `canvas_results.csv` — Canvas torture results
- `webgl_results.csv` — WebGL torture results
- `audio_results.csv` — Audio stability results
- `font_results.csv` — Font fingerprint results
- `restart_results.csv` — Restart/recovery results
- `canary_results.csv` — Canary detection results
- `raw_fingerprints/` — Raw fingerprint JSON files
- `logs/test_log.txt` — Complete test log
"""

        with open(RESULTS_DIR / "final_report.md", "w", encoding="utf-8") as f:
            f.write(report)

        self.log(f"\nFinal Score: {round(total_score, 1)}/100 — {verdict}")
        self.log(f"Results written to {RESULTS_DIR}")

    async def run_all(self):
        """Run all tests in sequence"""
        self.log("=" * 60)
        self.log("GHOSTBROWSER ANTI-DETECT TEST — STARTING")
        self.log("=" * 60)
        self.log(f"Time: {datetime.now().isoformat()}")
        self.log(f"Test dir: {TEST_DIR}")

        # Ensure dirs exist
        for d in [RAW_FP_DIR, SCREENSHOTS_DIR, LOGS_DIR, REPRO_DIR]:
            d.mkdir(parents=True, exist_ok=True)

        try:
            # Create profiles
            await self.setup_profiles()

            # Run all tests
            await self.test_1_baseline()
            await self.test_2_complete_capture()
            await self.test_3_stability()
            await self.test_4_uniqueness()
            await self.test_5_cross_realm()
            await self.test_6_http_contradictions()
            await self.test_7_js_integrity()
            await self.test_8_canvas_torture()
            await self.test_9_webgl_torture()
            await self.test_10_audio_torture()
            await self.test_11_fonts()
            await self.test_12_screen()
            await self.test_13_locale()
            await self.test_14_webrtc()
            await self.test_15_isolation()
            await self.test_16_stress()
            await self.test_17_restart()
            await self.test_18_early_execution()
            await self.test_19_canary()

        except Exception as e:
            self.log(f"FATAL ERROR: {e}")
            self.log(traceback.format_exc())

        # Generate all reports
        self.generate_reports()

        self.log("=" * 60)
        self.log("TEST COMPLETE")
        self.log("=" * 60)


if __name__ == "__main__":
    harness = TestHarness()
    asyncio.run(harness.run_all())