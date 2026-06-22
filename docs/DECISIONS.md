# Decisions: data sources, legal posture, cost & plan

Companion to [`ARCHITECTURE.md`](ARCHITECTURE.md). This records *which* external data
sources we rely on and *why*, the legal constraints, the budget, and the staged build order.

> **Verify before committing budget.** API pricing, tiers, rate limits, and access status in
> this niche change frequently. Re-check every figure at the cited signup/docs page before
> spending, and email GoCollect/PriceCharting for written consent if use ever goes beyond
> private/personal.

## 1. The decisive context

The hard part is the **data layer, not the engineering**. As of 2026 the canonical
sold-price sources are closed to new developers:

- **TCGplayer API** — "we are no longer granting new API access at this time." Treat as
  inaccessible; do **not** architect around it.
- **eBay Marketplace Insights API** (the canonical sold-price source) — "restricted and not
  open to new users at this time." Likely unobtainable even after a working sandbox.
- **Cardmarket (MKM) API** — not accepting new applicants, and explicitly bars dedicated
  apps from polling public prices on consecutive days. Unsuitable as a snapshot source.

So we build history around **self-serve commercial APIs** and treat eBay/TCGplayer official
data as a bonus, never a foundation.

## 2. Source selection

### Essential (build around these)

| Source | Role | Notes |
|---|---|---|
| **PriceCharting** | Backbone — cards **and** comics **and** games under one token | Current values only (no history API → we snapshot). ~$40/yr. API 1 call/sec; CSV every 24h. Prices from completed eBay + PriceCharting sales, updated by ~8am EST. |
| **One self-serve TCG API** | Richer live card data + native price history | Pick **one** of Scrydex / JustTCG / tcgapi.dev to start (see §3). Covers Pokémon, One Piece, Dragon Ball, Magic, etc. |
| **GoCollect** | Graded-comic FMV, 30/90/365-day sold averages, sales records, CGC/CBCS census | Pro = $9/mo or $89/yr, **~100 calls/day** (the binding constraint → curated watchlist). **Manual approval, no published SLA — apply early.** FMV currently CGC-graded comics only. |

### Nice-to-have

| Source | Role | Caveat |
|---|---|---|
| **ComicVine** | Free comic metadata: issues, characters, first appearances, key designations | **"Strictly for non-commercial use only."** 200 req/resource/hour, velocity blocks. Cache locally; never high-volume poll. |
| **TCGCSV** | Free daily mirror of TCGplayer categories/products/prices | Unofficial — ToS/reliability risk falls on us. No deep history. |
| **GemRate** | PSA/BGS/SGC/CGC population & gem rates | No open developer API; resold inside some card APIs (e.g. PokemonPriceTracker Business). |
| **eBay Browse API** | Current *ask* prices (not solds) | Production use needs approval + Buy-API contract. |

### Skip unless we scale commercially

TCGplayer API (closed), Cardmarket API (closed + anti-polling), eBay Marketplace Insights
(restricted), PSA public API (cert verification only — no pop/price endpoints), GPAnalysis &
Heritage Auctions (no open self-serve API).

## 3. Which TCG API to start with

All offer instant API keys (no approval wait). Start with one; the crosswalk
(`item_source_ids`) makes swapping or adding later cheap.

- **Scrydex** — successor to pokemontcg.io. Broadest data: metadata, market prices, **price
  history**, raw + graded valuations, population reports, graded eBay sold history, a
  dedicated One Piece `price_history` endpoint. Credit-based plans. **Recommended primary**
  for breadth + lineage.
- **JustTCG** — Magic, Pokémon, YGO, Lorcana, One Piece, Digimon, etc. Free ~100 req/day;
  up to **365-day daily history** on paid tiers; `min_price` filter; an **MCP endpoint** for
  agentic/LLM workflows. Explicitly permits combining with other sources.
