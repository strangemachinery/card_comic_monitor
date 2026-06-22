"""tcgapi.dev source worker (recommended default card source).

Resolves prices for cards by their TCGplayer product id via the per-card
endpoint on the free 100-req/day tier.

API: https://tcgapi.dev   Auth: `X-API-Key` header (TCGAPI_API_KEY env var)
  GET /v1/cards/tcgplayer/{product_id}
    response envelope:
      data.tcgplayer_id     -- the product ID
      data.total_listings   -- active listing count
      data.prices[]         -- [{printing, market_price, low_price, ...}]
      rate_limit.daily_remaining

  Rate limit: HTTP 429 when exhausted; daily_remaining echoed in every response.

  Note: the bulk POST endpoint (/v1/bulk/resolve/tcgplayer) requires a paid
  "Starter" tier. The per-card GET is free (100 req/day). With a small curated
  watchlist this costs 1 credit per card per run.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Iterable, Iterator

from ..models import FetchTarget, PriceSnapshot
from ..ratelimit import TokenBucket
from .base import Source

logger = logging.getLogger(__name__)

_SINGLE_URL = "https://api.tcgapi.dev/v1/cards/tcgplayer/{}"

_MARKET_KEYS = ("market_price", "market", "price")
_LOW_KEYS = ("low_price", "low", "lowPrice")
_LISTINGS_KEYS = ("total_listings", "listings", "num_listings", "totalListings")


def _first(obj: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def _to_cents(dollars: object) -> int | None:
    """Convert a USD dollar amount (float/str) to positive integer cents."""
    try:
        cents = round(float(dollars) * 100)
        return cents if cents > 0 else None
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _snapshot_from_record(record: dict, item_id: int) -> PriceSnapshot | None:
    """Build a raw-condition snapshot from one card data object, or None.

    Prices live in record["prices"] as [{printing, market_price, low_price}].
    Prefers "Normal" printing; falls back to the first entry (e.g. "Holofoil").
    """
    prices_list = record.get("prices")
    if isinstance(prices_list, list) and prices_list:
        price_rec = next(
            (p for p in prices_list
             if isinstance(p, dict) and p.get("printing", "").lower() == "normal"),
            prices_list[0] if isinstance(prices_list[0], dict) else None,
        )
    else:
        price_rec = record  # flat record fallback

    if price_rec is None:
        return None

    market = _to_cents(_first(price_rec, _MARKET_KEYS))
    if market is None:
        return None
    return PriceSnapshot(
        item_id=item_id,
        source=TcgapiSource.name,
        market_cents=market,
        low_cents=_to_cents(_first(price_rec, _LOW_KEYS)),
        volume=_to_int(_first(record, _LISTINGS_KEYS)),
        condition="raw",  # TCG market price = ungraded Near Mint
        grade="",
    )


class TcgapiSource(Source):
    name = "tcgapi"

    def __init__(self, limiter: TokenBucket, *, api_key: str | None = None) -> None:
        super().__init__(limiter)
        self.api_key = api_key or os.environ.get("TCGAPI_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "TCGAPI_API_KEY is not set. Get a free key at https://tcgapi.dev"
            )

    def _fetch_card(self, product_id: str) -> dict | None:
        """GET /v1/cards/tcgplayer/{id}; returns the data object or None."""
        req = urllib.request.Request(
            _SINGLE_URL.format(product_id),
            headers={
                "X-API-Key": self.api_key,
                "User-Agent": "card-comic-monitor/0.1",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def fetch(self, targets: Iterable[FetchTarget]) -> Iterator[PriceSnapshot]:
        by_pid: dict[str, int] = {t.source_native_id: t.item_id for t in targets}
        if not by_pid:
            return

        for pid, item_id in by_pid.items():
            self.limiter.acquire()
            try:
                record = self._fetch_card(pid)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise RuntimeError(
                        f"tcgapi: auth failed ({exc.code}) -- check TCGAPI_API_KEY"
                    ) from exc
                if exc.code == 429:
                    logger.warning("tcgapi: rate limited (429); stopping this run")
                    return
                logger.warning("tcgapi: HTTP %d for product_id %s, skipping", exc.code, pid)
                continue
            except OSError as exc:
                logger.warning("tcgapi: network error for product_id %s: %s", pid, exc)
                continue

            if record is None:
                logger.info("tcgapi: no data returned for product_id %s", pid)
                continue

            snap = _snapshot_from_record(record, item_id)
            if snap is not None:
                yield snap
            else:
                logger.info("tcgapi: no market price for product_id %s", pid)
