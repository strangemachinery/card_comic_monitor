"""tcgapi.dev source worker (recommended default card source).

Resolves prices for cards by their TCGplayer product id via the bulk endpoint,
so a whole watchlist of cards costs only a handful of API calls (quota-friendly
on the free 100 req/day tier).

API: https://tcgapi.dev   Auth: `X-API-Key` header (TCGAPI_API_KEY env var)
  POST /v1/bulk/resolve/tcgplayer
    body:  {"ids": [187172, 187171, ...]}   (integers)
    response envelope:
      data.resolved[]   -- list of {tcgplayer_id, card:{...}, prices:[{printing, market_price, low_price}]}
      data.not_found[]  -- IDs with no match
      meta.credits_consumed

  Rate limit: HTTP 429 with X-RateLimit-Reset / Retry-After when exhausted.
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

_BULK_URL = "https://api.tcgapi.dev/v1/bulk/resolve/tcgplayer"

# Max TCGplayer product ids per bulk request (chunked to be safe).
_BATCH_SIZE = 100

# Field name constants (confirmed from live API shape).
_PRODUCT_ID_KEYS = ("tcgplayer_id", "tcgplayerId", "product_id", "productId", "id")
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


def _extract_records(payload: object) -> list[dict]:
    """Pull resolved items from the bulk-resolve response envelope.

    Handles the documented shape (data.resolved[]) and also falls back to
    simpler envelopes for forward-compatibility.
    """
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            # Primary: data.resolved (bulk resolve endpoint)
            resolved = data.get("resolved")
            if isinstance(resolved, list):
                return [r for r in resolved if isinstance(r, dict)]
        elif isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        # Flat envelopes (other endpoints / future API versions)
        for key in ("resolved", "results", "cards", "products"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def _snapshot_from_record(record: dict, item_id: int) -> PriceSnapshot | None:
    """Build a raw-condition market snapshot from one resolved item, or None.

    Prices live in record["prices"] as [{printing, market_price, low_price}].
    We prefer the "Normal" printing; fall back to the first entry if absent.
    For flat records (non-bulk endpoints) we read price fields directly.
    """
    prices_list = record.get("prices")
    if isinstance(prices_list, list) and prices_list:
        price_rec = next(
            (p for p in prices_list
             if isinstance(p, dict) and p.get("printing", "").lower() == "normal"),
            prices_list[0] if isinstance(prices_list[0], dict) else None,
        )
    else:
        price_rec = record  # flat record path

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
        condition="raw",  # TCG market price is ungraded Near Mint
        grade="",
    )


def _chunks(seq: list, size: int) -> Iterator[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


class TcgapiSource(Source):
    name = "tcgapi"

    def __init__(self, limiter: TokenBucket, *, api_key: str | None = None) -> None:
        super().__init__(limiter)
        self.api_key = api_key or os.environ.get("TCGAPI_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "TCGAPI_API_KEY is not set. Get a free key at https://tcgapi.dev"
            )

    def _bulk_resolve(self, product_ids: list[str]) -> list[dict]:
        # API expects integer IDs
        body = json.dumps({"ids": [int(pid) for pid in product_ids]}).encode()
        req = urllib.request.Request(
            _BULK_URL,
            data=body,
            method="POST",
            headers={
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "card-comic-monitor/0.1",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return _extract_records(json.loads(resp.read().decode()))

    def fetch(self, targets: Iterable[FetchTarget]) -> Iterator[PriceSnapshot]:
        # Map TCGplayer product id -> canonical item_id for this run.
        by_pid: dict[str, int] = {t.source_native_id: t.item_id for t in targets}
        if not by_pid:
            return

        for batch in _chunks(list(by_pid), _BATCH_SIZE):
            self.limiter.acquire()
            try:
                records = self._bulk_resolve(batch)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise RuntimeError(
                        f"tcgapi: auth failed ({exc.code}) -- check TCGAPI_API_KEY"
                    ) from exc
                if exc.code == 429:
                    # Quota exhausted: stop cleanly rather than hammering.
                    logger.warning("tcgapi: rate limited (429); stopping this run")
                    return
                logger.warning("tcgapi: HTTP %d for batch, skipping", exc.code)
                continue
            except OSError as exc:
                logger.warning("tcgapi: network error for batch: %s", exc)
                continue

            returned: set[str] = set()
            for record in records:
                pid = _first(record, _PRODUCT_ID_KEYS)
                pid = None if pid is None else str(pid)
                item_id = by_pid.get(pid) if pid is not None else None
                if item_id is None:
                    continue
                returned.add(pid)
                snap = _snapshot_from_record(record, item_id)
                if snap is not None:
                    yield snap

            missing = set(batch) - returned
            if missing:
                logger.info("tcgapi: no price for %d id(s) in batch", len(missing))
