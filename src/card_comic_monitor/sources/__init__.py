"""Source workers: one per data vendor, all emitting PriceSnapshot objects."""

from __future__ import annotations

from ..ratelimit import limiter_for
from .base import Source
from .stub import StubSource

# Registry of available sources. Real vendors (pricecharting, scrydex,
# gocollect, ...) get added here as they are implemented in Week 2-3.
_FACTORIES: dict[str, type[Source]] = {
    StubSource.name: StubSource,
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
