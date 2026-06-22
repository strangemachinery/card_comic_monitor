"""Pokémon TCG bulk discovery source (api.pokemontcg.io).

A free, public API that returns TCGplayer market/low prices for the entire
Pokémon catalog (~19k cards). Paginates 250 cards per request, so a full
scan is ~75 requests — well within the free tier (1k/day anonymous, 20k/day
with a free API key set as POKEMONTCG_API_KEY).

Each card can carry several TCGplayer printings (holofoil, reverseHolofoil,
normal, ...). We emit one MarketSnapshot per priced printing, using the
printing name as sub_type so they coexist in market_snapshots.

Card shape (v2):
  {"data": [{"id": "base1-4", "name": "Charizard",
             "set": {"name": "Base"},
             "tcgplayer": {"prices": {
                 "holofoil": {"low": 192.99, "market": 220.5, ...}}}}],
   "page": 1, "pageSize": 250, "totalCount": 18900}
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Iterator

from ..models import MarketSnapshot
from ..ratelimit import TokenBucket

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.pokemontcg.io/v2/cards"
_PAGE_SIZE = 250
_MIN_MARKET_CENTS = 100  # skip cards under $1 (bulk commons / noise)
# Only fields we need — keeps each page small and fast.
_SELECT = "id,name,set,tcgplayer"


def _to_cents(val) -> int | None:
    try:
        v = float(val)
        return int(round(v * 100)) if v > 0 else None
    except (TypeError, ValueError):
        return None


class PokemontcgSource:
    """Emits MarketSnapshot objects for every priced Pokémon printing."""

    name = "pokemontcg"

    def __init__(self, limiter: TokenBucket, api_key: str | None = None) -> None:
        self.limiter = limiter
        self.api_key = api_key or os.getenv("POKEMONTCG_API_KEY", "")

    def _fetch_page(self, page: int) -> dict | None:
        url = (
            f"{_BASE_URL}?page={page}&pageSize={_PAGE_SIZE}"
            f"&select={_SELECT}"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "card-comic-monitor/1.0 (+github.com/strangemachinery/card_comic_monitor)"},
        )
        if self.api_key:
            req.add_header("X-Api-Key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                logger.warning("pokemontcg rate limited at page %s; stopping", page)
            else:
                logger.warning("pokemontcg HTTP %s at page %s", exc.code, page)
            return None
        except OSError as exc:
            logger.warning("pokemontcg network error at page %s: %s", page, exc)
            return None

    def discover(self) -> Iterator[MarketSnapshot]:
        """Yield a MarketSnapshot for every liquid printing across all pages."""
        page = 1
        while True:
            self.limiter.acquire()
            payload = self._fetch_page(page)
            if not payload:
                return
            cards = payload.get("data") or []
            if not cards:
                return
            for card in cards:
                yield from self._snapshots_for_card(card)
            # Stop once we've consumed the last page.
            total = payload.get("totalCount", 0)
            if page * _PAGE_SIZE >= total:
                return
            page += 1

    def _snapshots_for_card(self, card: dict) -> Iterator[MarketSnapshot]:
        product_id = str(card.get("id", ""))
        if not product_id:
            return
        tcg = card.get("tcgplayer") or {}
        prices = tcg.get("prices") or {}
        name = card.get("name", "")
        set_name = (card.get("set") or {}).get("name", "")
        for printing, fields in prices.items():
            if not isinstance(fields, dict):
                continue
            market_cents = _to_cents(fields.get("market"))
            if market_cents is None or market_cents < _MIN_MARKET_CENTS:
                continue
            yield MarketSnapshot(
                product_id   = product_id,
                source       = self.name,
                game         = "pokemon",
                name         = name,
                set_name     = set_name,
                sub_type     = printing,
                market_cents = market_cents,
                low_cents    = _to_cents(fields.get("low")),
            )
