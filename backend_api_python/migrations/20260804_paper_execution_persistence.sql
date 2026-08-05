-- V8: durable PAPER execution facts and restart checkpoints.
-- Expand-only; no Gate client, runtime, worker or live-trading wiring.

CREATE TABLE IF NOT EXISTS qd_paper_execution_orders (
    id UUID PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    idempotency_key VARCHAR(160) NOT NULL,
    request_fingerprint VARCHAR(128) NOT NULL CHECK (request_fingerprint <> ''),
    order_fingerprint VARCHAR(128) NOT NULL CHECK (order_fingerprint <> ''),
    market VARCHAR(40) NOT NULL,
    symbol VARCHAR(100) NOT NULL,
    market_type VARCHAR(20) NOT NULL CHECK (market_type IN ('spot','perpetual')),
    side VARCHAR(8) NOT NULL CHECK (side IN ('BUY','SELL')),
    order_type VARCHAR(16) NOT NULL CHECK (order_type IN ('MARKET','LIMIT')),
    quantity NUMERIC(38,18) NOT NULL CHECK (quantity > 0),
    limit_price NUMERIC(38,18),
    status VARCHAR(24) NOT NULL CHECK (status IN ('CREATED','REPLAYED','SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED')),
    fill_quantity NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (fill_quantity >= 0 AND fill_quantity <= quantity),
    fill_price NUMERIC(38,18),
    fee_amount NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    fee_asset VARCHAR(20) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((order_type = 'LIMIT' AND limit_price IS NOT NULL AND limit_price > 0) OR (order_type = 'MARKET' AND limit_price IS NULL)),
    CHECK ((status IN ('PARTIALLY_FILLED','FILLED') AND fill_price IS NOT NULL AND fill_price > 0) OR status NOT IN ('PARTIALLY_FILLED','FILLED')),
    UNIQUE(user_id, idempotency_key),
    UNIQUE(order_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_qd_paper_execution_orders_user
    ON qd_paper_execution_orders(user_id, created_at DESC, id);

CREATE TABLE IF NOT EXISTS qd_paper_execution_order_events (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES qd_paper_execution_orders(id) ON DELETE RESTRICT,
    event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
    event_type VARCHAR(24) NOT NULL CHECK (event_type IN ('SUBMITTED','CANCEL_REQUESTED','CANCELLED','REJECTED')),
    occurred_at TIMESTAMPTZ NOT NULL,
    event_fingerprint VARCHAR(128) NOT NULL UNIQUE CHECK (event_fingerprint <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(order_id, event_seq)
);
CREATE INDEX IF NOT EXISTS idx_qd_paper_execution_order_events_order
    ON qd_paper_execution_order_events(order_id, event_seq);

CREATE TABLE IF NOT EXISTS qd_paper_execution_fills (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES qd_paper_execution_orders(id) ON DELETE RESTRICT,
    quantity NUMERIC(38,18) NOT NULL CHECK (quantity > 0),
    price NUMERIC(38,18) NOT NULL CHECK (price > 0),
    fee_amount NUMERIC(38,18) NOT NULL CHECK (fee_amount >= 0),
    fee_asset VARCHAR(20) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    fill_fingerprint VARCHAR(128) NOT NULL UNIQUE CHECK (fill_fingerprint <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qd_paper_execution_fills_order
    ON qd_paper_execution_fills(order_id, occurred_at, id);

CREATE TABLE IF NOT EXISTS qd_paper_recovery_checkpoints (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    checkpoint_version BIGINT NOT NULL CHECK (checkpoint_version >= 0),
    last_order_id UUID REFERENCES qd_paper_execution_orders(id) ON DELETE RESTRICT,
    snapshot_fingerprint VARCHAR(128) NOT NULL CHECK (snapshot_fingerprint <> ''),
    status VARCHAR(16) NOT NULL CHECK (status IN ('READY','STALE','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, checkpoint_version)
);

CREATE OR REPLACE FUNCTION qd_reject_paper_execution_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'paper execution facts are append-only' USING ERRCODE = '55000';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_paper_execution_orders_append_only') THEN
        CREATE TRIGGER trg_qd_paper_execution_orders_append_only
            BEFORE UPDATE OR DELETE ON qd_paper_execution_orders
            FOR EACH ROW EXECUTE FUNCTION qd_reject_paper_execution_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_paper_execution_fills_append_only') THEN
        CREATE TRIGGER trg_qd_paper_execution_fills_append_only
            BEFORE UPDATE OR DELETE ON qd_paper_execution_fills
            FOR EACH ROW EXECUTE FUNCTION qd_reject_paper_execution_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_paper_execution_order_events_append_only') THEN
        CREATE TRIGGER trg_qd_paper_execution_order_events_append_only
            BEFORE UPDATE OR DELETE ON qd_paper_execution_order_events
            FOR EACH ROW EXECUTE FUNCTION qd_reject_paper_execution_mutation();
    END IF;
END $$;
