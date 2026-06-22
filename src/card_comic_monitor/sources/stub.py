"""An offline demo source.

It needs no API key and generates deterministic-but-varying prices so the whole
pipeline (migrate -> sync -> snapshot) can run and accrue history locally before
any real vendor is wired up. The price wobbles by day, which makes the eventual
"movers" metrics show non-trivial movement during development.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Iterable, Iterator

from ..models import FetchTarget, PriceSnapshot
from .base import Source


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def _demo_price_cents(native_id: str, day_ordinal: int) -> int:
    """A stable base price per item plus a smooth daily wobble."""
    seed = _seed(native_id)
    base = 500 + seed % 49_500  # $5.00 .. $500.00
    phase = (seed % 360) * math.pi / 180.0
    wobble = math.sin(day_ordinal / 7.0 + phase)  # ~weekly cycle, [-1, 1]
    return max(100, int(base * (1.0 + 0.08 * wobble)))


class StubSource(Source):
    name = "stub"

    def fetch(self, targets: Iterable[FetchTarget]) -> Iterator[PriceSnapshot]:
        day_ordinal = datetime.now(timezone.utc).date().toordinal()
        for target in targets:
            self.limiter.acquire()  # honor the (here, generous) rate limit
            market = _demo_price_cents(target.source_native_id, day_ordinal)
            yield PriceSnapshot(
                item_id=target.item_id,
                source=self.name,
                market_cents=market,
                low_cents=int(market * 0.9),
                high_cents=int(market * 1.15),
                volume=1 + _seed(target.source_native_id) % 25,
            )
