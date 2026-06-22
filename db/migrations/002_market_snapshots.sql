-- Market-wide discovery table: bulk price data from TCGCSV (and future
-- catalog sources). Intentionally has no FK to items -- it covers the whole
-- TCGplayer catalog, not just the curated watchlist.
--
-- product_id is the TCGplayer productId (text, matches item_source_ids.source_native_id
-- for any card that is also on the watchlist).
-- sub_type is the printing variant ("Holofoil", "Normal", "") -- part of the PK
-- because one product can have multiple printings with different prices.

CREATE TABLE IF NOT EXISTS market_snapshots (
    time         TIMESTAMPTZ NOT NULL,
    source       TEXT        NOT NULL,
    product_id   TEXT        NOT NULL,
    sub_type     TEXT        NOT NULL DEFAULT '',
    game         TEXT,
    name         TEXT,
    set_name     TEXT,
    market_cents BIGINT,
    low_cents    BIGINT,
    PRIMARY KEY (time, source, product_id, sub_type)
);

SELECT create_hypertable(
    'market_snapshots', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE
);

CREATE INDEX IF NOT EXISTS ix_mktsnap_lookup
    ON market_snapshots (source, product_id, time DESC);
CREATE INDEX IF NOT EXISTS ix_mktsnap_game
    ON market_snapshots (game, time DESC);
