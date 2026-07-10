import psutil
import asyncio
import time
from backend.browser_manager import active_browsers, close_profile

class SystemMonitor:
    """
    Monitors system resources (RAM, CPU).
    Provides zombie process cleanup and emergency memory release.
    """
    def __init__(self):
        self.is_running = False
        self.cpu_usage = 0.0
        self.ram_usage = 0.0
        self.active_processes = 0
        self.max_ram_threshold = 90.0 # 90% RAM usage limit

    async def start(self):
        if self.is_running: return
        self.is_running = True
        asyncio.create_task(self._monitor_loop())

    def stop(self):
        self.is_running = False

    async def _monitor_loop(self):
        while self.is_running:
            try:
                self.cpu_usage = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory()
                self.ram_usage = mem.percent
                
                # Count Chromium processes
                chromium_count = 0
                for proc in psutil.process_iter(['name']):
                    if proc.info['name'] and 'chrome' in proc.info['name'].lower():
                        chromium_count += 1
                self.active_processes = chromium_count

                # Emergency Memory Release
                if self.ram_usage >= self.max_ram_threshold:
                    await self._emergency_memory_release()

                # Run Fingerprint Evolver daily
                now = time.time()
                if now - getattr(self, 'last_evolve_time', 0) > 86400: # 24 hours
                    from backend.fingerprint_evolver import fingerprint_evolver
                    from backend.profile_manager import profile_manager
                    print("[SystemMonitor] Triggering daily profile evolution (aging)...")
                    for p in profile_manager.list_profiles():
                        fingerprint_evolver.evolve_profile(p["id"])
                    self.last_evolve_time = now

            except Exception as e:
                print(f"[SystemMonitor] Error in monitoring loop: {e}")
                
            await asyncio.sleep(10)

    async def _emergency_memory_release(self):
        """
        If RAM exceeds threshold, kill the oldest running profile to save the server.
        """
        print(f"[SystemMonitor] CRITICAL: RAM at {self.ram_usage}%. Initiating Emergency Release!")
        if not active_browsers:
            return
            
        from backend.profile_rotator import rotator
        # Find oldest profile
        oldest_profile_id = None
        oldest_time = time.time()
        
        for pid, start_time in rotator.profile_session_times.items():
            if start_time < oldest_time:
                oldest_time = start_time
                oldest_profile_id = pid
                
        if oldest_profile_id:
            print(f"[SystemMonitor] Emergency killing oldest profile: {oldest_profile_id}")
            await close_profile(oldest_profile_id)
            if oldest_profile_id in rotator.profile_session_times:
                del rotator.profile_session_times[oldest_profile_id]

    def get_health(self):
        return {
            "cpu_usage_percent": self.cpu_usage,
            "ram_usage_percent": self.ram_usage,
            "active_chromium_processes": self.active_processes,
            "status": "critical" if self.ram_usage >= self.max_ram_threshold else "healthy"
        }

system_monitor = SystemMonitor()
