"""Core data shapes shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PriceSnapshot:
    """One price observation, normalized to the common shape that every
    source worker emits and the repository upserts into `price_snapshots`."""

    item_id: int
    source: str
    market_cents: int | None
    low_cents: int | None = None
    mid_cents: int | None = None
    high_cents: int | None = None
    volume: int | None = None
    condition: str = "raw"
    grade: str = ""
    # When None, the repository stamps the current UTC day at insert time.
    time: datetime | None = None


@dataclass
class FetchTarget:
    """Tells a source which canonical item maps to which of its native ids."""

    item_id: int
    source_native_id: str


@dataclass
class WatchlistItem:
    """A logical collectible to track, with its per-source native ids."""

    title: str
    kind: str  # 'card' | 'comic'
    sources: dict[str, str]  # source -> source_native_id
    game: str | None = None
    set_name: str | None = None
    number: str | None = None
    variant: str | None = None
    is_key_issue: bool = False
    metadata: dict = field(default_factory=dict)
