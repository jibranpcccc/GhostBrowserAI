"""
GhostBrowser AI — Hardcore Test Suite
Tests ALL backend modules, API endpoints, stealth engine, auth, and frontend integrity.
Run: python -m pytest tests/test_hardcore.py -v
"""
import json
import os
import sys
import time
import hashlib
import tempfile
import shutil
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# SECTION 1: MODULE IMPORT TESTS
# ============================================================
class TestModuleImports:
    """Every module must import without error."""

    def test_import_main(self):
        from backend import main
        assert hasattr(main, 'app')

    def test_import_config(self):
        from backend.config import get_data_dir, get_bundled_dir
        assert callable(get_data_dir)
        assert callable(get_bundled_dir)

    def test_import_db(self):
        from backend import db
        assert hasattr(db, 'init_db')

    def test_import_profile_manager(self):
        from backend.profile_manager import profile_manager
        assert hasattr(profile_manager, 'list_profiles')

    def test_import_browser_manager(self):
        from backend import browser_manager
        assert hasattr(browser_manager, '_generate_spoofing_js')
        assert hasattr(browser_manager, 'launch_profile')

    def test_import_ai_generator(self):
        from backend import ai_generator
        assert hasattr(ai_generator, 'generate_fingerprint_ai') or hasattr(ai_generator, 'MODEL_CHAIN')

    def test_import_ai_auto_validator(self):
        from backend import ai_auto_validator

    def test_import_profile_creator(self):
        from backend.profile_creator import profile_creator
        assert hasattr(profile_creator, 'create_zero_leak_profile')

    def test_import_proxy_manager(self):
        from backend.proxy_manager import proxy_manager
        assert hasattr(proxy_manager, 'resolve_proxy_geo')

    def test_import_cloudflare_manager(self):
        from backend.cloudflare_manager import cloudflare_manager

    def test_import_macro_manager(self):
        from backend.macro_manager import macro_manager

    def test_import_scheduler_manager(self):
        from backend.scheduler_manager import SchedulerManager

    def test_import_system_monitor(self):
        from backend.system_monitor import system_monitor

    def test_import_api_automation(self):
        from backend.api_automation import router
        assert router is not None

    def test_import_synchronizer(self):
        from backend.synchronizer import router

    def test_import_rpa_recorder(self):
        from backend.rpa_recorder import router

    def test_import_profile_folders(self):
        from backend.profile_folders import router

    def test_import_bulk_operations(self):
        from backend.bulk_operations import router

    def test_import_profile_transfer(self):
        from backend.profile_transfer import router

    def test_import_profile_rotator(self):
        from backend.profile_rotator import rotator

    def test_import_cookie_robot(self):
        from backend.cookie_robot import cookie_robot

    def test_import_auth_manager(self):
        from backend.auth_manager import auth_manager, router
        assert hasattr(auth_manager, 'authenticate')
        assert hasattr(auth_manager, 'create_user')

    def test_import_api_keys(self):
        from backend.api_keys import api_key_manager, router
        assert hasattr(api_key_manager, 'create_key')

    def test_import_team_manager(self):
        from backend.team_manager import team_manager, router
        assert hasattr(team_manager, 'add_member')

    def test_import_proxy_providers(self):
        from backend.proxy_providers import provider_manager, router
        assert hasattr(provider_manager, 'add_provider')

    def test_import_local_profile_import(self):
        from backend.local_profile_import import router

    def test_import_extension_manager(self):
        from backend.extension_manager import extension_manager, router
        assert hasattr(extension_manager, 'install_extension')

    def test_import_fingerprint_templates(self):
        from backend.fingerprint_templates import template_manager, router
        assert len(template_manager._templates) >= 10  # At least 10 built-in templates


