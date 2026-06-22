# card_comic_monitor

A **self-hosted, single-user** price-tracking and investment-advisory tool for trading
cards (Pokémon, One Piece, Dragon Ball, Magic, sports) and comics. It snapshots prices
daily into a time-series database, computes "hottest movers" momentum metrics, and feeds
pre-aggregated structured data to an LLM for buy/hold/avoid decisions.

> **Status:** Design phase. No application code yet — this repo currently holds the
> architecture and decision records that the implementation will follow.

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
