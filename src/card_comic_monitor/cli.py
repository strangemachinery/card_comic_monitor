"""Command-line entrypoint: `ccm <command>`.

Commands:
  migrate          apply database migrations
  sync-watchlist   ensure watchlist items exist in the DB (create + crosswalk)
  snapshot         fetch current prices from enabled sources and upsert them
  show             print the most recent snapshot per item (sanity check)
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_settings
from .db import connect
from .migrate import run_migrations
from .repository import ensure_item, targets_for_source, upsert_snapshots
from .sources import available_sources, build_source
from .watchlist import load_watchlist

logger = logging.getLogger("card_comic_monitor")


def cmd_migrate(args, settings) -> int:
    with connect(settings) as conn:
        applied = run_migrations(conn)
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("Database already up to date.")
    return 0


def cmd_sync_watchlist(args, settings) -> int:
    items = load_watchlist(settings.watchlist_path)
    with connect(settings) as conn:
        for item in items:
            ensure_item(conn, item)
    print(f"Synced {len(items)} watchlist item(s) from {settings.watchlist_path}.")
    return 0


def cmd_snapshot(args, settings) -> int:
    sources = args.sources or list(settings.enabled_sources)
    if not sources:
        print("No sources enabled (set ENABLED_SOURCES or pass --source).")
        return 1
    total = 0
    with connect(settings) as conn:
        for name in sources:
            source = build_source(name)
            targets = targets_for_source(conn, name)
            if not targets:
                logger.warning("source %s has no targets in the watchlist", name)
                continue
            snapshots = list(source.fetch(targets))
            written = upsert_snapshots(conn, snapshots)
            total += written
            print(f"  {name}: {written} snapshot(s) from {len(targets)} target(s)")
    print(f"Wrote {total} snapshot(s).")
    return 0


def cmd_show(args, settings) -> int:
    query = """
        SELECT DISTINCT ON (i.item_id, ps.source)
               i.title, ps.source, ps.grade, ps.market_cents, ps.time
        FROM price_snapshots ps
        JOIN items i ON i.item_id = ps.item_id
        ORDER BY i.item_id, ps.source, ps.time DESC
    """
    with connect(settings) as conn:
        rows = conn.execute(query).fetchall()
    if not rows:
        print("No snapshots yet. Run `ccm snapshot` first.")
        return 0
    for title, source, grade, market_cents, when in rows:
        price = "n/a" if market_cents is None else f"${market_cents / 100:,.2f}"
        grade_str = f" [{grade}]" if grade else ""
        print(f"{when:%Y-%m-%d}  {price:>12}  {source:<13} {title}{grade_str}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccm", description=__doc__)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply database migrations").set_defaults(
        func=cmd_migrate
    )
    sub.add_parser(
        "sync-watchlist", help="create watchlist items + crosswalk in the DB"
    ).set_defaults(func=cmd_sync_watchlist)

    p_snap = sub.add_parser("snapshot", help="fetch and store current prices")
    p_snap.add_argument(
        "--source",
        dest="sources",
        action="append",
        choices=available_sources(),
        help="limit to this source (repeatable); defaults to ENABLED_SOURCES",
    )
    p_snap.set_defaults(func=cmd_snapshot)

    sub.add_parser("show", help="print the latest snapshot per item").set_defaults(
        func=cmd_show
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    return args.func(args, settings)


if __name__ == "__main__":
    sys.exit(main())
