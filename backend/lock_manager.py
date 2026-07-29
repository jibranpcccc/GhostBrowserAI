import asyncio
from collections import defaultdict

# ponytail: per-profile async locks backed by the stdlib defaultdict.
# Callers use lock_manager.acquire(profile_id) / release(profile_id).
locks = defaultdict(asyncio.Lock)


class _LockManager:
    async def acquire(self, profile_id: str):
        await locks[profile_id].acquire()

    def release(self, profile_id: str):
        if profile_id in locks and locks[profile_id].locked():
            locks[profile_id].release()


lock_manager = _LockManager()
