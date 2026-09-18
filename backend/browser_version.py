"""Authoritative BrowserVersion and Client Hints model for GhostBrowser AI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional


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
    def from_installed_engine(cls) -> "BrowserVersion":
        """Query config.py for installed Chromium executable info."""
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
        ua: str,
        sec_ch_ua: str,
        uadata: Optional[Dict[str, Any]],
        high_entropy: Optional[Dict[str, Any]] = None,
        is_headless_expected: bool = False
    ) -> List[str]:
        """Validate consistency across all version surfaces. Return list of issues found."""
        issues = []
        # 1. UA major check
        m_ua = re.search(r"Chrome/(\d+)", ua)
        if not m_ua or int(m_ua.group(1)) != self.major:
            issues.append(f"UA major version ({m_ua.group(1) if m_ua else 'None'}) != expected ({self.major})")

        # 2. Sec-CH-UA major check
        if f'v="{self.major}"' not in sec_ch_ua:
            issues.append(f"Sec-CH-UA header does not include major version {self.major}: {sec_ch_ua}")

        # 3. Headless check
        if not is_headless_expected and ("HeadlessChrome" in sec_ch_ua or "HeadlessChrome" in ua):
            issues.append(f"Unexpected 'HeadlessChrome' detected in normal profile branding: {sec_ch_ua}")

        # 4. JS userAgentData check
        if uadata:
            brands = uadata.get("brands") or []
            brand_names = [b.get("brand") for b in brands]
            if not is_headless_expected and "HeadlessChrome" in brand_names:
                issues.append(f"Unexpected 'HeadlessChrome' brand in navigator.userAgentData.brands: {brand_names}")
            if "Chromium" not in brand_names:
                issues.append(f"Missing 'Chromium' brand in navigator.userAgentData.brands: {brand_names}")

        # 5. High-entropy uaFullVersion check
        if high_entropy:
            ua_full = high_entropy.get("uaFullVersion")
            if ua_full and ua_full != self.full_version:
                issues.append(f"High-entropy uaFullVersion ({ua_full}) does not match engine full_version ({self.full_version})")

        return issues
