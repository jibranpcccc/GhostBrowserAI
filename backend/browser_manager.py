from playwright.async_api import async_playwright
import playwright_stealth
from backend.profile_manager import profile_manager
from backend.ai_scanner import ai_scanner

active_browsers = {}

async def launch_profile(profile_id: str, force_headless: bool = False):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        return {"status": "error", "message": "Profile not found"}

    # Run AI Scan
    scan_result = ai_scanner.scan_profile_before_launch(profile)
    if scan_result["status"] != "clean":
        return {"status": "error", "message": f"AI Scanner blocked launch: {scan_result['message']}"}

    from backend.lock_manager import lock_manager
    await lock_manager.acquire(profile_id)
    try:
        # Ensure playwright isn't already running for this profile
        if profile_id in active_browsers:
            return {"status": "success", "message": "Browser already running"}
            
        playwright = await async_playwright().start()
    
        args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-infobars',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process'
        ]
        
        import os
        from backend.config import get_bundled_dir
        extensions_dir = get_bundled_dir("backend", "extensions")
        from backend.security_hardening import security_hardening
        extension_paths = security_hardening.validate_extensions(extensions_dir)
        
        if extension_paths:
            paths_str = ",".join(extension_paths)
            args.append(f"--disable-extensions-except={paths_str}")
            args.append(f"--load-extension={paths_str}")

        proxy = None
        proxy_warning = None
        
        pinned_proxy_str = profile.get("proxy_pin")
        
        # 1. Ask Proxy Manager for a sticky proxy
        from backend.proxy_manager import proxy_manager
        
        if pinned_proxy_str:
            parts = pinned_proxy_str.replace("http://", "").split(":")
            if len(parts) == 4:
                assigned_proxy = {
                    "server": f"http://{parts[0]}:{parts[1]}",
                    "username": parts[2],
                    "password": parts[3]
                }
            elif len(parts) == 2:
                assigned_proxy = {"server": f"http://{parts[0]}:{parts[1]}"}
            else:
                assigned_proxy = {"server": pinned_proxy_str}
                
            original_proxy = assigned_proxy
        else:
            assigned_proxy = await proxy_manager.get_proxy_for_profile(profile_id)
            original_proxy = profile.get("proxy")
            
            if original_proxy and assigned_proxy and original_proxy.get("server") != assigned_proxy.get("server"):
                proxy_warning = f"Original proxy died. Fallback proxy {assigned_proxy.get('server')} used."
                
            if original_proxy and not assigned_proxy:
                # Fallback to baked-in if proxy_manager is empty, but verify health first!
                if await proxy_manager.check_proxy_health(original_proxy):
                    assigned_proxy = original_proxy
                else:
                    return {"status": "error", "message": "FAIL-CLOSED: All proxies are dead. Aborting browser launch to prevent IP leak."}
        if not original_proxy and not assigned_proxy:
            # Profile doesn't use proxies
            pass
            
        # 2. Network Leak Prevention Arguments (Always active)
        args.extend([
            '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
            '--enforce-webrtc-ip-permission-check'
        ])

        if assigned_proxy:
            proxy = {"server": assigned_proxy["server"]}
            if assigned_proxy.get("username"):
                proxy["username"] = assigned_proxy["username"]
                proxy["password"] = assigned_proxy["password"]
                
            args.append(f'--host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE {assigned_proxy["server"].replace("http://", "").split(":")[0]}"')

        # Fix #3: Viewport MUST match screen_resolution from the AI-generated fingerprint
        _early_adv = profile.get("advanced", {})
        _scr_parts = _early_adv.get("screen_resolution", "1920x1080").split("x")
        _vp_w = int(_scr_parts[0]) if len(_scr_parts) == 2 and _scr_parts[0].isdigit() else 1920
        _vp_h = int(_scr_parts[1]) if len(_scr_parts) == 2 and _scr_parts[1].isdigit() else 1080
        
        is_headless = force_headless or _early_adv.get("headless", False)

        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile["path"],
            headless=is_headless,
            args=args,
            proxy=proxy,
            user_agent=profile.get("user_agent"),
            timezone_id=profile.get("timezone"),
            locale=profile.get("locale"),
            viewport={"width": _vp_w, "height": _vp_h}  # Now matches AI-generated screen_resolution
        )
        
        if _early_adv.get("block_trackers", False):
            async def block_tracker_route(route, request):
                url = request.url.lower()
                trackers = ["google-analytics.com", "doubleclick.net", "facebook.com/tr", "hotjar.com", "pixel.facebook.com", "analytics", "tracker"]
                if any(t in url for t in trackers):
                    await route.abort()
                else:
                    await route.continue_()
            await context.route("**/*", block_tracker_route)

        page = context.pages[0] if context.pages else await context.new_page()
        stealth = playwright_stealth.stealth.Stealth(
            navigator_plugins=False,
            navigator_languages=False,
            navigator_vendor=False,
            navigator_user_agent=False,
            webgl_vendor=False,
            navigator_hardware_concurrency=False
        )
        await stealth.apply_stealth_async(page)
        
        # Attach Anomaly Detector
        from backend.ai_anomaly_detector import anomaly_detector
        await anomaly_detector.attach(profile_id, page)

        advanced = profile.get("advanced", {})
        cpu_cores = advanced.get("cpu_cores") or 4
        memory_gb = advanced.get("memory_gb") or 8
        canvas_noise = advanced.get("canvas_noise", True)
        webgl_noise = advanced.get("webgl_noise", True)
        audio_noise = advanced.get("audio_noise", True)
        screen_res = advanced.get("screen_resolution", "1920x1080")
        webrtc_mode = advanced.get("webrtc_mode", "altered")
        
        # AI Generated overrides
        ai_webgl_vendor = advanced.get("webgl_vendor", "Google Inc. (NVIDIA)")
        ai_webgl_renderer = advanced.get("webgl_renderer", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)")
        sec_ch_ua = advanced.get("sec_ch_ua", '"Not)A;Brand";v="8", "Chromium";v="135", "Google Chrome";v="135"')
        sec_ch_ua_platform = advanced.get("sec_ch_ua_platform", '"Windows"')
        ua = profile.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36")
        
        # navigator.languages synced to profile locale (prevents locale/timezone mismatch detection)
        profile_locale = profile.get("locale", advanced.get("locale", "en-US"))
        profile_languages = advanced.get("languages", [profile_locale, profile_locale.split("-")[0]] if "-" in profile_locale else [profile_locale])
        if "en" not in profile_languages:
            profile_languages.append("en")
        languages_js = str(profile_languages).replace("'", '"')

        # ================================================================
        # STEALTH+ HARDENING: All extra per-profile spoofing variables
        # ================================================================
        import re as _re

        # Fix #5: Stable canvas noise seed — deterministic per profile ID
        # (same output every call = real hardware behaviour; unique per profile)
        _pid_raw = profile.get("id", "ffffffff-0000-0000-0000-000000000000")
        _seed = int(_pid_raw.replace("-", "")[:8], 16)
        # Multiply by prime numbers to drastically shift the RGB values across profiles
        canvas_r_offset = advanced.get("canvas_r_offset", (_seed % 250)) 
        canvas_g_offset = advanced.get("canvas_g_offset", ((_seed * 7) % 250))
        canvas_b_offset = advanced.get("canvas_b_offset", ((_seed * 13) % 250))

        # Fix #7: devicePixelRatio — most 1080p Windows laptops use 1.25
        device_pixel_ratio = advanced.get("device_pixel_ratio", 1.25)

        # Fix #1: navigator.connection — mimic residential broadband
        connection_downlink = advanced.get("connection_downlink", round((_seed % 30) + 15, 1))  # 15-45 Mbps
        connection_rtt     = advanced.get("connection_rtt",      (_seed % 50) + 25)              # 25-75 ms

        # Fix #2: navigator.userAgentData platform
        ua_os_val = advanced.get("os", "Windows")
        ua_platform_str  = "macOS" if ua_os_val == "Mac" else ("Linux" if ua_os_val == "Linux" else "Windows")
        _ua_raw = profile.get("user_agent", "Chrome/135")
        _chrome_m = _re.search(r"Chrome/(\d+)", _ua_raw)
        chrome_major_ver = _chrome_m.group(1) if _chrome_m else "135"

        # Fix #9: Intl.DateTimeFormat — always pin to profile timezone
        profile_tz = profile.get("timezone", "UTC")

        # Fix #8: speechSynthesis.getVoices() — OS-appropriate voice list
        if ua_os_val == "Mac":
            speech_voices_json = '["Alex","Samantha","Victoria","Daniel","Karen","Moira","Tessa"]'
        else:
            speech_voices_json = '["Microsoft David Desktop","Microsoft Zira Desktop","Microsoft Mark Desktop","Microsoft David - English (United States)"]'


        spoofing_script = f"""
            // 0. Native Code Evasion Engine (Defeats CreepJS Lie Detection)
            const spoofedFunctions = new WeakMap();
            const originalToString = Function.prototype.toString;
            Function.prototype.toString = new Proxy(originalToString, {{
                apply: function(target, thisArg, args) {{
                    if (spoofedFunctions.has(thisArg)) {{
                        return `function ${{spoofedFunctions.get(thisArg)}}() {{ [native code] }}`;
                    }}
                    if (thisArg === Function.prototype.toString) {{
                        return `function toString() {{ [native code] }}`;
                    }}
                    return Reflect.apply(target, thisArg, args);
                }}
            }});
            const makeNative = (func, name) => {{
                spoofedFunctions.set(func, name || func.name || '');
                return func;
            }};

            // 1. Hardware Concurrency & Memory & User Agent
            Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {cpu_cores} }});
            Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {memory_gb} }});
            
            // Force User-Agent directly in JS context to ensure it overrides everything
            Object.defineProperty(navigator, 'userAgent', {{ get: () => "{ua}" }});
            
            // 1b. Navigator Languages (synced to proxy locale — defeats locale/timezone mismatch detection)
            Object.defineProperty(navigator, 'languages', {{ get: () => {languages_js} }});
            Object.defineProperty(navigator, 'language', {{ get: () => {languages_js}[0] }});

            // 2. Screen Resolution
            const screenRes = "{screen_res}".split('x');
            const width = parseInt(screenRes[0]);
            const height = parseInt(screenRes[1]);
            Object.defineProperty(window.screen, 'width', {{ get: () => width }});
            Object.defineProperty(window.screen, 'height', {{ get: () => height }});
            Object.defineProperty(window.screen, 'availWidth', {{ get: () => width }});
            Object.defineProperty(window.screen, 'availHeight', {{ get: () => height }});

            // 3. WebRTC Spoofing / Blocking
            if ("{webrtc_mode}" === "disabled") {{
                navigator.mediaDevices.getUserMedia = navigator.webkitGetUserMedia = navigator.mozGetUserMedia = navigator.getUserMedia = webkitRTCPeerConnection = RTCPeerConnection = MediaStreamTrack = undefined;
            }} else {{
                // Deep WebRTC Masking: intercept RTCPeerConnection events to remove local ICE candidates
                const OrigPeerConn = window.RTCPeerConnection || window.webkitRTCPeerConnection || window.mozRTCPeerConnection;
                if (OrigPeerConn) {{
                    window.RTCPeerConnection = window.webkitRTCPeerConnection = window.mozRTCPeerConnection = function(...args) {{
                        const pc = new OrigPeerConn(...args);
                        
                        // Intercept addEventListener
                        const origAddEventListener = pc.addEventListener.bind(pc);
                        pc.addEventListener = function(type, listener, options) {{
                            if (type === 'icecandidate') {{
                                const wrappedListener = function(event) {{
                                    if (event.candidate && event.candidate.candidate) {{
                                        const c = event.candidate.candidate;
                                        if (c.includes('.local') || c.includes('192.168.') || c.includes('10.')) return;
                                    }}
                                    return listener.call(this, event);
                                }};
                                return origAddEventListener(type, wrappedListener, options);
                            }}
                            return origAddEventListener(type, listener, options);
                        }};

                        // Intercept onicecandidate setter
                        Object.defineProperty(pc, 'onicecandidate', {{
                            set: function(handler) {{ this._onicecandidate = handler; }},
                            get: function() {{ return this._onicecandidate; }}
                        }});
                        
                        // Fire filtered events to the intercepted handler
                        origAddEventListener('icecandidate', (event) => {{
                            if (event.candidate && event.candidate.candidate) {{
                                const c = event.candidate.candidate;
                                if (c.includes('.local') || c.includes('192.168.') || c.includes('10.')) return;
                            }}
                            if (pc._onicecandidate) pc._onicecandidate(event);
                        }});
                        return pc;
                    }};
                    window.RTCPeerConnection.prototype = OrigPeerConn.prototype;
                    makeNative(window.RTCPeerConnection, 'RTCPeerConnection');
                }}
            }}

            // 4. Guaranteed Canvas Serialization Noise
            if ({str(canvas_noise).lower()}) {{
                const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = makeNative(function() {{
                    try {{
                        const ctx = this.getContext('2d');
                        if (ctx) {{
                            const oldStyle = ctx.fillStyle;
                            const oldAlpha = ctx.globalAlpha;
                            
                            // Force a 1x1 solid pixel in the top left.
                            // SwiftShader cannot ignore an opaque pixel.
                            ctx.globalAlpha = 1.0;
                            ctx.fillStyle = "rgb({canvas_r_offset}, {canvas_g_offset}, {canvas_b_offset})";
                            ctx.fillRect(0, 0, 1, 1);
                            
                            ctx.fillStyle = oldStyle;
                            ctx.globalAlpha = oldAlpha;
                        }}
                    }} catch (e) {{}}
                    return originalToDataURL.apply(this, arguments);
                }}, 'toDataURL');
                
                const originalToBlob = HTMLCanvasElement.prototype.toBlob;
                HTMLCanvasElement.prototype.toBlob = makeNative(function() {{
                    try {{
                        const ctx = this.getContext('2d');
                        if (ctx) {{
                            const oldStyle = ctx.fillStyle;
                            const oldAlpha = ctx.globalAlpha;
                            ctx.globalAlpha = 1.0;
                            ctx.fillStyle = "rgb({canvas_r_offset}, {canvas_g_offset}, {canvas_b_offset})";
                            ctx.fillRect(0, 0, 1, 1);
                            ctx.fillStyle = oldStyle;
                            ctx.globalAlpha = oldAlpha;
                        }}
                    }} catch (e) {{}}
                    return originalToBlob.apply(this, arguments);
                }}, 'toBlob');
            }}

            // 5. WebGL1 Noise Injection
            if ({str(webgl_noise).lower()}) {{
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = makeNative(function(parameter) {{
                    if (parameter === 37445) return '{ai_webgl_vendor}'; // UNMASKED_VENDOR_WEBGL
                    if (parameter === 37446) return '{ai_webgl_renderer}'; // UNMASKED_RENDERER_WEBGL
                    return getParameter.apply(this, [parameter]);
                }}, 'getParameter');
                
                const _origGetExt = WebGLRenderingContext.prototype.getExtension;
                WebGLRenderingContext.prototype.getExtension = makeNative(function(name) {{
                    const ext = _origGetExt.apply(this, arguments);
                    if (name === 'WEBGL_debug_renderer_info' && ext) {{
                        return new Proxy(ext, {{ get: function(t, p) {{
                            if (p === 'UNMASKED_VENDOR_WEBGL') return 37445;
                            if (p === 'UNMASKED_RENDERER_WEBGL') return 37446;
                            return t[p];
                        }} }});
                    }}
                    return ext;
                }}, 'getExtension');
            }}

            // Fix #6: WebGL2 — must patch separately or detector reads real GPU via webgl2 context
            if ({str(webgl_noise).lower()} && typeof WebGL2RenderingContext !== 'undefined') {{
                const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = makeNative(function(parameter) {{
                    if (parameter === 37445) return '{ai_webgl_vendor}';
                    if (parameter === 37446) return '{ai_webgl_renderer}';
                    return getParameter2.apply(this, [parameter]);
                }}, 'getParameter');
                // Also intercept getExtension for WEBGL_debug_renderer_info on WebGL2
                const _origGetExt2 = WebGL2RenderingContext.prototype.getExtension;
                WebGL2RenderingContext.prototype.getExtension = makeNative(function(name) {{
                    const ext = _origGetExt2.apply(this, arguments);
                    if (name === 'WEBGL_debug_renderer_info' && ext) {{
                        return new Proxy(ext, {{ get: function(t, p) {{
                            if (p === 'UNMASKED_VENDOR_WEBGL') return 37445;
                            if (p === 'UNMASKED_RENDERER_WEBGL') return 37446;
                            return t[p];
                        }} }});
                    }}
                    return ext;
                }}, 'getExtension');
            }}
            
            // 6. AudioContext Noise Injection
            if ({str(audio_noise).lower()}) {{
                const originalCreateOscillator = AudioContext.prototype.createOscillator;
                AudioContext.prototype.createOscillator = function() {{
                    const oscillator = originalCreateOscillator.apply(this, arguments);
                    const originalStart = oscillator.start;
                    oscillator.start = function() {{
                        oscillator.frequency.value += Math.random() * 0.001;
                        return originalStart.apply(this, arguments);
                    }};
                    return oscillator;
                }};
            }}
            
            // 7. Plugin and MimeType spoofing (basic)
            const plugins = {str(advanced.get('plugins', ['Chrome PDF Viewer']))};
            Object.defineProperty(navigator, 'plugins', {{
                get: () => plugins.map(p => ({{ name: p, description: p, filename: p + '.dll' }}))
            }});
            Object.defineProperty(navigator, 'mimeTypes', {{
                get: () => [{{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }}]
            }});
            
            // 8. Advanced Subpixel DOM Rect Noise (Defeats CreepJS Font/Layout fingerprinting)
            const noise = 0.000001 * Math.random();
            const originalGetClientRects = Element.prototype.getClientRects;
            Element.prototype.getClientRects = function() {{
                const rects = originalGetClientRects.apply(this, arguments);
                for (let i = 0; i < rects.length; i++) {{
                    const r = rects[i];
                    // Redefine properties to include microscopic noise
                    Object.defineProperties(r, {{
                        width: {{ get: () => r.width + noise }},
                        height: {{ get: () => r.height + noise }},
                        right: {{ get: () => r.right + noise }},
                        bottom: {{ get: () => r.bottom + noise }}
                    }});
                }}
                return rects;
            }};
            
            const originalGetBoundingClientRect = Element.prototype.getBoundingClientRect;
            Element.prototype.getBoundingClientRect = function() {{
                const r = originalGetBoundingClientRect.apply(this, arguments);
                Object.defineProperties(r, {{
                    width: {{ get: () => r.width + noise }},
                    height: {{ get: () => r.height + noise }},
                    right: {{ get: () => r.right + noise }},
                    bottom: {{ get: () => r.bottom + noise }}
                }});
                return r;
            }};

            // 9. Canvas Font Measurement Noise
            if ({str(canvas_noise).lower()}) {{
                const originalMeasureText = CanvasRenderingContext2D.prototype.measureText;
                CanvasRenderingContext2D.prototype.measureText = makeNative(function() {{
                    const result = originalMeasureText.apply(this, arguments);
                    Object.defineProperty(result, 'width', {{ get: () => result.width + (({canvas_r_offset} % 5) / 100.0) }});
                    return result;
                }}, 'measureText');
            }}
            
            // 10. WebGL readPixels Noise (Invisible 3D Rendering Spoof)
            if ({str(webgl_noise).lower()}) {{
                const injectReadPixelsNoise = function(contextType) {{
                    const originalReadPixels = contextType.prototype.readPixels;
                    contextType.prototype.readPixels = function() {{
                        originalReadPixels.apply(this, arguments);
                        // arguments[6] is the pixels array (Uint8Array or similar)
                        if (arguments[6] && arguments[6].length > 0) {{
                            // Perturb just a couple of pixels randomly to change the GPU signature hash
                            const noiseIndex = Math.floor(Math.random() * Math.min(arguments[6].length, 100));
                            arguments[6][noiseIndex] = Math.min(255, arguments[6][noiseIndex] + 1);
                        }}
                    }};
                }};
                injectReadPixelsNoise(WebGLRenderingContext);
                if (typeof WebGL2RenderingContext !== 'undefined') {{
                    injectReadPixelsNoise(WebGL2RenderingContext);
                }}
            }}

            // 11. Battery Status Spoofing (Passes Playwright checks)
            const fakeBattery = {{
                charging: false,
                chargingTime: Infinity,
                dischargingTime: 86400 * Math.random(),
                level: 0.85 + (0.10 * Math.random()), // 85-95%
                onchargingchange: null,
                onchargingtimechange: null,
                ondischargingtimechange: null,
                onlevelchange: null
            }};
            Object.defineProperty(navigator, 'getBattery', {{
                value: () => Promise.resolve(fakeBattery)
            }});
            
            // Fix #7: screen.colorDepth, screen.pixelDepth, devicePixelRatio
            Object.defineProperty(window.screen, 'colorDepth', {{ get: () => 24 }});
            Object.defineProperty(window.screen, 'pixelDepth', {{ get: () => 24 }});
            Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {device_pixel_ratio} }});

            // Fix #1: navigator.connection — spoof as residential WiFi (defeats proxy-type detection)
            (function() {{
                const _fakeConn = {{
                    effectiveType: '4g',
                    downlink: {connection_downlink},
                    rtt: {connection_rtt},
                    saveData: false,
                    type: 'wifi',
                    onchange: null,
                    addEventListener: function() {{}},
                    removeEventListener: function() {{}},
                    dispatchEvent: function() {{ return true; }}
                }};
                try {{ Object.defineProperty(navigator, 'connection', {{ get: () => _fakeConn }}); }} catch(e) {{}}
                try {{ Object.defineProperty(navigator, 'mozConnection', {{ get: () => undefined }}); }} catch(e) {{}}
                try {{ Object.defineProperty(navigator, 'webkitConnection', {{ get: () => undefined }}); }} catch(e) {{}}
            }})();

            // Fix #2: navigator.userAgentData — defeat Chrome version mismatch detection
            (function() {{
                const _brands = [
                    {{ brand: 'Not)A;Brand', version: '8' }},
                    {{ brand: 'Chromium', version: '{chrome_major_ver}' }},
                    {{ brand: 'Google Chrome', version: '{chrome_major_ver}' }}
                ];
                const _uaData = {{
                    brands: _brands,
                    mobile: false,
                    platform: '{ua_platform_str}',
                    getHighEntropyValues: function(hints) {{
                        const r = {{}};
                        if (hints.includes('platform')) r.platform = '{ua_platform_str}';
                        if (hints.includes('platformVersion')) r.platformVersion = '15.0.0';
                        if (hints.includes('architecture')) r.architecture = 'x86';
                        if (hints.includes('model')) r.model = '';
                        if (hints.includes('uaFullVersion')) r.uaFullVersion = '{chrome_major_ver}.0.0.0';
                        if (hints.includes('fullVersionList')) r.fullVersionList = [
                            {{ brand: 'Not)A;Brand', version: '8.0.0.0' }},
                            {{ brand: 'Chromium', version: '{chrome_major_ver}.0.0.0' }},
                            {{ brand: 'Google Chrome', version: '{chrome_major_ver}.0.0.0' }}
                        ];
                        if (hints.includes('bitness')) r.bitness = '64';
                        if (hints.includes('brands')) r.brands = _brands;
                        return Promise.resolve(r);
                    }},
                    toJSON: function() {{ return {{ brands: _brands, mobile: false, platform: '{ua_platform_str}' }}; }}
                }};
                try {{ Object.defineProperty(navigator, 'userAgentData', {{ get: () => _uaData }}); }} catch(e) {{}}
            }})();

            // Fix #9: Intl.DateTimeFormat — always return profile timezone, even in iframes
            (function() {{
                const _OrigDTF = Intl.DateTimeFormat;
                function _PatchedDTF(locales, options) {{
                    if (!options) options = {{}};
                    if (!options.timeZone) options.timeZone = '{profile_tz}';
                    return new _OrigDTF(locales, options);
                }}
                _PatchedDTF.prototype = _OrigDTF.prototype;
                _PatchedDTF.supportedLocalesOf = _OrigDTF.supportedLocalesOf.bind(_OrigDTF);
                try {{ Intl.DateTimeFormat = _PatchedDTF; }} catch(e) {{}}
            }})();

            // Fix #8: speechSynthesis.getVoices() — return OS-matching voice list
            (function() {{
                const _fakeVoices = {speech_voices_json}.map(function(name, i) {{
                    return {{ voiceURI: name, name: name, lang: 'en-US', localService: true, default: i === 0 }};
                }});
                if (window.speechSynthesis) {{
                    try {{
                        Object.defineProperty(window.speechSynthesis, 'getVoices', {{
                            value: function() {{ return _fakeVoices; }}, writable: false, configurable: true
                        }});
                    }} catch(e) {{}}
                    window.addEventListener('voiceschanged', function() {{}}, {{ once: true }});
                }}
            }})();

            // Fix #10: performance.timeOrigin — add session noise to break bot-timing analysis
            (function() {{
                try {{
                    const _realTO = performance.timeOrigin;
                    const _toNoise = (Math.random() * 200) - 100; // +/- 100 ms
                    Object.defineProperty(performance, 'timeOrigin', {{
                        get: function() {{ return _realTO + _toNoise; }}, configurable: true
                    }});
                }} catch(e) {{}}
                const _origNow = performance.now.bind(performance);
                const _nowNoise = (Math.random() - 0.5) * 10; // +/- 5 ms
                performance.now = function() {{ return _origNow() + _nowNoise; }};
            }})();

            // Fix #11: CSS media queries — force dark preference + no reduced-motion
            (function() {{
                const _origMM = window.matchMedia.bind(window);
                window.matchMedia = function(query) {{
                    const q = (query || '').toLowerCase();
                    const _base = {{ onchange: null, media: query,
                        addListener: function() {{}}, removeListener: function() {{}},
                        addEventListener: function() {{}}, removeEventListener: function() {{}},
                        dispatchEvent: function() {{ return true; }}
                    }};
                    if (q.includes('prefers-color-scheme: dark'))        return Object.assign({{}}, _base, {{ matches: true }});
                    if (q.includes('prefers-color-scheme: light'))       return Object.assign({{}}, _base, {{ matches: false }});
                    if (q.includes('prefers-reduced-motion: reduce'))    return Object.assign({{}}, _base, {{ matches: false }});
                    if (q.includes('prefers-reduced-motion: no-preference')) return Object.assign({{}}, _base, {{ matches: true }});
                    return _origMM(query);
                }};
            }})();

            Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
        """

        # Additional stealth scripts (apply to all future pages in context)
        await context.add_init_script(spoofing_script)
        
        # The first page is already created at 'about:blank'. 
        # add_init_script only runs on navigation, and about:blank often bypasses it.
        # Force a real navigation to a data URI to guarantee script injection before returning to the user.
        try:
            await page.goto("data:text/html,<html><body>Stealth Initialized</body></html>", wait_until="commit", timeout=15000)
        except Exception:
            pass

        active_browsers[profile_id] = {
            "playwright": playwright,
            "context": context,
            "page": page
        }

        # Handle manual window close by user
        context.on("close", lambda ctx: active_browsers.pop(profile_id, None))

        from backend.logging_config import logger
        logger.info(f"Browser launched for profile {profile_id}", extra={"profile_id": profile_id})

        res = {"status": "success", "message": "Browser launched successfully"}
        if proxy_warning:
            res["warning"] = proxy_warning
            logger.warning(proxy_warning)
            
        return res
    finally:
        await lock_manager.release(profile_id)

