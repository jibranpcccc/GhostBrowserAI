import asyncio
import random
import time
from backend.profile_manager import profile_manager
from backend.browser_manager import launch_profile, close_profile, is_profile_running, active_browsers
from backend.behavior_engine import BehaviorEngine

class ProfileRotator:
    def __init__(self, max_concurrent=15):
        self.max_concurrent = max_concurrent
        self.is_running = False
        self.profile_session_times = {} # profile_id -> start_time
        
        # Warmup URLs to build cookies before registration tasks
        self.warmup_urls = [
            "https://en.wikipedia.org/wiki/Main_Page",
            "https://www.youtube.com",
            "https://www.reddit.com",
            "https://news.ycombinator.com",
            "https://github.com"
        ]

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        asyncio.create_task(self._rotation_loop())

    def stop(self):
        self.is_running = False

    async def _warmup_routine(self, profile_id):
        """Perform some basic human-like browsing to build cookies."""
        try:
            # Wait a little bit for browser to fully initialize
            await asyncio.sleep(5)
            
            if not is_profile_running(profile_id):
                return
                
            browser_data = active_browsers.get(profile_id)
            if not browser_data: return
            
            page = browser_data["page"]
            
            # Fetch behavior config
            profile = profile_manager.get_profile(profile_id)
            behavior = profile.get("behavior", {}) if profile else {}
            
            engine = BehaviorEngine(page, behavior)
            
            # Visit a random warmup site
            target_url = random.choice(self.warmup_urls)
            print(f"[Rotator] Profile {profile_id} warming up at {target_url}")
            
            await page.goto(target_url, timeout=60000)
            await asyncio.sleep(random.uniform(2.0, 5.0))
            
            # Scroll around like a human
            for _ in range(random.randint(2, 5)):
                await engine.human_scroll(random.randint(300, 800))
                await asyncio.sleep(random.uniform(1.0, 4.0))
                
        except Exception as e:
            print(f"[Rotator] Warmup failed for {profile_id}: {e}")
            # Crash Recovery during warmup
            await close_profile(profile_id)
            if profile_id in self.profile_session_times:
                del self.profile_session_times[profile_id]

    async def _rotation_loop(self):
        while self.is_running:
            try:
                # 1. Clean up profiles that have run too long (e.g. > 60 mins)
                now = time.time()
                for pid, start_time in list(self.profile_session_times.items()):
                    if now - start_time > random.randint(3000, 4800): # 50-80 mins
                        print(f"[Rotator] Rotating out profile {pid} (session expired)")
                        await close_profile(pid)
                        del self.profile_session_times[pid]

                # 2. Check how many are currently active
                current_active_count = len([pid for pid in self.profile_session_times if is_profile_running(pid)])
                
                # Clean up tracking for ones that crashed or closed externally
                for pid in list(self.profile_session_times.keys()):
                    if not is_profile_running(pid):
                        del self.profile_session_times[pid]

                current_active_count = len(self.profile_session_times)

                if current_active_count < self.max_concurrent:
                    # System Health Check before launching
                    from backend.system_monitor import system_monitor
                    health = system_monitor.get_health()
                    
                    if health["status"] == "critical":
                        from backend.logging_config import logger
                        logger.warning(f"Rotator paused: System health critical (RAM: {health['ram_usage_percent']}%)")
                        await asyncio.sleep(60)
                        continue
                        
                    # Graceful Degradation: If CPU is spiking but RAM is okay, slow down launches
                    if health["cpu_usage_percent"] > 85.0:
                        from backend.logging_config import logger
                        logger.warning(f"Rotator throttling: CPU load high ({health['cpu_usage_percent']}%)")
                        await asyncio.sleep(30) # Add an extra 30s delay before trying to launch
                        continue

                    # We need to launch more profiles
                    all_profiles = profile_manager.list_profiles()
                    
                    # Find profiles not currently running
                    available = [p for p in all_profiles if not is_profile_running(p["id"])]
                    
                    if available:
                        # Pick a random one to rotate in
                        to_launch = random.choice(available)
                        print(f"[Rotator] Launching profile {to_launch['id']} to fill capacity ({current_active_count + 1}/{self.max_concurrent})")
                        
                        try:
                            result = await launch_profile(to_launch["id"])
                            if result.get("status") == "success":
                                self.profile_session_times[to_launch["id"]] = time.time()
                                # Start a warmup routine in the background
                                asyncio.create_task(self._warmup_routine(to_launch["id"]))
                                
                                # Randomized Fleet Cooldown: pause between 2 to 5 minutes before launching another
                                cooldown = random.randint(120, 300)
                                print(f"[Rotator] Cooldown initiated. Waiting {cooldown} seconds before next check.")
                                await asyncio.sleep(cooldown)
                                continue # Skip the default 10s sleep below
                            else:
                                print(f"[Rotator] Failed to launch {to_launch['id']}: {result.get('message')}")
                        except Exception as e:
                            print(f"[Rotator] CRASH caught during launch of {to_launch['id']}: {e}")
                            await close_profile(to_launch["id"])
                            
                            # Randomized Fleet Cooldown: pause between 2 to 5 minutes before launching another
                            cooldown = random.randint(120, 300)
                            print(f"[Rotator] Cooldown initiated. Waiting {cooldown} seconds before next check.")
                            await asyncio.sleep(cooldown)
                            continue # Skip the default 10s sleep below
            
            except Exception as e:
                print(f"[Rotator] Error in rotation loop: {e}")
                
            # Wait a bit before checking again
            await asyncio.sleep(10)

rotator = ProfileRotator(max_concurrent=15)