# ============================================================
# SECTION 2: AUTH SYSTEM TESTS
# ============================================================
class TestAuthSystem:
    """Full auth lifecycle: create user, login, session, permissions, logout."""

    def setup_method(self):
        from backend.auth_manager import AuthManager
        self.auth = AuthManager()
        # Clean up any existing test users
        for u in list(self.auth._users.keys()):
            if u.startswith('test_'):
                del self.auth._users[u]
        self.auth._save()

    def test_create_user(self):
        user = self.auth.create_user('test_user1', 'pass1234', 'Test User', 'member')
        assert user.username == 'test_user1'
        assert user.display_name == 'Test User'
        assert user.role == 'member'

    def test_duplicate_user_rejected(self):
        self.auth.create_user('test_dup', 'pass1234')
        with pytest.raises(ValueError, match="already exists"):
            self.auth.create_user('test_dup', 'pass5678')

    def test_password_hashing(self):
        h1 = self.auth._hash_password('testpass')
        h2 = self.auth._hash_password('testpass')
        assert h1 != h2  # Different salts
        assert self.auth._verify_password('testpass', h1)
        assert not self.auth._verify_password('wrongpass', h1)

    def test_authentication_success(self):
        self.auth.create_user('test_auth', 'mypass123')
        user = self.auth.authenticate('test_auth', 'mypass123')
        assert user is not None
        assert user.username == 'test_auth'

    def test_authentication_wrong_password(self):
        self.auth.create_user('test_auth2', 'mypass123')
        user = self.auth.authenticate('test_auth2', 'wrongpassword')
        assert user is None

    def test_authentication_nonexistent_user(self):
        user = self.auth.authenticate('nonexistent_user_xyz', 'pass')
        assert user is None

    def test_session_create_and_validate(self):
        user = self.auth.create_user('test_sess', 'pass1234')
        token = self.auth.create_session(user, ip='127.0.0.1')
        assert len(token) > 20
        validated = self.auth.validate_session(token)
        assert validated is not None
        assert validated.username == 'test_sess'

    def test_session_expired(self):
        user = self.auth.create_user('test_expiry', 'pass1234')
        token = self.auth.create_session(user)
        # Manually expire the session
        self.auth._sessions[token].expires_at = time.time() - 100
        assert self.auth.validate_session(token) is None

    def test_session_logout(self):
        user = self.auth.create_user('test_logout', 'pass1234')
        token = self.auth.create_session(user)
        assert self.auth.validate_session(token) is not None
        self.auth.logout(token)
        assert self.auth.validate_session(token) is None

    def test_role_permissions(self):
        self.auth.create_user('test_owner', 'pass1234', role='owner')
        self.auth.create_user('test_viewer', 'pass1234', role='viewer')
        owner = self.auth.authenticate('test_owner', 'pass1234')
        viewer = self.auth.authenticate('test_viewer', 'pass1234')
        assert owner.has_permission('delete_profile')
        assert not viewer.has_permission('delete_profile')
        assert viewer.has_permission('view_profile')

    def test_cannot_delete_owner(self):
        self.auth.create_user('test_owner2', 'pass1234', role='owner')
        with pytest.raises(ValueError, match="Cannot delete"):
            self.auth.delete_user('test_owner2')


# ============================================================
# SECTION 3: PROFILE MANAGEMENT TESTS
# ============================================================
class TestProfileManagement:
    """Profile CRUD, search, clone, metadata."""

    def setup_method(self):
        from backend.profile_manager import profile_manager
        self.pm = profile_manager

    def test_list_profiles(self):
        profiles = self.pm.list_profiles()
        assert isinstance(profiles, list)

    def test_create_profile(self):
        advanced = {
            'hardware_concurrency': 8,
            'device_memory': 8,
            'screen_width': 1920,
            'screen_height': 1080,
            'os': 'Windows',
        }
        profile = self.pm.create_profile(name='Test Hardcode Profile', advanced=advanced)
        assert 'id' in profile
        assert profile['name'] == 'Test Hardcode Profile'
        assert os.path.exists(profile['path'])
        # Cleanup
        self.pm.delete_profile(profile['id'])

    def test_get_profile(self):
        profiles = self.pm.list_profiles()
        if profiles:
            p = self.pm.get_profile(profiles[0]['id'])
            assert p is not None
            assert p['id'] == profiles[0]['id']

    def test_update_profile(self):
        profiles = self.pm.list_profiles()
        if profiles:
            pid = profiles[0]['id']
            self.pm.update_profile(pid, {'notes': 'test note'})
            p = self.pm.get_profile(pid)
            assert p.get('notes') == 'test note'
            # Restore
            self.pm.update_profile(pid, {'notes': ''})

    def test_delete_nonexistent_profile(self):
        result = self.pm.delete_profile('nonexistent-id-xyz')
        assert result is False


