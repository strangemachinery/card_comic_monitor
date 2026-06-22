"""Tests for the tcgapi.dev source worker (no network, no API key needed).

These focus on the parsing/extraction helpers and error handling -- the logic
that matters regardless of the exact wire field names. The helpers tolerate
several key spellings, so the happy-path tests use a representative payload.
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
    _chunks,
    _extract_records,
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


def test_extract_records_from_list_and_envelopes():
    assert _extract_records([{"a": 1}]) == [{"a": 1}]
    assert _extract_records({"data": [{"a": 1}]}) == [{"a": 1}]
    assert _extract_records({"results": [{"b": 2}]}) == [{"b": 2}]
    assert _extract_records({"nope": 1}) == []
    assert _extract_records("garbage") == []


def test_chunks():
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert list(_chunks([], 2)) == []


def test_snapshot_from_record_tolerates_key_variants():
    rec = {"productId": 6910, "market_price": 32.50, "low_price": 28.0,
           "total_listings": 42}
    snap = _snapshot_from_record(rec, item_id=1)
    assert snap.market_cents == 3250
    assert snap.low_cents == 2800
    assert snap.volume == 42
    assert snap.condition == "raw" and snap.grade == ""


def test_snapshot_from_record_none_without_market_price():
    assert _snapshot_from_record({"low_price": 10.0}, item_id=1) is None


# --- fetch() ---------------------------------------------------------------

@patch("urllib.request.urlopen")
def test_fetch_happy_path_maps_pid_to_item(mock_urlopen):
    mock_urlopen.return_value = _make_response({"data": [
        {"tcgplayer_id": "111", "market_price": 10.00, "low_price": 8.0,
         "total_listings": 5},
        {"tcgplayer_id": "222", "market_price": 25.50},
    ]})

    src = _src()
    targets = [
        FetchTarget(item_id=1, source_native_id="111"),
        FetchTarget(item_id=2, source_native_id="222"),
    ]
    snaps = {s.item_id: s for s in src.fetch(targets)}
    assert snaps[1].market_cents == 1000
    assert snaps[1].volume == 5
    assert snaps[2].market_cents == 2550
    assert snaps[2].low_cents is None  # absent in payload


@patch("urllib.request.urlopen")
def test_fetch_batches_into_one_call(mock_urlopen):
    # 3 targets should still be a single bulk request (well under batch size).
    mock_urlopen.return_value = _make_response({"data": []})
    src = _src()
    targets = [FetchTarget(item_id=i, source_native_id=str(i)) for i in range(3)]
    list(src.fetch(targets))
    assert mock_urlopen.call_count == 1


@patch("urllib.request.urlopen")
def test_fetch_skips_unknown_returned_ids(mock_urlopen):
    mock_urlopen.return_value = _make_response({"data": [
        {"tcgplayer_id": "999", "market_price": 5.0},  # not in our targets
    ]})
    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=1, source_native_id="111")]))
    assert snaps == []


@patch("urllib.request.urlopen")
def test_fetch_empty_targets_no_call(mock_urlopen):
    src = _src()
    assert list(src.fetch([])) == []
    mock_urlopen.assert_not_called()


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
    assert snaps == []  # 429 -> clean stop, no exception


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
