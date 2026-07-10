import os
import json
from datetime import datetime, timezone
from backend.profile_manager import profile_manager

class FingerprintEvolver:
    """
    Naturally ages browser profiles over time.
    Instead of a profile staying stagnant forever (which is a fingerprinting signal),
    this engine slowly increments minor browser version numbers and updates history data.
    """
    
    def evolve_profile(self, profile_id: str):
        profile = profile_manager.get_profile(profile_id)
        if not profile: return False
        
        advanced = profile.get("advanced", {})
        updates = {}
        advanced_updates = {}
        
        # 1. Bump Chrome Minor Version String if present in User Agent
        ua = profile.get("user_agent", "")
        if "Chrome/" in ua:
            try:
                parts = ua.split("Chrome/")
                version_part = parts[1].split(" ")[0]
                v_split = version_part.split(".")
                
                if len(v_split) == 4:
                    patch = int(v_split[3])
                    v_split[3] = str(patch + 1)
                    
                    new_version = ".".join(v_split)
                    new_ua = ua.replace(version_part, new_version)
                    
                    updates["user_agent"] = new_ua
                    
                    # HIGH-08 FIX: Also update sec_ch_ua AND client_hints.uaFullVersion
                    # Without this, navigator.userAgent and navigator.userAgentData.getHighEntropyValues
                    # return mismatched versions — a strong bot signal.
                    if "sec_ch_ua" in advanced:
                        advanced_updates["sec_ch_ua"] = advanced["sec_ch_ua"].replace(version_part, new_version)
                    
                    # Update uaFullVersion inside client_hints dict
                    client_hints = advanced.get("client_hints", {})
                    if client_hints:
                        client_hints["uaFullVersion"] = new_version
                        advanced_updates["client_hints"] = client_hints
                        
            except Exception:
                pass
                
        # 2. Increment "profile age" counter
        updates["age_days"] = profile.get("age_days", 0) + 1
        updates["last_evolved"] = datetime.now(timezone.utc).isoformat()
        
        if advanced_updates:
            updates["advanced"] = advanced_updates
        
        # LOW-04 FIX: Use update_profile() instead of direct dict mutation + private _save_metadata()
        # This is thread-safe and goes through proper validation.
        profile_manager.update_profile(profile_id, updates)
        return True

fingerprint_evolver = FingerprintEvolver()