# ============================================================
# SECTION 4: STEALTH ENGINE TESTS
# ============================================================
class TestStealthEngine:
    """Comprehensive stealth JS generation and validation."""

    def _gen_js(self, os_type='Windows', canvas=True, webgl=True, audio=True):
        from backend.browser_manager import _generate_spoofing_js
        config = {
            'id': 'a1b2c3d4-e5f6-7890-abcd-ef0123456789',
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136.0.0.0 Safari/537.36',
            'timezone': 'America/New_York',
            'locale': 'en-US',
            'advanced': {
                'hardware_concurrency': 8,
                'device_memory': 16,
                'screen_width': 1920,
                'screen_height': 1080,
                'os': os_type,
                'webgl_vendor': 'Google Inc. (NVIDIA)',
                'webgl_renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060)',
                'canvas_noise': canvas,
                'webgl_noise': webgl,
                'audio_noise': audio,
                'dom_rect_noise': True,
                'battery_spoof': True,
            }
        }
        return _generate_spoofing_js(config)

    def _write_and_check_syntax(self, js_code, filename='_test_check.js'):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename)
        with open(path, 'w') as f:
            f.write(js_code)
        import subprocess
        result = subprocess.run(['node', '--check', path], capture_output=True, text=True)
        os.remove(path)
        return result.returncode == 0, result.stderr

    def test_js_length(self):
        js = self._gen_js()
        assert len(js) > 50000, f"JS too short: {len(js)} chars"

    def test_js_syntax_valid(self):
        js = self._gen_js()
        valid, err = self._write_and_check_syntax(js)
        assert valid, f"JS syntax error: {err}"

    def test_webdriver_false(self):
        js = self._gen_js()
        assert 'webdriver' in js
        assert 'false' in js

    def test_chrome_object(self):
        js = self._gen_js()
        assert 'window.chrome' in js
        assert 'chrome.runtime' in js
        assert 'chrome.loadTimes' in js
        assert 'chrome.csi' in js
        assert 'chrome.app' in js

    def test_chrome_platform_os(self):
        js = self._gen_js()
        assert 'PlatformOs' in js
        assert 'MAC' in js
        assert 'WIN' in js
        assert 'ANDROID' in js

    def test_canvas_noise(self):
        js = self._gen_js(canvas=True)
        assert 'toDataURL' in js
        assert 'toBlob' in js
        assert 'getImageData' in js

    def test_webgl_noise(self):
        js = self._gen_js(webgl=True)
        assert 'getParameter' in js
        assert 'readPixels' in js
        assert 'UNMASKED_VENDOR_WEBGL' in js or '37447' in js
        assert 'UNMASKED_RENDERER_WEBGL' in js or '37446' in js

    def test_audio_noise(self):
        js = self._gen_js(audio=True)
        assert 'AudioContext' in js
        assert 'createOscillator' in js
        assert 'getChannelData' in js

    def test_domrect_noise(self):
        js = self._gen_js()
        assert 'getClientRects' in js
        assert 'getBoundingClientRect' in js

    def test_font_noise(self):
        js = self._gen_js()
        assert 'measureText' in js
        assert 'fonts.check' in js or 'document.fonts' in js

    def test_webrtc_blocking(self):
        js = self._gen_js()
        assert 'RTCPeerConnection' in js

    def test_spoofed_plugins(self):
        js = self._gen_js()
        assert 'plugins' in js
        assert 'PluginArray' in js

    def test_spoofed_screen(self):
        js = self._gen_js()
        assert 'screen' in js and 'width' in js

    def test_spoofed_hardware(self):
        js = self._gen_js()
        assert 'hardwareConcurrency' in js
        assert 'deviceMemory' in js

    def test_battery_spoof(self):
        js = self._gen_js()
        assert 'getBattery' in js

    def test_performance_timing(self):
        js = self._gen_js()
        assert 'performance.timeOrigin' in js
        assert 'performance.now' in js
        assert 'navigationStart' in js

    def test_navigation_timing_api(self):
        js = self._gen_js()
        assert 'getEntriesByType' in js
        assert 'navigation' in js

    def test_languages(self):
        js = self._gen_js()
        assert 'languages' in js
        assert 'language' in js

    def test_webdriver_detection_cleaned(self):
        js = self._gen_js()
        assert '$cdc_' in js or '__playwright' in js

    def test_visibility_state(self):
        js = self._gen_js()
        assert 'visibilityState' in js
        assert 'visible' in js

    def test_notification_permission(self):
        js = self._gen_js()
        assert 'Notification.permission' in js

    def test_max_touch_points(self):
        js = self._gen_js()
        assert 'maxTouchPoints' in js

    def test_cookie_enabled(self):
        js = self._gen_js()
        assert 'cookieEnabled' in js

    def test_dnt(self):
        js = self._gen_js()
        assert 'doNotTrack' in js

    def test_enumerate_devices(self):
        js = self._gen_js()
        assert 'enumerateDevices' in js

    def test_permissions_query(self):
        js = self._gen_js()
        assert 'permissions.query' in js

    def test_geolocation(self):
        js = self._gen_js()
        assert 'geolocation' in js
        assert 'getCurrentPosition' in js

    def test_service_worker(self):
        js = self._gen_js()
        assert 'serviceWorker' in js

    def test_crypto_digest(self):
        js = self._gen_js()
        assert 'crypto.subtle' in js
        assert 'digest' in js

    def test_svg_noise(self):
        js = self._gen_js()
        assert 'getBBox' in js

    def test_shared_array_buffer(self):
        js = self._gen_js()
        assert 'SharedArrayBuffer' in js

    def test_hardware_apis_blocked(self):
        js = self._gen_js()
        assert 'bluetooth' in js
        assert 'usb' in js
        assert 'serial' in js

    def test_css_media_queries(self):
        js = self._gen_js()
        assert 'matchMedia' in js
        assert 'prefers-reduced-motion' in js

    def test_screen_orientation(self):
        js = self._gen_js()
        assert 'screen.orientation' in js

    def test_webgpu(self):
        js = self._gen_js()
        assert 'navigator.gpu' in js or 'requestAdapter' in js

    def test_credentials(self):
        js = self._gen_js()
        assert 'navigator.credentials' in js

    def test_user_activation(self):
        js = self._gen_js()
        assert 'userActivation' in js

    def test_storage_apis(self):
        js = self._gen_js()
        assert 'storage.estimate' in js
        assert 'storage.persist' in js
        assert 'storage.persisted' in js

    def test_visual_viewport(self):
        js = self._gen_js()
        assert 'visualViewport' in js

    def test_intl_datetime(self):
        js = self._gen_js()
        assert 'Intl.DateTimeFormat' in js

    def test_speech_synthesis(self):
        js = self._gen_js()
        assert 'speechSynthesis' in js
        assert 'getVoices' in js

    def test_performance_observer(self):
        js = self._gen_js()
        assert 'PerformanceObserver' in js
        assert 'supportedEntryTypes' in js

    def test_gamepad_api(self):
        js = self._gen_js()
        assert 'getGamepads' in js

    def test_fonts_ready(self):
        js = self._gen_js()
        assert 'document.fonts' in js

    def test_offscreen_canvas(self):
        js = self._gen_js()
        assert 'OffscreenCanvas' in js

    def test_worker_patch(self):
        js = self._gen_js()
        assert 'Worker' in js

    def test_user_agent_data(self):
        js = self._gen_js()
        assert 'userAgentData' in js
        assert 'getHighEntropyValues' in js

    def test_connection(self):
        js = self._gen_js()
        assert 'navigator.connection' in js

    def test_error_stack(self):
        js = self._gen_js()
        assert 'captureStackTrace' in js

    def test_mac_os_specific(self):
        js = self._gen_js(os_type='Mac')
        assert 'MacIntel' in js
        assert 'Safari' in js or 'Apple' in js

    def test_linux_os_specific(self):
        js = self._gen_js(os_type='Linux')
        assert 'Linux' in js

    def test_performance_memory(self):
        js = self._gen_js()
        assert 'performance.memory' in js

    def test_outer_dimensions(self):
        js = self._gen_js()
        assert 'outerWidth' in js
        assert 'outerHeight' in js

    def test_webgl_wp_dict_values(self):
        js = self._gen_js()
        # Check critical WebGL constants exist
        assert '32776' in js  # ALIASED_POINT_SIZE_RANGE
        assert '32777' in js  # ALIASED_LINE_WIDTH_RANGE
        assert '36348' in js  # MAX_VARYING_VECTORS
        assert '34383' in js  # MAX_VIEWPORT_DIMS

    def test_font_enumeration_noise(self):
        js = self._gen_js()
        assert 'offsetWidth' in js

    def test_css_prefers_color(self):
        js = self._gen_js()
        assert 'prefers-color-scheme' in js

    def test_internet_date_format(self):
        js = self._gen_js()
        assert 'Date' in js


