import json

class AICoherenceValidator:
    """
    Validates that a generated fingerprint is logically coherent.
    If it finds conflicting hardware/software combos, it returns a score < 90.
    """
    
    def __init__(self):
        pass
        
    def validate(self, fingerprint: dict) -> dict:
        score = 100
        issues = []
        
        # 1. OS vs GPU check
        os_name = fingerprint.get("os", "")
        vendor = fingerprint.get("webgl_vendor", "").lower()
        renderer = fingerprint.get("webgl_renderer", "").lower()
        
        if os_name == "Mac":
            if "nvidia" in vendor or "nvidia" in renderer:
                score -= 50
                issues.append("Mac OS cannot have NVIDIA GPU renderer in modern environments.")
            if "amd" in vendor and "direct3d" in renderer:
                score -= 30
                issues.append("Mac OS does not use Direct3D.")
        
        if os_name == "Windows":
            if "apple" in vendor or "apple" in renderer:
                score -= 50
                issues.append("Windows OS cannot have Apple GPU renderer.")
        
        # 2. Hardware scaling
        cores = fingerprint.get("cpu_cores", 4)
        mem = fingerprint.get("memory_gb", 8)
        if cores > mem * 2:
            score -= 10
            issues.append(f"Unusual hardware combo: {cores} cores but only {mem}GB RAM.")
            
        # 3. Locale & Timezone basic logic
        tz = fingerprint.get("timezone", "")
        locale = fingerprint.get("locale", "")
        if "America" in tz and not locale.startswith("en") and not locale.startswith("es") and not locale.startswith("fr"):
            # Not strictly invalid, but slightly anomalous
            score -= 5
            issues.append(f"Timezone {tz} is slightly anomalous with locale {locale}.")
            
        # 4. User Agent and Client Hints match
        ua = fingerprint.get("user_agent", "")
        sec_ua = fingerprint.get("sec_ch_ua", "")
        sec_plat = fingerprint.get("sec_ch_ua_platform", "").strip('"')
        
        if os_name == "Windows" and sec_plat != "Windows":
            score -= 50
            issues.append(f"OS is Windows but ClientHints Platform is {sec_plat}")
        if os_name == "Mac" and sec_plat != "macOS":
            score -= 50
            issues.append(f"OS is Mac but ClientHints Platform is {sec_plat}")
            
        # Extract Chrome version from UA and sec_ch_ua to ensure match
        import re
        ua_match = re.search(r'Chrome/(\d+)', ua)
        sec_match = re.search(r'"Chromium";v="(\d+)"', sec_ua)
        if ua_match and sec_match:
            if ua_match.group(1) != sec_match.group(1):
                score -= 60
                issues.append(f"Chrome version mismatch! UA says {ua_match.group(1)} but sec-ch-ua says {sec_match.group(1)}")
                
        # 5. Resolution logic
        res = fingerprint.get("screen_resolution", "")
        if os_name == "Mac" and res in ["1920x1080", "1366x768"]:
            score -= 20
            issues.append(f"Resolution {res} is highly unusual for a Mac.")
            
        # 6. WebGL Vendor vs Renderer logic
        if "amd" in vendor and "radeon" not in renderer and "amd" not in renderer:
            score -= 30
            issues.append(f"WebGL Vendor is AMD but renderer is {renderer}")
        if "nvidia" in vendor and "geforce" not in renderer and "rtx" not in renderer and "gtx" not in renderer:
            score -= 30
            issues.append(f"WebGL Vendor is NVIDIA but renderer is {renderer}")
        if "intel" in vendor and "iris" not in renderer and "hd graphics" not in renderer and "uhd" not in renderer:
            score -= 30
            issues.append(f"WebGL Vendor is Intel but renderer is {renderer}")
            
        # 7. Network and Battery plausibility
        if fingerprint.get("battery_level", 1.0) < 0.05 and not fingerprint.get("battery_charging"):
            score -= 5
            issues.append("Battery is suspiciously low and not charging.")
            
        return {
            "score": score,
            "issues": issues,
            "passed": score >= 90
        }

coherence_validator = AICoherenceValidator()
