"""Load the curated watchlist of items to snapshot."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import WatchlistItem


def parse_watchlist(text: str) -> list[WatchlistItem]:
    """Parse watchlist YAML into WatchlistItem objects."""
    data = yaml.safe_load(text) or {}
    raw_items = data.get("items", [])
    items: list[WatchlistItem] = []
    for i, raw in enumerate(raw_items):
        title = raw.get("title")
        kind = raw.get("kind")
        sources = raw.get("sources") or {}
        if not title or not kind:
            raise ValueError(f"watchlist item #{i} missing required 'title'/'kind'")
        if kind not in ("card", "comic"):
            raise ValueError(f"watchlist item {title!r} has invalid kind {kind!r}")
        if not sources:
            raise ValueError(f"watchlist item {title!r} has no 'sources' bindings")
        items.append(
            WatchlistItem(
                title=title,
                kind=kind,
                sources={str(k): str(v) for k, v in sources.items()},
                game=raw.get("game"),
                set_name=raw.get("set_name"),
                number=raw.get("number"),
                variant=raw.get("variant"),
                is_key_issue=bool(raw.get("is_key_issue", False)),
                metadata=raw.get("metadata") or {},
            )
        )
    return items


def load_watchlist(path: Path) -> list[WatchlistItem]:
    return parse_watchlist(Path(path).read_text(encoding="utf-8"))