# ============================================================
# SECTION 5: FINGERPRINT TEMPLATE TESTS
# ============================================================
class TestFingerprintTemplates:
    """Template library operations."""

    def test_builtin_templates_count(self):
        from backend.fingerprint_templates import template_manager
        templates = template_manager.list_templates()
        assert len(templates) >= 10

    def test_template_categories(self):
        from backend.fingerprint_templates import template_manager
        desktop = template_manager.list_templates(category='desktop')
        mobile = template_manager.list_templates(category='mobile')
        assert len(desktop) >= 3
        assert len(mobile) >= 2

    def test_create_custom_template(self):
        from backend.fingerprint_templates import template_manager
        t = template_manager.create_template({
            'name': 'Test Template',
            'category': 'custom',
            'config': {'os': 'Windows', 'hardware_concurrency': 4}
        })
        assert t['name'] == 'Test Template'
        assert not t['builtin']
        # Cleanup
        template_manager.delete_template(t['id'])

    def test_cannot_delete_builtin(self):
        from backend.fingerprint_templates import template_manager
        templates = template_manager.list_templates()
        builtin = next(t for t in templates if t.get('builtin'))
        assert not template_manager.delete_template(builtin['id'])


# ============================================================
# SECTION 6: EXTENSION MANAGER TESTS
# ============================================================
class TestExtensionManager:
    """Extension install/uninstall/assign."""

    def test_list_available(self):
        from backend.extension_manager import extension_manager
        available = extension_manager.list_available()
        assert len(available) >= 8

    def test_install_extension(self):
        from backend.extension_manager import extension_manager
        result = extension_manager.install_extension('adblock', ['test-profile-123'])
        assert result['success']
        installed = extension_manager.get_installed()
        assert any(e['id'] == 'adblock' for e in installed)
        # Cleanup
        extension_manager.uninstall_extension('adblock')

    def test_uninstall_extension(self):
        from backend.extension_manager import extension_manager
        extension_manager.install_extension('lastpass')
        assert extension_manager.uninstall_extension('lastpass')
        installed = extension_manager.get_installed()
        assert not any(e['id'] == 'lastpass' for e in installed)

    def test_assign_to_profile(self):
        from backend.extension_manager import extension_manager
        extension_manager.install_extension('adblock')
        assert extension_manager.assign_to_profile('adblock', 'profile-abc')
        exts = extension_manager.get_profile_extensions('profile-abc')
        assert 'adblock' in exts
        # Cleanup
        extension_manager.uninstall_extension('adblock')

    def test_unknown_extension(self):
        from backend.extension_manager import extension_manager
        result = extension_manager.install_extension('nonexistent_ext_xyz')
        assert not result['success']


