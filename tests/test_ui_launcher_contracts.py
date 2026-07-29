"""Offline regression checks for the management UI and Windows launchers."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
START_BAT = (ROOT / "Start-GhostBrowser.bat").read_text(encoding="utf-8")
BUILD_BAT = (ROOT / "build.bat").read_text(encoding="utf-8")
RUN_SERVER = (ROOT / "run_server.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")


class IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element_id = dict(attrs).get("id")
        if element_id:
            self.ids.append(element_id)


def function_body(source: str, name: str) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
    if not match:
        raise AssertionError(f"Missing function: {name}")
    depth = 0
    for index in range(match.end() - 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise AssertionError(f"Unterminated function: {name}")


def element_ids(source: str) -> set[str]:
    parser = IdCollector()
    parser.feed(source)
    return set(parser.ids)


def first_table_header_count(source: str, tbody_id: str) -> int:
    tbody_match = re.search(
        rf'<tbody\s+id=["\']{re.escape(tbody_id)}["\']',
        source,
        flags=re.IGNORECASE,
    )
    if not tbody_match:
        raise AssertionError(f"Missing tbody: {tbody_id}")
    table_start = source.rfind("<table", 0, tbody_match.start())
    thead_start = source.rfind("<thead", table_start, tbody_match.start())
    thead_end = source.find("</thead>", thead_start, tbody_match.start())
    if min(table_start, thead_start, thead_end) < 0:
        raise AssertionError(f"Missing header for tbody: {tbody_id}")
    return len(re.findall(r"<th(?:\s|>)", source[thead_start:thead_end], re.IGNORECASE))


class UiLauncherContracts(unittest.TestCase):
    def test_html_ids_and_handlers_are_unique_and_defined(self) -> None:
        parser = IdCollector()
        parser.feed(HTML)
        duplicates = [key for key, count in Counter(parser.ids).items() if count > 1]
        self.assertEqual(duplicates, [])

        handlers = set(re.findall(r'onclick="\s*([A-Za-z_$][\w$]*)\s*\(', HTML))
        functions = set(re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", JS, re.MULTILINE))
        self.assertEqual(sorted(handlers - functions), [])

        declarations = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", JS, re.MULTILINE)
        self.assertEqual([key for key, count in Counter(declarations).items() if count > 1], [])

    def test_modal_tabs_fonts_and_mobile_contracts(self) -> None:
        self.assertNotIn("fonts.googleapis.com", HTML)
        self.assertNotIn("fonts.gstatic.com", HTML)
        self.assertNotRegex(HTML, r'id="tab-(?:network|stealth)"[^>]*style="[^"]*display\s*:\s*none')
        self.assertIn(".modal-overlay.show", CSS)
        self.assertIn("--sidebar-width-collapsed:", CSS)
        self.assertIn("@media (max-width: 600px)", CSS)
        self.assertIn("margin-left: 0", CSS)

    def test_automation_schema_and_wrappers(self) -> None:
        self.assertNotIn("cron_expression", JS)
        self.assertNotIn("job_id", JS)
        self.assertNotIn("schedule-profile-select", JS)
        self.assertIn("escHtml(s.cron)", JS)
        self.assertIn('data-action="delete-schedule"', JS)
        for name in ("startCookieRobotWarming", "startSyncSessionWrapper"):
            self.assertIn(f"function {name}", JS)

    def test_metadata_modal_ids_schema_and_pin_unchanged_contract(self) -> None:
        ids = element_ids(HTML)
        for expected_id in (
            "metadata-modal",
            "meta-tags",
            "meta-notes",
            "meta-proxy-pin",
            "meta-clear-proxy-pin",
        ):
            self.assertIn(expected_id, ids)

        open_body = function_body(JS, "openMetadataModal")
        save_body = function_body(JS, "saveMetadata")
        for expected_id in ("meta-tags", "meta-notes", "meta-proxy-pin"):
            self.assertIn(f"'{expected_id}'", open_body)
            self.assertIn(f"'{expected_id}'", save_body)
        for stale_id in ("profile-tags", "proxy-pin"):
            self.assertNotIn(f"'{stale_id}'", open_body)
            self.assertNotIn(f"'{stale_id}'", save_body)

        self.assertRegex(open_body, r"Array\.isArray\(p\.tags\)")
        self.assertRegex(open_body, r"p\.tags\.join\(")
        self.assertIn("p.notes", open_body)
        self.assertRegex(save_body, r"\.split\(\s*['\"]\s*,\s*['\"]\s*\)")
        self.assertRegex(save_body, r"\.map\([^)]*\.trim\(")
        self.assertRegex(save_body, r"\.filter\(\s*Boolean\s*\)")
        self.assertRegex(save_body, r"(?:const|let)\s+payload\s*=\s*\{[^}]*tags[^}]*notes")
        self.assertRegex(save_body, r"if\s*\([^)]*(?:proxyPin|proxy_pin)[^)]*\)[^{]*\{?[^}]*payload\.proxy_pin")
        self.assertNotRegex(save_body, r"JSON\.stringify\(\s*\{[^)]*proxy_pin")
        self.assertIn("clear_proxy_pin", save_body)
        pin_element = re.search(r"<input\b[^>]*\bid=[\"']meta-proxy-pin[\"'][^>]*>", HTML, re.IGNORECASE)
        self.assertIsNotNone(pin_element)
        self.assertRegex(
            pin_element.group(0).lower(),
            r"placeholder=[\"'][^\"']*(?:leave blank|unchanged)",
            "The redacted existing pin must not be silently cleared when only tags/notes are saved.",
        )

    def test_profile_table_has_eight_aligned_cells_and_list_tags(self) -> None:
        self.assertEqual(first_table_header_count(HTML, "profiles-grid"), 8)
        render_body = function_body(JS, "renderProfiles")
        self.assertIn("Array.isArray(p.tags)", render_body)
        self.assertEqual(
            len(re.findall(r"<td(?:\s|>)", render_body, re.IGNORECASE)),
            8,
            "Each generated profile row must align with all eight table headers.",
        )
        self.assertRegex(
            render_body,
            r"<td[^>]*>(?:(?!</td>).)*\$\{tagElements(?:\s*\|\|[^}]*)?\}(?:(?!</td>).)*</td>",
        )

    def test_profile_colors_pinning_and_test_then_connect_proxy_flow(self) -> None:
        ids = element_ids(HTML)
        for expected_id in (
            "edit-profile-proxy",
            "edit-proxy-test-button",
            "edit-proxy-connect-button",
            "edit-clear-proxy",
            "edit-proxy-test-result",
            "meta-profile-pinned",
        ):
            self.assertIn(expected_id, ids)

        render_body = function_body(JS, "renderProfiles")
        self.assertIn("safeProfileColor(p.color)", render_body)
        self.assertIn("profile-row-pinned", render_body)
        self.assertIn("p.pinned", render_body)
        self.assertIn('data-action="toggle-profile-pin"', render_body)

        test_body = function_body(JS, "testEnteredProxy")
        save_body = function_body(JS, "saveProfileEdits")
        connect_body = function_body(JS, "persistEditedProxy")
        self.assertIn("testedProxyValues[inputId] = proxyString", test_body)
        self.assertIn("proxyWasTested('edit-profile-proxy')", save_body)
        self.assertIn("proxyWasTested('edit-profile-proxy')", connect_body)
        self.assertIn("/proxy`", connect_body)
        self.assertIn("clear_proxy", connect_body)
        self.assertIn('data-action="resetProxyTest"', HTML)
        self.assertIn('["edit-profile-proxy"', HTML)

        create_body = function_body(JS, "submitCreateProfile")
        self.assertIn("proxyWasTested('new-profile-proxy')", create_body)

    def test_bulk_create_uses_proxy_string_and_reports_partial_results(self) -> None:
        body = function_body(JS, "submitCreateProfile")
        self.assertIn("proxy_string: proxyRaw || null", body)
        self.assertIn("profile.results", body)
        self.assertIn("failedResults", body)
        self.assertRegex(body, r"profile\.status\s*===\s*['\"]partial['\"]")
        self.assertRegex(body, r"failedResults\.length\s*>\s*0")
        self.assertIn("profile.success_count", body)
        self.assertNotIn("Bulk created ${count} profiles", body)

    def test_cookie_robot_uses_backend_status_field_names(self) -> None:
        body = function_body(JS, "fetchCookieRobotStatus")
        self.assertIn("status.sites_total", body)
        self.assertIn("status.state", body)
        self.assertNotIn("status.total_sites", body)
        self.assertNotIn("status.status", body)

    def test_proxy_authentication_badge_uses_public_boolean_only(self) -> None:
        body = function_body(JS, "fetchProxies")
        self.assertIn("p.authenticated", body)
        self.assertNotIn("p.username", body)
        self.assertNotIn("p.password", body)

    def test_metrics_failure_sets_explicit_offline_health_state(self) -> None:
        body = function_body(JS, "fetchMetrics")
        self.assertIn("Backend Offline", body)
        self.assertIn("health-dot offline", body)

    def test_settings_save_uses_checked_json_request_and_surfaces_error(self) -> None:
        body = function_body(JS, "saveSettings")
        self.assertIn("requestJson(", body)
        self.assertNotIn("await fetch(", body)
        self.assertIn("e.message", body)
        self.assertRegex(body, r"catch\s*\(")

    def test_html_escaping_stringifies_before_escaping_and_classes_are_allowlisted(self) -> None:
        escape_body = function_body(JS, "escHtml")
        self.assertRegex(escape_body, r"String\(\s*s\s*\?\?\s*['\"]['\"]\s*\)")
        self.assertNotRegex(escape_body, r"typeof\s+s\s*!==\s*['\"]string['\"][^}]*return\s+String")
        self.assertIn("function safeUiClass", JS)
        safe_class_body = function_body(JS, "safeUiClass")
        self.assertRegex(safe_class_body, r"(?:includes|has)\(")
        for function_name in ("renderLogs", "addActivity", "showToast"):
            with self.subTest(function=function_name):
                self.assertIn("safeUiClass(", function_body(JS, function_name))
        # Backend role allowlist is enforced server-side; UI renders whatever role
        # string is supplied, relying on safeUiClass for safety on dynamic classes.
        self.assertIn("safeUiClass", JS)

    def test_no_simulated_bypass_claims(self) -> None:
        for forbidden in ("CreepJS", "Sannysoft", "Bypassing", "Zero-Leak Ready", "zero-leak"):
            self.assertNotIn(forbidden, JS)

    def test_mutations_require_successful_http_response(self) -> None:
        for name in ("saveMetadata", "stopProfile", "deleteProfile", "deleteMacro", "deleteSchedule", "stopSyncSession"):
            self.assertIn("requestJson(", function_body(JS, name), name)

    def test_launcher_and_build_fail_closed(self) -> None:
        self.assertIn("venv\\Scripts\\python.exe", START_BAT)
        self.assertIn("run_server.py\" --check", START_BAT)
        self.assertIn("--reuse-existing", START_BAT)
        self.assertIn('if "%PREFLIGHT_EXIT%"=="10"', START_BAT)
        self.assertIn("Opening the existing dashboard", START_BAT)
        self.assertIn("if errorlevel 1", START_BAT.lower())

        self.assertIn('"%PYTHON%" -m PyInstaller', BUILD_BAT)
        self.assertNotIn('--add-data "cloudflare_accounts.txt', BUILD_BAT)
        self.assertNotIn('--add-data "cloudflare_accounts.priority.txt', BUILD_BAT)
        self.assertIn(r'dist\GhostBrowser\cloudflare_accounts.priority.txt', BUILD_BAT)
        self.assertIn("if errorlevel 1", BUILD_BAT.lower())
        self.assertIn("requirements-build.txt", BUILD_BAT)

        self.assertIn("def run_preflight", RUN_SERVER)
        self.assertIn("def _existing_ghostbrowser_is_ready", RUN_SERVER)
        self.assertIn("ALREADY_RUNNING_EXIT = 10", RUN_SERVER)
        self.assertIn("_wait_for_readiness_and_open", RUN_SERVER)
        self.assertIn('getattr(sys, "frozen", False)', RUN_SERVER)
        self.assertNotRegex(RUN_SERVER, r"subprocess\.run\([^\n]*playwright")

    def test_server_preflight_requires_runtime_modules_and_imports_application(self) -> None:
        required_modules = re.search(r"REQUIRED_MODULES\s*=\s*\((.*?)\)", RUN_SERVER, re.DOTALL)
        self.assertIsNotNone(required_modules)
        module_block = required_modules.group(1)
        for module in ("multipart", "croniter"):
            self.assertRegex(module_block, rf'["\']{module}["\']')
        preflight = re.search(
            r"def\s+run_preflight\(.*?\n(?=def\s+_wait_for_readiness_and_open)",
            RUN_SERVER,
            re.DOTALL,
        )
        self.assertIsNotNone(preflight)
        self.assertIn("from backend.main import app", preflight.group(0))
        main_body = re.search(r"def\s+main\(.*?\n(?=if __name__)", RUN_SERVER, re.DOTALL)
        self.assertIsNotNone(main_body)
        self.assertRegex(main_body.group(0), r"run_preflight\([^)]*import_application\s*=\s*True")

    def test_invalid_environment_port_reports_friendly_preflight_error(self) -> None:
        env = os.environ.copy()
        env["GHOSTBROWSER_PORT"] = "definitely-not-a-port"
        result = subprocess.run(
            [sys.executable, str(ROOT / "run_server.py"), "--check", "--no-browser"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[Preflight] ERROR:", output)
        self.assertIn("GHOSTBROWSER_PORT", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("invalid int value", output)

    def test_frozen_build_and_launcher_share_bundled_chromium_contract(self) -> None:
        for component in (CONFIG, BUILD_BAT):
            self.assertIn("playwright-browsers", component)
            self.assertIn("chrome-win64", component)
            self.assertIn("chrome.exe", component)
        self.assertIn('getattr(sys, "frozen", False)', CONFIG)
        self.assertIn("os.path.dirname(sys.executable)", CONFIG)
        self.assertIn('xcopy "%CHROMIUM_DIR%*"', BUILD_BAT)
        self.assertIn("dist\\GhostBrowser\\playwright-browsers\\chrome-win64\\chrome.exe", BUILD_BAT)
        self.assertNotIn('--add-data "cloudflare_accounts.txt', BUILD_BAT)
        self.assertNotIn('--add-data "cloudflare_accounts.priority.txt', BUILD_BAT)

    def test_launcher_display_url_follows_environment_host_and_port(self) -> None:
        self.assertIn("GHOSTBROWSER_HOST", START_BAT)
        self.assertIn("GHOSTBROWSER_PORT", START_BAT)
        self.assertIn("http://%DISPLAY_HOST%:%DISPLAY_PORT%", START_BAT)
        self.assertNotIn("Starting server on http://127.0.0.1:8000", START_BAT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
