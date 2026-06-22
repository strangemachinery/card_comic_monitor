# card_comic_monitor

A **self-hosted, single-user** price-tracking and investment-advisory tool for trading
cards (Pokémon, One Piece, Dragon Ball, Magic, sports) and comics. It snapshots prices
daily into a time-series database, computes "hottest movers" momentum metrics, and feeds
pre-aggregated structured data to an LLM for buy/hold/avoid decisions.

> **Status:** Week-1 spine implemented. The TimescaleDB schema, a rate-governed ETL
> pipeline with idempotent upserts, a watchlist loader, and an offline demo source are in
> place and runnable. Real vendor sources (PriceCharting, Scrydex, GoCollect, …) land next.

## Quick start

```bash
# 1. Database (Postgres 16 + TimescaleDB)
docker compose up -d db

# 2. Python env + install (kept inside a virtualenv)
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# 3. Configure
cp .env.example .env                 # adjust DATABASE_URL if needed
cp watchlist.example.yaml watchlist.yaml

# 4. Run the pipeline
ccm migrate            # apply DB migrations (creates the hypertable)
ccm sync-watchlist     # create items + cross-source id crosswalk
ccm snapshot           # fetch current prices (default source: stub) and upsert
ccm show               # print the latest snapshot per item

# Tests (no database required)
pytest
```

The default `stub` source needs no API key and generates deterministic demo prices, so the
pipeline runs end-to-end and starts accruing history immediately. Run `ccm snapshot` daily
(systemd timer or cron) — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §4.

## Layout

```
db/migrations/        SQL migrations (001 = items, crosswalk, price_snapshots hypertable)
src/card_comic_monitor/
  config.py           settings from env/.env
  db.py               connection helper
  models.py           PriceSnapshot, WatchlistItem, FetchTarget
  ratelimit.py        token-bucket governor (per-source documented limits)
  watchlist.py        YAML watchlist loader
  repository.py       identity resolution + idempotent snapshot upserts
  migrate.py          forward-only migration runner
  sources/            one worker per vendor (base + stub; real vendors next)
  cli.py              `ccm` entrypoint
tests/                unit tests (rate limiter, watchlist, stub source)
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design: data model, ingestion,
  identity resolution, momentum metrics, read API, and the LLM layer.
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — which data sources we use and why, the
  legal/ToS posture, cost estimates, and the staged build plan.

## At a glance

- **Stack (planned):** Postgres 16 + TimescaleDB, Python ETL workers, FastAPI read API.
- **Primary data sources:** PriceCharting (cards + comics + games, one token),
  one self-serve TCG pricing API (Scrydex / JustTCG / tcgapi.dev) for live card data and
  history, GoCollect for graded-comic FMV/sales/census, ComicVine for metadata.
- **Legal posture:** private, single-user, internal use only. Redistribution and
  public-facing deployment are forbidden by most source ToS without written consent.
- **Cost:** ~$11–21/month for the curated MVP; $250–600+/month for a broad/commercial build.

## Why a daily-snapshot design

The decisive constraint is the **data layer, not the engineering**. The canonical sold-price
sources are closed to new entrants in 2026 (TCGplayer API, Cardmarket API, and eBay's
Marketplace Insights API are all restricted), and the affordable commercial APIs
(PriceCharting in particular) expose **current values only — no historical series**.

So **our daily-snapshot pipeline _is_ our history.** History only accrues going forward and
cannot be backfilled, which is why standing up the snapshot spine early is the first
priority once we move from design to build.
