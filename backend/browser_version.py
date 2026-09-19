"""Authoritative BrowserVersion and Client Hints model for GhostBrowser AI."""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from pathlib import Path


def calculate_file_sha256(file_path: str | Path) -> Optional[str]:
    """Calculate the SHA-256 hex digest of a file."""
    try:
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return None
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None



@dataclass(frozen=True)
class BrowserVersion:
    product: str
    major: int
    minor: int
    build: int
    patch: int
    full_version: str

    @classmethod
    def parse(cls, version_str: str, product: str = "Chrome") -> "BrowserVersion":
        """Parse '130.0.6723.31' or 'Chrome/130.0.6723.31' into a BrowserVersion."""
        clean = str(version_str).strip()
        m = re.search(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", clean)
        if m:
            major, minor, build, patch = map(int, m.groups())
            return cls(
                product=product,
                major=major,
                minor=minor,
                build=build,
                patch=patch,
                full_version=f"{major}.{minor}.{build}.{patch}",
            )
        parts = [int(p) for p in re.findall(r"\d+", clean)]
        while len(parts) < 4:
            parts.append(0)
        return cls(
            product=product,
            major=parts[0],
            minor=parts[1],
            build=parts[2],
            patch=parts[3],
            full_version=f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}",
        )

    @classmethod
    def from_installed_engine(cls, executable_path: Optional[str] = None) -> "BrowserVersion":
        """Query config.py or an explicit executable for installed Chromium info."""
        if executable_path:
            from backend.engine_resolver import resolve_chromium_version
            ver = resolve_chromium_version(executable_path)
            if ver:
                return cls.parse(ver)
        from backend.config import get_installed_chromium_version
        return cls.parse(get_installed_chromium_version())

    def get_brands(
        self,
        is_headless_identity: bool = False,
        grease_brand: str = "Not?A_Brand",
        grease_version: str = "99"
    ) -> List[Dict[str, str]]:
        """Generate coherent navigator.userAgentData.brands."""
        if is_headless_identity:
            return [
                {"brand": "Chromium", "version": str(self.major)},
                {"brand": "HeadlessChrome", "version": str(self.major)},
                {"brand": grease_brand, "version": grease_version},
            ]
        return [
            {"brand": "Chromium", "version": str(self.major)},
            {"brand": "Google Chrome", "version": str(self.major)},
            {"brand": grease_brand, "version": grease_version},
        ]

    def get_full_version_list(
        self,
        is_headless_identity: bool = False,
        grease_brand: str = "Not?A_Brand",
        grease_version: str = "99.0.0.0"
    ) -> List[Dict[str, str]]:
        """Generate high-entropy fullVersionList."""
        if is_headless_identity:
            return [
                {"brand": "Chromium", "version": self.full_version},
                {"brand": "HeadlessChrome", "version": self.full_version},
                {"brand": grease_brand, "version": grease_version},
            ]
        return [
            {"brand": "Chromium", "version": self.full_version},
            {"brand": "Google Chrome", "version": self.full_version},
            {"brand": grease_brand, "version": grease_version},
        ]

    def get_sec_ch_ua(self, is_headless_identity: bool = False) -> str:
        """Format Sec-CH-UA HTTP header."""
        brands = self.get_brands(is_headless_identity=is_headless_identity)
        return ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in brands)

    def get_sec_ch_ua_full_version_list(self, is_headless_identity: bool = False) -> str:
        """Format Sec-CH-UA-Full-Version-List HTTP header."""
        fvl = self.get_full_version_list(is_headless_identity=is_headless_identity)
        return ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in fvl)

    def get_user_agent(
        self,
        os_name: str = "Windows",
        is_mobile: bool = False,
        use_full_version: bool = False
    ) -> str:
        """Format standard Chromium User-Agent header and DOM string."""
        if os_name == "Windows":
            os_token = "Windows NT 10.0; Win64; x64"
        elif os_name == "Mac":
            os_token = "Macintosh; Intel Mac OS X 10_15_7"
        elif os_name == "Linux":
            os_token = "X11; Linux x86_64"
        else:
            os_token = "Windows NT 10.0; Win64; x64"

        chrome_ver = self.full_version if use_full_version else f"{self.major}.0.0.0"
        if is_mobile:
            return f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Mobile Safari/537.36"
        return f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36"

    def validate_coherence(
        self,
        ua: Optional[str] = None,
        sec_ch_ua: Optional[str] = None,
        uadata: Optional[Dict[str, Any]] = None,
        high_entropy: Optional[Dict[str, Any]] = None,
        is_headless_expected: bool = False,
        *,
        http_ua: Optional[str] = None,
        sec_ch_ua_full_version_list: Optional[str] = None,
        sec_ch_ua_platform: Optional[str] = None,
        sec_ch_ua_mobile: Optional[str] = None,
        js_ua: Optional[str] = None,
        js_platform: Optional[str] = None,
        js_brands: Optional[List[Dict[str, str]]] = None,
        js_mobile: Optional[bool] = None,
        js_high_entropy: Optional[Dict[str, Any]] = None,
        is_mobile_expected: bool = False,
    ) -> List[str]:
        """Validate consistency across all version surfaces. Return list of issues found."""
        issues = []

        effective_ua = ua or js_ua or ""
        effective_high_entropy = high_entropy or js_high_entropy or {}
        effective_uadata = dict(uadata) if uadata else {}
        if js_brands is not None:
            effective_uadata["brands"] = js_brands
        if js_mobile is not None:
            effective_uadata["mobile"] = js_mobile
        if js_platform is not None and "platform" not in effective_uadata:
            effective_uadata["platform"] = js_platform

        # 1. HTTP User-Agent vs navigator.userAgent coherence
        if http_ua and effective_ua and http_ua != effective_ua:
            issues.append(f"HTTP User-Agent ('{http_ua}') does not match navigator.userAgent ('{effective_ua}')")

        # 2. UA major check
        if effective_ua:
            m_ua = re.search(r"Chrome/(\d+)", effective_ua)
            if not m_ua or int(m_ua.group(1)) != self.major:
                issues.append(f"UA major version ({m_ua.group(1) if m_ua else 'None'}) != expected ({self.major})")

        # 3. Sec-CH-UA major check
        if sec_ch_ua is not None:
            if f'v="{self.major}"' not in sec_ch_ua:
                issues.append(f"Sec-CH-UA header does not include major version {self.major}: {sec_ch_ua}")

        # 4. Sec-CH-UA-Full-Version-List check
        if sec_ch_ua_full_version_list is not None:
            if self.full_version not in sec_ch_ua_full_version_list:
                issues.append(f"Sec-CH-UA-Full-Version-List does not include engine full version {self.full_version}: {sec_ch_ua_full_version_list}")

        # 5. Headless branding check
        if not is_headless_expected:
            if sec_ch_ua and "HeadlessChrome" in sec_ch_ua:
                issues.append(f"Unexpected 'HeadlessChrome' detected in Sec-CH-UA header: {sec_ch_ua}")
            if effective_ua and "HeadlessChrome" in effective_ua:
                issues.append(f"Unexpected 'HeadlessChrome' detected in User-Agent string: {effective_ua}")

        # 6. JS userAgentData brands check
        if effective_uadata:
            brands = effective_uadata.get("brands") or []
            brand_names = [b.get("brand") for b in brands]
            if not is_headless_expected and "HeadlessChrome" in brand_names:
                issues.append(f"Unexpected 'HeadlessChrome' brand in navigator.userAgentData.brands: {brand_names}")
            if not any(b in brand_names for b in ("Chromium", "Google Chrome")):
                issues.append(f"Missing Chromium/Chrome brand in navigator.userAgentData.brands: {brand_names}")

            # 7. Mobile flag coherence
            uadata_mobile = effective_uadata.get("mobile")
            if uadata_mobile is not None and uadata_mobile != is_mobile_expected:
                issues.append(f"navigator.userAgentData.mobile ({uadata_mobile}) != expected ({is_mobile_expected})")

            # 8. Platform coherence
            uadata_platform = effective_uadata.get("platform")
            if sec_ch_ua_platform and uadata_platform:
                clean_sec_plat = sec_ch_ua_platform.strip('"').lower()
                clean_ua_plat = str(uadata_platform).strip('"').lower()
                if clean_sec_plat != clean_ua_plat:
                    issues.append(f"Sec-CH-UA-Platform ({sec_ch_ua_platform}) != navigator.userAgentData.platform ({uadata_platform})")

        # 9. Sec-CH-UA-Mobile HTTP header check
        if sec_ch_ua_mobile is not None:
            expected_mobile_header = "?1" if is_mobile_expected else "?0"
            if sec_ch_ua_mobile.strip() != expected_mobile_header:
                issues.append(f"Sec-CH-UA-Mobile header ('{sec_ch_ua_mobile}') != expected ('{expected_mobile_header}')")

        # 10. High-entropy uaFullVersion & fullVersionList check
        if effective_high_entropy:
            ua_full = effective_high_entropy.get("uaFullVersion")
            if ua_full and ua_full != self.full_version:
                issues.append(f"High-entropy uaFullVersion ({ua_full}) does not match engine full_version ({self.full_version})")

            fvl = effective_high_entropy.get("fullVersionList")
            if isinstance(fvl, list) and fvl:
                fvl_versions = [b.get("version") for b in fvl if isinstance(b, dict)]
                if not any(v == self.full_version for v in fvl_versions):
                    issues.append(f"High-entropy fullVersionList does not include engine full_version ({self.full_version}): {fvl_versions}")

        return issues