# ============================================================
# SECTION 7: PROXY PROVIDER TESTS
# ============================================================
class TestProxyProviders:
    """Proxy provider CRUD and templates."""

    def test_provider_templates(self):
        from backend.proxy_providers import PROVIDER_TEMPLATES
        assert 'brightdata' in PROVIDER_TEMPLATES
        assert 'oxylabs' in PROVIDER_TEMPLATES
        assert 'smartproxy' in PROVIDER_TEMPLATES
        assert 'custom' in PROVIDER_TEMPLATES

    def test_add_provider(self):
        from backend.proxy_providers import provider_manager
        pc = provider_manager.add_provider({
            'name': 'Test Provider',
            'type': 'custom',
            'host': 'proxy.test.com',
            'port': 8080,
            'username': 'user',
            'password': 'pass',
        })
        assert pc.name == 'Test Provider'
        assert pc.host == 'proxy.test.com'
        # Cleanup
        provider_manager.delete_provider(pc.id)

    def test_provider_url_generation(self):
        from backend.proxy_providers import ProviderConfig
        pc = ProviderConfig({
            'type': 'custom',
            'host': 'proxy.test.com',
            'port': 8080,
            'protocol': 'http',
            'username': 'user',
            'password': 'pass',
        })
        url = pc.get_proxy_url()
        assert 'user:pass@proxy.test.com:8080' in url


