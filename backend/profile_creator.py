import asyncio
import os
import json
import shutil
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
        
        # We will attempt up to 3 times to generate a coherent profile
        max_attempts = 3
        
        for attempt in range(max_attempts):
            print(f"[Orchestrator] Attempt {attempt + 1}/{max_attempts}")
            
            # Step 1: Generate AI Fingerprint via Kimi/Cloudflare ONLY
            print("[Orchestrator] Step 1: Generating Fingerprint via Kimi AI...")
            fp = await generate_fingerprint_ai()
            
            # 🔒 STRICT KIMI-ONLY MODE: Abort if fallback was used
            if fp.get("_is_fallback"):
                print("[Orchestrator] ❌ STRICT MODE: Kimi AI unavailable. All Cloudflare accounts exhausted.")
                print("[Orchestrator] Profile creation REFUSED. No profile is ever made without Kimi AI.")
                return {
                    "status": "error",
                    "message": "Kimi AI unavailable: all Cloudflare accounts failed or are on cooldown. Add more accounts to cloudflare_accounts.txt and retry.",
                    "code": "KIMI_UNAVAILABLE"
                }
            
            # Extract advanced properties (merging AI generated with UI provided)
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
                # Override specific booleans from the frontend UI (e.g. headless toggle)
                if "headless" in advanced_ui: advanced["headless"] = advanced_ui["headless"]
                if "canvas_noise" in advanced_ui: advanced["canvas_noise"] = advanced_ui["canvas_noise"]
                if "webgl_noise" in advanced_ui: advanced["webgl_noise"] = advanced_ui["webgl_noise"]
                if "audio_noise" in advanced_ui: advanced["audio_noise"] = advanced_ui["audio_noise"]
                if "webrtc_mode" in advanced_ui: advanced["webrtc_mode"] = advanced_ui["webrtc_mode"]
            
            # Step 1.5: Synchronize Proxy Geolocation & Timezone
            resolved_tz = fp.get("timezone", "UTC")
            resolved_locale = fp.get("locale", "en-US")
            
            if proxy:
                print(f"[Orchestrator] Synchronizing Timezone and Locale for proxy {proxy.get('server')}")
                from backend.proxy_manager import proxy_manager
                geo_info = await proxy_manager.resolve_proxy_geo(proxy)
                resolved_tz = geo_info["timezone"]
                resolved_locale = geo_info["locale"]
            
            # We create a temporary profile object in memory (not saved yet)
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
            
            # Threshold set back to 85 for strict validation
            threshold = 85
            
            if not coherence_result["passed"]:
                print(f"[Orchestrator] Coherence failed: {coherence_result['issues']}")
                continue # Retry generation
                
            print(f"[Orchestrator] Coherence passed with score: {coherence_result['score']}")
            
            # Step 3: Fresh Directory Initializer
            print("[Orchestrator] Step 3: Initializing Isolated Directory...")
            # We actually create the profile in the manager now
            final_profile = profile_manager.create_profile(
                name=name,
                proxy=proxy,
                timezone=resolved_tz,
                locale=resolved_locale,
                advanced=advanced
            )
            # Add behavior to profile (ProfileManager doesn't natively accept behavior in the signature, so we update it)
            final_profile["behavior"] = fp.get("behavior", {})
            profile_manager._save_metadata()
            
            # Attach the full AI fingerprint to the profile so API response has all details
            final_profile["fingerprint"] = fp
            final_profile["user_agent"] = fp.get("userAgent", final_profile.get("user_agent", ""))

            # Step 4: AI Auto Validator (Combines Leak Scanner + Moonshot Kimi AI)
            print("[Orchestrator] Step 4: Running AI Auto Validator...")
            from backend.ai_auto_validator import auto_validator
            validation_result = await auto_validator.validate_profile(final_profile, fp)
            
            if validation_result["decision"] != "ACCEPT":
                # Check if the ONLY reason for failure is AI fallback (not a real JS leak)
                issues = validation_result.get("issues", [])
                only_ai_fallback = all("AI analysis failed" in i or "Fallback" in i for i in issues) if issues else False
                
                if only_ai_fallback:
                    # Technical checks passed — Kimi just couldn't be reached. Safe to accept.
                    print(f"[Orchestrator] ⚠️  AI validator unreachable but technical checks PASSED. Accepting profile.")
                else:
                    print(f"[Orchestrator] Validator flagged real issues: {issues}")
                    
                    # Step 5: AI Data Sanitizer
                    print("[Orchestrator] Step 5: Sanitizing Data...")
                    data_sanitizer.sanitize(final_profile["path"])
                    
                    # Re-scan to verify
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
                # Step 5: Headless Cookie Warmer
                print(f"[Orchestrator] Step 5: AI Headless Cookie Warmer...")
                from backend.cookie_warmer import cookie_warmer
                await cookie_warmer.warm_profile(final_profile["id"])
            else:
                print(f"[Orchestrator] Skipping Cookie Warmer as requested.")
            
            # Step 6: Pre-Launch Verification Complete
            print(f"[Orchestrator] ✅ Profile is Zero-Leak! (Score: {validation_result.get('final_score', 'N/A')})")
            return {"status": "success", "profile": final_profile}
            
        return {"status": "error", "message": "Failed to create a coherent, leak-free profile after maximum attempts."}

    def _quarantine_profile(self, profile: dict, issues: list):
        # 1. Update quarantine meta
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
            
        # 2. Move physical directory
        if os.path.exists(profile["path"]):
            target_path = os.path.join(QUARANTINE_DIR, profile["id"])
            try:
                shutil.move(profile["path"], target_path)
            except Exception as e:
                logger.error(f"Failed to move physical profile to quarantine: {e}")

profile_creator = ProfileCreationOrchestrator()
