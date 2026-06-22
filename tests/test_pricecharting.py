"""Tests for the PriceCharting source worker (no network, no API key needed)."""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from card_comic_monitor.models import FetchTarget
from card_comic_monitor.ratelimit import limiter_for
from card_comic_monitor.sources.pricecharting import (
    PricechartingSource,
    _parse_cents,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(data: dict) -> MagicMock:
    """Fake urllib context-manager response returning JSON-encoded data."""
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="", code=code, msg="", hdrs={}, fp=None)


def _src(token: str = "test-token-40chars-padded-000000000000") -> PricechartingSource:
    return PricechartingSource(limiter=limiter_for("stub"), token=token)


# ---------------------------------------------------------------------------
# _parse_cents
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0,     None),   # no market data
    (-1,    None),   # sentinel / error value
    (None,  None),   # missing field
    ("",    None),   # bad string
    (1,     1),
    (3200,  3200),
    ("500", 500),    # API might return numeric strings
])
def test_parse_cents(value, expected):
    assert _parse_cents(value) == expected


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@patch("urllib.request.urlopen")
def test_fetch_yields_one_snapshot_per_nonzero_condition(mock_urlopen):
    mock_urlopen.return_value = _make_response({
        "id": "6910",
        "name": "Charizard - Base Set",
        "loose-price":    3200,
        "cib-price":      0,     # no data → skipped
        "new-price":      0,     # no data → skipped
        "graded-price":   15000,
        "grade-8-price":  8000,
        "grade-9-price":  12000,
        "grade-10-price": 45000,
    })

    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=1, source_native_id="6910")]))

    conditions = [(s.condition, s.grade, s.market_cents) for s in snaps]
    assert ("loose",  "",   3200)  in conditions
    assert ("graded", "",   15000) in conditions
    assert ("graded", "8",  8000)  in conditions
    assert ("graded", "9",  12000) in conditions
    assert ("graded", "10", 45000) in conditions
    assert len(snaps) == 5  # cib and new were zero


@patch("urllib.request.urlopen")
def test_fetch_all_nonzero_conditions(mock_urlopen):
    mock_urlopen.return_value = _make_response({
        "loose-price":    100,
        "cib-price":      200,
        "new-price":      300,
        "graded-price":   400,
        "grade-8-price":  500,
        "grade-9-price":  600,
        "grade-10-price": 700,
    })

    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=7, source_native_id="999")]))
    assert len(snaps) == 7
    for s in snaps:
        assert s.source == "pricecharting"
        assert s.item_id == 7


@patch("urllib.request.urlopen")
def test_fetch_multiple_targets(mock_urlopen):
    mock_urlopen.return_value = _make_response({"loose-price": 1000})

    src = _src()
    targets = [
        FetchTarget(item_id=1, source_native_id="111"),
        FetchTarget(item_id=2, source_native_id="222"),
    ]
    snaps = list(src.fetch(targets))
    assert {s.item_id for s in snaps} == {1, 2}
    assert mock_urlopen.call_count == 2


# ---------------------------------------------------------------------------
# Not-found / status=fail
# ---------------------------------------------------------------------------

@patch("urllib.request.urlopen")
def test_fetch_skips_status_fail(mock_urlopen):
    mock_urlopen.return_value = _make_response({"status": "fail"})

    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=1, source_native_id="0")]))
    assert snaps == []


@patch("urllib.request.urlopen")
def test_fetch_skips_http_404(mock_urlopen):
    mock_urlopen.side_effect = _http_error(404)

    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=1, source_native_id="0")]))
    assert snaps == []


# ---------------------------------------------------------------------------
# Auth errors raise immediately
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", [401, 403])
@patch("urllib.request.urlopen")
def test_fetch_raises_on_auth_error(mock_urlopen, code):
    mock_urlopen.side_effect = _http_error(code)

    src = _src()
    with pytest.raises(RuntimeError, match="auth failed"):
        list(src.fetch([FetchTarget(item_id=1, source_native_id="6910")]))


# ---------------------------------------------------------------------------
# Network errors are logged and skipped (run continues)
# ---------------------------------------------------------------------------

@patch("urllib.request.urlopen")
def test_fetch_skips_network_error(mock_urlopen):
    mock_urlopen.side_effect = OSError("connection refused")

    src = _src()
    snaps = list(src.fetch([FetchTarget(item_id=1, source_native_id="0")]))
    assert snaps == []


@patch("urllib.request.urlopen")
def test_fetch_continues_after_error(mock_urlopen):
    """A single network error should not abort remaining targets."""
    mock_urlopen.side_effect = [
        OSError("timeout"),
        _make_response({"loose-price": 500}),
    ]

    src = _src()
    targets = [
        FetchTarget(item_id=1, source_native_id="bad"),
        FetchTarget(item_id=2, source_native_id="ok"),
    ]
    snaps = list(src.fetch(targets))
    assert len(snaps) == 1
    assert snaps[0].item_id == 2


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

def test_no_token_raises(monkeypatch):
    monkeypatch.delenv("PRICECHARTING_TOKEN", raising=False)
    with pytest.raises(ValueError, match="PRICECHARTING_TOKEN"):
        PricechartingSource(limiter=limiter_for("stub"))


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("PRICECHARTING_TOKEN", "env-token-value")
    src = PricechartingSource(limiter=limiter_for("stub"))
    assert src.token == "env-token-value"
