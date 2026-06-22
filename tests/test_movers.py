"""Tests for movers.py (period parsing, SQL shape, error handling).

DB-dependent SQL is not tested here; integration coverage comes from
running `ccm movers` after a few days of real snapshots.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from card_comic_monitor.movers import PERIODS, MoverRow, query_movers


def test_periods_all_present():
    expected = {"1w", "1m", "2m", "3m", "4m", "5m", "6m", "1y"}
    assert set(PERIODS) == expected


def test_periods_are_timedeltas():
    for key, val in PERIODS.items():
        assert isinstance(val, timedelta), key


def test_periods_ordered_by_duration():
    durations = list(PERIODS.values())
    assert durations == sorted(durations), "PERIODS should increase in duration"


def test_invalid_period_raises():
    conn = MagicMock()
    with pytest.raises(ValueError, match="unknown period"):
        query_movers(conn, "99y")


def test_mover_row_fields():
    row = MoverRow(title="Charizard", source="tcgapi",
                   start_cents=58000, end_cents=63039, pct_change=8.7)
    assert row.pct_change == 8.7
    assert row.end_cents - row.start_cents == 5039


def test_query_movers_returns_empty_on_no_rows():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    result = query_movers(conn, "1w")
    assert result == []


def test_query_movers_maps_rows_to_dataclass():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        ("Charizard", "tcgapi", 58000, 63039, 8.7),
        ("Son Goku",  "tcgapi", 25,    25,    0.0),
    ]
    rows = query_movers(conn, "1m")
    assert len(rows) == 2
    assert rows[0].title == "Charizard"
    assert rows[0].pct_change == 8.7
    assert rows[1].end_cents == 25


def test_query_movers_excludes_stub_by_default():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    query_movers(conn, "1w")
    call_params = conn.execute.call_args[0][1]
    assert "stub" in call_params["exclude"]


def test_query_movers_all_sources_passes_empty_exclude():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    query_movers(conn, "1w", exclude_sources=())
    call_params = conn.execute.call_args[0][1]
    assert call_params["exclude"] == []
