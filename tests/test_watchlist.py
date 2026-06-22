"""Tests for watchlist parsing and the stub source (no database needed)."""

from __future__ import annotations

import pytest

from card_comic_monitor.models import FetchTarget
from card_comic_monitor.ratelimit import limiter_for
from card_comic_monitor.sources.stub import StubSource
from card_comic_monitor.watchlist import parse_watchlist

VALID = """
items:
  - title: Charizard
    kind: card
    game: pokemon
    sources:
      stub: demo-charizard
  - title: ASM
    kind: comic
    number: "300"
    is_key_issue: true
    sources:
      stub: demo-asm-300
"""


def test_parse_valid_watchlist():
    items = parse_watchlist(VALID)
    assert [i.title for i in items] == ["Charizard", "ASM"]
    assert items[0].game == "pokemon"
    assert items[0].sources == {"stub": "demo-charizard"}
    assert items[1].is_key_issue is True


@pytest.mark.parametrize(
    "bad",
    [
        "items:\n  - kind: card\n    sources: {stub: x}\n",  # missing title
        "items:\n  - title: X\n    sources: {stub: x}\n",  # missing kind
        "items:\n  - title: X\n    kind: toy\n    sources: {stub: x}\n",  # bad kind
        "items:\n  - title: X\n    kind: card\n",  # no sources
    ],
)
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_watchlist(bad)


def test_empty_watchlist_is_allowed():
    assert parse_watchlist("items: []") == []


def test_stub_source_is_deterministic_and_positive():
    targets = [FetchTarget(item_id=1, source_native_id="demo-charizard")]
    src = StubSource(limiter=limiter_for("stub"))
    a = list(src.fetch(targets))
    b = list(src.fetch(targets))
    assert len(a) == 1
    assert a[0].market_cents == b[0].market_cents  # deterministic within a day
    assert a[0].market_cents > 0
    assert a[0].low_cents < a[0].market_cents < a[0].high_cents
