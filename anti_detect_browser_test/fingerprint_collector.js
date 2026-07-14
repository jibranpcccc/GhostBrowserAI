async function collectFingerprint() {
    const fp = {};

    // 1. BROWSER IDENTITY
    fp.browser_identity = {
        userAgent: navigator.userAgent,
        appVersion: navigator.appVersion,
        platform: navigator.platform,
        vendor: navigator.vendor,
        product: navigator.product,
        productSub: navigator.productSub,
        webdriver: navigator.webdriver,
        appName: navigator.appName,
        appCodeName: navigator.appCodeName,
        cookieEnabled: navigator.cookieEnabled,
        doNotTrack: navigator.doNotTrack,
        globalPrivacyControl: navigator.globalPrivacyControl,
        hardwareConcurrency: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory,
        maxTouchPoints: navigator.maxTouchPoints,
        languages: navigator.languages,
        language: navigator.language,
        chromiumVersion: (navigator.userAgent.match(/Chrome\/([\d.]+)/) || [])[1] || 'unknown',
    };

    // 2. USER-AGENT CLIENT HINTS
    fp.client_hints = {};
    try {
        if (navigator.userAgentData) {
            const highEntropy = await navigator.userAgentData.getHighEntropyValues([
                'architecture', 'bitness', 'model', 'platform', 'platformVersion',
                'fullVersionList', 'fullVersion', 'mobile', 'wow64'
            ]);
            fp.client_hints = {
                brands: navigator.userAgentData.brands,
                mobile: navigator.userAgentData.mobile,
                platform: highEntropy.platform,
                platformVersion: highEntropy.platformVersion,
                architecture: highEntropy.architecture,
                bitness: highEntropy.bitness,
                model: highEntropy.model,
                fullVersionList: highEntropy.fullVersionList,
                fullVersion: highEntropy.fullVersion,
                wow64: highEntropy.wow64,
            };
        }
    } catch(e) { fp.client_hints.error = e.message; }

    // 3. SCREEN PROPERTIES
    fp.screen = {
        width: screen.width,
        height: screen.height,
        availWidth: screen.availWidth,
        availHeight: screen.availHeight,
        availTop: screen.availTop,
        availLeft: screen.availLeft,
        colorDepth: screen.colorDepth,
        pixelDepth: screen.pixelDepth,
        orientation: screen.orientation ? { type: screen.orientation.type, angle: screen.orientation.angle } : null,
    };

    // 4. WINDOW PROPERTIES
    fp.window = {
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        outerWidth: window.outerWidth,
        outerHeight: window.outerHeight,
        screenX: window.screenX,
        screenY: window.screenY,
        screenLeft: window.screenLeft,
        screenTop: window.screenTop,
        devicePixelRatio: window.devicePixelRatio,
        chrome: !!window.chrome,
        chromeRuntime: !!(window.chrome && window.chrome.runtime),
        chromeApp: !!(window.chrome && window.chrome.app),
    };

    // 5. VISUAL VIEWPORT
    fp.visual_viewport = {};
    try {
        if (window.visualViewport) {
            fp.visual_viewport = {
                width: window.visualViewport.width,
                height: window.visualViewport.height,
                offsetLeft: window.visualViewport.offsetLeft,
                offsetTop: window.visualViewport.offsetTop,
                pageLeft: window.visualViewport.pageLeft,
                pageTop: window.visualViewport.pageTop,
                scale: window.visualViewport.scale,
            };
        }
    } catch(e) {}

    // 6. LOCALE & TIMEZONE
    fp.locale = {
        language: navigator.language,
        languages: navigator.languages,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        utcOffset: new Date().getTimezoneOffset(),
        calendar: Intl.DateTimeFormat().resolvedOptions().calendar,
        numberingSystem: Intl.DateTimeFormat().resolvedOptions().numberingSystem,
        dateFormat: new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(new Date()),
        timeFormat: new Intl.DateTimeFormat(undefined, { timeStyle: 'full' }).format(new Date()),
    };

    // 7. CANVAS 2D FINGERPRINT
    fp.canvas_2d = {};
    try {
        const canvas = document.createElement('canvas');
        canvas.width = 280; canvas.height = 60;
        const ctx = canvas.getContext('2d');
        ctx.textBaseline = 'top';
        ctx.font = '14px Arial';
        ctx.fillStyle = '#f60';
        ctx.fillRect(125, 1, 62, 20);
        ctx.fillStyle = '#069';
        ctx.fillText('GhostBrowser canvas test \ud83d\ude03', 2, 15);
        ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
        ctx.fillText('GhostBrowser canvas test \ud83d\ude03', 4, 17);
        const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        fp.canvas_2d = {
            dataURL: canvas.toDataURL('image/png').substring(0, 100),
            hash: await hashString(Array.from(imgData.data).join(',')),
            width: canvas.width,
            height: canvas.height,
        };
    } catch(e) { fp.canvas_2d.error = e.message; }

    // 8. OFFSCREEN CANVAS
    fp.offscreen_canvas = {};
    try {
        if (typeof OffscreenCanvas !== 'undefined') {
            const oc = new OffscreenCanvas(280, 60);
            const octx = oc.getContext('2d');
            octx.textBaseline = 'top';
            octx.font = '14px Arial';
            octx.fillStyle = '#f60';
            octx.fillRect(125, 1, 62, 20);
            octx.fillStyle = '#069';
            octx.fillText('GhostBrowser canvas test \ud83d\ude03', 2, 15);
            octx.fillStyle = 'rgba(102, 204, 0, 0.7)';
            octx.fillText('GhostBrowser canvas test \ud83d\ude03', 4, 17);
            const ocImgData = octx.getImageData(0, 0, oc.width, oc.height);
            fp.offscreen_canvas = {
                supported: true,
                hash: await hashString(Array.from(ocImgData.data).join(',')),
                width: oc.width,
                height: oc.height,
            };
        } else {
            fp.offscreen_canvas = { supported: false };
        }
    } catch(e) { fp.offscreen_canvas.error = e.message; }

    // 9. WEBGL 1
    fp.webgl1 = {};
    try {
        const glCanvas = document.createElement('canvas');
        const gl = glCanvas.getContext('webgl');
        if (gl) {
            const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
            fp.webgl1 = {
                vendor: gl.getParameter(debugInfo ? debugInfo.UNMASKED_VENDOR_WEBGL : gl.VENDOR),
                renderer: gl.getParameter(debugInfo ? debugInfo.UNMASKED_RENDERER_WEBGL : gl.RENDERER),
                vendorRaw: gl.getParameter(gl.VENDOR),
                rendererRaw: gl.getParameter(gl.RENDERER),
                version: gl.getParameter(gl.VERSION),
                shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
                maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
                maxViewportDims: gl.getParameter(gl.MAX_VIEWPORT_DIMS),
                maxRenderbufferSize: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
                supportedExtensions: gl.getSupportedExtensions(),
                antialias: gl.getContextAttributes().antialias,
                colorBits: [gl.getParameter(gl.RED_BITS), gl.getParameter(gl.GREEN_BITS), gl.getParameter(gl.BLUE_BITS), gl.getParameter(gl.ALPHA_BITS)],
                depthBits: gl.getParameter(gl.DEPTH_BITS),
                stencilBits: gl.getParameter(gl.STENCIL_BITS),
            };
            try {
                const prec = gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT);
                fp.webgl1.shaderPrecision = { rangeMin: prec.rangeMin, rangeMax: prec.rangeMax, precision: prec.precision };
            } catch(e) {}
        }
    } catch(e) { fp.webgl1.error = e.message; }

    // 10. WEBGL 2
    fp.webgl2 = {};
    try {
        const gl2Canvas = document.createElement('canvas');
        const gl2 = gl2Canvas.getContext('webgl2');
        if (gl2) {
            const debugInfo2 = gl2.getExtension('WEBGL_debug_renderer_info');
            fp.webgl2 = {
                vendor: gl2.getParameter(debugInfo2 ? debugInfo2.UNMASKED_VENDOR_WEBGL : gl2.VENDOR),
                renderer: gl2.getParameter(debugInfo2 ? debugInfo2.UNMASKED_RENDERER_WEBGL : gl2.RENDERER),
                version: gl2.getParameter(gl2.VERSION),
                maxTextureSize: gl2.getParameter(gl2.MAX_TEXTURE_SIZE),
                supportedExtensions: gl2.getSupportedExtensions(),
            };
        } else {
            fp.webgl2 = { supported: false };
        }
    } catch(e) { fp.webgl2.error = e.message; }

    // 11. WEBGPU
    fp.webgpu = {};
    try {
        if (navigator.gpu) {
            const adapter = await navigator.gpu.requestAdapter();
            if (adapter) {
                const info = await adapter.requestAdapterInfo();
                fp.webgpu = {
                    supported: true,
                    vendor: info.vendor,
                    device: info.device,
                    architecture: info.architecture,
                    description: info.description,
                };
            }
        } else {
            fp.webgpu = { supported: false };
        }
    } catch(e) { fp.webgpu.error = e.message; }

    // 12. AUDIO FINGERPRINT
    fp.audio = {};
    try {
        const ac = new (window.AudioContext || window.webkitAudioContext)();
        fp.audio = {
            sampleRate: ac.sampleRate,
            baseLatency: ac.baseLatency,
            state: ac.state,
        };
        const oac = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 44100, 44100);
        const osc = oac.createOscillator();
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(10000, oac.currentTime);
        const comp = oac.createDynamicsCompressor();
        osc.connect(comp);
        comp.connect(oac.destination);
        osc.start(0);
        const renderedBuffer = await oac.startRendering();
        const data = renderedBuffer.getChannelData(0).slice(4500, 5000);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += Math.abs(data[i]);
        fp.audio.offlineHash = sum;
        fp.audio.offlineDataPoints = Array.from(data.slice(0, 20));
        await ac.close();
    } catch(e) { fp.audio.error = e.message; }

    // 13. FONT FINGERPRINT
    fp.fonts = {};
    try {
        const testFonts = ['Arial', 'Verdana', 'Times New Roman', 'Courier New', 'Georgia', 'Palatino', 'Garamond', 'Comic Sans MS', 'Impact', 'Lucida Console', 'Tahoma', 'Trebuchet MS', 'Helvetica', 'MS Serif', 'Arial Black', 'Segoe UI', 'SF Pro', 'Fira Code', 'Consolas', 'Menlo'];
        const baseFonts = ['monospace', 'sans-serif', 'serif'];
        const testString = 'mmmmmmmmmmlli';
        const testSize = '72px';
        const span = document.createElement('span');
        span.style.cssText = 'position:absolute;left:-9999px;font-size:' + testSize;
        span.textContent = testString;
        document.body.appendChild(span);
        const baseWidths = {};
        for (const base of baseFonts) {
            span.style.fontFamily = base;
            baseWidths[base] = span.offsetWidth;
        }
        const detected = [];
        for (const font of testFonts) {
            let found = false;
            for (const base of baseFonts) {
                span.style.fontFamily = '"' + font + '",' + base;
                if (span.offsetWidth !== baseWidths[base]) { found = true; break; }
            }
            if (found) detected.push(font);
        }
        document.body.removeChild(span);
        fp.fonts.detected = detected;
        fp.fonts.count = detected.length;

        if (typeof FontFace !== 'undefined') {
            fp.fonts.fontFaceApi = true;
            try {
                const ff = new FontFace('TestFont', 'url(data:font/woff2;base64,d09GMgABAAAAA)', { style: 'normal', weight: '400' });
                fp.fonts.fontFaceApiSupported = true;
            } catch(e) { fp.fonts.fontFaceApiSupported = false; }
        }

        if (document.fonts && document.fonts.check) {
            fp.fonts.checkArial = document.fonts.check('16px Arial');
            fp.fonts.checkFake = document.fonts.check('16px "NonExistentFont12345"');
        }
    } catch(e) { fp.fonts.error = e.message; }

    // 14. MEDIA DEVICES
    fp.media_devices = {};
    try {
        if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
            const devices = await navigator.mediaDevices.enumerateDevices();
            fp.media_devices = {
                count: devices.length,
                devices: devices.map(d => ({ kind: d.kind, label: d.label, deviceId: d.deviceId.substring(0, 20), groupId: d.groupId.substring(0, 20) })),
            };
        }
    } catch(e) { fp.media_devices.error = e.message; }

    // 15. PERMISSIONS
    fp.permissions = {};
    try {
        const permNames = ['geolocation', 'notifications', 'camera', 'microphone', 'midi', 'push', 'clipboard-read', 'clipboard-write'];
        for (const name of permNames) {
            try {
                const result = await navigator.permissions.query({ name });
                fp.permissions[name] = result.state;
            } catch(e) { fp.permissions[name] = 'unavailable'; }
        }
    } catch(e) { fp.permissions.error = e.message; }

    // 16. STORAGE
    fp.storage = {
        cookiesEnabled: navigator.cookieEnabled,
        localStorage: !!window.localStorage,
        sessionStorage: !!window.sessionStorage,
        indexedDB: !!window.indexedDB,
        serviceWorker: !!navigator.serviceWorker,
    };
    try {
        if (navigator.storage && navigator.storage.estimate) {
            const est = await navigator.storage.estimate();
            fp.storage.quota = est.quota;
            fp.storage.usage = est.usage;
        }
    } catch(e) {}

    // 17. PROPERTY DESCRIPTORS (native integrity)
    fp.property_descriptors = {};
    const checkProps = [
        [navigator, 'userAgent'], [navigator, 'platform'], [navigator, 'vendor'],
        [navigator, 'hardwareConcurrency'], [navigator, 'deviceMemory'],
        [navigator, 'languages'], [navigator, 'webdriver'],
        [screen, 'width'], [screen, 'height'], [screen, 'availWidth'],
        [window, 'outerWidth'], [window, 'innerWidth'],
    ];
    for (const [obj, prop] of checkProps) {
        try {
            const desc = Object.getOwnPropertyDescriptor(obj, prop);
            fp.property_descriptors[prop] = {
                configurable: desc ? desc.configurable : 'N/A',
                enumerable: desc ? desc.enumerable : 'N/A',
                writable: desc ? desc.writable : 'N/A',
                hasGetter: desc ? !!desc.get : false,
                hasSetter: desc ? !!desc.set : false,
                onPrototype: obj.hasOwnProperty(prop) ? 'own' : 'inherited',
            };
        } catch(e) { fp.property_descriptors[prop] = { error: e.message }; }
    }

    // 18. PROTOTYPE CHAIN
    fp.prototype_chain = {};
    try {
        fp.prototype_chain.navigatorProto = Object.getPrototypeOf(navigator).constructor.name;
        fp.prototype_chain.screenProto = Object.getPrototypeOf(screen).constructor.name;
        fp.prototype_chain.webglProto = WebGLRenderingContext ? WebGLRenderingContext.prototype.constructor.name : 'N/A';
        fp.prototype_chain.canvasProto = HTMLCanvasElement.prototype.constructor.name;
    } catch(e) {}

    // 19. JS ERRORS
    fp.js_errors = [];
    try {
        const tests = [
            () => JSON.stringify({}),
            () => Array.from([1,2,3]),
            () => new Date().toISOString(),
            () => Intl.DateTimeFormat().resolvedOptions(),
            () => Promise.resolve(1),
            () => Symbol('test'),
            () => Reflect.ownKeys({}),
        ];
        for (const test of tests) {
            try { test(); } catch(e) { fp.js_errors.push(e.message); }
        }
    } catch(e) {}

    // 20. CONNECTION
    fp.connection = {};
    try {
        if (navigator.connection) {
            fp.connection = {
                downlink: navigator.connection.downlink,
                effectiveType: navigator.connection.effectiveType,
                rtt: navigator.connection.rtt,
                saveData: navigator.connection.saveData,
                type: navigator.connection.type,
                metered: navigator.connection.metered,
                downlinkMax: navigator.connection.downlinkMax,
            };
        }
    } catch(e) {}

    // 21. NAVIGATOR EXTENDED APIS
    fp.navigator_extended = {
        cookieEnabled: navigator.cookieEnabled,
        clipboard: !!navigator.clipboard,
        credentials: !!navigator.credentials,
        userActivation: !!navigator.userActivation,
        keyboard: !!navigator.keyboard,
        locks: !!navigator.locks,
        mediaCapabilities: !!navigator.mediaCapabilities,
        mediaSession: !!navigator.mediaSession,
        share: !!navigator.share,
        bluetooth: navigator.bluetooth,
        usb: navigator.usb,
        serial: navigator.serial,
        hid: navigator.hid,
    };

    // 22. WEBRTC (fingerprint surface only)
    fp.webrtc = {};
    try {
        if (window.RTCPeerConnection) {
            fp.webrtc.available = true;
            fp.webrtc.constructorName = window.RTCPeerConnection.name;
            fp.webrtc.exists = typeof window.RTCPeerConnection === 'function';
        } else {
            fp.webrtc.available = false;
        }
    } catch(e) { fp.webrtc.error = e.message; }

    // 23. COMPUTE COMPOSITE HASH (exclude time-varying fields for stability)
    const _tf = fp.locale?.timeFormat; const _df = fp.locale?.dateFormat;
    if (fp.locale) { fp.locale.timeFormat = 'HH:MM:SS'; fp.locale.dateFormat = 'YYYY-MM-DD'; }
    fp.composite_hash = await hashString(JSON.stringify(fp));
    if (fp.locale) { fp.locale.timeFormat = _tf; fp.locale.dateFormat = _df; }

    // 24. TIMESTAMP
    fp.timestamp = Date.now();
    fp.iso_time = new Date().toISOString();

    return fp;
}

async function hashString(str) {
    const encoder = new TextEncoder();
    const data = encoder.encode(str);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, '0')).join('');
}

async function hashBlob(blob) {
    const buffer = await blob.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, '0')).join('');
}
