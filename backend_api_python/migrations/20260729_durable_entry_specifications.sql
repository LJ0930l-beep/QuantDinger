-- Durable Canonical Entry V2 persistence.  Expand-only; no runtime path is wired.
-- Typed columns are authoritative.  No opaque payload is used for replay facts.

CREATE TABLE IF NOT EXISTS qd_durable_entry_specifications (
    command_id UUID PRIMARY KEY,
    contract_version VARCHAR(32) NOT NULL
        CHECK (contract_version = 'canonical-entry-v2'),

    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL
        CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL
        CHECK (market_type <> '' AND market_type = LOWER(market_type)),

    action VARCHAR(20) NOT NULL CHECK (action IN (
        'OPEN','INCREASE','REDUCE','CLOSE','CANCEL','EMERGENCY_CLOSE','PROTECTION')),
    risk_effect VARCHAR(16) NOT NULL CHECK (risk_effect IN (
        'INCREASE_RISK','REDUCE_RISK','NEUTRAL')),

    side VARCHAR(8) CHECK (side IN ('BUY','SELL')),
    quantity NUMERIC(38,18),
    quantity_semantics VARCHAR(16) CHECK (quantity_semantics IN ('ABSOLUTE')),
    execution_kind VARCHAR(16) CHECK (execution_kind IN (
        'MARKET','LIMIT','STOP_MARKET','STOP_LIMIT')),
    limit_price NUMERIC(38,18),
    trigger_price NUMERIC(38,18),
    trigger_direction VARCHAR(16) CHECK (trigger_direction IN ('AT_OR_ABOVE','AT_OR_BELOW')),
    trigger_price_type VARCHAR(8) CHECK (trigger_price_type IN ('LAST','MARK','INDEX')),
    reduce_only BOOLEAN NOT NULL,
    position_side VARCHAR(8) NOT NULL CHECK (position_side IN ('NET','LONG','SHORT')),

    cancel_target_kind VARCHAR(24) CHECK (cancel_target_kind IN (
        'ECONOMIC_ORDER_ID','CLIENT_ORDER_ID','VENUE_ORDER_ID')),
    cancel_target_id VARCHAR(160),
    target_position_id VARCHAR(160),
    close_quantity NUMERIC(38,18),
    close_all BOOLEAN NOT NULL,
    economic_order_id UUID,

    economic_fingerprint VARCHAR(64) NOT NULL
        CHECK (economic_fingerprint ~ '^[0-9a-f]{64}$'),
    request_fingerprint VARCHAR(64) NOT NULL
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),

    actor_type VARCHAR(16) NOT NULL CHECK (actor_type IN (
        'STRATEGY','HUMAN','AGENT','MCP','GRID','PROTECTION','ADMIN')),
    actor_id VARCHAR(160) NOT NULL CHECK (actor_id <> ''),
    source VARCHAR(16) NOT NULL CHECK (source IN (
        'REST','MANUAL','STRATEGY','AGENT','MCP','GRID','PROTECTION')),
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('DISABLED','PAPER','SHADOW')),
    idempotency_key VARCHAR(160) NOT NULL CHECK (idempotency_key <> ''),
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (tenant_id, credential_id, account_scope, idempotency_key, contract_version),

    CHECK (
        cancel_target_kind IS NULL
        OR cancel_target_kind <> 'ECONOMIC_ORDER_ID'
        OR cancel_target_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),

    CHECK (
        (source = 'REST' AND actor_type = 'HUMAN') OR
        (source = 'MANUAL' AND actor_type = 'HUMAN') OR
        (source = 'STRATEGY' AND actor_type = 'STRATEGY') OR
        (source = 'AGENT' AND actor_type = 'AGENT') OR
        (source = 'MCP' AND actor_type = 'MCP') OR
        (source = 'GRID' AND actor_type = 'GRID') OR
        (source = 'PROTECTION' AND actor_type = 'PROTECTION')
    ),

    CHECK (
        (action = 'CANCEL'
            AND risk_effect = 'NEUTRAL'
            AND economic_order_id IS NULL
            AND side IS NULL
            AND quantity IS NULL
            AND quantity_semantics IS NULL
            AND execution_kind IS NULL
            AND limit_price IS NULL
            AND trigger_price IS NULL
            AND trigger_direction IS NULL
            AND trigger_price_type IS NULL
            AND target_position_id IS NULL
            AND close_quantity IS NULL
            AND close_all = FALSE
            AND reduce_only = FALSE
            AND position_side = 'NET'
            AND cancel_target_kind IS NOT NULL
            AND cancel_target_id IS NOT NULL
            AND cancel_target_id <> '')
        OR
        (action IN ('OPEN','INCREASE')
            AND risk_effect = 'INCREASE_RISK'
            AND economic_order_id IS NOT NULL
            AND side IS NOT NULL
            AND quantity IS NOT NULL AND quantity > 0
            AND quantity_semantics = 'ABSOLUTE'
            AND execution_kind IS NOT NULL
            AND reduce_only = FALSE
            AND cancel_target_kind IS NULL
            AND cancel_target_id IS NULL
            AND target_position_id IS NULL
            AND close_quantity IS NULL
            AND close_all = FALSE)
        OR
        (action IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION')
            AND risk_effect = 'REDUCE_RISK'
            AND economic_order_id IS NOT NULL
            AND side IS NOT NULL
            AND execution_kind IS NOT NULL
            AND reduce_only = TRUE
            AND target_position_id IS NOT NULL AND target_position_id <> ''
            AND quantity IS NULL
            AND quantity_semantics IS NULL
            AND cancel_target_kind IS NULL
            AND cancel_target_id IS NULL
            AND ((close_quantity IS NOT NULL AND close_quantity > 0 AND close_all = FALSE)
                OR (close_quantity IS NULL AND close_all = TRUE)))
    ),

    CHECK (
        execution_kind IS NULL
        OR (execution_kind = 'MARKET'
            AND limit_price IS NULL AND trigger_price IS NULL
            AND trigger_direction IS NULL AND trigger_price_type IS NULL)
        OR (execution_kind = 'LIMIT'
            AND limit_price IS NOT NULL AND trigger_price IS NULL
            AND trigger_direction IS NULL AND trigger_price_type IS NULL)
        OR (execution_kind = 'STOP_MARKET'
            AND limit_price IS NULL AND trigger_price IS NOT NULL
            AND trigger_direction IS NOT NULL AND trigger_price_type IS NOT NULL)
        OR (execution_kind = 'STOP_LIMIT'
            AND limit_price IS NOT NULL AND trigger_price IS NOT NULL
            AND trigger_direction IS NOT NULL AND trigger_price_type IS NOT NULL)
    ),
    CHECK ((limit_price IS NULL OR limit_price > 0)
        AND (trigger_price IS NULL OR trigger_price > 0)),
    CHECK (
        (action = 'PROTECTION' AND actor_type = 'PROTECTION' AND source = 'PROTECTION')
        OR action <> 'PROTECTION'
    ),
    CHECK (
        source <> 'PROTECTION'
        OR (action IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION')
            AND risk_effect = 'REDUCE_RISK')
    )
);

CREATE INDEX IF NOT EXISTS idx_qd_durable_entry_specifications_scope_created
    ON qd_durable_entry_specifications
        (tenant_id, credential_id, account_scope, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qd_durable_entry_specifications_economic_order
    ON qd_durable_entry_specifications (economic_order_id)
    WHERE economic_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_qd_durable_entry_specifications_request_fingerprint
    ON qd_durable_entry_specifications (request_fingerprint);

CREATE OR REPLACE FUNCTION qd_reject_durable_entry_specification_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'durable entry specifications are append-only' USING ERRCODE = '55000';
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_qd_durable_entry_specifications_append_only'
    ) THEN
        CREATE TRIGGER trg_qd_durable_entry_specifications_append_only
        BEFORE UPDATE OR DELETE ON qd_durable_entry_specifications
        FOR EACH ROW EXECUTE FUNCTION qd_reject_durable_entry_specification_mutation();
    END IF;
END $$;
