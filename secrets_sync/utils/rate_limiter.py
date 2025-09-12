from __future__ import annotations

import asyncio
import time
from typing import Optional


class TokenBucketRateLimiter:
    """Simple token bucket for asyncio.

    - rate: tokens per second replenished
    - capacity: max tokens bucket can hold
    """

    def __init__(self, rate: float, capacity: Optional[int] = None):
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else max(1.0, self.rate))
        self._tokens = self.capacity
        self._last = time.perf_counter()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.perf_counter()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Need to wait until next token is available
                deficit = 1.0 - self._tokens
                await asyncio.sleep(deficit / self.rate)
