# Architecture

This document describes the system design for `card_comic_monitor`. For the data-source
selection rationale, legal posture, and cost/timeline, see [`DECISIONS.md`](DECISIONS.md).

## 1. Goals & constraints

**Goal.** Maintain a private, daily-refreshed price history of trading cards and comics,
surface the "hottest movers" with liquidity-aware momentum metrics, and feed a compact,
pre-aggregated view to an LLM that emits structured buy/hold/avoid recommendations for the
operator's own investment decisions.

**Hard constraints that shape the design:**

1. **No affordable source provides historical price series.** PriceCharting's API exposes
   *current* values only ("Historic prices and historic sales are not supported"). The
   canonical sold-price sources (eBay Marketplace Insights, TCGplayer, Cardmarket) are
   closed/restricted to new entrants in 2026. **Therefore our daily-snapshot pipeline is
   our history** — it accrues going forward and cannot be backfilled. Start collecting ASAP.
2. **Tight API quotas.** GoCollect Pro allows ~100 calls/day; PriceCharting allows 1
   call/second (CSV once per 24h). Free TCG tiers are ~100 req/day. The design must favor
   **bulk/dump endpoints** and a **curated watchlist**, not brute-force polling.
3. **Internal/personal use only.** Most source ToS forbid redistribution and
   public-facing/third-party-accessible deployment without written consent. The tool stays
   **single-user and self-hosted** to remain in a defensible "personal use" posture.

**Planned stack:** Postgres 16 + TimescaleDB · Python ETL workers · FastAPI read API ·
scheduled via systemd timers or Kubernetes CronJobs.

## 2. System overview

```
                 ┌──────────────────────────────────────────────────────┐
   data sources  │  ETL workers (Python, one per source, rate-governed)  │
  ┌───────────┐  │  ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
  │PriceChart.│─▶│  │pricecharting│ │  tcg_api │ │ gocollect│ │comicvine│ │
  │ TCG API   │─▶│  │   worker    │ │  worker  │ │  worker  │ │ worker  │ │
  │ GoCollect │─▶│  └─────┬───────┘ └────┬─────┘ └────┬─────┘ └────┬────┘ │
  │ ComicVine │─▶│        │ normalize to common price_snapshot shape      │
  └───────────┘  │        └──────────────┴───────────┴───────────┘        │
                 └───────────────────────────┬──────────────────────────-─┘
                                              ▼ idempotent upsert
                          ┌─────────────────────────────────────┐
                          │      Postgres 16 + TimescaleDB       │
                          │  items · item_source_ids (crosswalk) │
                          │  price_snapshots (hypertable)        │
                          │  continuous aggregates → movers      │
                          └───────────────────┬──────────────────┘
                                              ▼ read-only
                          ┌─────────────────────────────────────┐
                          │   FastAPI read API (pre-aggregated)  │
                          │  /movers  /item/{id}/history  ...    │
                          └───────────────────┬──────────────────┘
                                              ▼ compact structured JSON
                          ┌─────────────────────────────────────┐
                          │  LLM layer → buy/hold/avoid JSON     │
                          │  (logged for backtesting)            │
                          └─────────────────────────────────────┘
```

## 3. Data model

### 3.1 Dimension tables

```sql
-- Canonical identity for a tracked collectible (a card printing or a comic issue+grade).
CREATE TABLE items (
    item_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         TEXT NOT NULL,          -- 'card' | 'comic'
    game         TEXT,                   -- 'pokemon' | 'one_piece' | 'dragon_ball' | 'magic' | NULL for comics
    title        TEXT NOT NULL,
    set_name     TEXT,                   -- card set / comic series
    number       TEXT,                   -- card number / comic issue number
    variant      TEXT,                   -- printing / cover variant
    is_key_issue BOOLEAN DEFAULT FALSE,  -- comics: first appearance / key designation
    metadata     JSONB DEFAULT '{}',     -- enrichment (first appearance, characters, etc.)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Crosswalk: one row per (item, source, native id). The join key across vendors.
CREATE TABLE item_source_ids (
    item_id         BIGINT NOT NULL REFERENCES items(item_id),
    source          TEXT   NOT NULL,     -- 'pricecharting' | 'scrydex' | 'gocollect' | 'comicvine' | 'tcgplayer'
    source_native_id TEXT  NOT NULL,     -- vendor product/item id (e.g. TCGplayer productId)
    PRIMARY KEY (source, source_native_id),
    UNIQUE (item_id, source)
);
```

### 3.2 Fact table (hypertable)

