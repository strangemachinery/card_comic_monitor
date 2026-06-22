"""Database connection helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg

from .config import Settings


@contextmanager
def connect(settings: Settings) -> Iterator[psycopg.Connection]:
    """Open a connection that commits on success and rolls back on error."""
    with psycopg.connect(settings.database_url) as conn:
        yield conn
