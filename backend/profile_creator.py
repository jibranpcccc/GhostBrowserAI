import asyncio
import os
import json
import shutil
import re
from backend.profile_manager import profile_manager
from backend.ai_generator import generate_fingerprint_ai, generate_fingerprint_fallback
from backend.ai_coherence_validator import coherence_validator
from backend.ai_leak_scanner import leak_scanner
from backend.ai_data_sanitizer import data_sanitizer
from backend.logging_config import logger

QUARANTINE_DIR = os.path.join(os.path.dirname(__file__), "..", "quarantined_profiles")
os.makedirs(QUARANTINE_DIR, exist_ok=True)
QUARANTINE_META = os.path.join(QUARANTINE_DIR, "quarantine_meta.json")

class ProfileCreationOrchestrator:
    """
    Coordinates the 6-step Zero-Leak Profile Creation flow.
    """

    async def create_zero_leak_profile(self, name: str, proxy: dict = None, advanced_ui: dict = None, skip_warming: bool = False) -> dict:
        print(f"[Orchestrator] Starting Zero-Leak Creation for: {name}")

        name_stripped = (name or "").strip()
        if not name_stripped:
            return {"status": "error", "message": "Validation failed: name cannot be empty or whitespace.", "code": "VALIDATION"}
        if ".." in name_stripped or "/" in name_stripped or chr(92) in name_stripped:
            return {"status": "error", "message": "Validation failed: name contains path traversal characters.", "code": "VALIDATION"}
        if advanced_ui:
            cores = advanced_ui.get("cpu_cores")
            if cores is not None and (not isinstance(cores, int) or cores < 1 or cores > 128):
                return {"status": "error", "message": f"Validation failed: CPU cores must be between 1 and 128, got {cores}.", "code": "VALIDATION"}
            tz = advanced_ui.get("timezone")
            if tz and not re.match(r"^[A-Za-z_]+/[A-Za-z_]+(/[A-Za-z_]+)?$", tz):
                return {"status": "error", "message": f"Validation failed: timezone '{tz}' does not match Region/City format.", "code": "VALIDATION"}
            loc = advanced_ui.get("locale")
            if loc and not re.match(r"^[a-z]{2,3}(-[A-Z]{2,3})?$", loc):
                return {"status": "error", "message": f"Validation failed: locale '{loc}' does not match expected format.", "code": "VALIDATION"}

        max_attempts = 3

        final_profile = None
        for attempt in range(max_attempts):
            try:
                print(f"[Orchestrator] Attempt {attempt + 1}/{max_attempts}")

                # Step 1: Generate AI Fingerprint via Cloudflare Workers AI ONLY
                print("[Orchestrator] Step 1: Generating Fingerprint via Cloudflare AI...")
                fp = await generate_fingerprint_ai()

                if fp.get("_is_fallback"):
                    print("[Orchestrator] STRICT MODE: Cloudflare AI unavailable. All accounts exhausted.")
                    print("[Orchestrator] Profile creation REFUSED. No profile is ever made without AI.")
                    return {
                        "status": "error",
                        "message": "Cloudflare AI unavailable: all accounts failed or are on cooldown. Add more accounts to cloudflare_accounts.txt and retry.",
                        "code": "KIMI_UNAVAILABLE"
                    }

                advanced = {
                    "os": fp.get("os"),
                    "screen_resolution": fp.get("screen_resolution"),
                    "cpu_cores": fp.get("cpu_cores"),
                    "memory_gb": fp.get("memory_gb"),
                    "webgl_vendor": fp.get("webgl_vendor"),
                    "webgl_renderer": fp.get("webgl_renderer"),
                    "audio_noise": fp.get("audio_noise", True),
                    "fonts": fp.get("fonts", []),
                    "plugins": fp.get("plugins", []),
                    "sec_ch_ua": fp.get("sec_ch_ua"),
                    "sec_ch_ua_platform": fp.get("sec_ch_ua_platform"),
                    "disable_automation": True,
                    "headless": False
                }

                if advanced_ui:
                    if "headless" in advanced_ui: advanced["headless"] = advanced_ui["headless"]
                    if "canvas_noise" in advanced_ui: advanced["canvas_noise"] = advanced_ui["canvas_noise"]
                    if "webgl_noise" in advanced_ui: advanced["webgl_noise"] = advanced_ui["webgl_noise"]
                    if "audio_noise" in advanced_ui: advanced["audio_noise"] = advanced_ui["audio_noise"]
                    if "webrtc_mode" in advanced_ui: advanced["webrtc_mode"] = advanced_ui["webrtc_mode"]

                resolved_tz = fp.get("timezone", "UTC")
                resolved_locale = fp.get("locale", "en-US")

                if proxy:
                    print(f"[Orchestrator] Synchronizing Timezone and Locale for proxy {proxy.get('server')}")
                    from backend.proxy_manager import proxy_manager
                    geo_info = await proxy_manager.resolve_proxy_geo(proxy)
                    resolved_tz = geo_info["timezone"]
                    resolved_locale = geo_info["locale"]

                temp_profile = {
                    "id": "temp_" + os.urandom(4).hex(),
                    "os": fp.get("os"),
                    "webgl_vendor": fp.get("webgl_vendor"),
                    "webgl_renderer": fp.get("webgl_renderer"),
                    "cpu_cores": fp.get("cpu_cores"),
                    "memory_gb": fp.get("memory_gb"),
                    "timezone": resolved_tz,
                    "locale": resolved_locale,
                    "advanced": advanced
                }

                # Step 2: AI Coherence Validation
                print("[Orchestrator] Step 2: Running Coherence Validation...")
                coherence_result = coherence_validator.validate(fp)

                if not coherence_result["passed"]:
                    print(f"[Orchestrator] Coherence failed: {coherence_result['issues']}")
                    continue

                print(f"[Orchestrator] Coherence passed with score: {coherence_result['score']}")

                # Step 3: Fresh Directory Initializer
                print("[Orchestrator] Step 3: Initializing Isolated Directory...")
                final_profile = profile_manager.create_profile(
                    name=name,
                    proxy=proxy,
                    timezone=resolved_tz,
                    locale=resolved_locale,
                    advanced=advanced
                )
                final_profile["behavior"] = fp.get("behavior", {})
                profile_manager._save_metadata()

                final_profile["fingerprint"] = fp
                final_profile["user_agent"] = fp.get("userAgent", final_profile.get("user_agent", ""))

                # Step 4: AI Auto Validator
                print("[Orchestrator] Step 4: Running AI Auto Validator...")
                from backend.ai_auto_validator import auto_validator
                validation_result = await auto_validator.validate_profile(final_profile, fp)

                if validation_result["decision"] != "ACCEPT":
                    issues = validation_result.get("issues", [])
                    only_ai_fallback = all("AI analysis failed" in i or "Fallback" in i for i in issues) if issues else False

                    if only_ai_fallback:
                        print(f"[Orchestrator] AI validator unreachable but technical checks PASSED. Accepting profile.")
                    else:
                        print(f"[Orchestrator] Validator flagged real issues: {issues}")

                        print("[Orchestrator] Step 5: Sanitizing Data...")
                        data_sanitizer.sanitize(final_profile["path"])

                        re_validation = await auto_validator.validate_profile(final_profile, fp)
                        re_issues = re_validation.get("issues", [])
                        re_only_ai = all("AI analysis failed" in i or "Fallback" in i for i in re_issues) if re_issues else False

                        if re_validation["decision"] != "ACCEPT" and not re_only_ai:
                            print("[Orchestrator] Sanitization failed. Quarantining profile.")
                            logger.warning(f"Profile {final_profile['id']} quarantined due to real leak: {re_issues}")
                            self._quarantine_profile(final_profile, re_issues)
                            profile_manager.delete_profile(final_profile["id"])
                            continue

                if not skip_warming:
                    print(f"[Orchestrator] Step 5: AI Headless Cookie Warmer...")
                    from backend.cookie_warmer import cookie_warmer
                    await cookie_warmer.warm_profile(final_profile["id"])
                else:
                    print(f"[Orchestrator] Skipping Cookie Warmer as requested.")

                # Step 6: Pre-Launch Verification Complete
                print(f"[Orchestrator] Profile is Zero-Leak! (Score: {validation_result.get('final_score', 'N/A')})")
                return {"status": "success", "profile": final_profile}

            except Exception as e:
                logger.error(f"[Orchestrator] Attempt {attempt+1} failed with exception: {e}")
                if final_profile and final_profile.get("id"):
                    try:
                        profile_manager.delete_profile(final_profile["id"])
                    except Exception:
                        pass
                # Continue to next attempt instead of returning immediately
                continue

        return {"status": "error", "message": "Failed to create a coherent, leak-free profile after maximum attempts."}

    def _quarantine_profile(self, profile: dict, issues: list):
        quarantined_data = []
        if os.path.exists(QUARANTINE_META):
            try:
                with open(QUARANTINE_META, "r") as f:
                    quarantined_data = json.load(f)
            except json.JSONDecodeError:
                pass

        profile["quarantine_reason"] = issues
        quarantined_data.append(profile)

        with open(QUARANTINE_META, "w") as f:
            json.dump(quarantined_data, f, indent=4)

        if os.path.exists(profile["path"]):
            target_path = os.path.join(QUARANTINE_DIR, profile["id"])
            try:
                shutil.move(profile["path"], target_path)
            except Exception as e:
                logger.error(f"Failed to move physical profile to quarantine: {e}")

profile_creator = ProfileCreationOrchestrator()
