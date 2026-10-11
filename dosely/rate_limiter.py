"""
dosely/rate_limiter.py
======================
In-memory sliding-window rate limiter for FastAPI routes.
Returns HTTP 429 Too Many Requests when limits are exceeded.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    """
    Sliding window rate limiter keeping track of timestamped requests per client key.
    """

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        timestamps = [ts for ts in self._history[key] if ts > cutoff]
        if len(timestamps) >= self.max_requests:
            self._history[key] = timestamps
            return False
        timestamps.append(now)
        self._history[key] = timestamps
        return True

    def reset(self) -> None:
        self._history.clear()


# Default limiter: 60 requests per minute
default_limiter = SlidingWindowRateLimiter(max_requests=60, window_seconds=60)


def rate_limit(max_requests: int = 60, window_seconds: int = 60) -> Callable:
    """FastAPI dependency to rate-limit an endpoint."""
    limiter = SlidingWindowRateLimiter(max_requests=max_requests, window_seconds=window_seconds)

    async def _dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        auth_header = request.headers.get("Authorization", "")
        key = f"{client_ip}:{auth_header[:30]}"
        if not limiter.is_allowed(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please retry after some time.",
            )

    return _dependency
