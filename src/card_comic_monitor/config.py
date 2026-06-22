"""Runtime configuration, loaded from the environment (and an optional .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DATABASE_URL = "postgresql://ccm:ccm@localhost:5432/ccm"


@dataclass(frozen=True)
class Settings:
    database_url: str
    watchlist_path: Path
    enabled_sources: tuple[str, ...]


def load_settings() -> Settings:
    """Read settings from the environment, loading a .env file if present."""
    load_dotenv()
    enabled = os.environ.get("ENABLED_SOURCES", "stub")
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        watchlist_path=Path(os.environ.get("WATCHLIST_PATH", "watchlist.yaml")),
        enabled_sources=tuple(s.strip() for s in enabled.split(",") if s.strip()),
    )