# ============================================================
# SECTION 8: TEAM MANAGER TESTS
# ============================================================
class TestTeamManager:
    """Team member CRUD and permissions."""

    def test_add_member(self):
        from backend.team_manager import team_manager
        member = team_manager.add_member('Test Member', 'test@example.com', 'operator')
        assert member.name == 'Test Member'
        assert member.role == 'operator'
        # Cleanup
        team_manager.remove_member(member.id)

    def test_role_permissions(self):
        from backend.team_manager import PERMISSIONS, Role
        assert 'delete_profile' in PERMISSIONS[Role.ADMIN]
        assert 'delete_profile' not in PERMISSIONS[Role.VIEWER]
        assert 'view_profile' in PERMISSIONS[Role.VIEWER]

    def test_invalid_role(self):
        from backend.team_manager import team_manager
        with pytest.raises(ValueError):
            team_manager.add_member('Bad Role', 'bad@test.com', 'superadmin')


# ============================================================
# SECTION 9: API KEY TESTS
# ============================================================
class TestAPIKeys:
    """API key CRUD."""

    def test_create_key(self):
        from backend.api_keys import api_key_manager
        result = api_key_manager.create_key('test-key')
        assert 'key' in result
        assert result['name'] == 'test-key'
        assert result['active']
        # Cleanup
        api_key_manager.revoke_key(result['key'])

    def test_revoke_key(self):
        from backend.api_keys import api_key_manager
        result = api_key_manager.create_key('revoke-test')
        assert api_key_manager.revoke_key(result['key'])
        assert not api_key_manager.validate(result['key'])

    def test_list_keys_masked(self):
        from backend.api_keys import api_key_manager
        result = api_key_manager.create_key('mask-test')
        keys = api_key_manager.list_keys()
        assert any(k['key'].endswith('...') for k in keys)
        api_key_manager.revoke_key(result['key'])


