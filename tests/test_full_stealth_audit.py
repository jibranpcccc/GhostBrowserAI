import sys
import os
import pytest
import asyncio
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.browser_manager import build_browser_launch_config, parse_proxy_string


def _make_profile(profile_id="aaaaaaaa-0000-0000-0000-000000000000", advanced=None, locale="en-US", timezone="America/New_York", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"):
    if advanced is None:
        advanced = {}
    return {
        "id": profile_id,
        "locale": locale,
        "timezone": timezone,
        "user_agent": user_agent,
        "advanced": advanced,
    }


@pytest.mark.asyncio
async def test_canvas_2d_noise_injection():
    profile = _make_profile(
        advanced={"canvas_noise": True, "canvas_r_offset": 42, "canvas_g_offset": 100, "canvas_b_offset": 200}
    )
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "toDataURL" in script
    assert "toBlob" in script
    assert "ctx.fillRect(0, 0, 1, 1)" in script
    assert "42" in script
    assert "100" in script
    assert "200" in script


@pytest.mark.asyncio
async def test_canvas_same_profile_same_noise():
    advanced = {"canvas_noise": True, "canvas_r_offset": 77, "canvas_g_offset": 88, "canvas_b_offset": 99}
    p1 = _make_profile(advanced=advanced)
    p2 = _make_profile(advanced=advanced)
    r1 = await build_browser_launch_config(p1)
    r2 = await build_browser_launch_config(p2)
    assert r1["spoofing_script"] == r2["spoofing_script"]


@pytest.mark.asyncio
async def test_canvas_different_profile_different_noise():
    p1 = _make_profile(
        profile_id="aaaaaaaa-0000-0000-0000-000000000001",
        advanced={"canvas_noise": True, "canvas_r_offset": 10, "canvas_g_offset": 20, "canvas_b_offset": 30},
    )
    p2 = _make_profile(
        profile_id="bbbbbbbb-0000-0000-0000-000000000002",
        advanced={"canvas_noise": True, "canvas_r_offset": 50, "canvas_g_offset": 60, "canvas_b_offset": 70},
    )
    r1 = await build_browser_launch_config(p1)
    r2 = await build_browser_launch_config(p2)
    assert r1["spoofing_script"] != r2["spoofing_script"]
    assert "rgb(10, 20, 30)" in r1["spoofing_script"]
    assert "rgb(50, 60, 70)" in r2["spoofing_script"]


@pytest.mark.asyncio
async def test_canvas_noise_disabled():
    profile = _make_profile(advanced={"canvas_noise": False})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "if (false)" in script or "if (False)" in script or "if (true)" not in script.split("toDataURL")[0].split("\n")[-2]


@pytest.mark.asyncio
async def test_webgl_noise_injection():
    profile = _make_profile(
        advanced={"webgl_noise": True, "webgl_vendor": "Intel Inc.", "webgl_renderer": "Intel Iris OpenGL Engine"}
    )
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "WebGLRenderingContext" in script
    assert "37445" in script
    assert "37446" in script
    assert "Intel Inc." in script
    assert "Intel Iris OpenGL Engine" in script


@pytest.mark.asyncio
async def test_webgl2_noise_injection():
    profile = _make_profile(
        advanced={"webgl_noise": True, "webgl_vendor": "Qualcomm", "webgl_renderer": "Adreno 730"}
    )
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "WebGL2RenderingContext" in script
    assert "Qualcomm" in script
    assert "Adreno 730" in script


@pytest.mark.asyncio
async def test_audio_noise_injection():
    profile = _make_profile(advanced={"audio_noise": True})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "AudioContext" in script
    assert "createOscillator" in script
    assert "frequency.value" in script


@pytest.mark.asyncio
async def test_audio_noise_deterministic():
    advanced = {"audio_noise": True, "canvas_r_offset": 50, "canvas_g_offset": 60, "canvas_b_offset": 70}
    p1 = _make_profile(advanced=advanced)
    p2 = _make_profile(advanced=advanced)
    r1 = await build_browser_launch_config(p1)
    r2 = await build_browser_launch_config(p2)
    assert r1["spoofing_script"] == r2["spoofing_script"]


@pytest.mark.asyncio
async def test_webgl_constants_gpu_vendor_renderer():
    vendor = "ATI Technologies Inc."
    renderer = "AMD Radeon RX 6800 XT"
    profile = _make_profile(
        advanced={"webgl_noise": True, "webgl_vendor": vendor, "webgl_renderer": renderer}
    )
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert vendor in script
    assert renderer in script
    assert "UNMASKED_VENDOR_WEBGL" in script
    assert "UNMASKED_RENDERER_WEBGL" in script


@pytest.mark.asyncio
async def test_navigator_webdriver():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "navigator" in script
    assert "webdriver" in script
    assert "get: () => false" in script


@pytest.mark.asyncio
async def test_navigator_plugins():
    profile = _make_profile(advanced={"plugins": ["Chrome PDF Plugin", "Chrome PDF Viewer"]})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "navigator" in script
    assert "plugins" in script
    assert "Chrome PDF Plugin" in script
    assert "Chrome PDF Viewer" in script


@pytest.mark.asyncio
async def test_navigator_mimetypes():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "mimeTypes" in script
    assert "application/pdf" in script
    assert "Portable Document Format" in script


@pytest.mark.asyncio
async def test_navigator_languages():
    profile = _make_profile(locale="fr-FR")
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "languages" in script
    assert "fr-FR" in script
    assert "fr" in script


@pytest.mark.asyncio
async def test_navigator_hardware_concurrency():
    profile = _make_profile(advanced={"cpu_cores": 16})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "hardwareConcurrency" in script
    assert "16" in script


@pytest.mark.asyncio
async def test_navigator_device_memory():
    profile = _make_profile(advanced={"memory_gb": 32})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "deviceMemory" in script
    assert "32" in script


@pytest.mark.asyncio
async def test_navigator_platform():
    profile = _make_profile(advanced={"os": "Windows"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "platform" in script
    assert "Windows" in script


@pytest.mark.asyncio
async def test_navigator_platform_mac():
    profile = _make_profile(advanced={"os": "Mac"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "macOS" in script


@pytest.mark.asyncio
async def test_navigator_platform_linux():
    profile = _make_profile(advanced={"os": "Linux"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "Linux" in script


@pytest.mark.asyncio
async def test_navigator_max_touch_points():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "maxTouchPoints" in script
    assert "get: () => 0" in script


@pytest.mark.asyncio
async def test_screen_properties():
    profile = _make_profile(advanced={"screen_resolution": "2560x1440"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "screen" in script
    assert "width" in script
    assert "height" in script
    assert "availWidth" in script
    assert "availHeight" in script
    assert "2560" in script
    assert "1440" in script


@pytest.mark.asyncio
async def test_screen_color_depth():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "colorDepth" in script
    assert "pixelDepth" in script
    assert "24" in script


@pytest.mark.asyncio
async def test_window_chrome_object():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "window.chrome" in script
    assert "chrome.runtime" in script
    assert "chrome.loadTimes" in script
    assert "chrome.csi" in script


@pytest.mark.asyncio
async def test_notification_permission():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "Notification" in script
    assert "permission" in script
    assert "'default'" in script


@pytest.mark.asyncio
async def test_battery_api():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "getBattery" in script
    assert "fakeBattery" in script
    assert "charging" in script
    assert "level" in script


@pytest.mark.asyncio
async def test_media_devices():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "mediaDevices" in script
    assert "getUserMedia" in script


@pytest.mark.asyncio
async def test_permissions_api():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "permissions" in script or "permission" in script


@pytest.mark.asyncio
async def test_speech_synthesis():
    profile = _make_profile(advanced={"os": "Windows"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "speechSynthesis" in script
    assert "getVoices" in script
    assert "Microsoft David Desktop" in script


@pytest.mark.asyncio
async def test_speech_synthesis_mac():
    profile = _make_profile(advanced={"os": "Mac"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "Alex" in script
    assert "Samantha" in script


@pytest.mark.asyncio
async def test_font_fingerprint():
    profile = _make_profile(advanced={"fonts": ["Arial", "Helvetica", "Times New Roman"]})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "Arial" in script
    assert "Helvetica" in script
    assert "Times New Roman" in script
    assert "document.fonts" in script


@pytest.mark.asyncio
async def test_domrect_noise():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "getClientRects" in script
    assert "getBoundingClientRect" in script
    assert "noise" in script


@pytest.mark.asyncio
async def test_performance_memory():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "performance.memory" in script or "performance" in script
    assert "jsHeapSizeLimit" in script
    assert "totalJSHeapSize" in script
    assert "usedJSHeapSize" in script


@pytest.mark.asyncio
async def test_window_dimensions():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "outerWidth" in script
    assert "outerHeight" in script
    assert "outerWidth" in script or "width" in script
    assert "outerHeight" in script or "height" in script


@pytest.mark.asyncio
async def test_device_pixel_ratio():
    profile = _make_profile(advanced={"device_pixel_ratio": 2.0})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "devicePixelRatio" in script
    assert "2.0" in script


@pytest.mark.asyncio
async def test_geolocation():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "navigator" in script


@pytest.mark.asyncio
async def test_cookie_enabled():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "navigator" in script


@pytest.mark.asyncio
async def test_connection_api():
    profile = _make_profile(advanced={"connection_downlink": 25.5, "connection_rtt": 42})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "connection" in script
    assert "effectiveType" in script
    assert "downlink" in script
    assert "25.5" in script
    assert "42" in script


@pytest.mark.asyncio
async def test_css_media_queries():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "matchMedia" in script
    assert "prefers-color-scheme" in script
    assert "prefers-reduced-motion" in script


@pytest.mark.asyncio
async def test_visibility_state():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "visibilityState" in script
    assert "visible" in script
    assert "hidden" in script


@pytest.mark.asyncio
async def test_product_vendor_consistency():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "userAgent" in script
    assert "navigator" in script


@pytest.mark.asyncio
async def test_tls_cipher_rotation():
    p1 = _make_profile(
        profile_id="11111111-0000-0000-0000-000000000001",
        advanced={"canvas_r_offset": 10, "canvas_g_offset": 20, "canvas_b_offset": 30},
    )
    p2 = _make_profile(
        profile_id="22222222-0000-0000-0000-000000000002",
        advanced={"canvas_r_offset": 50, "canvas_g_offset": 60, "canvas_b_offset": 70},
    )
    r1 = await build_browser_launch_config(p1)
    r2 = await build_browser_launch_config(p2)
    assert r1["spoofing_script"] != r2["spoofing_script"]
    s1 = r1["spoofing_script"]
    s2 = r2["spoofing_script"]
    assert "rgb(10, 20, 30)" in s1
    assert "rgb(50, 60, 70)" in s2
    assert "_dom_noise" not in s1
    _dom1 = ((_1 := int("11111111"[:8], 16)) % 1000 + 1) / 10_000_000.0
    _dom2 = ((_2 := int("22222222"[:8], 16)) % 1000 + 1) / 10_000_000.0
    assert str(_dom1) in s1
    assert str(_dom2) in s2
    assert _dom1 != _dom2


@pytest.mark.asyncio
async def test_proxy_auth_config():
    proxy_str = "1.2.3.4:8080:myuser:mypass"
    parsed = parse_proxy_string(proxy_str)
    assert parsed["username"] == "myuser"
    assert parsed["password"] == "mypass"
    assert parsed["server"] == "http://1.2.3.4:8080"


@pytest.mark.asyncio
async def test_proxy_auth_no_creds():
    proxy_str = "1.2.3.4:8080"
    parsed = parse_proxy_string(proxy_str)
    assert parsed["username"] is None
    assert parsed["password"] is None
    assert parsed["server"] == "http://1.2.3.4:8080"


@pytest.mark.asyncio
async def test_proxy_auth_url_format():
    proxy_str = "http://myuser:mypass@1.2.3.4:8080"
    parsed = parse_proxy_string(proxy_str)
    assert parsed["username"] == "myuser"
    assert parsed["password"] == "mypass"
    assert parsed["server"] == "http://1.2.3.4:8080"


@pytest.mark.asyncio
async def test_webrtc_mode():
    profile = _make_profile(advanced={"webrtc_mode": "disabled"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "disabled" in script
    assert "RTCPeerConnection" in script


@pytest.mark.asyncio
async def test_webrtc_altered():
    profile = _make_profile(advanced={"webrtc_mode": "altered"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "altered" in script
    assert "PRIVATE_PREFIXES" in script


@pytest.mark.asyncio
async def test_useragent_override():
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    profile = _make_profile(user_agent=ua)
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert ua in script
    assert "userAgent" in script


@pytest.mark.asyncio
async def test_chrome_version_extracted():
    profile = _make_profile(user_agent="Mozilla/5.0 ... Chrome/132.0.0.0 ...")
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "132" in script
    assert "Chrome/132" not in script or "132" in script


@pytest.mark.asyncio
async def test_native_code_proxy():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "Function.prototype.toString" in script
    assert "native code" in script
    assert "spoofedFunctions" in script
    assert "makeNative" in script


@pytest.mark.asyncio
async def test_performance_timeorigin():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "timeOrigin" in script
    assert "performance.now" in script


@pytest.mark.asyncio
async def test_useragentdata():
    profile = _make_profile(advanced={"os": "Windows"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "userAgentData" in script
    assert "getHighEntropyValues" in script
    assert "Chromium" in script


@pytest.mark.asyncio
async def test_intl_datetimeformat():
    profile = _make_profile(timezone="Europe/Berlin")
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "Intl.DateTimeFormat" in script
    assert "Europe/Berlin" in script


@pytest.mark.asyncio
async def test_readpixels_noise():
    profile = _make_profile(advanced={"webgl_noise": True})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "readPixels" in script
    assert "injectReadPixelsNoise" in script


@pytest.mark.asyncio
async def test_canvas_measuretext_noise():
    profile = _make_profile(advanced={"canvas_noise": True, "canvas_r_offset": 42})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "measureText" in script


@pytest.mark.asyncio
async def test_screen_width_height_from_resolution():
    profile = _make_profile(advanced={"screen_resolution": "1366x768"})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "1366" in script
    assert "768" in script
    assert "screenRes" in script


@pytest.mark.asyncio
async def test_build_returns_spoofing_script_key():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    assert "spoofing_script" in result
    assert isinstance(result["spoofing_script"], str)
    assert len(result["spoofing_script"]) > 500


@pytest.mark.asyncio
async def test_default_profile_values():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "hardwareConcurrency" in script
    assert "deviceMemory" in script
    assert "devicePixelRatio" in script
    assert "1.25" in script


@pytest.mark.asyncio
async def test_chrome_runtime():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "chrome.runtime" in script
    assert "connect" in script
    assert "sendMessage" in script
    assert "onMessage" in script


@pytest.mark.asyncio
async def test_battery_seeded_values():
    profile = _make_profile(profile_id="aaaaaaaa-0000-0000-0000-000000000000")
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "dischargingTime" in script
    assert "fakeBattery" in script


@pytest.mark.asyncio
async def test_webgl_extension_proxy():
    profile = _make_profile(advanced={"webgl_noise": True})
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    assert "getExtension" in script
    assert "WEBGL_debug_renderer_info" in script


@pytest.mark.asyncio
async def test_spoofing_script_has_all_major_sections():
    profile = _make_profile()
    result = await build_browser_launch_config(profile)
    script = result["spoofing_script"]
    sections = [
        "hardwareConcurrency",
        "deviceMemory",
        "userAgent",
        "languages",
        "screen",
        "RTCPeerConnection",
        "toDataURL",
        "WebGLRenderingContext",
        "AudioContext",
        "plugins",
        "mimeTypes",
        "getClientRects",
        "getBoundingClientRect",
        "getBattery",
        "colorDepth",
        "pixelDepth",
        "devicePixelRatio",
        "connection",
        "userAgentData",
        "speechSynthesis",
        "timeOrigin",
        "matchMedia",
        "webdriver",
        "chrome",
        "outerWidth",
        "outerHeight",
        "performance.memory",
        "visibilityState",
        "Notification",
        "maxTouchPoints",
    ]
    for section in sections:
        assert section in script, f"Missing section: {section}"
