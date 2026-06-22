"""Source workers: one per data vendor, all emitting PriceSnapshot objects."""

from __future__ import annotations

from ..ratelimit import limiter_for
from .base import Source
from .pricecharting import PricechartingSource
from .stub import StubSource
from .tcgapi import TcgapiSource

# Registry of available sources. Real vendors are added here as implemented.
# tcgapi (cards) is the recommended default; gocollect (comics) is next.
# pricecharting is kept as an opt-in option (its API needs a pricier tier).
_FACTORIES: dict[str, type[Source]] = {
    StubSource.name: StubSource,
    TcgapiSource.name: TcgapiSource,
    PricechartingSource.name: PricechartingSource,
}


def build_source(name: str) -> Source:
    """Instantiate a source by name with its rate-limit governor attached."""
    try:
        cls = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES)) or "(none)"
        raise ValueError(f"unknown source {name!r}; known sources: {known}")
    return cls(limiter=limiter_for(name))


def available_sources() -> list[str]:
    return sorted(_FACTORIES)
