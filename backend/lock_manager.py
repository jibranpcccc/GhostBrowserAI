import asyncio

class LockManager:
    """
    Manages asyncio Locks on a per-profile basis.
    Ensures that multiple async tasks (like the rotator and an API request)
    do not attempt to launch, close, or modify the same profile at the exact same time.
    """
    def __init__(self):
        self.locks = {}
        self.global_lock = asyncio.Lock()

    async def acquire(self, profile_id: str):
        """
        Acquires the lock for a specific profile_id.
        """
        async with self.global_lock:
            if profile_id not in self.locks:
                self.locks[profile_id] = asyncio.Lock()
        
        await self.locks[profile_id].acquire()

    async def release(self, profile_id: str):
        """
        Releases the lock for a specific profile_id.
        """
        if profile_id in self.locks:
            if self.locks[profile_id].locked():
                self.locks[profile_id].release()

lock_manager = LockManager()
