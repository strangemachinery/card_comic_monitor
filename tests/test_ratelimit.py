"""Unit tests for the token-bucket limiter, using an injected fake clock."""

from __future__ import annotations

import pytest

from card_comic_monitor.ratelimit import TokenBucket, limiter_for


class FakeClock:
    """A controllable monotonic clock; `sleep` advances it instead of waiting."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


def make(capacity, refill):
    clock = FakeClock()
    bucket = TokenBucket(
        capacity, refill, monotonic=clock.monotonic, sleep=clock.sleep
    )
    return bucket, clock


def test_capacity_allows_immediate_burst():
    bucket, clock = make(capacity=5, refill=1.0)
    for _ in range(5):
        bucket.acquire()
    assert clock.slept == 0.0  # full bucket, no waiting


def test_blocks_until_refill():
    bucket, clock = make(capacity=1, refill=1.0)  # 1 token/sec
    bucket.acquire()  # drains the only token
    bucket.acquire()  # must wait ~1s for one refill
    assert clock.slept == pytest.approx(1.0)


def test_refill_rate_governs_throughput():
    bucket, clock = make(capacity=1, refill=2.0)  # 2 tokens/sec
    bucket.acquire()
    bucket.acquire()
    assert clock.slept == pytest.approx(0.5)


def test_requesting_more_than_capacity_raises():
    bucket, _ = make(capacity=1, refill=1.0)
    with pytest.raises(ValueError):
        bucket.acquire(2)


def test_invalid_construction():
    with pytest.raises(ValueError):
        TokenBucket(0, 1.0)
    with pytest.raises(ValueError):
        TokenBucket(1, 0)


def test_limiter_for_known_and_unknown_sources():
    assert limiter_for("pricecharting").refill_per_second == pytest.approx(1.0)
    # Unknown source falls back to the conservative default.
    assert limiter_for("does-not-exist").capacity == 1.0
