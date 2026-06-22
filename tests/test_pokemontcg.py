"""Tests for the pokemontcg.io bulk discovery source (no network required)."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from card_comic_monitor.ratelimit import limiter_for
from card_comic_monitor.sources.pokemontcg import PokemontcgSource, _to_cents


def _make_response(payload) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="", code=code, msg="", hdrs={}, fp=None)


def _card(card_id, name, set_name, prices, *, printing="holofoil") -> dict:
    return {
        "id": card_id,
        "name": name,
        "set": {"name": set_name},
        "tcgplayer": {"prices": prices},
    }


def _page(cards, total) -> dict:
    return {"data": cards, "page": 1, "pageSize": 250, "totalCount": total}


def _src(api_key="") -> PokemontcgSource:
    return PokemontcgSource(limiter=limiter_for("stub"), api_key=api_key)


# --- helpers ------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (220.50, 22050),
    ("8.0", 800),
    (0,      None),
    (-1.0,   None),
    (None,   None),
])
def test_to_cents(value, expected):
    assert _to_cents(value) == expected


# --- discover() ---------------------------------------------------------------

@patch("urllib.request.urlopen")
def test_discover_emits_one_snapshot_per_printing(mock_urlopen):
    card = _card("base1-4", "Charizard", "Base", {
        "holofoil":        {"low": 192.99, "market": 220.50},
        "reverseHolofoil": {"low": 50.0,   "market": 60.0},
    })
    mock_urlopen.return_value = _make_response(_page([card], total=1))
    snaps = {s.sub_type: s for s in _src().discover()}
    assert set(snaps) == {"holofoil", "reverseHolofoil"}
    assert snaps["holofoil"].market_cents == 22050
    assert snaps["holofoil"].low_cents == 19299
    assert snaps["holofoil"].name == "Charizard"
    assert snaps["holofoil"].set_name == "Base"
    assert snaps["holofoil"].game == "pokemon"
    assert snaps["holofoil"].source == "pokemontcg"
    assert snaps["holofoil"].product_id == "base1-4"


@patch("urllib.request.urlopen")
def test_discover_filters_sub_dollar_and_missing_market(mock_urlopen):
    card = _card("x-1", "Pidgey", "Base", {
        "normal":   {"low": 0.10, "market": 0.25},   # under $1 → skip
        "holofoil": {"low": 1.0},                      # no market → skip
    })
    mock_urlopen.return_value = _make_response(_page([card], total=1))
    assert list(_src().discover()) == []


@patch("urllib.request.urlopen")
def test_discover_paginates_until_total_reached(mock_urlopen):
    p1 = _page([_card(f"a-{i}", "C", "S", {"normal": {"market": 5.0}})
                for i in range(250)], total=300)
    p2 = _page([_card(f"b-{i}", "C", "S", {"normal": {"market": 5.0}})
                for i in range(50)], total=300)
    mock_urlopen.side_effect = [_make_response(p1), _make_response(p2)]
    snaps = list(_src().discover())
    assert len(snaps) == 300
    assert mock_urlopen.call_count == 2


@patch("urllib.request.urlopen")
def test_discover_stops_on_single_full_page(mock_urlopen):
    page = _page([_card("a-1", "C", "S", {"normal": {"market": 5.0}})], total=1)
    mock_urlopen.return_value = _make_response(page)
    list(_src().discover())
    assert mock_urlopen.call_count == 1


@patch("urllib.request.urlopen")
def test_discover_stops_on_rate_limit(mock_urlopen):
    mock_urlopen.side_effect = _http_error(429)
    assert list(_src().discover()) == []


@patch("urllib.request.urlopen")
def test_discover_stops_on_network_error(mock_urlopen):
    # First call fails with a network error (skip), second page also fails → loop exits.
    mock_urlopen.side_effect = OSError("connection reset")
    assert list(_src().discover()) == []


@patch("urllib.request.urlopen")
def test_discover_skips_500_page_and_continues(mock_urlopen):
    """A 500 on one page should skip it and continue scanning."""
    good_card = _card("a-1", "Charizard", "Base", {"holofoil": {"market": 10.0}})
    good_page = _page([good_card], total=2)
    mock_urlopen.side_effect = [
        _make_response(good_page),  # page 1: OK
        _http_error(500),           # page 2: server error → skip
        # discover exits because page 2 skip + total=2 guard triggers
    ]
    # We get the cards from page 1 and gracefully skip page 2.
    snaps = list(_src().discover())
    assert len(snaps) == 1
    assert snaps[0].name == "Charizard"


@patch("urllib.request.urlopen")
def test_discover_handles_card_without_tcgplayer(mock_urlopen):
    card = {"id": "x-1", "name": "Promo", "set": {"name": "SWSH"}}
    mock_urlopen.return_value = _make_response(_page([card], total=1))
    assert list(_src().discover()) == []


@patch("urllib.request.urlopen")
def test_discover_sends_user_agent(mock_urlopen):
    mock_urlopen.return_value = _make_response(_page([], total=0))
    list(_src().discover())
    req = mock_urlopen.call_args[0][0]
    ua = req.get_header("User-agent")
    assert ua and "card-comic-monitor" in ua


@patch("urllib.request.urlopen")
def test_discover_sends_api_key_header_when_set(mock_urlopen):
    mock_urlopen.return_value = _make_response(_page([], total=0))
    list(_src(api_key="secret").discover())
    req = mock_urlopen.call_args[0][0]
    assert req.get_header("X-api-key") == "secret"


@patch("urllib.request.urlopen")
def test_discover_no_api_key_header_when_unset(mock_urlopen):
    mock_urlopen.return_value = _make_response(_page([], total=0))
    list(_src().discover())
    req = mock_urlopen.call_args[0][0]
    assert req.get_header("X-api-key") is None


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("POKEMONTCG_API_KEY", "env-key")
    assert PokemontcgSource(limiter=limiter_for("stub")).api_key == "env-key"
