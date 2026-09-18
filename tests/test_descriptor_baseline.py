"""Regression test for Phase 7: Native Property Descriptor Preservation.

Verifies that GhostBrowser anti-detect definitions reside on prototypes (Navigator.prototype,
Screen.prototype) and do NOT pollute instance own properties.
"""

import os
import tempfile
import pytest
from playwright.async_api import async_playwright
from backend.browser_manager import build_browser_launch_config


@pytest.mark.asyncio
async def test_native_property_descriptors():
    profile_dir = tempfile.mkdtemp(prefix="ghost_desc_test_")
    profile = {
        "id": "11112222-3333-4444-5555-666677778888",
        "name": "descriptor-test",
        "path": profile_dir,
        "proxy": None,
        "timezone": "America/New_York",
        "locale": "en-US",
        "advanced": {
            "os": "Windows",
            "screen_resolution": "1920x1080",
            "device_scale_factor": 1.0,
            "cpu_cores": 8,
            "memory_gb": 16,
        },
    }

    config = await build_browser_launch_config(profile, force_headless=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=config["headless"],
            args=config["args"],
            user_agent=config["user_agent"],
            viewport=config["viewport"],
            device_scale_factor=config["device_scale_factor"],
            locale=config["locale"],
            timezone_id=config["timezone_id"],
        )
        try:
            await context.add_init_script(config["spoofing_script"])
            page = await context.new_page()

            telemetry = await page.evaluate("""
                () => {
                    const checkProp = (obj, prop) => {
                        const isOwn = Object.prototype.hasOwnProperty.call(obj, prop);
                        const desc = Object.getOwnPropertyDescriptor(obj, prop);
                        let val = null;
                        try {
                            if (desc && !desc.get) {
                                val = desc.value;
                            } else if (obj !== Navigator.prototype && obj !== Screen.prototype) {
                                val = obj[prop];
                            }
                        } catch (e) {}
                        return {
                            isOwn: isOwn,
                            hasGetter: desc ? typeof desc.get === 'function' : false,
                            hasSetter: desc ? typeof desc.set === 'function' : false,
                            enumerable: desc ? desc.enumerable : null,
                            configurable: desc ? desc.configurable : null,
                            value: val
                        };
                    };

                    const navProps = {};
                    ['hardwareConcurrency', 'deviceMemory', 'webdriver', 'userAgent', 'platform', 'languages', 'language', 'maxTouchPoints'].forEach(p => {
                        navProps[p] = checkProp(navigator, p);
                    });

                    const protoProps = {};
                    ['hardwareConcurrency', 'webdriver', 'platform', 'languages', 'language', 'maxTouchPoints'].forEach(p => {
                        protoProps[p] = checkProp(Navigator.prototype, p);
                    });

                    const screenProps = {};
                    ['width', 'height', 'availWidth', 'availHeight'].forEach(p => {
                        screenProps[p] = checkProp(window.screen, p);
                    });

                    const screenProtoProps = {};
                    ['width', 'height', 'availWidth', 'availHeight'].forEach(p => {
                        screenProtoProps[p] = checkProp(Screen.prototype, p);
                    });

                    return {
                        navProps,
                        protoProps,
                        screenProps,
                        screenProtoProps
                    };
                }
            """)

            nav = telemetry["navProps"]
            proto = telemetry["protoProps"]
            screen = telemetry["screenProps"]
            screen_proto = telemetry["screenProtoProps"]

            # 1. Navigator instance MUST NOT have own properties for these
            for prop in ['hardwareConcurrency', 'deviceMemory', 'webdriver', 'languages', 'language', 'maxTouchPoints']:
                assert not nav[prop]["isOwn"], f"navigator.{prop} must NOT be an own property"

            # 2. Navigator.prototype MUST have own accessor properties
            for prop in ['hardwareConcurrency', 'webdriver', 'languages', 'language', 'maxTouchPoints']:
                assert proto[prop]["isOwn"], f"Navigator.prototype.{prop} must be an own property"
                assert proto[prop]["hasGetter"], f"Navigator.prototype.{prop} must have a getter"
                assert not proto[prop]["hasSetter"], f"Navigator.prototype.{prop} must NOT have a setter"
                assert proto[prop]["configurable"], f"Navigator.prototype.{prop} must be configurable"

            # 3. Values must match profile
            assert nav["hardwareConcurrency"]["value"] == 8
            assert nav["deviceMemory"]["value"] == 16
            assert nav["webdriver"]["value"] is False

            # 4. Screen instance must NOT have own properties, Screen.prototype MUST
            for prop in ['width', 'height', 'availWidth', 'availHeight']:
                assert not screen[prop]["isOwn"], f"window.screen.{prop} must NOT be an own property"
                assert screen_proto[prop]["isOwn"], f"Screen.prototype.{prop} must be an own property on prototype"
                assert screen_proto[prop]["hasGetter"], f"Screen.prototype.{prop} must have a getter"

            assert screen["width"]["value"] == 1920
            assert screen["height"]["value"] == 1080

        finally:
            await context.close()

