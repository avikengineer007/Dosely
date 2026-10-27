"""
dosely/rate_limiter.py
======================
In-memory sliding-window rate limiter for FastAPI routes.
Returns HTTP 429 Too Many Requests with Retry-After when limits are exceeded.
Keyed strictly by client IP and/or authenticated user ID (never by raw token).
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Callable, Optional

from fastapi import HTTPException, Request, status

_TRUSTED_PROXIES = {
    ip.strip()
    for ip in os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1,localhost").split(",")
    if ip.strip()
}


def get_client_ip(request: Request) -> str:
    """
    Extract the client IP address.
    Honors X-Forwarded-For ONLY if the immediate client is a configured trusted proxy.
    """
    direct_ip = request.client.host if request.client else "127.0.0.1"
    if direct_ip in _TRUSTED_PROXIES:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            first_ip = xff.split(",")[0].strip()
            if first_ip:
                return first_ip
    return direct_ip


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

    def get_retry_after(self, key: str) -> int:
        """Seconds until at least one request expires from the window."""
        now = time.time()
        cutoff = now - self.window_seconds
        timestamps = [ts for ts in self._history[key] if ts > cutoff]
        if not timestamps:
            return 1
        oldest = min(timestamps)
        remaining = int(self.window_seconds - (now - oldest)) + 1
        return max(1, remaining)

    def reset(self) -> None:
        self._history.clear()


# Default limiter: 60 requests per minute
default_limiter = SlidingWindowRateLimiter(max_requests=60, window_seconds=60)


def rate_limit(
    max_requests: int = 60,
    window_seconds: int = 60,
    key_prefix: str = "ip",
) -> Callable:
    """
    FastAPI dependency to rate-limit an endpoint by client IP.
    Never relies on Authorization header/token to prevent token-rotation bypass.
    """
    limiter = SlidingWindowRateLimiter(max_requests=max_requests, window_seconds=window_seconds)

    async def _dependency(request: Request) -> None:
        client_ip = get_client_ip(request)
        key = f"{key_prefix}:{client_ip}"
        if not limiter.is_allowed(key):
            retry_after = limiter.get_retry_after(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please retry after some time.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency
