"""A token-bucket rate limiter so each source honors its documented quota.

Per-source defaults live in SOURCE_RATE_LIMITS. Examples from docs/DECISIONS.md:
PriceCharting is 1 call/second; free TCG tiers and GoCollect Pro are ~100/day.

The clock and sleep functions are injectable to keep the limiter unit-testable
without real time passing.
"""

from __future__ import annotations

import threading
import time as _time
from typing import Callable

# source -> (capacity, refill_tokens_per_second)
SOURCE_RATE_LIMITS: dict[str, tuple[float, float]] = {
    # Generous for the offline demo source.
    "stub": (1000.0, 1000.0),
    # PriceCharting API: 1 call per second.
    "pricecharting": (1.0, 1.0),
    # GoCollect Pro: ~100 calls/day  ->  100 / 86400 s.
    "gocollect": (100.0, 100.0 / 86_400.0),
    # Free TCG tiers: ~100 requests/day.
    "scrydex": (100.0, 100.0 / 86_400.0),
}

# Conservative fallback for any source without an explicit entry.
DEFAULT_RATE_LIMIT: tuple[float, float] = (1.0, 1.0)


class TokenBucket:
    """Classic token bucket. `acquire` blocks until a token is available."""

    def __init__(
        self,
        capacity: float,
        refill_per_second: float,
        *,
        monotonic: Callable[[], float] = _time.monotonic,
        sleep: Callable[[float], None] = _time.sleep,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be positive")
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self._tokens = float(capacity)
        self._monotonic = monotonic
        self._sleep = sleep
        self._last = monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(
                self.capacity, self._tokens + elapsed * self.refill_per_second
            )
            self._last = now

    def acquire(self, tokens: float = 1.0) -> None:
        """Block until `tokens` are available, then consume them."""
        if tokens > self.capacity:
            raise ValueError("requested tokens exceed bucket capacity")
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self.refill_per_second
            self._sleep(wait)


def limiter_for(source: str, **kwargs) -> TokenBucket:
    """Build a TokenBucket using the documented limit for `source`."""
    capacity, refill = SOURCE_RATE_LIMITS.get(source, DEFAULT_RATE_LIMIT)
    return TokenBucket(capacity, refill, **kwargs)
