import os
import shutil

class AIDataSanitizer:
    """
    Physically scrubs the profile's User Data directory if leakage is detected.
    """
    
    def sanitize(self, profile_path: str):
        print(f"[Sanitizer] Scrubbing profile path: {profile_path}")
        
        # In Playwright, user data dirs have a 'Default' subfolder that contains the actual storage
        default_dir = os.path.join(profile_path, "Default")
        if not os.path.exists(default_dir):
            return {"status": "success", "message": "Nothing to sanitize yet."}
            
        targets = [
            "Cookies",
            "Cookies-journal",
            "Local Storage",
            "Session Storage",
            "IndexedDB",
            "History",
            "Web Data",
            "Network"
        ]
        
        cleaned = 0
        for target in targets:
            target_path = os.path.join(default_dir, target)
            if os.path.exists(target_path):
                try:
                    if os.path.isdir(target_path):
                        shutil.rmtree(target_path)
                    else:
                        os.remove(target_path)
                    cleaned += 1
                except Exception as e:
                    print(f"[Sanitizer] Warning: Could not remove {target_path} - {e}")
                    
        return {"status": "success", "message": f"Sanitized {cleaned} storage artifacts."}

data_sanitizer = AIDataSanitizer()
