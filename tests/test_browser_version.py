"""Unit tests for backend.browser_version module."""
import pytest
from backend.browser_version import BrowserVersion

def test_browser_version_parse_standard():
    bv = BrowserVersion.parse("130.0.6723.31")
    assert bv.major == 130
    assert bv.minor == 0
    assert bv.build == 6723
    assert bv.patch == 31
    assert bv.full_version == "130.0.6723.31"

def test_browser_version_parse_with_prefix():
    bv = BrowserVersion.parse("Chrome/132.0.6834.110")
    assert bv.major == 132
    assert bv.minor == 0
    assert bv.build == 6834
    assert bv.patch == 110
    assert bv.full_version == "132.0.6834.110"

def test_browser_version_from_installed():
    bv = BrowserVersion.from_installed_engine()
    assert bv.major > 100
    assert bv.full_version != ""

def test_get_brands_normal_profile():
    bv = BrowserVersion.parse("130.0.6723.31")
    brands = bv.get_brands(is_headless_identity=False)
    brand_names = [b["brand"] for b in brands]
    assert "Chromium" in brand_names
    assert "Google Chrome" in brand_names
    assert "HeadlessChrome" not in brand_names
    for b in brands:
        if b["brand"] in ("Chromium", "Google Chrome"):
            assert b["version"] == "130"

def test_get_brands_headless_profile():
    bv = BrowserVersion.parse("130.0.6723.31")
    brands = bv.get_brands(is_headless_identity=True)
    brand_names = [b["brand"] for b in brands]
    assert "HeadlessChrome" in brand_names
    assert "Google Chrome" not in brand_names

def test_sec_ch_ua_headers():
    bv = BrowserVersion.parse("130.0.6723.31")
    sec_ch_ua = bv.get_sec_ch_ua(is_headless_identity=False)
    assert '"Chromium";v="130"' in sec_ch_ua
    assert '"Google Chrome";v="130"' in sec_ch_ua
    assert "HeadlessChrome" not in sec_ch_ua

    fvl_header = bv.get_sec_ch_ua_full_version_list(is_headless_identity=False)
    assert '"Chromium";v="130.0.6723.31"' in fvl_header
    assert '"Google Chrome";v="130.0.6723.31"' in fvl_header
    assert "HeadlessChrome" not in fvl_header

def test_validate_coherence_passes_coherent():
    bv = BrowserVersion.parse("130.0.6723.31")
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    sec_ch_ua = '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"'
    uadata = {
        "brands": [
            {"brand": "Chromium", "version": "130"},
            {"brand": "Google Chrome", "version": "130"},
            {"brand": "Not?A_Brand", "version": "99"},
        ]
    }
    high_entropy = {"uaFullVersion": "130.0.6723.31"}
    issues = bv.validate_coherence(ua, sec_ch_ua, uadata, high_entropy, is_headless_expected=False)
    assert len(issues) == 0

def test_validate_coherence_fails_headless_leak():
    bv = BrowserVersion.parse("130.0.6723.31")
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    sec_ch_ua = '"Chromium";v="130", "HeadlessChrome";v="130", "Not?A_Brand";v="99"'
    issues = bv.validate_coherence(ua, sec_ch_ua, None, None, is_headless_expected=False)
    assert any("HeadlessChrome" in i for i in issues)

def test_validate_coherence_comprehensive():
    bv = BrowserVersion.parse("147.0.7727.15")
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    http_ua = ua
    sec_ch_ua = '"Chromium";v="147", "Google Chrome";v="147", "Not?A_Brand";v="99"'
    sec_ch_ua_fvl = '"Chromium";v="147.0.7727.15", "Google Chrome";v="147.0.7727.15", "Not?A_Brand";v="99.0.0.0"'
    uadata = {
        "brands": [
            {"brand": "Chromium", "version": "147"},
            {"brand": "Google Chrome", "version": "147"},
            {"brand": "Not?A_Brand", "version": "99"},
        ],
        "mobile": False,
        "platform": "Windows"
    }
    high_entropy = {
        "uaFullVersion": "147.0.7727.15",
        "fullVersionList": [
            {"brand": "Chromium", "version": "147.0.7727.15"},
            {"brand": "Google Chrome", "version": "147.0.7727.15"},
            {"brand": "Not?A_Brand", "version": "99.0.0.0"}
        ]
    }
    issues = bv.validate_coherence(
        ua=ua,
        sec_ch_ua=sec_ch_ua,
        uadata=uadata,
        high_entropy=high_entropy,
        is_headless_expected=False,
        http_ua=http_ua,
        sec_ch_ua_full_version_list=sec_ch_ua_fvl,
        sec_ch_ua_platform='"Windows"',
        sec_ch_ua_mobile="?0",
        js_platform="Win32",
        is_mobile_expected=False
    )
    assert len(issues) == 0

    # Contradiction: HTTP UA mismatch
    bad_http = bv.validate_coherence(
        ua=ua,
        sec_ch_ua=sec_ch_ua,
        uadata=uadata,
        http_ua="Mozilla/5.0 Different"
    )
    assert any("does not match" in i for i in bad_http)

    # Contradiction: mobile flag mismatch
    bad_mobile = bv.validate_coherence(
        ua=ua,
        sec_ch_ua=sec_ch_ua,
        uadata=uadata,
        sec_ch_ua_mobile="?1",
        is_mobile_expected=False
    )
    assert any("Sec-CH-UA-Mobile" in i for i in bad_mobile)
