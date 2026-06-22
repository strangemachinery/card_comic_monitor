"""Price-momentum queries: percent change over a configurable look-back window.

Uses two DISTINCT ON sub-queries to find the most-recent snapshot and the
most-recent snapshot *before* the look-back cutoff, then computes % change.
Rows where either end is missing (item added mid-period) are silently excluded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import psycopg

# Supported period aliases -> look-back timedelta
PERIODS: dict[str, timedelta] = {
    "1w":  timedelta(weeks=1),
    "1m":  timedelta(days=30),
    "2m":  timedelta(days=60),
    "3m":  timedelta(days=90),
    "4m":  timedelta(days=120),
    "5m":  timedelta(days=150),
    "6m":  timedelta(days=180),
    "1y":  timedelta(days=365),
}

_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (item_id, source)
           item_id, source, market_cents AS end_cents
    FROM   price_snapshots
    WHERE  market_cents IS NOT NULL
    ORDER  BY item_id, source, time DESC
),
anchor AS (
    SELECT DISTINCT ON (item_id, source)
           item_id, source, market_cents AS start_cents
    FROM   price_snapshots
    WHERE  time        <= now() - %(lookback)s
      AND  market_cents IS NOT NULL
    ORDER  BY item_id, source, time DESC
)
SELECT i.title,
       l.source,
       a.start_cents,
       l.end_cents,
       round(
           (l.end_cents - a.start_cents)::numeric / a.start_cents * 100,
       1) AS pct_change
FROM   latest  l
JOIN   anchor  a USING (item_id, source)
JOIN   items   i USING (item_id)
WHERE  l.source != ALL(%(exclude)s)
ORDER  BY pct_change DESC
"""


@dataclass
class MoverRow:
    title: str
    source: str
    start_cents: int
    end_cents: int
    pct_change: float


def query_movers(
    conn: psycopg.Connection,
    period: str,
    *,
    exclude_sources: tuple[str, ...] = ("stub",),
) -> list[MoverRow]:
    """Return items sorted by % price change over `period`.

    Args:
        period: one of the PERIODS keys (e.g. "1w", "1m", "1y").
        exclude_sources: sources to omit (stub by default — its prices are
            deterministic noise, not real market signal).

    Returns an empty list when there is not yet enough history for the period.
    """
    if period not in PERIODS:
        known = ", ".join(PERIODS)
        raise ValueError(f"unknown period {period!r}; choose from: {known}")
    rows = conn.execute(
        _SQL,
        {"lookback": PERIODS[period], "exclude": list(exclude_sources)},
    ).fetchall()
    return [MoverRow(*r) for r in rows]