async def close_profile(profile_id: str):
    from backend.lock_manager import lock_manager
    await lock_manager.acquire(profile_id)
    try:
        if profile_id in active_browsers:
            browser_data = active_browsers[profile_id]
            try:
                await browser_data["context"].close()
            except:
                pass
            try:
                await browser_data["playwright"].stop()
            except:
                pass
            del active_browsers[profile_id]
            from backend.logging_config import logger
            logger.info(f"Browser closed for profile {profile_id}", extra={"profile_id": profile_id})
            return {"status": "success", "message": "Browser closed"}
        return {"status": "error", "message": "Browser is not running"}
    finally:
        await lock_manager.release(profile_id)

def is_profile_running(profile_id: str):
    return profile_id in active_browsers

async def get_profile_cookies(profile_id: str):
    is_running = profile_id in active_browsers
    if not is_running:
        res = await launch_profile(profile_id, force_headless=True)
        if res.get("status") == "error":
            return {"status": "error", "message": res.get("message", "Launch failed")}
            
    try:
        context = active_browsers[profile_id]["context"]
        cookies = await context.cookies()
        return {"status": "success", "cookies": cookies}
    finally:
        if not is_running:
            await close_profile(profile_id)

async def set_profile_cookies(profile_id: str, cookies: list):
    is_running = profile_id in active_browsers
    if not is_running:
        res = await launch_profile(profile_id, force_headless=True)
        if res.get("status") == "error":
            return {"status": "error", "message": res.get("message", "Launch failed")}
            
    try:
        context = active_browsers[profile_id]["context"]
        await context.add_cookies(cookies)
        return {"status": "success"}
    finally:
        if not is_running:
            await close_profile(profile_id)
