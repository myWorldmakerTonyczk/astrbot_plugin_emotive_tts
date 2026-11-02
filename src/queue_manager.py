import asyncio
from contextlib import asynccontextmanager


class QueueFull(RuntimeError):
    pass


class WorkGate:
    """FIFO lock at synthesis entry; bounded waiters and concurrent API jobs."""

    def __init__(self, concurrency=2, capacity=16):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.capacity = capacity
        self.pending = 0
        self.slots = {}

    @asynccontextmanager
    async def enter(self, key):
        if self.pending >= self.capacity:
            raise QueueFull()
        self.pending += 1
        slot = self.slots.setdefault(key, [asyncio.Lock(), 0])
        slot[1] += 1
        try:
            async with slot[0], self.semaphore:
                yield
        finally:
            self.pending -= 1
            slot[1] -= 1
            if not slot[1]:
                self.slots.pop(key, None)
