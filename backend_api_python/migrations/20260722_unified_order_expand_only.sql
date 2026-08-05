-- Phase 0 / PR-02: expand-only unified-order safety kernel schema.
-- This migration intentionally creates empty, unreferenced structures only.

CREATE TABLE IF NOT EXISTS qd_order_commands (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    actor_type VARCHAR(16) NOT NULL CHECK (actor_type IN ('STRATEGY','HUMAN','AGENT','MCP','GRID','PROTECTION','ADMIN')),
    actor_id VARCHAR(160) NOT NULL,
    source VARCHAR(32) NOT NULL,
    action VARCHAR(20) NOT NULL CHECK (action IN ('OPEN','INCREASE','REDUCE','CLOSE','CANCEL','EMERGENCY_CLOSE','PROTECTION')),
    account_scope VARCHAR(160) NOT NULL,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE RESTRICT,
    request_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_fingerprint VARCHAR(128) NOT NULL CHECK (request_fingerprint <> ''),
    idempotency_key VARCHAR(180) NOT NULL CHECK (idempotency_key <> ''),
    status VARCHAR(16) NOT NULL CHECK (status IN ('ACCEPTED','PROCESSING','SUCCEEDED','FAILED','CANCELLED')),
    correlation_id VARCHAR(160) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_order_commands_idempotency
    ON qd_order_commands(tenant_id, source, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_qd_order_commands_account_created
    ON qd_order_commands(account_scope, created_at DESC);

CREATE TABLE IF NOT EXISTS qd_instrument_rule_snapshots (
    id UUID PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    rule_version VARCHAR(100) NOT NULL CHECK (rule_version <> ''),
    tick_size NUMERIC(38,18) NOT NULL CHECK (tick_size > 0),
    quantity_step NUMERIC(38,18) NOT NULL CHECK (quantity_step > 0),
    minimum_quantity NUMERIC(38,18) NOT NULL CHECK (minimum_quantity >= 0),
    minimum_notional NUMERIC(38,18) NOT NULL CHECK (minimum_notional >= 0),
    price_scale INTEGER NOT NULL CHECK (price_scale >= 0),
    quantity_scale INTEGER NOT NULL CHECK (quantity_scale >= 0),
    rounding_policy_version VARCHAR(100) NOT NULL CHECK (rounding_policy_version <> ''),
    raw_rules_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(exchange, market_type, instrument_id, rule_version)
);

CREATE TABLE IF NOT EXISTS qd_order_intents_v2 (
    id UUID PRIMARY KEY,
    command_id UUID NOT NULL REFERENCES qd_order_commands(id) ON DELETE RESTRICT,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    economic_order_id UUID NOT NULL,
    intent_version INTEGER NOT NULL CHECK (intent_version >= 1),
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    side VARCHAR(8) NOT NULL CHECK (side IN ('BUY','SELL')),
    position_side VARCHAR(12) NOT NULL DEFAULT '' CHECK (position_side IN ('','LONG','SHORT')),
    reduce_only BOOLEAN NOT NULL DEFAULT FALSE,
    order_type VARCHAR(24) NOT NULL,
    execution_algo VARCHAR(32) NOT NULL,
    time_in_force VARCHAR(16) NOT NULL DEFAULT '',
    target_quantity NUMERIC(38,18) NOT NULL CHECK (target_quantity > 0),
    limit_price NUMERIC(38,18),
    quote_notional NUMERIC(38,18),
    instrument_rule_snapshot_id UUID NOT NULL REFERENCES qd_instrument_rule_snapshots(id) ON DELETE RESTRICT,
    instrument_rule_version VARCHAR(100) NOT NULL CHECK (instrument_rule_version <> ''),
    rounding_mode VARCHAR(32) NOT NULL,
    strategy_run_id INTEGER,
    portfolio_id VARCHAR(96) NOT NULL DEFAULT '',
    rebalance_group_id VARCHAR(128) NOT NULL DEFAULT '',
    payload_hash VARCHAR(128) NOT NULL CHECK (payload_hash <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (limit_price IS NULL OR limit_price > 0),
    CHECK (quote_notional IS NULL OR quote_notional > 0),
    UNIQUE(command_id, intent_version),
    UNIQUE(id, tenant_id, credential_id, account_scope, instrument_id, market_type),
    UNIQUE(id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
);
CREATE INDEX IF NOT EXISTS idx_qd_order_intents_v2_scope
    ON qd_order_intents_v2(account_scope, instrument_id, created_at DESC);

CREATE TABLE IF NOT EXISTS qd_economic_orders (
    id UUID PRIMARY KEY,
    intent_id UUID NOT NULL UNIQUE,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    user_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    state VARCHAR(32) NOT NULL CHECK (state IN ('CREATED','RISK_PENDING','RISK_RESERVED','SUBMITTING','SUBMITTED','SUBMISSION_UNKNOWN','PARTIALLY_FILLED','FILLED','CANCEL_REQUESTED','CANCELLING','CANCELLED','REJECTED','FAILED','RECONCILIATION_REQUIRED')),
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    target_quantity NUMERIC(38,18) NOT NULL CHECK (target_quantity > 0),
    cumulative_filled_qty NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (cumulative_filled_qty >= 0),
    cumulative_fee_quote NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (cumulative_fee_quote >= 0),
    overfill_qty NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (overfill_qty >= 0),
    last_event_seq BIGINT NOT NULL DEFAULT 0 CHECK (last_event_seq >= 0),
    active_fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (active_fencing_token >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(id, tenant_id, credential_id, account_scope, instrument_id, market_type),
    FOREIGN KEY(intent_id, id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_order_intents_v2(id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_qd_economic_orders_scope_state
    ON qd_economic_orders(account_scope, instrument_id, state);

CREATE TABLE IF NOT EXISTS qd_risk_reservations (
    id UUID PRIMARY KEY,
    command_id UUID NOT NULL REFERENCES qd_order_commands(id) ON DELETE RESTRICT,
    economic_order_id UUID REFERENCES qd_economic_orders(id) ON DELETE RESTRICT,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    reservation_kind VARCHAR(32) NOT NULL,
    currency VARCHAR(20) NOT NULL,
    reserved_notional NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (reserved_notional >= 0),
    reserved_margin NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (reserved_margin >= 0),
    reserved_position_qty NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (reserved_position_qty >= 0),
    limits_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_input_hash VARCHAR(128) NOT NULL CHECK (risk_input_hash <> ''),
    state VARCHAR(16) NOT NULL CHECK (state IN ('ACTIVE','CONSUMED','RELEASED','EXPIRED')),
    expires_at TIMESTAMPTZ,
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_risk_reservations_active_command_kind
    ON qd_risk_reservations(command_id, reservation_kind) WHERE state = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_qd_risk_reservations_active_scope
    ON qd_risk_reservations(account_scope, expires_at) WHERE state = 'ACTIVE';

CREATE TABLE IF NOT EXISTS qd_order_state_events (
    id UUID PRIMARY KEY,
    economic_order_id UUID NOT NULL REFERENCES qd_economic_orders(id) ON DELETE RESTRICT,
    event_seq BIGINT NOT NULL CHECK (event_seq >= 1),
    from_state VARCHAR(32) CHECK (from_state IS NULL OR from_state IN ('CREATED','RISK_PENDING','RISK_RESERVED','SUBMITTING','SUBMITTED','SUBMISSION_UNKNOWN','PARTIALLY_FILLED','FILLED','CANCEL_REQUESTED','CANCELLING','CANCELLED','REJECTED','FAILED','RECONCILIATION_REQUIRED')),
    to_state VARCHAR(32) NOT NULL CHECK (to_state IN ('CREATED','RISK_PENDING','RISK_RESERVED','SUBMITTING','SUBMITTED','SUBMISSION_UNKNOWN','PARTIALLY_FILLED','FILLED','CANCEL_REQUESTED','CANCELLING','CANCELLED','REJECTED','FAILED','RECONCILIATION_REQUIRED')),
    reason_code VARCHAR(64) NOT NULL,
    actor_type VARCHAR(16) NOT NULL,
    evidence_hash VARCHAR(128) NOT NULL DEFAULT '',
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(economic_order_id, event_seq)
);
CREATE INDEX IF NOT EXISTS idx_qd_order_state_events_occurred
    ON qd_order_state_events(economic_order_id, occurred_at);

CREATE TABLE IF NOT EXISTS qd_submission_attempts (
    id UUID PRIMARY KEY,
    economic_order_id UUID NOT NULL,
    exchange VARCHAR(50) NOT NULL,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    child_seq INTEGER NOT NULL CHECK (child_seq >= 1),
    attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
    role VARCHAR(16) NOT NULL CHECK (role IN ('PRIMARY','FALLBACK','PROTECTION','EMERGENCY')),
    canonical_client_order_id VARCHAR(128) NOT NULL CHECK (canonical_client_order_id <> ''),
    venue_client_order_id VARCHAR(128) NOT NULL CHECK (venue_client_order_id <> ''),
    request_fingerprint VARCHAR(128) NOT NULL CHECK (request_fingerprint <> ''),
    request_json_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
    state VARCHAR(20) NOT NULL CHECK (state IN ('READY','SUBMITTING','ACKED','UNKNOWN','CONFIRMED_ABSENT','REJECTED')),
    lease_owner VARCHAR(160) NOT NULL DEFAULT '',
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    started_at TIMESTAMPTZ,
    response_at TIMESTAMPTZ,
    unknown_since TIMESTAMPTZ,
    last_error_class VARCHAR(128) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(economic_order_id, child_seq, attempt_no),
    UNIQUE(exchange, credential_id, market_type, venue_client_order_id),
    UNIQUE(id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type),
    FOREIGN KEY(economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_economic_orders(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_qd_submission_attempts_recovery
    ON qd_submission_attempts(exchange, credential_id, state, unknown_since);

CREATE TABLE IF NOT EXISTS qd_exchange_orders (
    id UUID PRIMARY KEY,
    attempt_id UUID NOT NULL UNIQUE,
    economic_order_id UUID NOT NULL,
    parent_exchange_order_id UUID REFERENCES qd_exchange_orders(id) ON DELETE RESTRICT,
    child_role VARCHAR(32) NOT NULL,
    exchange VARCHAR(50) NOT NULL,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    market_type VARCHAR(20) NOT NULL,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    exchange_order_id VARCHAR(160),
    venue_client_order_id VARCHAR(128) NOT NULL CHECK (venue_client_order_id <> ''),
    raw_status VARCHAR(64) NOT NULL DEFAULT '',
    normalized_state VARCHAR(32) NOT NULL CHECK (normalized_state IN ('SUBMITTED','PARTIALLY_FILLED','FILLED','SUBMISSION_UNKNOWN','CANCEL_REQUESTED','CANCELLING','CANCELLED','REJECTED','RECONCILIATION_REQUIRED')),
    requested_qty NUMERIC(38,18) NOT NULL CHECK (requested_qty > 0),
    cumulative_filled_qty NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (cumulative_filled_qty >= 0),
    avg_fill_price NUMERIC(38,18),
    cancel_state VARCHAR(32) NOT NULL DEFAULT '',
    last_exchange_update_at TIMESTAMPTZ,
    last_observed_at TIMESTAMPTZ,
    raw_payload_hash VARCHAR(128) NOT NULL DEFAULT '',
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (avg_fill_price IS NULL OR avg_fill_price > 0),
    UNIQUE(exchange, credential_id, market_type, venue_client_order_id),
    UNIQUE(exchange, credential_id, exchange_order_id),
    FOREIGN KEY(economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_economic_orders(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_submission_attempts(id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS qd_exchange_order_observations (
    id UUID PRIMARY KEY,
    exchange_order_id UUID REFERENCES qd_exchange_orders(id) ON DELETE RESTRICT,
    attempt_id UUID REFERENCES qd_submission_attempts(id) ON DELETE RESTRICT,
    observation_source VARCHAR(16) NOT NULL CHECK (observation_source IN ('REST','WS','BACKFILL','MANUAL')),
    payload_hash VARCHAR(128) NOT NULL CHECK (payload_hash <> ''),
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (exchange_order_id IS NOT NULL OR attempt_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_exchange_order_observations_order_evidence
    ON qd_exchange_order_observations(exchange_order_id, observation_source, payload_hash)
    WHERE exchange_order_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_exchange_order_observations_attempt_evidence
    ON qd_exchange_order_observations(attempt_id, observation_source, payload_hash)
    WHERE attempt_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS qd_exchange_fill_events (
    id UUID PRIMARY KEY,
    key_version VARCHAR(32) NOT NULL CHECK (key_version <> ''),
    dedupe_key VARCHAR(256) NOT NULL CHECK (dedupe_key <> ''),
    exchange VARCHAR(50) NOT NULL,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    exchange_order_pk UUID REFERENCES qd_exchange_orders(id) ON DELETE RESTRICT,
    economic_order_id UUID NOT NULL REFERENCES qd_economic_orders(id) ON DELETE RESTRICT,
    intent_id UUID NOT NULL REFERENCES qd_order_intents_v2(id) ON DELETE RESTRICT,
    exchange_order_id VARCHAR(160) NOT NULL DEFAULT '',
    exchange_fill_id VARCHAR(160) NOT NULL DEFAULT '',
    venue_trade_sequence VARCHAR(160) NOT NULL DEFAULT '',
    instrument_id VARCHAR(100) NOT NULL,
    side VARCHAR(8) NOT NULL CHECK (side IN ('BUY','SELL')),
    position_side VARCHAR(12) NOT NULL DEFAULT '' CHECK (position_side IN ('','LONG','SHORT')),
    liquidity_role VARCHAR(16) NOT NULL DEFAULT '',
    price NUMERIC(38,18) NOT NULL CHECK (price > 0),
    quantity NUMERIC(38,18) NOT NULL CHECK (quantity > 0),
    quote_quantity NUMERIC(38,18) NOT NULL CHECK (quote_quantity >= 0),
    fee_amount NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    fee_asset VARCHAR(20) NOT NULL DEFAULT '',
    fee_quote_amount NUMERIC(38,18),
    exchange_event_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    source VARCHAR(16) NOT NULL CHECK (source IN ('WS','REST','BACKFILL','MANUAL')),
    source_cursor VARCHAR(256) NOT NULL DEFAULT '',
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_payload_hash VARCHAR(128) NOT NULL CHECK (raw_payload_hash <> ''),
    normalizer_version VARCHAR(64) NOT NULL,
    instrument_rule_version VARCHAR(100) NOT NULL,
    supersedes_event_id UUID REFERENCES qd_exchange_fill_events(id) ON DELETE RESTRICT,
    quarantine_state VARCHAR(32) NOT NULL DEFAULT 'CLEAR' CHECK (quarantine_state IN ('CLEAR','QUARANTINED','RECONCILIATION_REQUIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(exchange, credential_id, dedupe_key, key_version),
    FOREIGN KEY(economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_economic_orders(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT,
    FOREIGN KEY(intent_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_order_intents_v2(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_qd_exchange_fill_events_history
    ON qd_exchange_fill_events(credential_id, exchange_event_at, id);
CREATE INDEX IF NOT EXISTS idx_qd_exchange_fill_events_order
    ON qd_exchange_fill_events(exchange_order_pk, id);

CREATE TABLE IF NOT EXISTS qd_ledger_transactions (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    transaction_type VARCHAR(32) NOT NULL CHECK (transaction_type IN ('TRADE','FEE','FUNDING','REALIZED_PNL','BALANCE_ADJUSTMENT','EXTERNAL_TRADE','REVERSAL','CORRECTION')),
    source_event_type VARCHAR(64) NOT NULL,
    source_event_id UUID NOT NULL,
    reverses_transaction_id UUID REFERENCES qd_ledger_transactions(id) ON DELETE RESTRICT,
    effective_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valuation_ccy VARCHAR(20) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    correlation_id VARCHAR(160) NOT NULL DEFAULT '',
    description_code VARCHAR(64) NOT NULL,
    CHECK ((transaction_type = 'REVERSAL' AND reverses_transaction_id IS NOT NULL) OR (transaction_type <> 'REVERSAL' AND reverses_transaction_id IS NULL)),
    UNIQUE(source_event_type, source_event_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_ledger_transactions_reversal_once
    ON qd_ledger_transactions(reverses_transaction_id)
    WHERE transaction_type = 'REVERSAL' AND reverses_transaction_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS qd_ledger_entries (
    id UUID PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES qd_ledger_transactions(id) ON DELETE RESTRICT,
    line_no INTEGER NOT NULL CHECK (line_no >= 1),
    book VARCHAR(16) NOT NULL CHECK (book IN ('QUANTITY','MONETARY')),
    account_code VARCHAR(96) NOT NULL,
    asset VARCHAR(20) NOT NULL,
    signed_amount NUMERIC(38,18) NOT NULL,
    quantity NUMERIC(38,18),
    unit_price NUMERIC(38,18),
    value_in_valuation_ccy NUMERIC(38,18),
    instrument_id VARCHAR(100) NOT NULL DEFAULT '',
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE RESTRICT,
    economic_order_id UUID REFERENCES qd_economic_orders(id) ON DELETE RESTRICT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (quantity IS NULL OR quantity >= 0),
    CHECK (unit_price IS NULL OR unit_price > 0),
    UNIQUE(transaction_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_qd_ledger_entries_replay
    ON qd_ledger_entries(economic_order_id, transaction_id);

CREATE TABLE IF NOT EXISTS qd_position_projections (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE RESTRICT,
    instrument_id VARCHAR(100) NOT NULL,
    side VARCHAR(12) NOT NULL CHECK (side IN ('LONG','SHORT')),
    quantity NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    average_cost NUMERIC(38,18),
    realized_pnl NUMERIC(38,18) NOT NULL DEFAULT 0,
    last_event_seq BIGINT NOT NULL DEFAULT 0 CHECK (last_event_seq >= 0),
    projection_version INTEGER NOT NULL CHECK (projection_version >= 1),
    policy_version VARCHAR(64) NOT NULL,
    rebuilt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (average_cost IS NULL OR average_cost > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_position_projections_strategy_scope
    ON qd_position_projections(tenant_id, credential_id, account_scope, strategy_id, instrument_id, side, projection_version)
    WHERE strategy_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_position_projections_unassigned_scope
    ON qd_position_projections(tenant_id, credential_id, account_scope, instrument_id, side, projection_version)
    WHERE strategy_id IS NULL;

CREATE TABLE IF NOT EXISTS qd_pnl_projections (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    projection_version INTEGER NOT NULL CHECK (projection_version >= 1),
    realized_pnl NUMERIC(38,18) NOT NULL DEFAULT 0,
    fee_amount NUMERIC(38,18) NOT NULL DEFAULT 0,
    funding_amount NUMERIC(38,18) NOT NULL DEFAULT 0,
    net_realized_pnl NUMERIC(38,18) NOT NULL DEFAULT 0,
    mark_price NUMERIC(38,18),
    mark_at TIMESTAMPTZ,
    unrealized_pnl NUMERIC(38,18),
    last_ledger_seq BIGINT NOT NULL DEFAULT 0 CHECK (last_ledger_seq >= 0),
    rebuilt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (mark_price IS NULL OR mark_price > 0),
    UNIQUE(tenant_id, credential_id, account_scope, instrument_id, projection_version)
);

CREATE TABLE IF NOT EXISTS qd_reconciliation_checkpoints (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    exchange VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL CHECK (status IN ('HEALTHY','STALE','FAILED','CONFLICT')),
    last_orders_cursor VARCHAR(256) NOT NULL DEFAULT '',
    last_fills_cursor VARCHAR(256) NOT NULL DEFAULT '',
    last_positions_cursor VARCHAR(256) NOT NULL DEFAULT '',
    last_balances_cursor VARCHAR(256) NOT NULL DEFAULT '',
    last_funding_cursor VARCHAR(256) NOT NULL DEFAULT '',
    last_success_at TIMESTAMPTZ,
    evidence_hash VARCHAR(128) NOT NULL DEFAULT '',
    unresolved_count INTEGER NOT NULL DEFAULT 0 CHECK (unresolved_count >= 0),
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    sla_deadline TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(credential_id, exchange, market_type, account_scope, instrument_id)
);
CREATE INDEX IF NOT EXISTS idx_qd_reconciliation_checkpoints_health
    ON qd_reconciliation_checkpoints(credential_id, market_type, status, last_success_at);

CREATE TABLE IF NOT EXISTS qd_reconciliation_issues (
    id UUID PRIMARY KEY,
    checkpoint_id UUID NOT NULL REFERENCES qd_reconciliation_checkpoints(id) ON DELETE RESTRICT,
    key_version VARCHAR(32) NOT NULL CHECK (key_version <> ''),
    dedupe_key VARCHAR(256) NOT NULL CHECK (dedupe_key <> ''),
    issue_type VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('OPEN','RESOLVED','QUARANTINED')),
    evidence_hash VARCHAR(128) NOT NULL,
    resolution_event_id UUID REFERENCES qd_order_state_events(id) ON DELETE RESTRICT,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(checkpoint_id, key_version, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_qd_reconciliation_issues_active
    ON qd_reconciliation_issues(checkpoint_id, occurred_at) WHERE status IN ('OPEN','QUARANTINED');

CREATE TABLE IF NOT EXISTS qd_transactional_outbox (
    event_id UUID PRIMARY KEY,
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id UUID NOT NULL,
    aggregate_version BIGINT NOT NULL CHECK (aggregate_version >= 0),
    event_type VARCHAR(96) NOT NULL,
    payload_json JSONB NOT NULL,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    publish_attempts INTEGER NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
    lease_owner VARCHAR(160) NOT NULL DEFAULT '',
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(aggregate_id, aggregate_version, event_type)
);
CREATE INDEX IF NOT EXISTS idx_qd_transactional_outbox_pending
    ON qd_transactional_outbox(available_at, event_id) WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS qd_consumer_inbox (
    consumer_name VARCHAR(96) NOT NULL,
    event_id UUID NOT NULL REFERENCES qd_transactional_outbox(event_id) ON DELETE RESTRICT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    result_hash VARCHAR(128) NOT NULL DEFAULT '',
    PRIMARY KEY(consumer_name, event_id)
);

CREATE TABLE IF NOT EXISTS qd_projection_snapshots (
    id UUID PRIMARY KEY,
    projection_name VARCHAR(96) NOT NULL,
    projection_version INTEGER NOT NULL CHECK (projection_version >= 1),
    policy_version VARCHAR(64) NOT NULL,
    account_scope VARCHAR(160) NOT NULL,
    last_event_seq BIGINT NOT NULL CHECK (last_event_seq >= 0),
    snapshot_hash VARCHAR(128) NOT NULL CHECK (snapshot_hash <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(projection_name, projection_version, account_scope, last_event_seq)
);