```sql
CREATE TABLE price_snapshots (
    time         TIMESTAMPTZ NOT NULL,
    item_id      BIGINT      NOT NULL REFERENCES items(item_id),
    source       TEXT        NOT NULL,
    condition    TEXT        NOT NULL DEFAULT 'raw',  -- 'raw' | 'loose' | 'sealed' ...
    grade        TEXT        NOT NULL DEFAULT '',     -- 'PSA10' | 'CGC9.8' | '' for raw
    market_cents BIGINT,                              -- primary value
    low_cents    BIGINT,
    mid_cents    BIGINT,
    high_cents   BIGINT,
    volume       INT,                                 -- sale count in source's window, if provided
    PRIMARY KEY (time, item_id, source, condition, grade)
);

SELECT create_hypertable('price_snapshots', 'time', chunk_time_interval => INTERVAL '7 days');
CREATE INDEX ON price_snapshots (item_id, time DESC);

-- One snapshot per item/source/condition/grade per day; re-runs dedupe.
CREATE UNIQUE INDEX uq_snapshot_day
  ON price_snapshots (item_id, source, condition, grade, (time::date));
```

**Scale.** ~50,000 items × 1 snapshot/day × 5 years ≈ 91M rows — comfortable on a single
TimescaleDB node with 2–4 GB RAM. Enable **columnstore compression** on chunks older than
~30 days (90%+ savings). Keep all raw data (it is small); no retention policy needed.

## 4. Ingestion / ETL

- **One worker per source**, each normalizing the source response into the common
  `price_snapshots` shape and upserting idempotently (`ON CONFLICT ... DO UPDATE` against
  `uq_snapshot_day`) so re-runs are safe and don't burn quota twice.
- **Per-source rate-limit governor** (token bucket) honoring each API's documented limit:
  PriceCharting 1 call/sec, GoCollect ~100 calls/day, free TCG tiers ~100 req/day.
- **Prefer bulk/dump endpoints** over per-item calls to conserve quota: PriceCharting CSV
  (regenerated every 24h), TCGCSV daily CSV mirror, Scrydex/tcgapi.dev bulk price endpoints.
- **Checkpoint progress** so a mid-run failure resumes without re-spending the daily quota.
- **Schedule** with systemd timers or K8s CronJobs; stagger jobs; respect `Retry-After` and
  data-freshness (e.g. PriceCharting updates by ~8am EST; don't fetch before the dump exists).

## 5. Identity resolution ("same item" across sources)

- **Cards:** anchor on the **TCGplayer `productId`** — most card APIs (JustTCG, Scrydex,
  tcgapi.dev, PokemonPriceTracker) already carry it, so it is the natural join key. Map
  PriceCharting product IDs onto the canonical `item_id` via `item_source_ids`.
- **Comics:** key on (series + issue number + variant + grade + grading company). Use the
  **ComicVine issue ID** as a stable metadata anchor and the **GoCollect item ID** for pricing.
- **Fuzzy inputs** (e.g. raw eBay-style titles): normalize (lowercase, strip punctuation,
  parse set/number/condition) before matching; lean on any "Parse Title" helper a source offers.

## 6. "Hottest movers" momentum metrics

Computed in SQL via continuous aggregates / window functions and materialized into a daily
`movers` table (one row per item × window) that the API reads directly.

| Metric | Definition | Purpose |
|---|---|---|
| **Percent change** | `(P_t − P_{t−k}) / P_{t−k}` over 1w/1m/2–6m/1y | raw momentum |
| **Volume-weighted change** | % change weighted by sale count in the window | discount illiquid spikes |
| **Velocity** | slope of a linear fit of price over the window | trend strength |
| **Acceleration** | change in velocity between consecutive windows | catch items just starting to move |
| **Category z-score** | `(item_return − mean_category_return) / stddev_category_return` | judge a Pokémon move vs Pokémon, not comics |

**Liquidity filters are essential** to kill false signals: require a minimum sale count
and/or minimum price (e.g. ≥ N sales in window, market ≥ $5) before an item is eligible to
be a "mover." Apply `min_price`-style filters at the API layer too where supported.

## 7. Read API for the LLM

A small **read-only** FastAPI service. Indicative endpoints:

- `GET /movers?window=1m&category=pokemon&limit=25`
- `GET /item/{id}/history?window=6m`
- `GET /recommendations/inputs`

**Pre-aggregate before the LLM — never dump raw rows.** Per item, send compact structured
JSON: current price, % change per window, velocity/acceleration, z-score, liquidity stats,
population/scarcity, and key-issue flags. For history, downsample to the windows that matter
(weekly points for ≤6m, monthly for 1y) rather than daily arrays. This keeps the context
small and the reasoning grounded.

## 8. LLM layer

- Feed the pre-aggregated structured JSON; have the LLM emit **structured JSON output**
  (`{ action: buy|hold|avoid, confidence, rationale, target_horizon }`).
- **Log every recommendation** alongside the inputs so calls can be backtested against
  subsequent price movement.
- Default to a current, capable Claude model for the reasoning step. JustTCG and tcgapi.dev
  expose MCP/agent-friendly endpoints if the LLM should ever pull live data directly.

## 9. Open questions for the build phase

- Initial watchlist composition and size (drives whether GoCollect Pro's 100/day cap holds).
- Which single TCG API to start with (Scrydex vs JustTCG vs tcgapi.dev) — see DECISIONS.
- Whether to run on the existing self-hosted cluster (K8s CronJobs) or a single small VM
  (systemd timers).
