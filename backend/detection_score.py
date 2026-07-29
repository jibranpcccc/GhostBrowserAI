"""Profile detection-risk scorer and scanner router."""
from fastapi import APIRouter, Depends, HTTPException
from backend.profile_manager import profile_manager
from backend.fingerprint_surface_registry import get_surface_registry, get_protection_score
from backend.auth import require_admin_token
from backend.ai_coherence_validator import coherence_validator

router = APIRouter(tags=["detection-risk"])


def score_profile(profile: dict) -> dict:
    """Return a 0-100 detection-risk score and a list of factors."""
    score = 0
    factors = []
    recommendations = []

    advanced = profile.get("advanced") or {}
    fingerprint = profile.get("fingerprint") or {}
    ua = profile.get("user_agent") or advanced.get("user_agent") or ""
    os_name = advanced.get("os") or "Windows"

    if not ua or not ua.startswith("Mozilla/"):
        score += 30
        factors.append("Missing or invalid user agent")
        recommendations.append("Regenerate the profile so the AI builds a platform-coherent user agent")

    if os_name.lower() not in ua.lower() and not (
        (os_name == "Mac" and "Macintosh" in ua)
        or (os_name == "Linux" and "Linux" in ua)
        or (os_name == "Windows" and "Windows" in ua)
    ):
        score += 25
        factors.append("User agent OS does not match profile OS")
    webrtc_mode = advanced.get("webrtc_mode", "protected")
    if webrtc_mode not in ("disabled", "protected"):
        score += 20
        factors.append("WebRTC mode is not protected")
        recommendations.append("Set advanced.webrtc_mode to 'protected' or 'disabled'")

    proxy = profile.get("proxy") or profile.get("proxy_pin")
    if not proxy:
        score += 20
        factors.append("No proxy assigned; profile uses direct connection")
        recommendations.append("Assign a proxy to the profile")

    if not advanced.get("canvas_noise_seed"):
        score += 5
        factors.append("Canvas noise seed missing")
    if not advanced.get("audio_noise_seed"):
        score += 5
        factors.append("Audio noise seed missing")
    if not advanced.get("webgl_vendor") or not advanced.get("webgl_renderer"):
        score += 5
        factors.append("WebGL vendor/renderer not set")

    profile_locale = profile.get("locale") or advanced.get("locale")
    profile_tz = profile.get("timezone") or advanced.get("timezone")
    if profile_locale and profile_tz:
        tz_region = profile_tz.split("/")[0].lower()
        locale_region = profile_locale.split("-")[-1].lower()
        if tz_region != locale_region and not (
            (tz_region == "america" and locale_region in ("us", "ca", "mx"))
            or (tz_region == "europe" and locale_region in ("gb", "de", "fr", "it", "es", "nl", "pl"))
            or (tz_region == "asia" and locale_region in ("jp", "kr", "cn", "in", "sg"))
        ):
            score += 10
            factors.append("Timezone and locale region mismatch")

    if not advanced.get("device_scale_factor"):
        score += 5
        factors.append("Device scale factor missing")
    if not advanced.get("cpu_cores"):
        score += 5
        factors.append("CPU cores missing")
    if not advanced.get("memory_gb"):
        score += 5
        factors.append("Memory GB missing")

    score = min(100, max(0, score))
    return {
        "risk_score": score,
        "compatibility_score": compatibility_score(profile)["compatibility_score"],
        "factors": factors,
        "recommendations": recommendations,
        "profile_id": profile["id"],
    }


def compatibility_score(profile: dict) -> dict:
    """Score profile configuration correctness without measuring detection evasion."""
    advanced = profile.get("advanced") or {}
    fingerprint = profile.get("fingerprint") or {}
    issues = []
    recommendations = []
    score = 100

    def require(passed, points, issue, recommendation):
        nonlocal score
        if not passed:
            score -= points
            issues.append(issue)
            recommendations.append(recommendation)

    ua = profile.get("user_agent") or advanced.get("user_agent") or fingerprint.get("userAgent") or ""
    proxy = profile.get("proxy") or profile.get("proxy_pin")
    require(bool(proxy), 15, "No proxy configured", "Configure a valid profile proxy.")
    require(advanced.get("webrtc_mode", "protected") in ("protected", "disabled"), 15, "WebRTC is not protected", "Set advanced.webrtc_mode to 'protected' or 'disabled'.")
    require(bool(advanced.get("canvas_noise_seed") or fingerprint.get("canvas_noise_seed")) and bool(advanced.get("audio_noise_seed") or fingerprint.get("audio_noise_seed")), 15, "Canvas or audio noise seed missing", "Set stable canvas_noise_seed and audio_noise_seed values.")

    locale = profile.get("locale") or advanced.get("locale") or fingerprint.get("locale") or ""
    timezone = profile.get("timezone") or advanced.get("timezone") or fingerprint.get("timezone") or ""
    tz_region = timezone.split("/", 1)[0].lower()
    locale_region = locale.split("-")[-1].lower()
    locale_matches_timezone = bool(locale and timezone) and (
        tz_region == locale_region
        or (tz_region == "america" and locale_region in ("us", "ca", "mx"))
        or (tz_region == "europe" and locale_region in ("gb", "de", "fr", "it", "es", "nl", "pl"))
        or (tz_region == "asia" and locale_region in ("jp", "kr", "cn", "in", "sg"))
    )
    require(locale_matches_timezone, 15, "Timezone and locale do not match", "Use a locale appropriate for the configured timezone.")

    resolution = advanced.get("screen_resolution") or fingerprint.get("screen_resolution") or ""
    try:
        width, height = (int(value) for value in str(resolution).split("x", 1))
        resolution_valid = 100 <= width <= 10000 and 100 <= height <= 10000
    except (TypeError, ValueError):
        resolution_valid = False
    require(resolution_valid, 10, "Screen resolution is invalid", "Set screen_resolution to WIDTHxHEIGHT within supported bounds.")
    require(isinstance(ua, str) and ua.startswith("Mozilla/") and "Chrome/" in ua, 15, "User agent is invalid", "Use a Chromium user agent matching the profile OS.")

    coherence = coherence_validator.validate(fingerprint)
    require(coherence.get("passed") is True, 15, "Fingerprint coherence validation failed", "Repair fingerprint fields so coherence validation passes.")
    return {
        "compatibility_score": max(0, score),
        "issues": issues,
        "recommendations": recommendations,
        "profile_id": profile.get("id"),
        "fingerprint_coherence": coherence,
    }


@router.get("/api/profiles/{profile_id}/detection-risk")
def get_detection_risk(profile_id: str, _auth: None = Depends(require_admin_token)):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success", **score_profile(profile)}


@router.get("/api/profiles/{profile_id}/compatibility")
def get_profile_compatibility(profile_id: str, _auth: None = Depends(require_admin_token)):
    profile = profile_manager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success", **compatibility_score(profile)}


@router.get("/api/anti-detect/surfaces")
def get_surfaces(_auth: None = Depends(require_admin_token)):
    return {
        "status": "success",
        "score": get_protection_score(),
        "surfaces": get_surface_registry(),
    }
