"""TCGCSV bulk discovery source.

Downloads TCGplayer price files from tcgcsv.com — a free, no-auth mirror of
the entire TCGplayer catalog updated daily. One HTTP request per set gives
prices for every card in that set, making full-catalog scans cheap.

URL scheme:
  GET https://tcgcsv.com/tcgplayer/{categoryId}/groups         -> [{groupId, name}]
  GET https://tcgcsv.com/tcgplayer/{categoryId}/{groupId}/prices   -> [{productId, subTypeName, marketPrice, lowPrice}]
  GET https://tcgcsv.com/tcgplayer/{categoryId}/{groupId}/products -> [{productId, name, groupId}]

Category IDs for supported games are in CATEGORY_IDS.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Iterator

from ..models import MarketSnapshot
from ..ratelimit import TokenBucket

logger = logging.getLogger(__name__)

# TCGplayer category IDs on tcgcsv.com
CATEGORY_IDS: dict[str, int] = {
    "pokemon":    3,
    "one_piece":  65,
    "dragonball": 65,  # placeholder — verify actual ID
    "mtg":        1,
}

_BASE_URL = "https://tcgcsv.com/tcgplayer"
_MIN_MARKET_CENTS = 100  # skip cards under $1 (noise / bulk commons)


def _fetch_json(url: str) -> list | dict | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
    except OSError as exc:
        logger.warning("tcgcsv network error %s: %s", url, exc)
        return None


def _to_cents(val) -> int | None:
    try:
        v = float(val)
        return int(round(v * 100)) if v > 0 else None
    except (TypeError, ValueError):
        return None


class TcgcsvSource:
    """Emits MarketSnapshot objects for every product in the given categories."""

    name = "tcgcsv"

    def __init__(self, limiter: TokenBucket, categories: list[str] | None = None) -> None:
        self.limiter = limiter
        self.categories = categories or list(CATEGORY_IDS)

    def discover(self) -> Iterator[MarketSnapshot]:
        """Yield MarketSnapshot for every liquid product across all configured categories."""
        for game in self.categories:
            cat_id = CATEGORY_IDS.get(game)
            if cat_id is None:
                logger.warning("tcgcsv: unknown game %r, skipping", game)
                continue
            yield from self._scan_category(game, cat_id)

    def _scan_category(self, game: str, cat_id: int) -> Iterator[MarketSnapshot]:
        self.limiter.acquire()
        groups_raw = _fetch_json(f"{_BASE_URL}/{cat_id}/groups")
        if not groups_raw:
            return
        groups = groups_raw if isinstance(groups_raw, list) else groups_raw.get("results", [])

        for group in groups:
            group_id = group.get("groupId")
            set_name  = group.get("name", "")
            if not group_id:
                continue
            yield from self._scan_group(game, cat_id, group_id, set_name)

    def _scan_group(
        self, game: str, cat_id: int, group_id: int, set_name: str
    ) -> Iterator[MarketSnapshot]:
        # Fetch prices and products in parallel (two quick requests per set).
        self.limiter.acquire()
        prices_raw = _fetch_json(f"{_BASE_URL}/{cat_id}/{group_id}/prices")
        self.limiter.acquire()
        products_raw = _fetch_json(f"{_BASE_URL}/{cat_id}/{group_id}/products")

        if not prices_raw or not products_raw:
            return

        prices   = prices_raw   if isinstance(prices_raw,   list) else prices_raw.get("results",   [])
        products = products_raw if isinstance(products_raw, list) else products_raw.get("results", [])

        # Build product_id -> name lookup from products endpoint.
        name_map: dict[str, str] = {
            str(p["productId"]): p.get("name", "")
            for p in products
            if p.get("productId")
        }

        for price in prices:
            product_id = str(price.get("productId", ""))
            if not product_id:
                continue
            market_cents = _to_cents(price.get("marketPrice"))
            if market_cents is None or market_cents < _MIN_MARKET_CENTS:
                continue  # skip missing or sub-$1 noise
            yield MarketSnapshot(
                product_id   = product_id,
                source       = self.name,
                game         = game,
                name         = name_map.get(product_id, ""),
                set_name     = set_name,
                sub_type     = price.get("subTypeName") or "",
                market_cents = market_cents,
                low_cents    = _to_cents(price.get("lowPrice")),
            )
