"""Tests for the tcgapi.dev source worker (no network, no API key needed).

Payloads mirror the confirmed live API shape from GET /v1/cards/tcgplayer/{id}:
  {
    "data": {
      "tcgplayer_id": 42382,
      "total_listings": 110,
      "prices": [{"printing": "Holofoil", "market_price": 630.39, "low_price": 192.99}]
    },
    "rate_limit": {"daily_limit": 100, "daily_remaining": 99, ...}
  }
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from card_comic_monitor.models import FetchTarget
from card_comic_monitor.ratelimit import limiter_for
from card_comic_monitor.sources.tcgapi import (
    TcgapiSource,
    _snapshot_from_record,
    _to_cents,
    _to_int,
)


def _make_response(payload) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _card_payload(tcgplayer_id, market_price, low_price=None, total_listings=None,
                  printing="Holofoil") -> dict:
    """Build a correctly-shaped GET /v1/cards/tcgplayer/{id} response."""
    price = {"printing": printing, "market_price": market_price}
    if low_price is not None:
        price["low_price"] = low_price
    data = {"tcgplayer_id": tcgplayer_id, "prices": [price]}
    if total_listings is not None:
        data["total_listings"] = total_listings
    return {"data": data, "rate_limit": {"daily_limit": 100, "daily_remaining": 97}}


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="", code=code, msg="", hdrs={}, fp=None)


def _src(api_key: str = "test-key") -> TcgapiSource:
    return TcgapiSource(limiter=limiter_for("stub"), api_key=api_key)


# --- unit helpers ----------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (32.50, 3250),
    ("12.34", 1234),
    (0, None),
    (-1.0, None),
    (None, None),
    ("", None),
    ("abc", None),
])
def test_to_cents(value, expected):
    assert _to_cents(value) == expected


@pytest.mark.parametrize("value,expected", [
    (5, 5), ("7", 7), (None, None), ("x", None),
])
def test_to_int(value, expected):
    assert _to_int(value) == expected


def test_snapshot_from_record_reads_nested_prices():
    record = {"tcgplayer_id": 42382, "total_listings": 110,
              "prices": [{"printing": "Holofoil", "market_price": 630.39, "low_price": 192.99}]}
    snap = _snapshot_from_record(record, item_id=1)
    assert snap.market_cents == 63039
    assert snap.low_cents == 19299
    assert snap.volume == 110
    assert snap.condition == "raw" and snap.grade == ""


def test_snapshot_from_record_prefers_normal_printing():
    record = {
        "tcgplayer_id": 1,
        "prices": [
            {"printing": "Holofoil", "market_price": 50.0},
            {"printing": "Normal", "market_price": 10.0, "low_price": 8.0},
        ],
    }
    snap = _snapshot_from_record(record, item_id=1)
    assert snap.market_cents == 1000
    assert snap.low_cents == 800


def test_snapshot_from_record_falls_back_to_first_printing():
    record = {"tcgplayer_id": 1,
              "prices": [{"printing": "Holofoil", "market_price": 50.0}]}
    snap = _snapshot_from_record(record, item_id=1)
    assert snap.market_cents == 5000


def test_snapshot_from_record_flat_fallback():
    # Non-nested path: price fields directly on record
    snap = _snapshot_from_record({"market_price": 32.50, "low_price": 28.0}, item_id=1)
    assert snap.market_cents == 3250
    assert snap.low_cents == 2800


def test_snapshot_from_record_none_without_market_price():
    assert _snapshot_from_record({"prices": [{"printing": "Normal"}]}, item_id=1) is None
    assert _snapshot_from_record({}, item_id=1) is None


# --- fetch() ---------------------------------------------------------------

@patch("urllib.request.urlopen")
def test_fetch_happy_path(mock_urlopen):
    mock_urlopen.side_effect = [
        _make_response(_card_payload(111, 10.00, 8.0, total_listings=5)),
        _make_response(_card_payload(222, 25.50)),
    ]
    src = _src()
    targets = [
        FetchTarget(item_id=1, source_native_id="111"),
        FetchTarget(item_id=2, source_native_id="222"),
    ]
    snaps = {s.item_id: s for s in src.fetch(targets)}
    assert snaps[1].market_cents == 1000
    assert snaps[1].low_cents == 800
    assert snaps[1].volume == 5
    assert snaps[2].market_cents == 2550
    assert snaps[2].low_cents is None


@patch("urllib.request.urlopen")
def test_fetch_makes_one_request_per_card(mock_urlopen):
    mock_urlopen.return_value = _make_response({"data": None})
    src = _src()
    targets = [FetchTarget(item_id=i, source_native_id=str(i)) for i in range(3)]
    list(src.fetch(targets))
    assert mock_urlopen.call_count == 3


@patch("urllib.request.urlopen")
def test_fetch_uses_get_with_id_in_url(mock_urlopen):
    mock_urlopen.return_value = _make_response(_card_payload(42382, 630.39))
    src = _src()
    list(src.fetch([FetchTarget(item_id=1, source_native_id="42382")]))
    req = mock_urlopen.call_args[0][0]
    assert "42382" in req.full_url
    assert req.data is None  # GET has no body


@patch("urllib.request.urlopen")
def test_fetch_empty_targets_no_call(mock_urlopen):
    src = _src()
    assert list(src.fetch([])) == []
    mock_urlopen.assert_not_called()


@patch("urllib.request.urlopen")
def test_fetch_skips_null_data(mock_urlopen):
    mock_urlopen.return_value = _make_response({"data": None})
    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=1, source_native_id="111")]))
    assert snaps == []


@pytest.mark.parametrize("code", [401, 403])
@patch("urllib.request.urlopen")
def test_fetch_raises_on_auth_error(mock_urlopen, code):
    mock_urlopen.side_effect = _http_error(code)
    src = _src()
    with pytest.raises(RuntimeError, match="auth failed"):
        list(src.fetch([FetchTarget(item_id=1, source_native_id="111")]))


@patch("urllib.request.urlopen")
def test_fetch_stops_on_rate_limit(mock_urlopen):
    mock_urlopen.side_effect = _http_error(429)
    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=1, source_native_id="111")]))
    assert snaps == []


@patch("urllib.request.urlopen")
def test_fetch_skips_network_error(mock_urlopen):
    mock_urlopen.side_effect = OSError("connection reset")
    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=1, source_native_id="111")]))
    assert snaps == []


def test_no_api_key_raises(monkeypatch):
    monkeypatch.delenv("TCGAPI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TCGAPI_API_KEY"):
        TcgapiSource(limiter=limiter_for("stub"))


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("TCGAPI_API_KEY", "env-key")
    assert TcgapiSource(limiter=limiter_for("stub")).api_key == "env-key"
