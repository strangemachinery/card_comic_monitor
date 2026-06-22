"""A minimal forward-only SQL migration runner.

Applies every db/migrations/*.sql file once, in filename order, recording
applied filenames in a `schema_migrations` table.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

# repo_root/src/card_comic_monitor/migrate.py -> repo_root
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_ENSURE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _applied(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def run_migrations(
    conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply any pending migrations; return the filenames that were applied."""
    conn.execute(_ENSURE_TABLE)
    done = _applied(conn)
    pending = sorted(
        p for p in migrations_dir.glob("*.sql") if p.name not in done
    )
    applied: list[str] = []
    for path in pending:
        logger.info("applying migration %s", path.name)
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
        )
        applied.append(path.name)
    return applied
