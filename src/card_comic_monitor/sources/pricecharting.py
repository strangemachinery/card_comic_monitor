"""PriceCharting source worker.

Fetches current prices for items whose source_native_id in the crosswalk is a
PriceCharting product id (an integer string, e.g. "6910"). One API call per
product; the token bucket in ratelimit.py holds this to 1 call/second.

API docs: https://www.pricecharting.com/api-documentation
  GET https://www.pricecharting.com/api/product?id={id}&t={token}
  Auth:    query param `t`  (40-char token from PRICECHARTING_TOKEN env var)
  Prices:  integers in USD cents; 0 means no data for that condition.
  History: not available — this daily snapshot IS the history.
  Updates: by ~08:00 EST daily; safe to schedule after that.
  ToS:     personal/internal use; no redistribution without written consent.

Each product response yields up to seven PriceSnapshot rows (one per
condition/grade with a non-zero price): loose, cib, sealed, graded (generic),
and graded at internal grades 8/9/10 (approximately PSA/CGC 8/9/10).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable, Iterator

from ..models import FetchTarget, PriceSnapshot
from ..ratelimit import TokenBucket
from .base import Source

logger = logging.getLogger(__name__)

_API_URL = "https://www.pricecharting.com/api/product"

# (api_field, condition, grade)
# grade-N-price uses PriceCharting's internal 1-10 scale, which maps
# approximately to PSA/CGC/BGS grades; exact equivalence is per their site.
# Zero-price rows are skipped (no market data for that condition on this item).
_PRICE_FIELDS: list[tuple[str, str, str]] = [
    ("loose-price",    "loose",  ""),
    ("cib-price",      "cib",    ""),
    ("new-price",      "sealed", ""),
    ("graded-price",   "graded", ""),
    ("grade-8-price",  "graded", "8"),
    ("grade-9-price",  "graded", "9"),
    ("grade-10-price", "graded", "10"),
]


def _parse_cents(value: object) -> int | None:
    """Return the API value in cents, or None if zero/missing/invalid."""
    try:
        v = int(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


class PricechartingSource(Source):
    """One API call per product id; yields one snapshot per non-zero condition."""

    name = "pricecharting"

    def __init__(self, limiter: TokenBucket, *, token: str | None = None) -> None:
        super().__init__(limiter)
        self.token = token or os.environ.get("PRICECHARTING_TOKEN", "")
        if not self.token:
            raise ValueError(
                "PRICECHARTING_TOKEN is not set. "
                "Get a token at https://www.pricecharting.com/api-documentation"
            )

    def _get_product(self, product_id: str) -> dict:
        params = urllib.parse.urlencode({"id": product_id, "t": self.token})
        url = f"{_API_URL}?{params}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "card-comic-monitor/0.1"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    def fetch(self, targets: Iterable[FetchTarget]) -> Iterator[PriceSnapshot]:
        for target in targets:
            self.limiter.acquire()
            try:
                data = self._get_product(target.source_native_id)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise RuntimeError(
                        f"pricecharting: auth failed ({exc.code}) — "
                        "check PRICECHARTING_TOKEN"
                    ) from exc
                logger.warning(
                    "pricecharting: HTTP %d for product %s, skipping",
                    exc.code,
                    target.source_native_id,
                )
                continue
            except OSError as exc:
                logger.warning(
                    "pricecharting: network error for product %s: %s",
                    target.source_native_id,
                    exc,
                )
                continue

            if data.get("status") == "fail":
                logger.warning(
                    "pricecharting: product %s not found, skipping",
                    target.source_native_id,
                )
                continue

            for field, condition, grade in _PRICE_FIELDS:
                market = _parse_cents(data.get(field))
                if market is None:
                    continue
                yield PriceSnapshot(
                    item_id=target.item_id,
                    source=self.name,
                    market_cents=market,
                    condition=condition,
                    grade=grade,
                )