# ============================================================
# SECTION 10: FRONTEND INTEGRITY TESTS
# ============================================================
class TestFrontendIntegrity:
    """Verify HTML/JS/CSS consistency."""

    def _read(self, filename):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', filename)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def test_no_duplicate_ids(self):
        html = self._read('index.html')
        import re
        ids = re.findall(r'id="([^"]+)"', html)
        seen = set()
        duplicates = []
        for i in ids:
            if i in seen:
                duplicates.append(i)
            seen.add(i)
        assert len(duplicates) == 0, f"Duplicate IDs found: {duplicates}"

    def test_all_pages_exist(self):
        html = self._read('index.html')
        pages = ['dashboard', 'profiles', 'proxies', 'automation', 'ai-status', 'logs', 'settings']
        for p in pages:
            assert f'id="page-{p}"' in html, f"Missing page section: page-{p}"

    def test_all_modals_exist(self):
        html = self._read('index.html')
        modals = ['create-modal', 'edit-modal', 'metadata-modal', 'scan-modal',
                  'cookie-modal', 'fingerprint-modal', 'run-macro-modal', 'macro-modal',
                  'schedule-modal', 'cf-import-modal']
        for m in modals:
            assert f'id="{m}"' in html, f"Missing modal: {m}"

    def test_js_syntax(self):
        import subprocess
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'app.js')
        result = subprocess.run(['node', '--check', js_path], capture_output=True, text=True)
        assert result.returncode == 0, f"JS syntax error: {result.stderr}"

    def test_css_valid(self):
        css = self._read('style.css')
        # Count braces
        opens = css.count('{')
        closes = css.count('}')
        assert opens == closes, f"Unbalanced CSS braces: {opens} opens vs {closes} closes"

    def test_no_orphaned_css(self):
        css = self._read('style.css')
        # Check for CSS properties outside any selector (between } and next {)
        lines = css.split('\n')
        in_rule = False
        brace_depth = 0
        orphaned_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            brace_depth += stripped.count('{') - stripped.count('}')
            if brace_depth <= 0 and stripped and not stripped.startswith(('/*', '*', '//', '@', '.', '#', ':', '}', '{')):
                if any(prop in stripped for prop in ['display:', 'align-items:', 'gap:', 'padding:', 'font-size:', 'color:']):
                    orphaned_lines.append((i + 1, stripped))
        assert len(orphaned_lines) == 0, f"Orphaned CSS properties: {orphaned_lines}"

    def test_all_onclick_functions_exist(self):
        html = self._read('index.html')
        js = self._read('app.js')
        import re
        onclicks = re.findall(r'onclick="(\w+)\(', html)
        onchange = re.findall(r'onchange="(\w+)\(', html)
        oninput = re.findall(r'oninput="(\w+)\(', html)
        all_handlers = set(onclicks + onchange + oninput)
        # Filter out JS builtins and inline operations
        skip = {'this', 'event', 'document', 'window', 'console', 'alert', 'prompt',
                'parseInt', 'parseFloat', 'setTimeout', 'setInterval', 'fetch',
                'JSON', 'Math', 'Date', 'String', 'Number', 'Boolean', 'Array',
                'classList', 'style', 'innerHTML', 'value', 'checked', 'click',
                'submit', 'close', 'open', 'focus', 'blur', 'select'}
        missing = []
        for fn in all_handlers:
            if fn in skip or fn.startswith('switch') and 'Tab' in fn:
                continue
            if fn not in js:
                missing.append(fn)
        assert len(missing) == 0, f"Functions called in HTML but not defined in JS: {missing}"

    def test_auth_button_exists(self):
        html = self._read('index.html')
        assert 'auth-user-btn' in html
        assert 'auth-user-label' in html

    def test_search_input_exists(self):
        html = self._read('index.html')
        assert 'id="profile-search"' in html

    def test_extensions_section(self):
        html = self._read('index.html')
        assert 'extensions-list' in html
        assert 'templates-list' in html
        assert 'proxy-providers-list' in html
        assert 'import-browser-list' in html


# ============================================================
# SECTION 11: API ENDPOINT SMOKE TESTS (no server required)
# ============================================================
class TestAPIStructure:
    """Verify all routes exist in the FastAPI app."""

    def test_app_has_routes(self):
        from backend.main import app
        routes = [r.path for r in app.routes if hasattr(r, 'path')]
        expected = [
            '/api/profiles',
            '/api/profiles/search',
            '/api/profiles/{profile_id}/clone-exact',
            '/api/profiles/{profile_id}/launch',
            '/api/profiles/{profile_id}/close',
            '/api/profiles/{profile_id}/metadata',
            '/api/proxies',
            '/api/auth/login',
            '/api/auth/logout',
            '/api/auth/me',
            '/api/auth/register',
            '/api/auth/users',
            '/api/api-keys',
            '/api/team/members',
            '/api/proxy-providers',
            '/api/extensions/available',
            '/api/extensions/installed',
            '/api/fingerprint-templates',
            '/api/import/browsers',
            '/api/macros',
            '/api/system/health',
        ]
        for ep in expected:
            # Normalize path params
            normalized = ep.replace('{profile_id}', 'x').replace('{member_id}', 'x').replace('{key}', 'x').replace('{macro_id}', 'x').replace('{provider_id}', 'x').replace('{template_id}', 'x').replace('{ext_id}', 'x').replace('{job_id}', 'x').replace('{profile_id}/', 'x/')
            assert normalized in routes or ep in routes, f"Missing endpoint: {ep}"


# ============================================================
# SECTION 12: CONFIG & DB TESTS
# ============================================================
class TestConfigAndDB:
    """Config paths and database initialization."""

    def test_data_dir_exists(self):
        from backend.config import get_data_dir
        d = get_data_dir("profiles_data")
        assert os.path.isdir(d)

    def test_db_init(self):
        from backend.db import init_db
        init_db()  # Should not raise

    def test_db_schema(self):
        import sqlite3
        from backend.config import get_data_dir
        db_path = get_data_dir("backend", "proxies.db")
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert 'proxies' in tables
            conn.close()

    def test_cloudflare_accounts_exist(self):
        accounts_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cloudflare_accounts.txt')
        if os.path.exists(accounts_path):
            with open(accounts_path) as f:
                lines = [l.strip() for l in f if l.strip()]
            assert len(lines) > 0, f"Expected accounts, found {len(lines)}"


