"""Database reads/writes: identity resolution and idempotent snapshot upserts."""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from .models import FetchTarget, PriceSnapshot, WatchlistItem


def utc_day(when: datetime | None = None) -> datetime:
    """Truncate a timestamp to the start of its UTC day."""
    when = when or datetime.now(timezone.utc)
    when = when.astimezone(timezone.utc)
    return when.replace(hour=0, minute=0, second=0, microsecond=0)


def _resolve_existing(
    conn: psycopg.Connection, item: WatchlistItem
) -> int | None:
    """Return the canonical item_id if any of the item's source bindings are
    already in the crosswalk, else None."""
    for source, native_id in item.sources.items():
        row = conn.execute(
            "SELECT item_id FROM item_source_ids "
            "WHERE source = %s AND source_native_id = %s",
            (source, native_id),
        ).fetchone()
        if row:
            return row[0]
    return None


def ensure_item(conn: psycopg.Connection, item: WatchlistItem) -> int:
    """Insert the item and its source bindings if new; return its item_id.

    Identity is anchored on the crosswalk: if any source/native_id is already
    known, that canonical item_id is reused.
    """
    existing = _resolve_existing(conn, item)
    if existing is not None:
        # Register any source bindings we don't have yet (e.g. a new vendor id).
        for source, native_id in item.sources.items():
            conn.execute(
                "INSERT INTO item_source_ids (item_id, source, source_native_id) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (existing, source, native_id),
            )
        return existing

    row = conn.execute(
        "INSERT INTO items (kind, game, title, set_name, number, variant, "
        "is_key_issue, metadata) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING item_id",
        (
            item.kind,
            item.game,
            item.title,
            item.set_name,
            item.number,
            item.variant,
            item.is_key_issue,
            Jsonb(item.metadata),
        ),
    ).fetchone()
    item_id = row[0]
    for source, native_id in item.sources.items():
        conn.execute(
            "INSERT INTO item_source_ids (item_id, source, source_native_id) "
            "VALUES (%s, %s, %s)",
            (item_id, source, native_id),
        )
    return item_id


def targets_for_source(
    conn: psycopg.Connection, source: str
) -> list[FetchTarget]:
    """All (item_id, native_id) pairs the given source can price."""
    rows = conn.execute(
        "SELECT item_id, source_native_id FROM item_source_ids WHERE source = %s "
        "ORDER BY item_id",
        (source,),
    ).fetchall()
    return [FetchTarget(item_id=r[0], source_native_id=r[1]) for r in rows]


UPSERT_SNAPSHOT = """
INSERT INTO price_snapshots
    (time, item_id, source, condition, grade,
     market_cents, low_cents, mid_cents, high_cents, volume)
VALUES
    (%(time)s, %(item_id)s, %(source)s, %(condition)s, %(grade)s,
     %(market_cents)s, %(low_cents)s, %(mid_cents)s, %(high_cents)s, %(volume)s)
ON CONFLICT (time, item_id, source, condition, grade) DO UPDATE SET
    market_cents = EXCLUDED.market_cents,
    low_cents    = EXCLUDED.low_cents,
    mid_cents    = EXCLUDED.mid_cents,
    high_cents   = EXCLUDED.high_cents,
    volume       = EXCLUDED.volume
"""


def upsert_snapshots(
    conn: psycopg.Connection,
    snapshots: list[PriceSnapshot],
    *,
    day: datetime | None = None,
) -> int:
    """Idempotently upsert a batch of snapshots; returns the row count.

    Snapshots without an explicit `time` are stamped with the current UTC day,
    so re-running on the same day overwrites rather than duplicates.
    """
    day = day or utc_day()
    params = [
        {
            "time": s.time or day,
            "item_id": s.item_id,
            "source": s.source,
            "condition": s.condition,
            "grade": s.grade,
            "market_cents": s.market_cents,
            "low_cents": s.low_cents,
            "mid_cents": s.mid_cents,
            "high_cents": s.high_cents,
            "volume": s.volume,
        }
        for s in snapshots
    ]
    if not params:
        return 0
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SNAPSHOT, params)
    return len(params)
