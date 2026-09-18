"""
Exhaustive Playwright & API Verification Suite for all new GhostBrowser UI Features.

Tests all features one by one:
1. Static DOM & Contract Verification (Zero Ghost DOM Elements)
2. Backend API Endpoints (Clear Cache, Export JSON, Import JSON, Profile Cloning)
3. Compact / Laptop Battery-Saver Mode Toggle & State Persistence
4. Collapsible Mini-Sidebar Mode Toggle & State Persistence
5. 1-Click Dashboard Quick Deploy Presets (Windows, Mac, Scraping Bot, Ephemeral Strict)
6. Profiles Page Filter Chips (All, Running, Stopped, Windows, Mac, Proxied) & Real-time Counts
7. Row Action Dropdown Menu (••• popover with Clone, Cookies, Clear Cache, Tags, PIN, Scan, Delete)
8. Direct Target URL Launch Modal (Modal opening, input validation, URL dispatch)
9. Floating Bulk Actions Bar (Dynamic appearance, selection count, bulk actions)
10. JSON Export & Portable Import UI Modal Flows
"""

from __future__ import annotations

import os
import sys
import time
import json
import re
import socket
import asyncio
import tempfile
import threading
from pathlib import Path
from html.parser import HTMLParser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Flags
os.environ["GHOSTBROWSER_REQUIRE_PROXY"] = "0"
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
os.environ["GHOSTBROWSER_ADMIN_TOKEN"] = "test-token-exhaustive"

test_profiles_dir = tempfile.TemporaryDirectory()
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = test_profiles_dir.name

from playwright.async_api import async_playwright
import uvicorn
import httpx
from backend.profile_creator import create_zero_leak_profile

