import os
import json
import uuid
import asyncio
from datetime import datetime
from backend.config import get_data_dir
from backend.macro_runner import MacroRunner

SCHEDULES_FILE = get_data_dir("scheduled_macros.json")

class SchedulerManager:
    def __init__(self, browser_manager, profile_manager, macro_manager):
        self.browser_manager = browser_manager
        self.profile_manager = profile_manager
        self.macro_manager = macro_manager
        self.runner = MacroRunner()
        self.schedules = []
        self._load_schedules()
        self.is_running = False
        self._task = None

    def _load_schedules(self):
        if os.path.exists(SCHEDULES_FILE):
            try:
                with open(SCHEDULES_FILE, "r") as f:
                    self.schedules = json.load(f)
            except Exception:
                # ROBUSTNESS FIX: Backup corrupted file before resetting
                import shutil
                backup_path = SCHEDULES_FILE + f".corrupted.{int(datetime.now().timestamp())}.json"
                try:
                    shutil.copy2(SCHEDULES_FILE, backup_path)
                    print(f"[SchedulerManager] ⚠️ Corrupted schedules file backed up to {backup_path}")
                except Exception:
                    pass
                self.schedules = []
        else:
            self.schedules = []

    def _save_schedules(self):
        with open(SCHEDULES_FILE, "w") as f:
            json.dump(self.schedules, f, indent=4)

    def add_schedule(self, macro_id: str, profile_ids: list, cron_expr: str):
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "macro_id": macro_id,
            "profile_ids": profile_ids,
            "cron": cron_expr,
            "created_at": datetime.now().isoformat()
        }
        self.schedules.append(job)
        self._save_schedules()
        return job

    def delete_schedule(self, job_id: str):
        for job in self.schedules:
            if job["id"] == job_id:
                self.schedules.remove(job)
                self._save_schedules()
                return True
        return False

    def list_schedules(self):
        return self.schedules

    def _is_time_match(self, cron_expr, dt):
        """Simple cron matcher for minute/hour/day/month/weekday"""
        try:
            import croniter
            return croniter.croniter.match(cron_expr, dt)
        except ImportError:
            # Fallback simple cron parser if croniter is missing
            parts = cron_expr.split()
            if len(parts) != 5: return False
            
            minute, hour, dom, month, dow = parts
            
            def match_part(part, val):
                if part == "*": return True
                try:
                    if "/" in part:
                        _, step = part.split("/")
                        return val % int(step) == 0
                    return int(part) == val
                except Exception: return False
            
            return (match_part(minute, dt.minute) and
                    match_part(hour, dt.hour) and
                    match_part(dom, dt.day) and
                    match_part(month, dt.month) and
                    match_part(dow, dt.isoweekday() % 7))

    async def _loop(self):
        while self.is_running:
            now = datetime.now()
            # Only trigger at the start of a minute (0 seconds) to prevent multiple triggers
            if now.second == 0:
                for job in self.schedules:
                    if self._is_time_match(job["cron"], now):
                        print(f"[Scheduler] Triggering job {job['id']} (Macro: {job['macro_id']})")
                        # C4 FIX: Fetch the macro dict before passing to run_macro_bulk
                        # run_macro_bulk expects (profile_ids: List[str], macro: dict), not macro_id
                        macro = self.macro_manager.get_macro(job["macro_id"])
                        if macro:
                            asyncio.create_task(self.runner.run_macro_bulk(job["profile_ids"], macro))
                        else:
                            print(f"[Scheduler] ⚠️  Macro {job['macro_id']} not found! Skipping job {job['id']}.")
            
            # Sleep 1 second (this loop wakes every second but triggers logic only when second == 0)
            await asyncio.sleep(1)

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._loop())
            print("[Scheduler] Started background cron loop")

    def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
