"""In-process protection of the expensive endpoints. State is per worker process."""

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import HTTPException, status


class AdmissionLimiter:
    """Rejects requests beyond a number of concurrent ones instead of queueing them without bound.

    Requests are served by a single inference thread, so queued requests would only wait longer and longer.
    """

    def __init__(self, max_concurrent: int, retry_after_seconds: int = 10) -> None:
        self._max_concurrent = max_concurrent
        self._retry_after = str(retry_after_seconds)
        self._active = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        # The event loop is single-threaded, so the counter needs no lock
        if self._active >= self._max_concurrent:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The service is busy, retry later",
                headers={"Retry-After": self._retry_after},
            )
        self._active += 1
        try:
            yield
        finally:
            self._active -= 1


class RateLimiter:
    """Fixed one-minute window of requests per client key (the end user's IP)."""

    def __init__(self, requests_per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = requests_per_minute
        self._clock = clock
        self._window = -1
        self._counts: dict[str, int] = {}

    def check(self, key: str) -> None:
        window = int(self._clock() // 60)
        if window != self._window:
            self._window = window
            self._counts = {}
        count = self._counts.get(key, 0)
        if count >= self._limit:
            retry_after = str(60 - int(self._clock() % 60))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, retry later",
                headers={"Retry-After": retry_after},
            )
        self._counts[key] = count + 1