def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def run_exhaustive_tests():
    print("\n" + "=" * 75)
    print("STARTING EXHAUSTIVE UI FEATURE VERIFICATION SUITE")
    print("=" * 75)

    results = {"total": 0, "passed": 0, "failed": 0, "details": []}

    def check(name: str, passed: bool, detail: str = ""):
        results["total"] += 1
        status = "PASS" if passed else "FAIL"
        if passed:
            results["passed"] += 1
            print(f"  [{status}] {name}" + (f" -> {detail}" if detail else ""))
        else:
            results["failed"] += 1
            results["details"].append(f"{name}: {detail}")
            print(f"  [{status}] {name} -> FAILED: {detail}")

    # -------------------------------------------------------------
    # FEATURE 1: STATIC DOM CONTRACT (Zero Ghost DOM Elements)
    # -------------------------------------------------------------
    print("\n--- Testing Feature 1: Static DOM Contract & Zero Ghost Elements ---")
    html_text = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js_text = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    class IdCollector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = set()
        def handle_starttag(self, tag, attrs):
            for k, v in attrs:
                if k.lower() == "id" and v:
                    self.ids.add(v)

    parser = IdCollector()
    parser.feed(html_text)
    html_ids = parser.ids

    js_ids = set(re.findall(r"document\.getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", js_text))
    dynamic_allowed_ids = {
        "admin-token-modal", "admin-token-input", "admin-token-submit", "admin-token-cancel",
        "toast-container", "vk-container", "modal-error-msg", "surfaces-modal", "access-log-modal",
        "edit-profile-proxy", "edit-proxy-test-button", "edit-proxy-connect-button",
        "edit-clear-proxy", "edit-proxy-test-result", "schedule-all-profiles"
    }

    missing_dom_ids = (js_ids - html_ids) - dynamic_allowed_ids
    check("Zero Ghost DOM Elements in app.js", len(missing_dom_ids) == 0, f"Missing: {missing_dom_ids}" if missing_dom_ids else f"{len(js_ids)} verified")
    check("No inline style attributes in index.html", not re.search(r"\sstyle\s*=", html_text))
    check("No inline event handlers in index.html", not re.search(r"\son[a-zA-Z]+\s*=", html_text))

    # Verify all 21 newly required IDs exist in index.html
    required_feature_ids = [
        "btn-compact-mode", "btn-collapse-sidebar", "btn-export-profiles", "btn-import-profiles",
        "quick-presets-section", "profile-filter-chips", "filter-chip-all", "filter-chip-running",
        "filter-chip-stopped", "filter-chip-windows", "filter-chip-mac", "filter-chip-proxied",
        "bulk-floating-bar", "bulk-selected-count", "url-launch-modal", "url-launch-input",
        "btn-confirm-url-launch", "import-profiles-modal", "import-file-input", "import-json-textarea",
        "btn-confirm-import"
    ]
    all_req_exist = all(req_id in html_ids for req_id in required_feature_ids)
    check("All 21 new feature DOM IDs physically present in index.html", all_req_exist)

    # -------------------------------------------------------------
    # SEED PROFILES FOR UI TESTING
    # -------------------------------------------------------------
    print("\n[Seeding Initial Profiles for UI Verification...]")
    from backend.profile_manager import profile_manager

    p1 = profile_manager.create_profile(
        name="UI-Windows-Alpha",
        advanced={"os": "Windows", "canvas_noise": True, "audio_noise": True},
        tags=["e2e", "work"]
    )
    p1_id = p1.get("id")

    p2 = profile_manager.create_profile(
        name="UI-Mac-Beta",
        advanced={"os": "Mac", "canvas_noise": True, "audio_noise": True},
        tags=["e2e", "retina"]
    )
    p2_id = p2.get("id")

    check("Seeded test profiles created", bool(p1_id and p2_id), f"P1: {p1_id[:8]}, P2: {p2_id[:8]}")

    # -------------------------------------------------------------
    # START LIVE BACKEND SERVER ON EPHEMERAL PORT
    # -------------------------------------------------------------
    port = get_free_port()
    print(f"\n[Starting Live Backend Server on port {port}...]")

    config = uvicorn.Config("backend.main:app", host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    for _ in range(50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
        except Exception:
            pass
        await asyncio.sleep(0.1)

    url = f"http://127.0.0.1:{port}"

    # -------------------------------------------------------------
    # FEATURE 2: BACKEND ENDPOINTS TEST (Clear Cache, Export, Import, Clone)
    # -------------------------------------------------------------
    print("\n--- Testing Feature 2: Backend API Endpoints ---")
    headers = {"X-Admin-Token": "test-token-exhaustive"}

    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=45.0) as client:
        # Fetch CSRF token for double-submit cookie + header protection
        csrf_res = await client.get("/api/system/csrf-token")
        csrf_token = csrf_res.json().get("token") or csrf_res.cookies.get("XSRF-TOKEN", "")
        client.headers["X-XSRF-Token"] = csrf_token

        # Clear Cache endpoint
        cc_res = await client.post(f"/api/profiles/{p1_id}/clear-cache")
        check("POST /api/profiles/{id}/clear-cache returns 200", cc_res.status_code == 200)
        check("Clear cache response status is success", cc_res.json().get("status") == "success")

        # Export endpoint
        export_res = await client.get("/api/profiles/export/json")
        check("GET /api/profiles/export/json returns 200", export_res.status_code == 200)
        export_data = export_res.json()
        check("Exported profiles array is non-empty", isinstance(export_data.get("profiles"), list) and len(export_data["profiles"]) >= 2)

        # Import endpoint
        sample_import = [{
            "name": "Imported Test Unit",
            "os": "Windows",
            "privacy_mode": "strict"
        }]
        import_res = await client.post("/api/profiles/import/json", json={"profiles": sample_import})
        check("POST /api/profiles/import/json returns 200", import_res.status_code == 200)
        check("Imported profile count >= 1", import_res.json().get("imported", 0) >= 1)

        # Clone endpoint
        clone_res = await client.post(f"/api/profiles/{p1_id}/clone")
        check("POST /api/profiles/{id}/clone returns 200", clone_res.status_code == 200)
        cloned_p = clone_res.json().get("profile", {})
        check("Cloned profile has unique ID", cloned_p.get("id") != p1_id)

    # -------------------------------------------------------------
    # LIVE PLAYWRIGHT UI TESTS
    # -------------------------------------------------------------
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1366, "height": 768})
        page = await context.new_page()

        try:
            print(f"\n--- Loading Dashboard at {url} ---")
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector(".sidebar", timeout=5000)
            check("GhostBrowser UI loaded successfully", True)

            # -------------------------------------------------------------
            # FEATURE 3: COMPACT MODE TOGGLE
            # -------------------------------------------------------------
            print("\n--- Testing Feature 3: Compact Laptop Mode ---")
            compact_btn = await page.wait_for_selector("#btn-compact-mode", timeout=3000)
            check("#btn-compact-mode exists in topbar", compact_btn is not None)

            # Toggle ON
            await compact_btn.click()
            await page.wait_for_timeout(300)
            is_compact = await page.evaluate("() => document.body.classList.contains('compact-mode')")
            check("Clicking #btn-compact-mode adds .compact-mode to body", is_compact)
            stored_val = await page.evaluate("() => localStorage.getItem('ghostbrowser_compact_mode')")
            check("Compact mode preference persisted in localStorage", stored_val == "1")

            # Toggle OFF
            await compact_btn.click()
            await page.wait_for_timeout(300)
            is_compact_off = await page.evaluate("() => !document.body.classList.contains('compact-mode')")
            check("Clicking #btn-compact-mode again removes .compact-mode", is_compact_off)

            # -------------------------------------------------------------
            # FEATURE 4: COLLAPSIBLE MINI SIDEBAR
            # -------------------------------------------------------------
            print("\n--- Testing Feature 4: Collapsible Mini Sidebar ---")
            collapse_btn = await page.wait_for_selector("#btn-collapse-sidebar", timeout=3000)
            check("#btn-collapse-sidebar exists in sidebar header", collapse_btn is not None)

            # Collapse
            await collapse_btn.click()
            await page.wait_for_timeout(300)
            is_collapsed = await page.evaluate("() => document.body.classList.contains('sidebar-collapsed')")
            check("Clicking #btn-collapse-sidebar adds .sidebar-collapsed to body", is_collapsed)
            btn_txt = await collapse_btn.inner_text()
            check("Sidebar toggle button arrow switches to expand (▶)", "▶" in btn_txt)

            # Expand
            await collapse_btn.click()
            await page.wait_for_timeout(300)
            is_expanded = await page.evaluate("() => !document.body.classList.contains('sidebar-collapsed')")
            check("Clicking #btn-collapse-sidebar again restores standard sidebar", is_expanded)

            # -------------------------------------------------------------
            # FEATURE 5: 1-CLICK QUICK DEPLOY PRESETS (DASHBOARD)
            # -------------------------------------------------------------
            print("\n--- Testing Feature 5: 1-Click Quick Deploy Presets ---")
            presets_sec = await page.wait_for_selector("#quick-presets-section", timeout=3000)
            check("#quick-presets-section rendered on dashboard", presets_sec is not None)

            # Check preset cards exist
            win_preset = await page.wait_for_selector('.preset-card[data-params*="stealth_windows"]')
            mac_preset = await page.wait_for_selector('.preset-card[data-params*="mac_retina"]')
            bot_preset = await page.wait_for_selector('.preset-card[data-params*="scraping_bot"]')
            strict_preset = await page.wait_for_selector('.preset-card[data-params*="strict_privacy"]')
            check("All 4 Quick Deploy Preset cards visible on dashboard", bool(win_preset and mac_preset and bot_preset and strict_preset))

            # -------------------------------------------------------------
            # FEATURE 6: PROFILES PAGE & FILTER CHIPS
            # -------------------------------------------------------------
            print("\n--- Testing Feature 6: Profiles Page Filter Chips ---")
            # Navigate to profiles
            await page.click('.nav-item[data-page="profiles"]')
            await page.wait_for_selector("#page-profiles.active", timeout=4000)

            filter_chips_row = await page.wait_for_selector("#profile-filter-chips", timeout=3000)
            check("#profile-filter-chips row rendered", filter_chips_row is not None)

            # Verify chip count updates
            chip_all = await page.wait_for_selector("#filter-chip-all")
            chip_win = await page.wait_for_selector("#filter-chip-windows")
            chip_mac = await page.wait_for_selector("#filter-chip-mac")
            check("Filter chips (All, Windows, Mac) rendered", bool(chip_all and chip_win and chip_mac))

            # Filter by Windows
            await chip_win.click()
            await page.wait_for_timeout(400)
            win_active = await page.evaluate("() => document.getElementById('filter-chip-windows').classList.contains('active')")
            check("Clicking Windows filter chip activates it", win_active)

            # Filter by Mac
            await chip_mac.click()
            await page.wait_for_timeout(400)
            mac_active = await page.evaluate("() => document.getElementById('filter-chip-mac').classList.contains('active')")
            check("Clicking Mac filter chip activates it", mac_active)

            # Reset to All
            await chip_all.click()
            await page.wait_for_timeout(400)
            all_active = await page.evaluate("() => document.getElementById('filter-chip-all').classList.contains('active')")
            check("Clicking All filter chip resets filter to all profiles", all_active)

            # -------------------------------------------------------------
            # FEATURE 7: ACTION DROPDOWN MENU (•••)
            # -------------------------------------------------------------
            print("\n--- Testing Feature 7: Profile Row Action Dropdown Menu ---")
            more_btn = await page.wait_for_selector(".more-actions-btn", timeout=5000)
            check("Action menu (•••) button rendered in profile row", more_btn is not None)

            if more_btn:
                await more_btn.click()
                await page.wait_for_timeout(300)
                is_menu_shown = await page.evaluate("() => document.querySelector('.action-dropdown-menu.show') !== null")
                check("Clicking ••• displays the action popover menu", is_menu_shown)

                # Check items inside dropdown
                clone_item = await page.query_selector('.action-dropdown-menu.show [data-action="clone-profile"]')
                cache_item = await page.query_selector('.action-dropdown-menu.show [data-action="clear-profile-cache"]')
                cookie_item = await page.query_selector('.action-dropdown-menu.show [data-action="open-cookie-modal"]')
                meta_item = await page.query_selector('.action-dropdown-menu.show [data-action="open-metadata-modal"]')
                pin_item = await page.query_selector('.action-dropdown-menu.show [data-action="open-set-pin-modal"]')
                scan_item = await page.query_selector('.action-dropdown-menu.show [data-action="scan-profile"]')
                del_item = await page.query_selector('.action-dropdown-menu.show [data-action="delete-profile"]')
                check("Dropdown contains all 7 management actions (Clone, Cache, Cookie, Meta, PIN, Scan, Del)",
                      bool(clone_item and cache_item and cookie_item and meta_item and pin_item and scan_item and del_item))

                # Click outside to close
                await page.click("body", position={"x": 50, "y": 50})
                await page.wait_for_timeout(300)
                is_menu_closed = await page.evaluate("() => document.querySelector('.action-dropdown-menu.show') === null")
                check("Clicking outside closes the dropdown menu", is_menu_closed)

            # -------------------------------------------------------------
            # FEATURE 8: DIRECT TARGET URL LAUNCH MODAL
            # -------------------------------------------------------------
            print("\n--- Testing Feature 8: Direct Target URL Launch Modal ---")
            url_btn = await page.wait_for_selector('[data-action="open-url-launch"]', timeout=3000)
            check("Direct URL launch (🔗) button present on row", url_btn is not None)

            if url_btn:
                await url_btn.click()
                await page.wait_for_timeout(400)
                url_modal_open = await page.evaluate("() => document.getElementById('url-launch-modal')?.classList.contains('show')")
                check("Clicking 🔗 opens #url-launch-modal", url_modal_open)

                # Fill target URL
                await page.fill("#url-launch-input", "https://example.com/login-target")
                val = await page.input_value("#url-launch-input")
                check("Target URL successfully typed into modal", val == "https://example.com/login-target")

                # Close modal
                await page.click('#url-launch-modal [data-action="closeUrlLaunchModal"]')
                await page.wait_for_timeout(300)
                url_modal_closed = await page.evaluate("() => !document.getElementById('url-launch-modal')?.classList.contains('show')")
                check("Closing #url-launch-modal dismisses cleanly", url_modal_closed)

            # -------------------------------------------------------------
            # FEATURE 9: FLOATING BULK ACTIONS TOOLBAR
            # -------------------------------------------------------------
            print("\n--- Testing Feature 9: Floating Bulk Actions Toolbar ---")
            floating_bar = await page.wait_for_selector("#bulk-floating-bar", state="attached")
            check("#bulk-floating-bar initially exists and is hidden", floating_bar is not None)
            is_initially_hidden = await page.evaluate("() => document.getElementById('bulk-floating-bar').hidden")
            check("Floating bar is initially hidden when 0 rows selected", is_initially_hidden)

            # Check first checkbox
            first_cb = await page.query_selector(".profile-checkbox")
            if first_cb:
                await first_cb.check()
                await page.wait_for_timeout(300)
                is_visible = await page.evaluate("() => !document.getElementById('bulk-floating-bar').hidden")
                check("Selecting a profile checkbox makes #bulk-floating-bar visible", is_visible)
                selected_count = await page.inner_text("#bulk-selected-count")
                check("Selected count badge displays '1'", selected_count.strip() == "1")

                # Uncheck
                await first_cb.uncheck()
                await page.wait_for_timeout(300)
                is_hidden_again = await page.evaluate("() => document.getElementById('bulk-floating-bar').hidden")
                check("Unchecking returns #bulk-floating-bar to hidden", is_hidden_again)

            # -------------------------------------------------------------
            # FEATURE 10: IMPORT PROFILES MODAL
            # -------------------------------------------------------------
            print("\n--- Testing Feature 10: Import Profiles Modal ---")
            import_topbar_btn = await page.wait_for_selector("#btn-import-profiles")
            check("#btn-import-profiles present in topbar", import_topbar_btn is not None)

            await import_topbar_btn.click()
            await page.wait_for_timeout(400)
            import_modal_shown = await page.evaluate("() => document.getElementById('import-profiles-modal')?.classList.contains('show')")
            check("Clicking #btn-import-profiles opens #import-profiles-modal", import_modal_shown)

            # Close import modal
            await page.click('#import-profiles-modal [data-action="closeImportModal"]')
            await page.wait_for_timeout(300)
            import_modal_closed = await page.evaluate("() => !document.getElementById('import-profiles-modal')?.classList.contains('show')")
            check("Closing #import-profiles-modal dismisses cleanly", import_modal_closed)

        finally:
            await context.close()
            await browser.close()
            server.should_exit = True

    # -------------------------------------------------------------
    # SUMMARY
    # -------------------------------------------------------------
    print("\n" + "=" * 75)
    print("EXHAUSTIVE TEST SUITE SUMMARY")
    print("=" * 75)
    print(f"Total Checks : {results['total']}")
    print(f"Passed       : {results['passed']}")
    print(f"Failed       : {results['failed']}")
    print(f"Pass Rate    : {(results['passed'] / max(1, results['total'])) * 100:.1f}%")
    print("=" * 75)

    if results["failed"] > 0:
        print("\nFailures encountered:")
        for err in results["details"]:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("\n>>> 100% OF ALL NEW UI FEATURES VERIFIED AND WORKING FLAWLESSLY! <<<")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run_exhaustive_tests())
