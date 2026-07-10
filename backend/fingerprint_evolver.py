import os
import json
from datetime import datetime
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
        
        # 1. Bump Chrome Minor Version String if present in User Agent
        ua = profile.get("user_agent", "")
        if "Chrome/" in ua:
            try:
                # e.g., "Mozilla/5.0... Chrome/114.0.5735.199 Safari/537.36"
                parts = ua.split("Chrome/")
                version_part = parts[1].split(" ")[0]
                v_split = version_part.split(".")
                
                # Increment the build/patch number slightly to simulate a background browser update
                if len(v_split) == 4:
                    build = int(v_split[2])
                    patch = int(v_split[3])
                    v_split[3] = str(patch + 1) # minor patch bump
                    
                    new_version = ".".join(v_split)
                    new_ua = ua.replace(version_part, new_version)
                    
                    profile["user_agent"] = new_ua
                    
                    # Also update sec-ch-ua if present
                    if "sec_ch_ua" in advanced:
                        advanced["sec_ch_ua"] = advanced["sec_ch_ua"].replace(version_part, new_version)
            except Exception:
                pass # If parsing fails, just skip UA evolution
                
        # 2. Increment "profile age" counter
        age = profile.get("age_days", 0)
        profile["age_days"] = age + 1
        profile["last_evolved"] = datetime.utcnow().isoformat()
        
        # Save back to disk
        profile_manager._save_metadata()
        return True

fingerprint_evolver = FingerprintEvolver()
