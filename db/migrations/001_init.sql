-- Week-1 spine: canonical identity, cross-source crosswalk, and the
-- price-snapshot hypertable. See docs/ARCHITECTURE.md section 3.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Canonical identity for a tracked collectible (a card printing or a
-- comic issue + grade).
CREATE TABLE IF NOT EXISTS items (
    item_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('card', 'comic')),
    game         TEXT,                       -- 'pokemon' | 'one_piece' | ... ; NULL for comics
    title        TEXT NOT NULL,
    set_name     TEXT,                       -- card set / comic series
    number       TEXT,                       -- card number / comic issue number
    variant      TEXT,                       -- printing / cover variant
    is_key_issue BOOLEAN NOT NULL DEFAULT FALSE,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Crosswalk: one row per (item, source, native id). The vendor-agnostic join key.
CREATE TABLE IF NOT EXISTS item_source_ids (
    item_id          BIGINT NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    source           TEXT   NOT NULL,        -- 'pricecharting' | 'scrydex' | 'gocollect' | 'stub' | ...
    source_native_id TEXT   NOT NULL,        -- vendor product/item id (e.g. TCGplayer productId)
    PRIMARY KEY (source, source_native_id),
    UNIQUE (item_id, source)
);

-- Daily price observations. Workers write `time` truncated to the UTC day so
-- the primary key gives one row per item/source/condition/grade/day and
-- re-runs upsert idempotently.
--
-- NOTE: TimescaleDB requires the partitioning column (`time`) to be part of any
-- unique/primary key, which is why the day-grain dedup lives in the PK via a
-- date-truncated `time` rather than a separate `(time::date)` expression index.
CREATE TABLE IF NOT EXISTS price_snapshots (
    time         TIMESTAMPTZ NOT NULL,
    item_id      BIGINT      NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    source       TEXT        NOT NULL,
    condition    TEXT        NOT NULL DEFAULT 'raw',   -- 'raw' | 'loose' | 'sealed' ...
    grade        TEXT        NOT NULL DEFAULT '',      -- 'PSA10' | 'CGC9.8' | '' for raw
    market_cents BIGINT,                               -- primary value
    low_cents    BIGINT,
    mid_cents    BIGINT,
    high_cents   BIGINT,
    volume       INT,                                  -- sale count in source's window, if provided
    PRIMARY KEY (time, item_id, source, condition, grade)
);

SELECT create_hypertable(
    'price_snapshots', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_snapshots_item_time
    ON price_snapshots (item_id, time DESC);
