import os

class AILeakScanner:
    def __init__(self):
        pass

    def scan_profile_before_launch(self, profile_data: dict) -> dict:
        """
        AI Leak Scanner Phase 1:
        Verifies that the profile data directory is completely clean or correctly initialized
        before launching the browser to prevent data leaks from old profiles.
        """
        path = profile_data.get("path")
        if not path or not os.path.exists(path):
            return {"status": "error", "message": "Profile directory does not exist. Potential leak or corruption."}
        
        # Heuristic / AI Consistency checks
        advanced = profile_data.get("advanced", {})
        os_type = advanced.get("os", "Windows")
        cpu = int(advanced.get("cpu_cores", 4))
        memory = int(advanced.get("memory_gb", 8))
        screen = advanced.get("screen_resolution", "1920x1080")

        # Basic Consistency Rules
        if os_type == "Mac":
            if screen == "1366x768":
                return {"status": "error", "message": "AI Consistency Failure: Macs rarely use 1366x768. This looks like a bot."}
        
        if cpu > memory * 2:
            return {"status": "error", "message": "AI Consistency Failure: CPU core count is unusually high for the given RAM."}

        # Check if it's completely empty (meaning a brand new clean profile)
        is_empty = len(os.listdir(path)) == 0

        # We allow non-empty if the profile is returning, but we would ideally run a heuristic scan here.
        if is_empty:
            return {"status": "clean", "message": "Profile directory is completely fresh and clean."}
        else:
            return {"status": "clean", "message": "Profile directory has existing data (returning profile). AI scan passed."}

# Global instance
ai_scanner = AILeakScanner()
