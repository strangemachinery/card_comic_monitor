"""Tests for the tcgcsv bulk discovery source (no network required)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from card_comic_monitor.ratelimit import limiter_for
from card_comic_monitor.sources.tcgcsv import (
    CATEGORY_IDS,
    TcgcsvSource,
    _to_cents,
)


def _make_response(payload) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _src(categories=None) -> TcgcsvSource:
    return TcgcsvSource(limiter=limiter_for("stub"), categories=categories)


# --- helpers ------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (10.00, 1000),
    ("5.99", 599),
    (0,     None),
    (-1.0,  None),
    (None,  None),
])
def test_to_cents(value, expected):
    assert _to_cents(value) == expected


def test_category_ids_have_known_games():
    assert "pokemon" in CATEGORY_IDS
    assert "mtg" in CATEGORY_IDS


# --- discover() ---------------------------------------------------------------

GROUPS_RESP  = [{"groupId": 1001, "name": "Base Set"}]
PRICES_RESP  = [
    {"productId": 42, "subTypeName": "Holofoil", "marketPrice": 630.39, "lowPrice": 192.99},
    {"productId": 43, "subTypeName": "Normal",   "marketPrice": 0.50},   # below $1 → filtered
    {"productId": 44, "subTypeName": "",          "marketPrice": 5.00},
]
PRODUCTS_RESP = [
    {"productId": 42, "name": "Charizard"},
    {"productId": 43, "name": "Bulbasaur"},
    {"productId": 44, "name": "Pikachu"},
]


@patch("urllib.request.urlopen")
def test_discover_yields_liquid_products(mock_urlopen):
    mock_urlopen.side_effect = [
        _make_response(GROUPS_RESP),
        _make_response(PRICES_RESP),
        _make_response(PRODUCTS_RESP),
    ]
    snaps = list(_src(["pokemon"]).discover())
    # product 43 has marketPrice 0.50 → under $1 → excluded
    assert len(snaps) == 2
    ids = {s.product_id for s in snaps}
    assert ids == {"42", "44"}


@patch("urllib.request.urlopen")
def test_discover_maps_name_from_products(mock_urlopen):
    mock_urlopen.side_effect = [
        _make_response(GROUPS_RESP),
        _make_response(PRICES_RESP),
        _make_response(PRODUCTS_RESP),
    ]
    snaps = {s.product_id: s for s in _src(["pokemon"]).discover()}
    assert snaps["42"].name == "Charizard"
    assert snaps["42"].market_cents == 63039
    assert snaps["42"].low_cents == 19299
    assert snaps["42"].sub_type == "Holofoil"
    assert snaps["42"].set_name == "Base Set"
    assert snaps["42"].game == "pokemon"
    assert snaps["42"].source == "tcgcsv"


@patch("urllib.request.urlopen")
def test_discover_handles_results_wrapper(mock_urlopen):
    """TCGCSV sometimes wraps lists in {"results": [...]}."""
    mock_urlopen.side_effect = [
        _make_response({"results": GROUPS_RESP}),
        _make_response({"results": PRICES_RESP}),
        _make_response({"results": PRODUCTS_RESP}),
    ]
    snaps = list(_src(["pokemon"]).discover())
    assert len(snaps) == 2


@patch("urllib.request.urlopen")
def test_discover_skips_group_on_network_error(mock_urlopen):
    mock_urlopen.side_effect = [
        _make_response(GROUPS_RESP),
        OSError("timeout"),   # prices fetch fails
        _make_response(PRODUCTS_RESP),
    ]
    snaps = list(_src(["pokemon"]).discover())
    assert snaps == []


@patch("urllib.request.urlopen")
def test_discover_unknown_game_skips(mock_urlopen):
    snaps = list(_src(["no_such_game"]).discover())
    assert snaps == []
    mock_urlopen.assert_not_called()


@patch("urllib.request.urlopen")
def test_discover_empty_groups(mock_urlopen):
    mock_urlopen.return_value = _make_response([])
    snaps = list(_src(["pokemon"]).discover())
    assert snaps == []
