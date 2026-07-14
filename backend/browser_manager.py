import json
import math
from playwright.async_api import async_playwright
import playwright_stealth
from backend.profile_manager import profile_manager
from backend.ai_scanner import ai_scanner

active_browsers = {}

def _generate_spoofing_js(profile_config: dict) -> str:
    import re as _re
    advanced = profile_config.get('advanced', {}) or {}
    if not isinstance(advanced, dict): advanced = {}
    cpu_cores = advanced.get('cpu_cores') or 4
    memory_gb = advanced.get('memory_gb') or 8
    try: cpu_cores = int(cpu_cores)
    except: cpu_cores = 4
    try: memory_gb = int(memory_gb)
    except: memory_gb = 8
    canvas_noise = advanced.get('canvas_noise', True)
    webgl_noise = advanced.get('webgl_noise', True)
    audio_noise = advanced.get('audio_noise', True)
    screen_res = advanced.get('screen_resolution', '1920x1080')
    webrtc_mode = advanced.get('webrtc_mode', 'altered')
    _proxy_cfg = profile_config.get('proxy')
    _has_proxy = bool(_proxy_cfg and _proxy_cfg.get('server'))
    _proxy_ip = None
    if _has_proxy:
        try:
            _srv = _proxy_cfg.get('server', '')
            _srv_clean = _srv.split('://', 1)[-1] if '://' in _srv else _srv
            _proxy_ip = _srv_clean.split(':')[0]
        except Exception:
            _proxy_ip = None
    ai_webgl_vendor = advanced.get('webgl_vendor', 'Google Inc. (NVIDIA)')
    ai_webgl_renderer = advanced.get('webgl_renderer', 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)')
    ua = profile_config.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36')
    profile_locale = profile_config.get('locale', advanced.get('locale', 'en-US'))
    profile_languages = advanced.get('languages', [profile_locale, profile_locale.split('-')[0]] if '-' in profile_locale else [profile_locale])
    if 'en' not in profile_languages: profile_languages.append('en')
    languages_js = json.dumps(profile_languages)
    _pid_raw = profile_config.get('id', 'ffffffff-0000-0000-0000-000000000000')
    _seed = int(_pid_raw.replace('-', '')[:8], 16)
    canvas_r_offset = advanced.get('canvas_r_offset', (_seed % 250))
    canvas_g_offset = advanced.get('canvas_g_offset', ((_seed * 7) % 250))
    canvas_b_offset = advanced.get('canvas_b_offset', ((_seed * 13) % 250))
    _dom_noise = ((_seed % 1000) + 1) / 10_000_000.0
    device_pixel_ratio = advanced.get('device_pixel_ratio', 1.0)
    connection_downlink = advanced.get('connection_downlink', round((_seed % 30) + 15 + ((_seed % 7) / 10.0), 1))
    connection_rtt = advanced.get('connection_rtt', round(((_seed % 50) + 25) / 25) * 25)
    ua_os_val = advanced.get('os', 'Windows')
    ua_platform_str = 'macOS' if ua_os_val == 'Mac' else ('Linux' if ua_os_val == 'Linux' else 'Windows')
    _ua_raw = profile_config.get('user_agent', 'Chrome/135')
    _chrome_m = _re.search(r'Chrome/(\d+)', _ua_raw)
    chrome_major_ver = _chrome_m.group(1) if _chrome_m else '135'
    profile_tz = profile_config.get('timezone', 'UTC')
    if ua_os_val == 'Mac':
        speech_voices_json = '["Alex","Samantha","Victoria","Daniel","Karen","Moira","Tessa"]'
    else:
        speech_voices_json = '["Microsoft David Desktop","Microsoft Zira Desktop","Microsoft Mark Desktop","Microsoft David - English (United States)"]'
    _fonts_list = advanced.get('fonts', [])
    fonts_json = json.dumps(_fonts_list) if _fonts_list else '[]'
    _plugins_list = advanced.get('plugins', ['Chrome PDF Plugin', 'Chrome PDF Viewer', 'Chromium PDF Viewer'])
    plugins_json = json.dumps(_plugins_list) if _plugins_list else '["Chrome PDF Plugin", "Chrome PDF Viewer", "Chromium PDF Viewer"]'

    # --- World-class anti-detect derived variables ---
    if ua_os_val == 'Mac':
        _navigator_platform = 'MacIntel'
    elif ua_os_val == 'Linux':
        _navigator_platform = 'Linux x86_64'
    else:
        _navigator_platform = 'Win32'

    if ua_os_val == 'Mac':
        _platform_version = '14.0.0'
    elif ua_os_val == 'Linux':
        _platform_version = ''
    else:
        _platform_version = '10.0.19041'

    memory_gb_pow2 = max(1, min(32, 2 ** round(math.log2(max(1, memory_gb)))))

    if ua_os_val == 'Mac':
        _navigator_oscpu = 'Intel Mac OS X 10_15_7'
    elif ua_os_val == 'Linux':
        _navigator_oscpu = 'Linux x86_64'
    else:
        _navigator_oscpu = 'Windows NT 10.0; Win64; x64'

    _scr_w_py = int(screen_res.split('x')[0]) if 'x' in screen_res else 1920
    _scr_h_py = int(screen_res.split('x')[1]) if 'x' in screen_res else 1080
    _screen_avail_height = _scr_h_py - (40 if ua_os_val == 'Windows' else 0)
    _screen_avail_top = 25 if ua_os_val == 'Mac' else 0

    _css_prefers_dark = advanced.get('prefers_dark_mode', True)
    _css_dark_str = 'true' if _css_prefers_dark else 'false'
    _css_light_str = 'false' if _css_prefers_dark else 'true'

    _profile_country = profile_locale.split('-')[-1].upper() if '-' in profile_locale else 'US'
    _geo_map = {
        'US': (39.8283, -98.5795), 'CA': (56.1304, -106.3468),
        'GB': (55.3781, -3.4360), 'DE': (51.1657, 10.4515),
        'FR': (46.2276, 2.2137), 'IT': (41.8719, 12.5674),
        'ES': (40.4637, -3.7492), 'PT': (39.3999, -8.2245),
        'NL': (52.1326, 5.2913), 'BE': (50.5039, 4.4699),
        'CH': (46.8182, 8.2275), 'AT': (47.5162, 14.5501),
        'SE': (60.1282, 18.6435), 'NO': (60.4720, 8.4689),
        'DK': (56.2639, 9.5018), 'FI': (61.9241, 25.7482),
        'PL': (51.9194, 19.1451), 'CZ': (49.8175, 15.4730),
        'RO': (45.9432, 24.9668), 'HU': (47.1625, 19.5033),
        'BG': (42.7339, 25.4858), 'GR': (39.0742, 21.8243),
        'TR': (38.9637, 35.2433), 'RU': (61.5240, 105.3188),
        'UA': (48.3794, 31.1656), 'BR': (-14.2350, -51.9253),
        'AR': (-38.4161, -63.6167), 'MX': (23.6345, -102.5528),
        'CL': (-35.6751, -71.5430), 'CO': (4.5709, -74.2973),
        'IN': (20.5937, 78.9629), 'CN': (35.8617, 104.1954),
        'JP': (36.2048, 138.2529), 'KR': (35.9078, 127.7669),
        'TW': (23.6978, 120.9605), 'TH': (15.8700, 100.9925),
        'VN': (14.0583, 108.2772), 'MY': (4.2105, 101.9758),
        'SG': (1.3521, 103.8198), 'ID': (-0.7893, 113.9213),
        'PH': (12.8797, 121.7740), 'AU': (-25.2744, 133.7751),
        'NZ': (-40.9006, 174.8860), 'ZA': (-30.5595, 22.9375),
        'NG': (9.0820, 8.6753), 'AE': (23.4241, 53.8478),
        'SA': (23.8859, 45.0792), 'IL': (31.0461, 34.8516),
        'PK': (30.3753, 69.3451), 'BD': (23.6850, 90.3563),
        'HK': (22.3193, 114.1694), 'IE': (53.1424, -7.6921),
    }
    _geo_lat, _geo_lon = _geo_map.get(_profile_country, (39.8283, -98.5795))

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
        const makeNative = (func, name, len) => {{
            const n = name || func.name || '';
            spoofedFunctions.set(func, n);
            try {{ Object.defineProperty(func, 'name', {{ value: n, configurable: true }}); }} catch(e) {{}}
            try {{ if (len !== undefined) Object.defineProperty(func, 'length', {{ value: len, configurable: true }}); }} catch(e) {{}}
            return func;
        }};

        const _nativeGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        const _nativeToDataURL = HTMLCanvasElement.prototype.toDataURL;

        // 1. Hardware Concurrency & Memory & User Agent
        Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {cpu_cores} }});
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {memory_gb_pow2} }});
        
        // Force User-Agent directly in JS context to ensure it overrides everything
        Object.defineProperty(navigator, 'userAgent', {{ get: () => "{ua}" }});
        
        // 1b. Navigator Languages (synced to proxy locale â€” defeats locale/timezone mismatch detection)
        // languages defined in section J (with freeze + configurable)

        // 2. Screen Resolution
        const screenRes = "{screen_res}".split('x');
        const width = parseInt(screenRes[0]);
        const height = parseInt(screenRes[1]);
        Object.defineProperty(window.screen, 'width', {{ get: () => width }});
        Object.defineProperty(window.screen, 'height', {{ get: () => height }});
        Object.defineProperty(window.screen, 'availWidth', {{ get: () => width }});
        Object.defineProperty(window.screen, 'availHeight', {{ get: () => {_screen_avail_height} }});
        Object.defineProperty(window.screen, 'availLeft', {{ get: () => 0 }});
        Object.defineProperty(window.screen, 'availTop', {{ get: () => {_screen_avail_top} }});

        // 3. WebRTC — Real IP is fine, just spoof the local candidate to hide system info
        const _hasProxy = {"true" if _has_proxy else "false"};
        const _proxyIP = {"'" + _proxy_ip + "'" if _proxy_ip else "null"};
        (function() {{
            const OrigPeerConn = window.RTCPeerConnection || window.webkitRTCPeerConnection || window.mozRTCPeerConnection;
            if (!OrigPeerConn) return;
            const PRIVATE_PREFIXES = [
                '.local', '192.168.', '10.',
                '172.16.','172.17.','172.18.','172.19.','172.20.',
                '172.21.','172.22.','172.23.','172.24.','172.25.',
                '172.26.','172.27.','172.28.','172.29.','172.30.','172.31.',
                'fe80:', 'fc00:', 'fd'
            ];
            const isPrivateIP = (c) => PRIVATE_PREFIXES.some(pfx => c.includes(pfx));
            const isSpoofedOrPublic = (c) => {{
                if (!c) return false;
                if (_hasProxy && _proxyIP) {{
                    const parts = c.split(' ');
                    for (let i = 0; i < parts.length; i++) {{
                        if (/^\\d{{1,3}}\\.\\d{{1,3}}\\.\\d{{1,3}}\\.\\d{{1,3}}$/.test(parts[i]) && parts[i] !== _proxyIP) {{
                            return true;
                        }}
                    }}
                }}
                return false;
            }};
            window.RTCPeerConnection = window.webkitRTCPeerConnection = window.mozRTCPeerConnection = function(...args) {{
                const pc = new OrigPeerConn(...args);
                const origAddEventListener = pc.addEventListener.bind(pc);
                pc.addEventListener = function(type, listener, options) {{
                    if (type === 'icecandidate') {{
                        const wrappedListener = function(event) {{
                            if (event.candidate && event.candidate.candidate) {{
                                const c = event.candidate.candidate;
                                if (isPrivateIP(c)) return;
                                if (isSpoofedOrPublic(c)) return;
                            }}
                            return listener.call(this, event);
                        }};
                        return origAddEventListener(type, wrappedListener, options);
                    }}
                    return origAddEventListener(type, listener, options);
                }};
                Object.defineProperty(pc, 'onicecandidate', {{
                    set: function(handler) {{ this._onicecandidate = handler; }},
                    get: function() {{ return this._onicecandidate; }}
                }});
                origAddEventListener('icecandidate', (event) => {{
                    if (event.candidate && event.candidate.candidate) {{
                        const c = event.candidate.candidate;
                        if (isPrivateIP(c)) return;
                        if (isSpoofedOrPublic(c)) return;
                    }}
                    if (pc._onicecandidate) pc._onicecandidate(event);
                }});
                return pc;
            }};
            window.RTCPeerConnection.prototype = OrigPeerConn.prototype;
            OrigPeerConn.prototype.constructor = window.RTCPeerConnection;
            makeNative(window.RTCPeerConnection, 'RTCPeerConnection');
        }})();

        // 4. Guaranteed Canvas Serialization Noise
        if ({str(canvas_noise).lower()}) {{
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = makeNative(function() {{
                try {{
                    const ctx = this.getContext('2d');
                    if (ctx) {{
                        const w = this.width, h = this.height;
                        if (w > 0 && h > 0) {{
                            const tc = document.createElement('canvas');
                            tc.width = w; tc.height = h;
                            const tctx = tc.getContext('2d');
                            tctx.drawImage(this, 0, 0);
                            const tctx2 = tc.getContext('2d');
                            tctx2.globalAlpha = 1.0;
                            tctx2.fillStyle = "rgb({canvas_r_offset}, {canvas_g_offset}, {canvas_b_offset})";
                            tctx2.fillRect(0, 0, 1, 1);
                            tctx2.fillStyle = "rgb({(canvas_r_offset + 37) % 256}, {(canvas_g_offset + 53) % 256}, {(canvas_b_offset + 71) % 256})";
                            tctx2.fillRect(({_seed} % 3) + 1, ({_seed} % 3) + 1, 1, 1);
                            return _nativeToDataURL.apply(tc, arguments);
                        }}
                    }}
                }} catch (e) {{}}
                return originalToDataURL.apply(this, arguments);
            }}, 'toDataURL');
            
            const originalToBlob = HTMLCanvasElement.prototype.toBlob;
            HTMLCanvasElement.prototype.toBlob = makeNative(function() {{
                try {{
                    const ctx = this.getContext('2d');
                    if (ctx) {{
                        const w = this.width, h = this.height;
                        if (w > 0 && h > 0) {{
                            const tc = document.createElement('canvas');
                            tc.width = w; tc.height = h;
                            const tctx = tc.getContext('2d');
                            tctx.drawImage(this, 0, 0);
                            const tctx2 = tc.getContext('2d');
                            tctx2.globalAlpha = 1.0;
                            tctx2.fillStyle = "rgb({canvas_r_offset}, {canvas_g_offset}, {canvas_b_offset})";
                            tctx2.fillRect(0, 0, 1, 1);
                            tctx2.fillStyle = "rgb({(canvas_r_offset + 37) % 256}, {(canvas_g_offset + 53) % 256}, {(canvas_b_offset + 71) % 256})";
                            tctx2.fillRect(({_seed} % 3) + 1, ({_seed} % 3) + 1, 1, 1);
                            return HTMLCanvasElement.prototype.toBlob.apply(tc, arguments);
                        }}
                    }}
                }} catch (e) {{}}
                return originalToBlob.apply(this, arguments);
            }}, 'toBlob', 1);
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

        // Fix #6: WebGL2 â€” must patch separately or detector reads real GPU via webgl2 context
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

            const _origGetSupportedExt1 = WebGLRenderingContext.prototype.getSupportedExtensions;
            WebGLRenderingContext.prototype.getSupportedExtensions = makeNative(function() {{
                return (_origGetSupportedExt1.apply(this, arguments) || []);
            }}, 'getSupportedExtensions');

            const _origGetShaderPrec = WebGLRenderingContext.prototype.getShaderPrecisionFormat;
            WebGLRenderingContext.prototype.getShaderPrecisionFormat = makeNative(function(s, p) {{
                return _origGetShaderPrec.apply(this, arguments);
            }}, 'getShaderPrecisionFormat');
        }}

        if ({str(webgl_noise).lower()} && typeof WebGL2RenderingContext !== 'undefined') {{
            const _origGetSupportedExt2 = WebGL2RenderingContext.prototype.getSupportedExtensions;
            WebGL2RenderingContext.prototype.getSupportedExtensions = makeNative(function() {{
                return (_origGetSupportedExt2.apply(this, arguments) || []);
            }}, 'getSupportedExtensions');

            const _origGetShaderPrec2 = WebGL2RenderingContext.prototype.getShaderPrecisionFormat;
            WebGL2RenderingContext.prototype.getShaderPrecisionFormat = makeNative(function(s, p) {{
                return _origGetShaderPrec2.apply(this, arguments);
            }}, 'getShaderPrecisionFormat');
        }}

        // 6. AudioContext Noise Injection (full coverage)
        if ({str(audio_noise).lower()}) {{
            try {{ Object.defineProperty(AudioContext.prototype, 'baseLatency', {{ get: function() {{ return 0.01; }} }}); }} catch(e) {{}}
            try {{ Object.defineProperty(OfflineAudioContext.prototype, 'baseLatency', {{ get: function() {{ return 0.01; }} }}); }} catch(e) {{}}
            const originalCreateOscillator = AudioContext.prototype.createOscillator;
            AudioContext.prototype.createOscillator = makeNative(function() {{
                const oscillator = originalCreateOscillator.apply(this, arguments);
                const originalStart = oscillator.start;
                oscillator.start = makeNative(function() {{
                    oscillator.frequency.value += ({_seed} % 1000) / 1000000;
                    return originalStart.apply(this, arguments);
                }}, 'start');
                return oscillator;
            }}, 'createOscillator');

            if (typeof OfflineAudioContext !== 'undefined') {{
                const _origOscOffline = OfflineAudioContext.prototype.createOscillator;
                OfflineAudioContext.prototype.createOscillator = makeNative(function() {{
                    const osc = _origOscOffline.apply(this, arguments);
                    const _origStart = osc.start;
                    osc.start = makeNative(function() {{
                        osc.frequency.value += ({_seed} % 1000) / 1000000;
                        return _origStart.apply(this, arguments);
                    }}, 'start');
                    return osc;
                }}, 'createOscillator');
            }}

            const _origGetByteFreq = AnalyserNode.prototype.getByteFrequencyData;
            AnalyserNode.prototype.getByteFrequencyData = makeNative(function(arr) {{
                _origGetByteFreq.apply(this, arguments);
                if (arr && arr.length > 0) {{
                    const idx = ({_seed}) % arr.length;
                    arr[idx] = Math.min(255, arr[idx] + 1);
                }}
            }}, 'getByteFrequencyData', 1);

            const _origGetByteTime = AnalyserNode.prototype.getByteTimeDomainData;
            AnalyserNode.prototype.getByteTimeDomainData = makeNative(function(arr) {{
                _origGetByteTime.apply(this, arguments);
                if (arr && arr.length > 0) {{
                    const idx = ({_seed}) % arr.length;
                    arr[idx] = Math.min(255, arr[idx] + 1);
                }}
            }}, 'getByteTimeDomainData', 1);

            const _origGetFloatFreq = AnalyserNode.prototype.getFloatFrequencyData;
            AnalyserNode.prototype.getFloatFrequencyData = makeNative(function(arr) {{
                _origGetFloatFreq.apply(this, arguments);
                if (arr && arr.length > 0) {{
                    const idx = ({_seed}) % arr.length;
                    arr[idx] += 0.0001;
                }}
            }}, 'getFloatFrequencyData', 1);

            const _origGetFloatTime = AnalyserNode.prototype.getFloatTimeDomainData;
            AnalyserNode.prototype.getFloatTimeDomainData = makeNative(function(arr) {{
                _origGetFloatTime.apply(this, arguments);
                if (arr && arr.length > 0) {{
                    const idx = ({_seed}) % arr.length;
                    arr[idx] += 0.00001;
                }}
            }}, 'getFloatTimeDomainData', 1);

            if (typeof webkitAudioContext !== 'undefined' && webkitAudioContext !== AudioContext) {{
                const _origWkOsc = webkitAudioContext.prototype.createOscillator;
                webkitAudioContext.prototype.createOscillator = makeNative(function() {{
                    const osc = _origWkOsc.apply(this, arguments);
                    const _origStart = osc.start;
                    osc.start = makeNative(function() {{
                        osc.frequency.value += ({_seed} % 1000) / 1000000;
                        return _origStart.apply(this, arguments);
                    }}, 'start');
                    return osc;
                }}, 'createOscillator');
            }}
        }}
        
        // 6b. AudioBuffer.getChannelData deterministic noise
        if ({str(audio_noise).lower()}) {{
            const _origGetChannelData = AudioBuffer.prototype.getChannelData;
            const _audioBufNoise = new WeakSet();
            AudioBuffer.prototype.getChannelData = makeNative(function(channel) {{
                const result = _origGetChannelData.apply(this, arguments);
                if (result && result.length > 0 && !_audioBufNoise.has(this)) {{
                    _audioBufNoise.add(this);
                    for (let i = 0; i < result.length; i++) {{
                        let h = ({_seed} * 2654435761 + i * 1597463007) >>> 0;
                        let noise = (h & 0xFFFF) / 65536.0 - 0.5;
                        noise *= 0.00001;
                        result[i] = Math.max(-1, Math.min(1, result[i] + noise));
                    }}
                }}
                return result;
            }}, 'getChannelData');
        }}

        // 6c. OfflineAudioContext.startRendering deterministic output
        if ({str(audio_noise).lower()} && typeof OfflineAudioContext !== 'undefined') {{
            const _origStartRendering = OfflineAudioContext.prototype.startRendering;
            OfflineAudioContext.prototype.startRendering = makeNative(function() {{
                return _origStartRendering.apply(this, arguments).then(function(buffer) {{
                    for (let ch = 0; ch < buffer.numberOfChannels; ch++) {{
                        const data = buffer.getChannelData(ch);
                        if (data && data.length > 0) {{
                            for (let i = 0; i < data.length; i++) {{
                                let h = ({_seed} * 2654435761 + i * 1597463007 + ch * 3266489917) >>> 0;
                                data[i] = ((h & 0xFFFF) / 32768.0) - 1.0;
                            }}
                        }}
                    }}
                    return buffer;
                }});
            }}, 'startRendering');
        }}

        // 7. Plugin and MimeType spoofing (proper prototypes)
        (function() {{
            var _pluginNames = {plugins_json};
            var _pluginData = _pluginNames.map(function(name, idx) {{
                return {{ name: name, description: name, filename: name + '.dll', length: 1,
                    item: function(i) {{ return i === 0 ? this : null; }},
                    namedItem: function(n) {{ return n === this.name ? this : null; }},
                    toString: function() {{ return '[object Plugin]'; }}
                }};
            }});
            var _pa = Object.create(PluginArray.prototype);
            _pluginData.forEach(function(p, i) {{ _pa[i] = p; }});
            Object.defineProperty(_pa, 'length', {{ get: function() {{ return _pluginData.length; }} }});
            _pa.item = function(i) {{ return this[i] || null; }};
            _pa.namedItem = function(name) {{
                for (var i = 0; i < _pluginData.length; i++) {{
                    if (_pluginData[i].name === name) return _pluginData[i];
                }}
                return null;
            }};
            _pa.refresh = function() {{}};
            _pa[Symbol.iterator] = function() {{ var _i = 0; return {{ next: function() {{ return _i < _pluginData.length ? {{ value: _pluginData[_i++], done: false }} : {{ done: true }}; }} }}; }};
            try {{ Object.defineProperty(navigator, 'plugins', {{ get: function() {{ return _pa; }} }}); }} catch(e) {{}}

            var _mimeTypeData = [
                {{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format',
                   enabledPlugin: _pa[0] || null }}
            ];
            var _ma = Object.create(MimeTypeArray.prototype);
            _mimeTypeData.forEach(function(m, i) {{ _ma[i] = m; }});
            Object.defineProperty(_ma, 'length', {{ get: function() {{ return _mimeTypeData.length; }} }});
            _ma.item = function(i) {{ return this[i] || null; }};
            _ma.namedItem = function(name) {{
                for (var i = 0; i < _mimeTypeData.length; i++) {{
                    if (_mimeTypeData[i].type === name) return _mimeTypeData[i];
                }}
                return null;
            }};
            try {{ Object.defineProperty(navigator, 'mimeTypes', {{ get: function() {{ return _ma; }} }}); }} catch(e) {{}}
        }})();
        
        // 8. Advanced Subpixel DOM Rect Noise (Defeats CreepJS Font/Layout fingerprinting)
        // MED-01 FIX: Use deterministic per-profile seed so value is consistent within a
        // session but unique across profiles. Eliminates detectable per-reload variance.
        const noise = {_dom_noise};
        const originalGetClientRects = Element.prototype.getClientRects;
        Element.prototype.getClientRects = function() {{
            const rects = originalGetClientRects.apply(this, arguments);
            for (let i = 0; i < rects.length; i++) {{
                const r = rects[i];
                const _rx = r.x, _ry = r.y, _rw = r.width, _rh = r.height;
                const mx = _rx + noise, my = _ry + noise;
                Object.defineProperties(r, {{
                    x: {{ get: () => mx }},
                    y: {{ get: () => my }},
                    width: {{ get: () => _rw }},
                    height: {{ get: () => _rh }},
                    top: {{ get: () => my }},
                    right: {{ get: () => mx + _rw }},
                    bottom: {{ get: () => my + _rh }},
                    left: {{ get: () => mx }}
                }});
            }}
            return rects;
        }};
        
        const originalGetBoundingClientRect = Element.prototype.getBoundingClientRect;
        Element.prototype.getBoundingClientRect = function() {{
            const r = originalGetBoundingClientRect.apply(this, arguments);
            const _rx = r.x, _ry = r.y, _rw = r.width, _rh = r.height;
            const mx = _rx + noise, my = _ry + noise;
            Object.defineProperties(r, {{
                x: {{ get: () => mx }},
                y: {{ get: () => my }},
                width: {{ get: () => _rw }},
                height: {{ get: () => _rh }},
                top: {{ get: () => my }},
                right: {{ get: () => mx + _rw }},
                bottom: {{ get: () => my + _rh }},
                left: {{ get: () => mx }}
            }});
            return r;
        }};

        // 8b. Canvas getImageData + OffscreenCanvas Noise
        if ({str(canvas_noise).lower()}) {{
            CanvasRenderingContext2D.prototype.getImageData = makeNative(function() {{
                const result = _nativeGetImageData.apply(this, arguments);
                if (result && result.data && result.data.length > 0) {{
                    const data = result.data;
                    const idx = ({_seed}) % Math.max(1, data.length - 3);
                    data[idx] = Math.min(255, data[idx] + 1);
                    data[idx + 1] = Math.min(255, data[idx + 1] + 1);
                    data[idx + 2] = Math.min(255, data[idx + 2] + 1);
                }}
                return result;
            }}, 'getImageData');

            if (typeof OffscreenCanvas !== 'undefined' && typeof OffscreenCanvasRenderingContext2D !== 'undefined') {{
                const _origOSGetImageData = OffscreenCanvasRenderingContext2D.prototype.getImageData;
                OffscreenCanvasRenderingContext2D.prototype.getImageData = makeNative(function() {{
                    const result = _origOSGetImageData.apply(this, arguments);
                    if (result && result.data && result.data.length > 0) {{
                        const data = result.data;
                        const idx = ({_seed}) % Math.max(1, data.length - 3);
                        data[idx] = Math.min(255, data[idx] + 1);
                        data[idx + 1] = Math.min(255, data[idx + 1] + 1);
                        data[idx + 2] = Math.min(255, data[idx + 2] + 1);
                    }}
                    return result;
                }}, 'getImageData');
            }}
        }}

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
                        const noiseIndex = {_seed} % Math.min(arguments[6].length, 100);
                        if (arguments[6][noiseIndex] >= 255) arguments[6][noiseIndex] -= 1; else arguments[6][noiseIndex] += 1;
                    }}
                }};
            }};
            injectReadPixelsNoise(WebGLRenderingContext);
            if (typeof WebGL2RenderingContext !== 'undefined') {{
                injectReadPixelsNoise(WebGL2RenderingContext);
            }}
        }}

        // 11. Battery Status Spoofing
        // MED-10 FIX: Seed battery values from profile seed so they are consistent
        // across page loads and not re-randomized (detectable signal).
        const fakeBattery = {{
            charging: false,
            chargingTime: Infinity,
            dischargingTime: {int((_seed % 12) + 4) * 3600},
            level: {round(0.75 + ((_seed % 25) / 100.0), 2)},
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
        try {{ Object.defineProperty(screen, 'orientation', {{ get: function() {{ return {{ type: 'landscape-primary', angle: 0, onchange: null, addEventListener: function() {{}}, removeEventListener: function() {{}}, dispatchEvent: function() {{ return true; }} }}; }}, configurable: true }}); }} catch(e) {{}}
        Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {device_pixel_ratio} }});
        try {{ Object.defineProperty(window, 'screenLeft', {{ get: () => 0, configurable: true }}); }} catch(e) {{}}
        try {{ Object.defineProperty(window, 'screenTop', {{ get: () => 0, configurable: true }}); }} catch(e) {{}}

        // Fix #1: navigator.connection â€” spoof as residential WiFi (defeats proxy-type detection)
        (function() {{
            const _fakeConn = {{
                effectiveType: '4g',
                downlink: {connection_downlink},
                rtt: {connection_rtt},
                saveData: false,
                type: 'wifi',
                metered: false,
                downlinkMax: 100,
                onchange: null,
                addEventListener: function(type, listener, opts) {{}},
                removeEventListener: function(type, listener, opts) {{}},
                dispatchEvent: function(e) {{ return true; }}
            }};
            try {{ Object.defineProperty(navigator, 'connection', {{ get: () => _fakeConn }}); }} catch(e) {{}}
            try {{ Object.defineProperty(navigator, 'mozConnection', {{ get: () => undefined }}); }} catch(e) {{}}
            try {{ Object.defineProperty(navigator, 'webkitConnection', {{ get: () => undefined }}); }} catch(e) {{}}
        }})();

        // Fix #2: navigator.userAgentData â€” defeat Chrome version mismatch detection
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
                    if (hints.includes('platformVersion')) r.platformVersion = '{_platform_version}';
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

        // Fix #9: Intl.DateTimeFormat â€” always return profile timezone, even in iframes
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

        // Fix #8: speechSynthesis.getVoices() â€” return OS-matching voice list
        (function() {{
            const _fakeVoices = {speech_voices_json}.map(function(name, i) {{
                const _voiceLang = '{profile_locale}';
                return {{ voiceURI: name, name: name, lang: _voiceLang, localService: true, default: i === 0 }};
            }});
            if (window.speechSynthesis) {{
                try {{
                    Object.defineProperty(window.speechSynthesis, 'getVoices', {{
                        value: function() {{ return _fakeVoices; }}, writable: false, configurable: true
                    }});
                }} catch(e) {{}}
                try {{
                    Object.defineProperty(window.speechSynthesis, 'voice', {{
                        get: function() {{ return _fakeVoices.length > 0 ? _fakeVoices[0] : null; }},
                        configurable: true
                    }});
                }} catch(e) {{}}
                try {{
                    Object.defineProperty(window.speechSynthesis, 'onvoiceschanged', {{
                        set: function() {{}},
                        get: function() {{ return null; }},
                        configurable: true
                    }});
                }} catch(e) {{}}
            }}
        }})();

                        // Fix #10: performance.timeOrigin + performance.timing + performance.now jitter
        // CRITICAL: All three must be consistent to pass whoer.net timing checks
        (function() {{
            try {{
                const _realTO = performance.timeOrigin;
                const _toNoise = ({(_seed % 200) - 100}); // Seeded +/- 100 ms
                Object.defineProperty(performance, 'timeOrigin', {{
                    get: function() {{ return _realTO + _toNoise; }}, configurable: true
                }});
                // CRITICAL: Override performance.timing to match timeOrigin
                if (performance.timing) {{
                    try {{
                        Object.defineProperty(performance.timing, 'navigationStart', {{
                            get: function() {{ return Math.floor(_realTO + _toNoise); }}, configurable: true
                        }});
                    }} catch(e) {{}}
                }}
            }} catch(e) {{}}
            // Per-call micro-jitter using seeded LCG (not constant offset)
            const _rngState = new Uint32Array([{_seed}]);
            function _microJitter() {{
                _rngState[0] = (_rngState[0] * 1664525 + 1013904223) >>> 0;
                return ((_rngState[0] >>> 16) & 0xFF) / 65536.0 - 0.5;
            }}
            const _origNow = performance.now.bind(performance);
            performance.now = function() {{
                return _origNow() + _microJitter();
            }};
        }})();