- **tcgapi.dev** — 89+ games (all of TCGplayer's catalog). Static `X-API-Key`, instant
  signup. Has `/v1/prices/top-movers` and bulk price endpoints (directly useful for our
  movers feature). History starts March 2025, weekly points for cards ≥ $1.

> **Decision:** begin with **Scrydex** as the primary live-card source (breadth + native
> history + One Piece coverage). Revisit if its credit pricing or SLA disappoints — JustTCG
> (MCP + cheap 365-day history) and tcgapi.dev (native top-movers) are the fallbacks.

## 4. Legal / ToS posture

**Common pattern:** internal/personal use is allowed; **redistribution and public-facing /
third-party-accessible deployment are forbidden without written consent.**

- **PriceCharting:** "Data can only be redistributed with express written consent." Personal
  and internal business use OK; data must not be used in software accessible to third parties
  without written permission.
- **GoCollect:** personal-use license; agree not to reproduce/redistribute/exploit content
  for commercial purposes without express written consent. Anti-scraping clause bars
  robots/spiders; §3.1 bars storing content "in any public or private electronic retrieval
  system" without consent. → Caching FMV for any **commercial** angle needs written consent
  (likely Enterprise). For a **private single-user** tool we are in a defensible posture.
- **ComicVine:** "strictly for non-commercial use only" — revoked key on commercial use.
- **Cardmarket:** do not snapshot directly (anti-polling + no credential sharing); use a
  reseller API instead.

**Operating rules baked into this project:**

1. Stay **private, single-user, self-hosted.** Do not expose the tool or its cached prices
   to any third party.
2. Use **official/commercial APIs**, not scraping. Honor anti-automation clauses.
3. **Storing values we legitimately pulled for our own internal use** is consistent with
   these terms — the prohibition targets *redistribution* and *public* systems.
4. If the tool ever goes multi-user or commercial: obtain **written redistribution consent**
   from PriceCharting and GoCollect, **drop ComicVine** (swap to GCD dumps or a licensed
   metadata source), and migrate to commercial-licensed API tiers.

## 5. Cost

| Build | Monthly data cost | Composition |
|---|---|---|
| **MVP (curated)** | **~$11–21/mo** | PriceCharting Premium (~$40/yr) + GoCollect Pro ($89/yr) + one TCG API free/Hobby tier + free ComicVine key. |
| **Complete (broad + commercial-grade)** | **~$250–600+/mo** | Scrydex or tcgapi.dev Business + PokemonPriceTracker Business (JP + graded + pop) + GoCollect Enterprise + PriceCharting Legendary + optional paid eBay-sold scraping. |

Infra: ~$0 marginal on existing self-hosted hardware; ~$20–40/mo on a small cloud VM.
TimescaleDB fits a 2–4 GB VM. LLM usage is the swing factor — a daily run over a few hundred
items is typically a few dollars/month.

## 6. Staged build plan

1. **Week 1 — Stand up the spine.** Deploy Postgres + TimescaleDB; create `items`,
   `item_source_ids`, `price_snapshots` (hypertable). Get a free ComicVine key and a free TCG
   API key. **Start snapshotting a small watchlist immediately** — history only accrues going
   forward.
2. **Week 2–3 — Add paid sources.** Subscribe to PriceCharting and GoCollect Pro;
   **submit the GoCollect API access request now** (manual approval). Build one ETL worker
   per source with rate-limit governors and idempotent upserts.
3. **Week 4 — Metrics + API.** Continuous aggregates + the daily `movers` table (percent
   change, volume-weighted change, velocity/acceleration, category z-scores, liquidity
   filters). Expose the read API.
4. **Week 5 — LLM layer.** Feed pre-aggregated JSON; emit structured buy/hold/avoid JSON;
   log every recommendation for backtesting.
5. **Scale decision.** Broad comic coverage or commercial use → negotiate GoCollect
   Enterprise and move TCG to a Business tier.

**Triggers that change the plan:** comic watchlist > ~100 issues/day → GoCollect Enterprise.
Noisy movers → tighten liquidity thresholds. Public/commercial → written redistribution
consent + licensed tiers. eBay Marketplace Insights approval → make sold-price data the
primary truth source and demote estimate APIs.

## 7. Known caveats

- GoCollect FMV is currently **CGC-graded comics only**; raw/non-comic FMV is partial.
- Several alternative TCG APIs are young startups — assess longevity/SLA before sole reliance.
- PriceCharting has **no historical API** — our snapshot pipeline is the only history, so it
  must run from day one (the past cannot be backfilled).
- Exact GoCollect JSON field names, per-second rate limits, and approval turnaround are
  login-gated and unverified until we have access.
