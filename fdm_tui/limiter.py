"""Bandwidth limiting."""

import asyncio
import time


class RateLimiter:
    """Token bucket limiting a throughput (bytes/second) shared by several coroutines."""

    def __init__(self, rate: int = 0):
        self.rate = rate  # bytes per second, 0 = unlimited
        self.tokens = float(rate)
        self.last = time.monotonic()

    def set_rate(self, rate: int) -> None:
        self.rate = rate
        self.tokens = float(rate)
        self.last = time.monotonic()

    async def acquire(self, n: int) -> None:
        """Wait as long as needed so that `n` more bytes respect the rate."""
        if self.rate <= 0:
            return
        now = time.monotonic()
        self.tokens = min(self.tokens + (now - self.last) * self.rate, float(self.rate))
        self.last = now
        self.tokens -= n
        if self.tokens < 0:  # in debt: wait for the debt to be repaid
            await asyncio.sleep(-self.tokens / self.rate)