# ============================================================
# SECTION 13: EDGE CASE & STRESS TESTS
# ============================================================
class TestEdgeCases:
    """Edge cases that break things in production."""

    def test_empty_profile_name(self):
        from backend.profile_manager import profile_manager
        profile = profile_manager.create_profile(name='', advanced={})
        assert profile['name'] == ''
        profile_manager.delete_profile(profile['id'])

    def test_unicode_profile_name(self):
        from backend.profile_manager import profile_manager
        profile = profile_manager.create_profile(name='日本語テスト 🔥 Profile', advanced={})
        assert 'id' in profile
        profile_manager.delete_profile(profile['id'])

    def test_very_long_profile_name(self):
        from backend.profile_manager import profile_manager
        long_name = 'A' * 500
        profile = profile_manager.create_profile(name=long_name, advanced={})
        assert 'id' in profile
        profile_manager.delete_profile(profile['id'])

    def test_concurrent_session_tokens(self):
        from backend.auth_manager import AuthManager
        auth = AuthManager()
        user = auth.create_user('test_concurrent', 'pass1234')
        tokens = [auth.create_session(user) for _ in range(50)]
        assert len(set(tokens)) == 50  # All unique
        for t in tokens:
            assert auth.validate_session(t) is not None
        # Cleanup
        auth.delete_user('test_concurrent')

    def test_stealth_js_with_all_os_types(self):
        from backend.browser_manager import _generate_spoofing_js
        import subprocess
        for os_type in ['Windows', 'Mac', 'Linux']:
            config = {
                'id': 'a0b1c2d3-e4f5-6789-abcd-ef0123456789',
                'user_agent': 'Mozilla/5.0 Chrome/136.0.0.0',
                'timezone': 'UTC',
                'locale': 'en-US',
                'advanced': {
                    'hardware_concurrency': 4,
                    'device_memory': 4,
                    'screen_width': 1366,
                    'screen_height': 768,
                    'os': os_type,
                    'canvas_noise': True,
                    'webgl_noise': True,
                    'audio_noise': True,
                }
            }
            js = _generate_spoofing_js(config)
            assert len(js) > 40000
            # Syntax check
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), f'_test_{os_type}.js')
            with open(path, 'w') as f:
                f.write(js)
            result = subprocess.run(['node', '--check', path], capture_output=True, text=True)
            os.remove(path)
            assert result.returncode == 0, f"JS syntax error for {os_type}: {result.stderr}"

    def test_stealth_js_with_mobile(self):
        from backend.browser_manager import _generate_spoofing_js
        import subprocess
        config = {
            'id': 'b0c1d2e3-f4a5-6789-bcde-f01234567890',
            'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Chrome/136.0.0.0 Mobile Safari/537.36',
            'timezone': 'America/New_York',
            'locale': 'en-US',
            'advanced': {
                'hardware_concurrency': 6,
                'device_memory': 4,
                'screen_width': 393,
                'screen_height': 852,
                'os': 'iOS',
                'canvas_noise': True,
                'webgl_noise': True,
                'audio_noise': True,
            }
        }
        js = _generate_spoofing_js(config)
        assert len(js) > 40000
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_test_mobile.js')
        with open(path, 'w') as f:
            f.write(js)
        result = subprocess.run(['node', '--check', path], capture_output=True, text=True)
        os.remove(path)
        assert result.returncode == 0

    def test_metadata_with_tags_and_notes(self):
        from backend.profile_manager import profile_manager
        profiles = profile_manager.list_profiles()
        if profiles:
            pid = profiles[0]['id']
            profile_manager.update_profile(pid, {
                'tags': ['tag1', 'tag2', 'tag3'],
                'notes': 'Test notes with unicode: 日本語',
                'proxy_pin': 'test-pin-123',
            })
            p = profile_manager.get_profile(pid)
            assert 'tag1' in p.get('tags', [])
            assert '日本語' in p.get('notes', '')
            # Cleanup
            profile_manager.update_profile(pid, {'tags': [], 'notes': '', 'proxy_pin': ''})


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