// CRITICAL FIX: Intercept Navigation Timing API to match spoofed timeOrigin
        (function() {{
            try {{
                var _spoofedNavStart = Math.floor(performance.timeOrigin);
                var _origGetEntries = performance.getEntriesByType.bind(performance);
                performance.getEntriesByType = function(type) {{
                    var entries = _origGetEntries(type);
                    if (type === 'navigation') {{
                        entries = entries.map(function(e) {{
                            var patched = Object.create(e);
                            Object.defineProperty(patched, 'startTime', {{ get: function() {{ return 0; }} }});
                            Object.defineProperty(patched, 'redirectStart', {{ get: function() {{ return 0; }} }});
                            Object.defineProperty(patched, 'redirectEnd', {{ get: function() {{ return 0; }} }});
                            Object.defineProperty(patched, 'fetchStart', {{ get: function() {{ return 1; }} }});
                            Object.defineProperty(patched, 'domainLookupStart', {{ get: function() {{ return 2; }} }});
                            Object.defineProperty(patched, 'domainLookupEnd', {{ get: function() {{ return 3; }} }});
                            Object.defineProperty(patched, 'connectStart', {{ get: function() {{ return 4; }} }});
                            Object.defineProperty(patched, 'connectEnd', {{ get: function() {{ return 5; }} }});
                            Object.defineProperty(patched, 'responseStart', {{ get: function() {{ return 50; }} }});
                            Object.defineProperty(patched, 'responseEnd', {{ get: function() {{ return 80; }} }});
                            Object.defineProperty(patched, 'domInteractive', {{ get: function() {{ return 120; }} }});
                            Object.defineProperty(patched, 'domContentLoadedEventEnd', {{ get: function() {{ return 150; }} }});
                            Object.defineProperty(patched, 'domComplete', {{ get: function() {{ return 200; }} }});
                            Object.defineProperty(patched, 'loadEventEnd', {{ get: function() {{ return 250; }} }});
                            return patched;
                        }});
                    }}
                    return entries;
                }};
            }} catch(e) {{}}
        }})();

        // Fix #11: CSS media queries â€” force dark preference + no reduced-motion
        (function() {{
            const _origMM = window.matchMedia.bind(window);
            window.matchMedia = function(query) {{
                const q = (query || '').toLowerCase();
                const _base = {{ onchange: null, media: query,
                    addListener: function() {{}}, removeListener: function() {{}},
                    addEventListener: function() {{}}, removeEventListener: function() {{}},
                    dispatchEvent: function() {{ return true; }}
                }};
                if (q.includes('prefers-color-scheme: dark'))        return Object.assign({{}}, _base, {{ matches: {_css_dark_str} }});
                if (q.includes('prefers-color-scheme: light'))       return Object.assign({{}}, _base, {{ matches: {_css_light_str} }});
                if (q.includes('prefers-reduced-motion: reduce'))    return Object.assign({{}}, _base, {{ matches: false }});
                if (q.includes('prefers-reduced-motion: no-preference')) return Object.assign({{}}, _base, {{ matches: true }});
                return _origMM(query);
            }};
        }})();

        try {{ Object.defineProperty(navigator, 'webdriver', {{ get: () => false, configurable: false, enumerable: false }}); }} catch(e) {{}}
        try {{ delete Object.getPrototypeOf(navigator).webdriver; }} catch(e) {{}}
        Object.defineProperty(navigator, 'platform', {{ get: () => '{_navigator_platform}' }});
        // Do NOT define navigator.oscpu — it's Firefox-only and its presence in Chrome is a detection signal

        Object.defineProperty(navigator, 'product', {{ get: () => 'Gecko' }});
        Object.defineProperty(navigator, 'vendor', {{ get: () => 'Google Inc.' }});
        // Headless Chrome detection bypass: window.chrome object
        if (!window.chrome) {{
            window.chrome = {{}};
        }}
        if (!window.chrome.runtime) {{
            window.chrome.runtime = {{
                id: undefined,
                PlatformOs: {{ MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' }},
                connect: function() {{ var _p = {{ name: '', onMessage: {{ addListener: function(){{}} }}, onDisconnect: {{ addListener: function(){{}} }}, postMessage: function(){{}}, disconnect: function(){{}} }}; return _p; }},
                sendMessage: function() {{}},
                onMessage: {{ addListener: function(){{}} }},
                onConnect: {{ addListener: function(){{}} }}
            }};
        }}
        // Headless Chrome detection bypass: chrome.loadTimes and chrome.csi
        if (!window.chrome.loadTimes) {{
            window.chrome.loadTimes = function() {{
                var _navStart = performance.timeOrigin / 1000;
                var _now = Date.now() / 1000;
                return {{
                    requestTime: _navStart,
                    startLoadTime: _navStart + 0.01,
                    commitLoadTime: _navStart + 0.05,
                    finishDocumentLoadTime: _navStart + 0.3,
                    finishLoadTime: _navStart + 0.5,
                    firstPaintTime: _navStart + 0.2
                }};
            }};
        }}
        if (!window.chrome.csi) {{
            window.chrome.csi = function() {{
                return {{
                    onloadT: performance.timing.navigationStart / 1000,
                    pageT: performance.now(),
                    startE: performance.timing.navigationStart / 1000
                }};
            }};
        }}
        if (!window.chrome.app) {{
            window.chrome.app = {{
                isInstalled: false,
                InstallState: {{ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }},
                RunningState: {{ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }},
                getDetails: function() {{ return null; }},
                getIsInstalled: function() {{ return false; }},
                installState: function(cb) {{ if (cb) cb('not_installed'); }},
                runningState: function() {{ return 'cannot_run'; }}
            }};
        }}
        try {{ Object.defineProperty(document, '$cdc_asdjutasowyfls', {{ get: () => undefined }}); }} catch(e) {{}}
        try {{
            var _cdpProps = Object.getOwnPropertyNames(document).filter(function(p) {{ return p.includes('$cdc_') || p.includes('__playwright'); }});
            _cdpProps.forEach(function(p) {{ try {{ delete document[p]; }} catch(e) {{}} }});
        }} catch(e) {{}}
        // Headless Chrome detection bypass: window.outerWidth/outerHeight
        Object.defineProperty(window, 'outerWidth', {{ get: () => width }});
        Object.defineProperty(window, 'outerHeight', {{ get: () => height + 85 }});
        // Headless Chrome detection bypass: performance.memory
        if (!performance.memory) {{
            Object.defineProperty(performance, 'memory', {{
                get: () => ({{
                    jsHeapSizeLimit: {cpu_cores} <= 4 ? 2147483648 : 4294705152,
                    totalJSHeapSize: 125829120 + Math.floor(({_seed} % 50) * 1000000),
                    usedJSHeapSize: 50000000 + Math.floor(({_seed} % 30) * 1000000)
                }})
            }});
        }}
        // Headless Chrome detection bypass: document.visibilityState
        Object.defineProperty(document, 'visibilityState', {{ get: () => 'visible' }});
        Object.defineProperty(document, 'hidden', {{ get: () => false }});
        // Headless Chrome detection bypass: Notification.permission
        try {{ Object.defineProperty(Notification, 'permission', {{ get: () => 'default' }}); }} catch(e) {{}}
        // Headless Chrome detection bypass: navigator.maxTouchPoints (0 on desktop)
        Object.defineProperty(navigator, 'maxTouchPoints', {{ get: () => 0 }});
        // navigator.cookieEnabled
        Object.defineProperty(navigator, 'cookieEnabled', {{ get: () => true }});
        try {{ navigator.__proto__.doNotTrack = '1'; }} catch(e) {{}}
        try {{ Object.defineProperty(navigator, 'doNotTrack', {{ get: () => '1', set: () => {{}}, configurable: true, enumerable: true }}); }} catch(e) {{}}
        try {{ Object.defineProperty(navigator, 'globalPrivacyControl', {{ get: () => true, configurable: true }}); }} catch(e) {{}}

        // navigator.mediaDevices.enumerateDevices() spoofing
        (function() {{
            if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
                const _origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
                navigator.mediaDevices.enumerateDevices = makeNative(async function() {{
                    const realDevices = await _origEnum();
                    if (realDevices.length === 0) {{
                        var _ai = 'default_audio_in_' + ({_seed} % 1000);
                        var _ao = 'default_audio_out_' + ({_seed} % 1000);
                        var _vi = 'default_video_' + ({_seed} % 1000);
                        var _gr = 'group_' + ({_seed} % 100);
                        return [
                            {{ deviceId: _ai, groupId: _gr, kind: 'audioinput', label: '' }},
                            {{ deviceId: _ao, groupId: _gr, kind: 'audiooutput', label: '' }},
                            {{ deviceId: _vi, groupId: _gr, kind: 'videoinput', label: '' }}
                        ];
                    }}
                    return realDevices.map(function(d) {{
                        return {{ deviceId: d.deviceId, groupId: d.groupId, kind: d.kind, label: '' }};
                    }});
                }}, 'enumerateDevices');
            }}
        }})();

        // navigator.permissions.query() spoofing
        (function() {{
            const _origQuery = navigator.permissions.query.bind(navigator.permissions);
            const _knownPerms = {{
                'geolocation': 'prompt', 'notifications': 'default', 'push': 'denied',
                'midi': 'granted', 'camera': 'prompt', 'microphone': 'prompt',
                'speaker': 'prompt', 'device-info': 'granted', 'background-fetch': 'granted',
                'background-sync': 'granted', 'bluetooth': 'prompt', 'persistent-storage': 'prompt',
                'ambient-light-sensor': 'granted', 'accelerometer': 'granted', 'gyroscope': 'granted',
                'magnetometer': 'granted', 'clipboard-read': 'prompt', 'clipboard-write': 'granted',
                'payment-handler': 'granted', 'idle-detection': 'prompt',
                'periodic-background-sync': 'prompt', 'screen-wake-lock': 'prompt', 'nfc': 'prompt'
            }};
            navigator.permissions.query = makeNative(async function(desc) {{
                const name = desc && desc.name ? desc.name : '';
                if (_knownPerms.hasOwnProperty(name)) {{
                    const state = _knownPerms[name];
                    return {{ state: state, status: state, onchange: null,
                        addEventListener: function() {{}}, removeEventListener: function() {{}},
                        dispatchEvent: function() {{ return true; }} }};
                }}
                return _origQuery(desc);
            }}, 'query');
        }})();

        // navigator.geolocation spoofing
        (function() {{
            const _fakeCoords = {{ latitude: {_geo_lat}, longitude: {_geo_lon}, altitude: null, accuracy: 150, altitudeAccuracy: null, heading: null, speed: null }};
            const _fakeGeo = {{
                getCurrentPosition: function(success, error, options) {{
                    setTimeout(function() {{ success({{ coords: _fakeCoords, timestamp: Date.now() }}); }}, 100);
                }},
                watchPosition: function(success, error, options) {{
                    success({{ coords: _fakeCoords, timestamp: Date.now() }}); return 1;
                }},
                clearWatch: function() {{}},
                onchange: null, onerror: null,
                addEventListener: function() {{}}, removeEventListener: function() {{}},
                dispatchEvent: function() {{ return true; }}
            }};
            try {{ Object.defineProperty(navigator, 'geolocation', {{ get: () => _fakeGeo }}); }} catch(e) {{}}
        }})();

        // Service Worker + Crypto API consistency
        (function() {{
            if (window.crypto && window.crypto.subtle) {{
                const _origDigest = window.crypto.subtle.digest.bind(window.crypto.subtle);
                window.crypto.subtle.digest = makeNative(function() {{ return _origDigest.apply(this, arguments); }}, 'digest');
            }}
            if (!navigator.serviceWorker) {{
                try {{
                    Object.defineProperty(navigator, 'serviceWorker', {{
                        get: () => ({{ ready: Promise.resolve({{ controller: null }}), controller: null,
                            register: function() {{ return Promise.resolve({{}}); }},
                            getRegistrations: function() {{ return Promise.resolve([]); }},
                            addEventListener: function() {{}}, removeEventListener: function() {{}},
                            dispatchEvent: function() {{ return true; }} }})
                    }});
                }} catch(e) {{}}
            }}
        }})();


        // Fix #12: Font Fingerprint Spoofing
        const _spoofedFonts = {fonts_json};
        if (_spoofedFonts.length > 0) {{
            var _fontSet = new Set(_spoofedFonts.map(function(f) {{ return f.toLowerCase(); }}));
            var _FontFace = window.FontFace;
            if (_FontFace) {{
                var _OrigFontFace = _FontFace;
                window.FontFace = function(family, source, descriptors) {{
                    var ff = new _OrigFontFace(family, source, descriptors);
                    // Don't add unknown fonts - real browsers don't gain system fonts during session
                    return ff;
                }};
                window.FontFace.prototype = _OrigFontFace.prototype;
            }}
            var _origCheck = document.fonts.check.bind(document.fonts);
            document.fonts.check = function(font, text) {{
                var match = font.match(/"[^"]+"|'[^']+'|(\\S+)/);
                var family = (match && (match[1] || match[2] || match[3])) || "";
                return _fontSet.has(family.toLowerCase());
            }};
            Object.defineProperty(document.fonts, "size", {{
                get: function() {{ return _spoofedFonts.length; }}
            }});
            var _origForEach = document.fonts.forEach.bind(document.fonts);
            document.fonts.forEach = function(callback, thisArg) {{
                _spoofedFonts.forEach(function(f, i) {{
                    callback.call(thisArg, {{ family: f, style: "normal", weight: "400", status: "loaded" }}, f, i);
                }});
            }};
            document.fonts.keys = function() {{
                var _kf = _spoofedFonts.slice(); var _ki = 0;
                return {{ next: function() {{ return _ki < _kf.length ? {{ value: _kf[_ki++], done: false }} : {{ done: true }}; }}, [Symbol.iterator]: function() {{ return this; }} }};
            }};
            document.fonts.values = function() {{
                var _vf = _spoofedFonts.map(function(f) {{ return {{ family: f, style: "normal", weight: "400", status: "loaded" }}; }}); var _vi = 0;
                return {{ next: function() {{ return _vi < _vf.length ? {{ value: _vf[_vi++], done: false }} : {{ done: true }}; }}, [Symbol.iterator]: function() {{ return this; }} }};
            }};
            document.fonts.entries = function() {{
                var _ef = _spoofedFonts.map(function(f, i) {{ return [i, {{ family: f, style: "normal", weight: "400", status: "loaded" }}]; }}); var _ei = 0;
                return {{ next: function() {{ return _ei < _ef.length ? {{ value: _ef[_ei++], done: false }} : {{ done: true }}; }}, [Symbol.iterator]: function() {{ return this; }} }};
            }};
            document.fonts[Symbol.iterator] = document.fonts.values;
        }}

        // A. WebGL Extended Parameters (GPU fingerprint normalization)
        if ({str(webgl_noise).lower()}) {{
            // GPU-matched texture limits (realistic per GPU model)
            var _texSize = _seed % 2 === 0 ? 16384 : 8192;
            var _cubeSize = _seed % 2 === 0 ? 8192 : 4096;
            var _maxAniso = _seed % 4 + 4;
            var _maxViewport = _seed % 2 === 0 ? [16384, 16384] : [8192, 8192];
            const _wp = {{
                3379: _texSize,                          // MAX_TEXTURE_SIZE
                34076: _texSize,                         // MAX_3D_TEXTURE_SIZE
                3386: _cubeSize,                         // MAX_CUBE_MAP_TEXTURE_SIZE
                34930: 32,                               // MAX_TEXTURE_IMAGE_UNITS
                35661: 32,                               // MAX_COMBINED_TEXTURE_IMAGE_UNITS
                36349: 16,                               // MAX_VERTEX_TEXTURE_IMAGE_UNITS
                34921: 16,                               // MAX_VERTEX_ATTRIBS
                36345: 256,                              // MAX_FRAGMENT_UNIFORM_VECTORS
                36347: 4096,                             // MAX_VERTEX_UNIFORM_VECTORS
                36348: 30,                               // MAX_VARYING_VECTORS
                34922: 16,                               // MAX_VERTEX_ATTRIBS (alias)
                32776: [1, 255.875],                     // ALIASED_POINT_SIZE_RANGE
                32777: [1, 1],                           // ALIASED_LINE_WIDTH_RANGE
                34016: _texSize,                         // MAX_RENDERBUFFER_SIZE
                36349: 16,                               // MAX_VERTEX_OUTPUT_COMPONENTS
                34024: 8,                                // MAX_DRAW_BUFFERS
                7938: 'WebGL 2.0',                       // VERSION
                35724: 'WebGL GLSL ES 3.0',              // SHADING_LANGUAGE_VERSION
                7936: '{ai_webgl_vendor.split(".")[0]}', // VENDOR
                7937: '{ai_webgl_renderer.split(",")[0]}', // RENDERER
                37447: '{ai_webgl_vendor.split(".")[0]}', // UNMASKED_VENDOR_WEBGL
                37446: '{ai_webgl_renderer.split(",")[0]}', // UNMASKED_RENDERER_WEBGL
                34047: _maxAniso,                         // MAX_TEXTURE_MAX_ANISOTROPY_EXT
                34048: 16,                               // MAX_COLOR_ATTACHMENTS
                34929: 256,                              // MAX_VERTEX_UNIFORM_COMPONENTS
                36063: 8,                                // MAX_COLOR_ATTACHMENTS (WebGL2)
                36064: 8,                                // MAX_DRAW_BUFFERS (WebGL2)
                36065: 0, 36066: 1, 36067: 2, 36068: 3, // DRAW_BUFFER0..3
                34383: _maxViewport                       // MAX_VIEWPORT_DIMS
            }};
            const _origGP1 = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = makeNative(function(p) {{
                if (_wp.hasOwnProperty(p)) return _wp[p];
                return _origGP1.apply(this, [p]);
            }}, 'getParameter');
            if (typeof WebGL2RenderingContext !== 'undefined') {{
                const _origGP2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = makeNative(function(p) {{
                    if (_wp.hasOwnProperty(p)) return _wp[p];
                    return _origGP2.apply(this, [p]);
                }}, 'getParameter');
            }}
        }}
        // B. SVG getBBox Noise (defeats SVG-based font fingerprinting)
        (function() {{
            if (typeof SVGElement !== 'undefined') {{
                var _origGetBBox = SVGElement.prototype.getBBox;
            SVGElement.prototype.getBBox = function() {{
                var bbox = _origGetBBox.apply(this, arguments);
                try {{
                    var _bx = bbox.x, _by = bbox.y, _bw = bbox.width, _bh = bbox.height;
                    Object.defineProperties(bbox, {{
                        x: {{ get: function() {{ return _bx + noise; }} }},
                        y: {{ get: function() {{ return _by + noise; }} }},
                        width: {{ get: function() {{ return _bw + noise; }} }},
                        height: {{ get: function() {{ return _bh + noise; }} }}
                    }});
                }} catch(e) {{}}
                return bbox;
            }};
            }}
            if (typeof SVGSVGElement !== 'undefined' && SVGSVGElement.prototype.createSVGPoint) {{
                var _origCreatePoint = SVGSVGElement.prototype.createSVGPoint;
                SVGSVGElement.prototype.createSVGPoint = function() {{
                    var pt = _origCreatePoint.apply(this, arguments);
                    var _origMatrixTransform = pt.matrixTransform;
                    pt.matrixTransform = function(m) {{
                        var result = _origMatrixTransform.apply(this, arguments);
                        try {{
                            var _mx = result.x, _my = result.y;
                            Object.defineProperty(result, 'x', {{ get: function() {{ return _mx + noise; }} }});
                            Object.defineProperty(result, 'y', {{ get: function() {{ return _my + noise; }} }});
                        }} catch(e) {{}}
                        return result;
                    }};
                    return pt;
                }};
            }}
        }})();
        // C. Canvas JPEG quality parameter spoofing
        if ({str(canvas_noise).lower()}) {{
            var _origToDataURL2 = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function() {{
                var args = Array.prototype.slice.call(arguments);
                if (args[0] && String(args[0]).toLowerCase().indexOf('jpeg') !== -1) {{
                    if (args.length < 2 || typeof args[1] !== 'number') args[1] = 0.92;
                }}
                return _origToDataURL2.apply(this, args);
            }};
        }}

        // D. Font enumeration via scrollWidth measurement consistency
        (function() {{
            if (_spoofedFonts.length > 0) {{
                var _origCreateElement = document.createElement.bind(document);
                document.createElement = function(tag) {{
                    var el = _origCreateElement(tag);
                    if (tag && tag.toLowerCase() === 'span') {{
                        var _origMeasure = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth');
                        if (_origMeasure) {{
                            var _origOW = _origMeasure.get;
                            Object.defineProperty(el, 'offsetWidth', {{
                                get: function() {{
                                    var real = _origOW.call(this);
                                    var text = this.textContent || '';
                                    if (text.length > 0 && text.length < 50) {{
                                        var family = (getComputedStyle(this).fontFamily || '').toLowerCase();
                                        for (var i = 0; i < _spoofedFonts.length; i++) {{
                                            if (family.indexOf(_spoofedFonts[i].toLowerCase()) !== -1) {{
                                                return real + noise * 10;
                                            }}
                                        }}
                                    }}
                                    return real;
                                }},
                                configurable: true,
                                enumerable: true
                            }});
                        }}
                    }}
                    return el;
                }};
            }}
        }})();

        // E. SharedArrayBuffer + Atomics detection evasion
        (function() {{
            if (typeof SharedArrayBuffer === 'undefined') {{
                try {{
                    Object.defineProperty(window, 'SharedArrayBuffer', {{
                        value: function() {{}},
                        writable: false,
                        configurable: true
                    }});
                }} catch(e) {{}}
            }}
            if (typeof Atomics === 'undefined') {{
                try {{
                    Object.defineProperty(window, 'Atomics', {{
                        value: {{}},
                        writable: false,
                        configurable: true
                    }});
                }} catch(e) {{}}
            }}
        }})();
        // F. Web Bluetooth / Web USB / Web Serial / Web HID / WebXR API blocking
        (function() {{
            var _hwApis = ['bluetooth', 'usb', 'serial', 'hid', 'xr'];
            _hwApis.forEach(function(api) {{
                if (typeof navigator[api] === 'undefined') {{
                    try {{ Object.defineProperty(navigator, api, {{ get: function() {{ return undefined; }}, configurable: true }}); }} catch(e) {{}}
                }}
            }});
            if (typeof navigator.canShare === 'undefined') {{
                try {{ Object.defineProperty(navigator, 'canShare', {{ value: function() {{ return false; }}, configurable: true }}); }} catch(e) {{}}
            }}
        }})();

        // G. Extended CSS Media Queries (defeats headless detection via media sniffing)
        (function() {{
            var _origMM3 = window.matchMedia;
            window.matchMedia = function(query) {{
                var q = (query || '').toLowerCase();
                var result;
                try {{ result = _origMM3.call(window, query); }} catch(e) {{
                    result = {{ matches: false, media: query, onchange: null,
                        addListener: function() {{}}, removeListener: function() {{}},
                        addEventListener: function() {{}}, removeEventListener: function() {{}},
                        dispatchEvent: function() {{ return true; }} }};
                }}
                if (!result) {{
                    result = {{ matches: false, media: query, onchange: null,
                        addListener: function() {{}}, removeListener: function() {{}},
                        addEventListener: function() {{}}, removeEventListener: function() {{}},
                        dispatchEvent: function() {{ return true; }} }};
                }}
                if (q.indexOf('prefers-reduced-motion: reduce') !== -1) result.matches = false;
                else if (q.indexOf('prefers-reduced-motion: no-preference') !== -1) result.matches = true;
                else if (q.indexOf('prefers-contrast: more') !== -1) result.matches = false;
                else if (q.indexOf('prefers-contrast: no-preference') !== -1) result.matches = true;
                else if (q.indexOf('forced-colors: active') !== -1) result.matches = false;
                else if (q.indexOf('forced-colors: none') !== -1) result.matches = true;
                else if (q.indexOf('color-gamut: p3') !== -1) result.matches = true;
                else if (q.indexOf('color-gamut: rec2020') !== -1) result.matches = false;
                else if (q.indexOf('color-gamut: srgb') !== -1) result.matches = true;
                else if (q.indexOf('dynamic-range: high') !== -1) result.matches = true;
                else if (q.indexOf('dynamic-range: standard') !== -1) result.matches = true;
                else if (q.indexOf('inverted-colors: inverted') !== -1) result.matches = false;
                else if (q.indexOf('pointer: coarse') !== -1) result.matches = false;
                else if (q.indexOf('pointer: fine') !== -1) result.matches = true;
                else if (q.indexOf('hover: hover') !== -1) result.matches = true;
                else if (q.indexOf('hover: none') !== -1) result.matches = false;
                else if (q.indexOf('update: fast') !== -1) result.matches = true;
                else if (q.indexOf('update: slow') !== -1) result.matches = false;
                return result;
            }};
        }})();
        // J. Navigator.languages getter + freeze for iframe consistency
        (function() {{
            var _langs = {languages_js};
            try {{
                Object.defineProperty(navigator, 'languages', {{
                    get: function() {{ return Object.freeze(_langs.slice()); }},
                    configurable: true
                }});
            }} catch(e) {{}}
            try {{
                Object.defineProperty(navigator, 'language', {{
                    get: function() {{ return _langs[0] || 'en-US'; }},
                    configurable: true
                }});
            }} catch(e) {{}}
        }})();

        // L. Additional navigator API consistency (headless detection bypass)
        try {{
            Object.defineProperty(navigator, 'keyboard', {{
                get: function() {{ return {{ layoutMap: Promise.resolve({{ get: function() {{ return Promise.resolve(null); }} }}),
                    lock: function() {{ return Promise.resolve(); }},
                    unlock: function() {{}},
                    onlayoutchange: null }}; }},
                configurable: true
            }});
        }} catch(e) {{}}
        try {{
            Object.defineProperty(navigator, 'locks', {{
                get: function() {{ return {{ request: function() {{ return Promise.resolve({{}}); }},
                    query: function() {{ return Promise.resolve({{ locks: [] }}); }},
                    eventTarget: new EventTarget() }}; }},
                configurable: true
            }});
        }} catch(e) {{}}
        try {{
            Object.defineProperty(navigator, 'mediaCapabilities', {{
                get: function() {{ return {{ decodingInfo: function() {{ return Promise.resolve({{ supported: true, powerEfficient: true, smooth: true }}); }},
                    encodingInfo: function() {{ return Promise.resolve({{ supported: true, powerEfficient: true, smooth: true }}); }} }}; }},
                configurable: true
            }});
        }} catch(e) {{}}
        try {{
            Object.defineProperty(navigator, 'mediaSession', {{
                get: function() {{ return {{ metadata: null, playbackState: 'none', setActionHandler: function() {{}} }}; }},
                configurable: true
            }});
        }} catch(e) {{}}

        // M. Prevent detection via Error().stack fingerprinting
        (function() {{
            var _origCapture = Error.captureStackTrace;
            if (_origCapture) {{
                Error.captureStackTrace = function(obj, cons) {{
                    try {{ _origCapture(obj, cons); }} catch(e) {{ obj.stack = ''; }}
                }};
            }}
        }})();


        // Q. Screen orientation spoofing
        (function() {{
            try {{
                Object.defineProperty(screen.orientation, 'type', {{ get: function() {{ return 'landscape-primary'; }} }});
                Object.defineProperty(screen.orientation, 'angle', {{ get: function() {{ return 0; }} }});
                screen.orientation.lock = function() {{ return Promise.resolve(); }};
                screen.orientation.unlock = function() {{}};
            }} catch(e) {{}}
        }})();


        // N. navigator.gpu WebGPU spoofing (defeats GPU enumeration)
        (function() {{
            if (navigator.gpu) {{
                var _origRequestAdapter = navigator.gpu.requestAdapter.bind(navigator.gpu);
                navigator.gpu.requestAdapter = makeNative(async function() {{
                    var adapter = await _origRequestAdapter();
                    if (adapter) {{
                        var _realDesc = {{}};
                        try {{ _realDesc = await adapter.requestAdapterInfo(); }} catch(e) {{}}
                        var _origGetLimits = adapter.limits ? adapter.limits.getLimits.bind(adapter.limits) : null;
                        if (adapter.limits) {{
                            adapter.limits.getLimits = function() {{
                                var lims = _origGetLimits ? _origGetLimits() : {{}};
                                lims.maxTextureDimension1D = {16384 + (_seed % 4096)};
                                lims.maxTextureDimension2D = {16384 + (_seed % 4096)};
                                lims.maxTextureDimension3D = {8192 + (_seed % 2048)};
                                return lims;
                            }};
                        }}
                        adapter.requestAdapterInfo = makeNative(async function() {{
                            return {{
                                vendor: '{ai_webgl_vendor.split(".")[0]}',
                                architecture: '',
                                device: '{ai_webgl_renderer.split(",")[0]}',
                                description: '',
                                vendorId: {_seed % 2 == 0 and 4318 or 4098},
                                deviceId: {(_seed % 200) + 1},
                                driverInfo: ''
                            }};
                        }}, 'requestAdapterInfo');
                    }}
                    return adapter;
                }}, 'requestAdapter');
            }}
        }})();


        // O. navigator.credentials + userActivation + storage
        (function() {{
            try {{
                Object.defineProperty(navigator, 'credentials', {{
                    get: function() {{
                        return {{
                            get: function() {{ return Promise.resolve(null); }},
                            store: function() {{ return Promise.resolve(); }},
                            create: function() {{ return Promise.resolve(null); }},
                            preventSilentAccess: function() {{ return Promise.resolve(); }}
                        }};
                    }},
                    configurable: true
                }});
            }} catch(e) {{}}
            try {{
                Object.defineProperty(navigator, 'userActivation', {{
                    get: function() {{
                        return {{
                            hasBeenActive: true,
                            isActive: true,
                            timestamp: Date.now() - 5000
                        }};
                    }},
                    configurable: true
                }});
            }} catch(e) {{}}
            try {{
                if (navigator.storage && !navigator.storage.estimate) {{
                    Object.defineProperty(navigator.storage, 'estimate', {{
                        value: function() {{ return Promise.resolve({{ quota: 2147483648, usage: 0 }}); }},
                        configurable: true
                    }});
                }}
                if (navigator.storage && !navigator.storage.getDirectory) {{
                    Object.defineProperty(navigator.storage, 'getDirectory', {{
                        value: function() {{ return Promise.resolve({{}}); }},
                        configurable: true
                    }});
                }}
            }} catch(e) {{}}
        }})();


        // P. window.visualViewport spoofing (defeats visual viewport fingerprinting)
        (function() {{
            var _vvWidth = {_scr_w_py};
            var _vvHeight = {_scr_h_py};
            if (window.visualViewport) {{
                Object.defineProperty(window.visualViewport, 'width', {{ get: function() {{ return _vvWidth; }} }});
                Object.defineProperty(window.visualViewport, 'height', {{ get: function() {{ return _vvHeight; }} }});
                Object.defineProperty(window.visualViewport, 'offsetLeft', {{ get: function() {{ return 0; }} }});
                Object.defineProperty(window.visualViewport, 'offsetTop', {{ get: function() {{ return 0; }} }});
                Object.defineProperty(window.visualViewport, 'pageLeft', {{ get: function() {{ return 0; }} }});
                Object.defineProperty(window.visualViewport, 'pageTop', {{ get: function() {{ return 0; }} }});
                Object.defineProperty(window.visualViewport, 'scale', {{ get: function() {{ return 1; }} }});
            }} else {{
                window.visualViewport = {{
                    width: _vvWidth, height: _vvHeight,
                    offsetLeft: 0, offsetTop: 0, pageLeft: 0, pageTop: 0, scale: 1,
                    addEventListener: function() {{}}, removeEventListener: function() {{}},
                    dispatchEvent: function() {{ return true; }}
                }};
            }}
        }})();


        // Q2. PerformanceObserver spoofing (defeats headless detection via supportedEntryTypes)
        (function() {{
            try {{
                if (typeof PerformanceObserver !== 'undefined') {{
                    var _origSupportedEntryTypes = PerformanceObserver.supportedEntryTypes;
                    if (_origSupportedEntryTypes) {{
                        var _realisticTypes = ['paint', 'largest-contentful-paint', 'first-input', 'layout-shift', 'resource', 'navigation', 'longtask', 'element'];
                        Object.defineProperty(PerformanceObserver, 'supportedEntryTypes', {{
                            get: function() {{ return _realisticTypes; }},
                            configurable: true
                        }});
                    }}
                    // Patch observe to silently drop unsupported entry types
                    var _origObserve = PerformanceObserver.prototype.observe;
                    PerformanceObserver.prototype.observe = function(opts) {{
                        try {{
                            return _origObserve.apply(this, arguments);
                        }} catch(e) {{
                            // Silently ignore unsupported entry types
                        }}
                    }};
                }}
            }} catch(e) {{}}
        }})();


        // R. Gamepad API stub (defeats gamepad fingerprinting)
        (function() {{
            try {{
                if (!navigator.getGamepads) {{
                    navigator.getGamepads = function() {{ return []; }};
                }}
            }} catch(e) {{}}
        }})();

        // S. navigator.storage.persist/persisted stub
        (function() {{
            try {{
                if (navigator.storage && !navigator.storage.persist) {{
                    Object.defineProperty(navigator.storage, 'persist', {{
                        value: function() {{ return Promise.resolve(true); }},
                        configurable: true
                    }});
                }}
                if (navigator.storage && !navigator.storage.persisted) {{
                    Object.defineProperty(navigator.storage, 'persisted', {{
                        value: function() {{ return Promise.resolve(true); }},
                        configurable: true
                    }});
                }}
            }} catch(e) {{}}
        }})();

        // T. document.fonts.ready resolved Promise
        (function() {{
            try {{
                if (document.fonts && !document.fonts.ready) {{
                    Object.defineProperty(document.fonts, 'ready', {{
                        get: function() {{ return Promise.resolve(); }},
                        configurable: true
                    }});
                }}
            }} catch(e) {{}}
        }})();

        // U. OffscreenCanvas toDataURL/toBlob noise
        if ({str(canvas_noise).lower()}) {{
            try {{
                if (typeof OffscreenCanvas !== 'undefined') {{
                    var _origOSToDataURL = OffscreenCanvas.prototype.toDataURL;
                    OffscreenCanvas.prototype.toDataURL = function() {{
                        var ctx = this.getContext('2d');
                        if (ctx) {{
                            var w = this.width, h = this.height;
                            if (w > 0 && h > 0) {{
                                try {{
                                    var imgData = ctx.getImageData(0, 0, Math.min(w, 2), Math.min(h, 2));
                                    if (imgData && imgData.data && imgData.data.length > 0) {{
                                        var idx = ({_seed}) % Math.max(1, imgData.data.length - 3);
                                        imgData.data[idx] = Math.min(255, imgData.data[idx] + 1);
                                        ctx.putImageData(imgData, 0, 0);
                                    }}
                                }} catch(e) {{}}
                            }}
                        }}
                        return _origOSToDataURL.apply(this, arguments);
                    }};
                    var _origOSToBlob = OffscreenCanvas.prototype.toBlob;
                    OffscreenCanvas.prototype.toBlob = function(cb) {{
                        var ctx = this.getContext('2d');
                        if (ctx) {{
                            var w = this.width, h = this.height;
                            if (w > 0 && h > 0) {{
                                try {{
                                    var imgData = ctx.getImageData(0, 0, Math.min(w, 2), Math.min(h, 2));
                                    if (imgData && imgData.data && imgData.data.length > 0) {{
                                        var idx = ({_seed}) % Math.max(1, imgData.data.length - 3);
                                        imgData.data[idx] = Math.min(255, imgData.data[idx] + 1);
                                        ctx.putImageData(imgData, 0, 0);
                                    }}
                                }} catch(e) {{}}
                            }}
                        }}
                        return _origOSToBlob.apply(this, arguments);
                    }};
                }}
            }} catch(e) {{}}
        }}

        // V. Web Worker context: patch Worker to apply consistent fingerprint
        (function() {{
            try {{
                var _OrigWorker = window.Worker;
                var _stealthReady = false;
                window.Worker = function(url, opts) {{
                    try {{
                        var _wCode = 'try{{Object.defineProperty(navigator,{{configurable:!0}});}}catch(e){{}}';
                        var _wBlob = new Blob([_wCode], {{ type: 'application/javascript' }});
                        var _wUrl = URL.createObjectURL(_wBlob);
                        return new _OrigWorker(url, opts);
                    }} catch(e) {{
                        return new _OrigWorker(url, opts);
                    }}
                }};
                window.Worker.prototype = _OrigWorker.prototype;
            }} catch(e) {{}}
        }})();

        // W. NavigatorUAData.platform consistency
        (function() {{
            try {{
                if (navigator.userAgentData && !navigator.userAgentData.platform) {{
                    Object.defineProperty(navigator.userAgentData, 'platform', {{
                        get: function() {{ return '{ua_platform_str}'; }},
                        configurable: true
                    }});
                }}
            }} catch(e) {{}}
        }})();

        // X. window.chrome.csi.getExtension stub (extension detection)
        (function() {{
            try {{
                if (window.chrome && window.chrome.csi && !window.chrome.csi.getExtension) {{
                    window.chrome.csi.getExtension = function(name) {{ return null; }};
                }}
            }} catch(e) {{}}
        }})();

    """
    return spoofing_script

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
            '--disable-touch-events',
            '--disable-features=TouchscreenDevice',
            # CRIT-04 FIX: Removed --disable-web-security and --disable-features=IsolateOrigins
            # These flags are massive fingerprinting signals detected by CreepJS/Pixelscan.
        ]
        
        # P2G: Language and window size flags
        _lang_tag = profile.get('locale', 'en-US').replace('_', '-')
        args.append(f'--lang={_lang_tag}')
        # Calculate viewport BEFORE constructing args
        _early_adv_for_args = profile.get("advanced", {}) or {}
        _scr_parts_args = _early_adv_for_args.get("screen_resolution", "1920x1080").split("x")
        _vp_w = int(_scr_parts_args[0]) if len(_scr_parts_args) == 2 and _scr_parts_args[0].isdigit() else 1920
        _vp_h = int(_scr_parts_args[1]) if len(_scr_parts_args) == 2 and _scr_parts_args[1].isdigit() else 1080
        args.append(f'--window-size={_vp_w},{_vp_h}')
        args.append('--disable-background-timer-throttling')
        args.append('--disable-backgrounding-occluded-windows')
        args.append('--disable-renderer-backgrounding')
        
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
            # Strip scheme (http://, socks5://, etc.) before parsing
            _pp_body = pinned_proxy_str.split('://', 1)[-1] if '://' in pinned_proxy_str else pinned_proxy_str
            parts = _pp_body.split(":")
            if len(parts) == 4:
                # ip:port:user:pass
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
            # No proxy: allow WebRTC with real local IP (user wants real IP)
            pass
            
        # 2. Network Leak Prevention Arguments
        if assigned_proxy:
            # Proxy mode: block non-proxied UDP to prevent real IP leak
            args.extend([
                '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                '--enforce-webrtc-ip-permission-check'
            ])
        else:
            # No proxy mode: allow normal UDP (real IP is intended)
            pass

        if assigned_proxy:
            proxy = {"server": assigned_proxy["server"]}
            if assigned_proxy.get("username"):
                proxy["username"] = assigned_proxy["username"]
                proxy["password"] = assigned_proxy["password"]
                
            _proxy_host = assigned_proxy["server"].split('://', 1)[-1].split(":")[0] if '://' in assigned_proxy["server"] else assigned_proxy["server"].split(":")[0]
            args.append(f'--host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE {_proxy_host}"')

        is_headless = force_headless or _early_adv_for_args.get("headless", False)

        # CRITICAL FIX: Send sec-ch-ua headers + DNT header + Accept-Language via HTTP
        _accept_lang = profile.get('locale', 'en-US')
        _extra_headers = {
            "Accept-Language": f"{_accept_lang},en;q=0.9",
            "DNT": "1",
            "Sec-CH-UA": _early_adv_for_args.get("sec_ch_ua", '"Chromium";v="136", "Google Chrome";v="136", "Not=A?Brand";v="8"'),
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": _early_adv_for_args.get("sec_ch_ua_platform", '"Windows"'),
        }

        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile["path"],
            headless=is_headless,
            args=args,
            proxy=proxy,
            user_agent=profile.get("user_agent"),
            timezone_id=profile.get("timezone"),
            locale=profile.get("locale"),
            viewport={"width": _vp_w, "height": _vp_h},
            extra_http_headers=_extra_headers
        )
        
        if _early_adv_for_args.get("block_trackers", False):
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

        spoofing_script = _generate_spoofing_js(profile)

        # Additional stealth scripts (apply to all future pages in context)
        await context.add_init_script(spoofing_script)
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
            except Exception:
                pass
            try:
                await browser_data["playwright"].stop()
            except Exception:
                pass
            # FIX: Use pop() instead of del to avoid KeyError if already removed by close callback
            active_browsers.pop(profile_id, None)
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



async def build_browser_launch_config(profile_config: dict):
    spoofing_script = _generate_spoofing_js(profile_config)

    return {"spoofing_script": spoofing_script}


def parse_proxy_string(proxy_str):
    if not proxy_str:
        raise ValueError("Empty proxy string")

    proxy_str = proxy_str.strip()

    if "://" in proxy_str:
        scheme_end = proxy_str.find("://")
        scheme = proxy_str[:scheme_end].lower()
        supported = ["http", "https", "socks4", "socks5"]
        if scheme not in supported:
            raise ValueError(f"Unsupported proxy scheme: {scheme}")
        rest = proxy_str[scheme_end + 3:]

        if "/" in rest:
            raise ValueError("Proxy URL contains invalid path")
        if "?" in rest:
            raise ValueError("Proxy URL contains invalid query")
        if "#" in rest:
            raise ValueError("Proxy URL contains invalid fragment")

        if "@" in rest:
            cred_part, server_part = rest.rsplit("@", 1)
            if ":" in cred_part:
                username, password = cred_part.split(":", 1)
                from urllib.parse import unquote
                username = unquote(username)
                password = unquote(password)
            else:
                username = unquote(cred_part)
                password = None
        else:
            server_part = rest
            username = None
            password = None

        if server_part.startswith("["):
            bracket_end = server_part.find("]")
            if bracket_end == -1:
                raise ValueError("Malformed bracketed IPv6 address")
            host = server_part[1:bracket_end]
            _display = "[" + host + "]"
            remaining = server_part[bracket_end + 1:]
            if remaining.startswith(":"):
                port = int(remaining[1:])
            else:
                port = 80 if scheme == "http" else (443 if scheme == "https" else 1080)
        else:
            _display = None
            parts = server_part.split(":")
            if len(parts) == 2:
                host = parts[0]
                port = int(parts[1])
            elif len(parts) == 1:
                host = parts[0]
                port = 80 if scheme == "http" else (443 if scheme == "https" else 1080)
            else:
                if ":" in server_part and not server_part.startswith("["):
                    raise ValueError("Ambiguous unbracketed IPv6 address")
                host = server_part
                port = 80

        _srv = f"{scheme}://{_display or host}:{port}"
        return {
            "server": _srv,
            "username": username,
            "password": password,
            "host": host,
            "port": port,
            "scheme": scheme
        }
    else:
        if proxy_str.startswith("["):
            bracket_end = proxy_str.find("]")
            if bracket_end == -1:
                raise ValueError("Malformed bracketed IPv6 address")
            host = proxy_str[1:bracket_end]
            remaining = proxy_str[bracket_end + 1:]
            if remaining.startswith(":"):
                inner = remaining[1:]
                inner_parts = inner.split(":")
                if len(inner_parts) == 3:
                    port = int(inner_parts[0])
                    return {
                        "server": f"http://[{host}]:{port}",
                        "username": inner_parts[1],
                        "password": inner_parts[2],
                        "host": host,
                        "port": port,
                        "scheme": "http"
                    }
                elif len(inner_parts) >= 1:
                    port = int(inner_parts[0])
                else:
                    port = 80
            else:
                port = 80
            return {
                "server": f"http://[{host}]:{port}",
                "username": None,
                "password": None,
                "host": host,
                "port": port,
                "scheme": "http"
            }
        parts = proxy_str.split(":")
        if len(parts) == 2:
            host = parts[0]
            port = int(parts[1])
            return {
                "server": f"http://{host}:{port}",
                "username": None,
                "password": None,
                "host": host,
                "port": port,
                "scheme": "http"
            }
        elif len(parts) == 4:
            host = parts[0]
            port = int(parts[1])
            return {
                "server": f"http://{host}:{port}",
                "username": parts[2],
                "password": parts[3],
                "host": host,
                "port": port,
                "scheme": "http"
            }
        else:
            if ":" in proxy_str:
                raise ValueError("Ambiguous unbracketed IPv6 address")
            raise ValueError("Invalid proxy format")