-- RF-01A: authoritative persisted sources for runtime hard-risk facts.
-- Expand-only.  These tables are source facts, not a trading runtime path.

CREATE TABLE IF NOT EXISTS qd_authoritative_risk_policies (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL CHECK (contract_version = 'authoritative-risk-facts-v1'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    strategy_scope VARCHAR(160) NOT NULL CHECK (strategy_scope <> ''),
    policy_identity VARCHAR(160) NOT NULL CHECK (policy_identity <> ''),
    policy_version VARCHAR(160) NOT NULL CHECK (policy_version <> ''),
    policy_fingerprint VARCHAR(64) NOT NULL CHECK (policy_fingerprint ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    max_age_seconds INTEGER NOT NULL CHECK (max_age_seconds >= 0),
    reservation_ttl_seconds INTEGER NOT NULL CHECK (reservation_ttl_seconds > 0),
    valuation_currency VARCHAR(32) NOT NULL CHECK (valuation_currency <> '' AND valuation_currency = UPPER(valuation_currency)),
    max_gross_notional NUMERIC(38,18) NOT NULL CHECK (max_gross_notional >= 0),
    max_net_notional NUMERIC(38,18) NOT NULL CHECK (max_net_notional >= 0),
    max_instrument_notional NUMERIC(38,18) NOT NULL CHECK (max_instrument_notional >= 0),
    max_leverage NUMERIC(38,18) NOT NULL CHECK (max_leverage > 0),
    minimum_available_margin NUMERIC(38,18) NOT NULL CHECK (minimum_available_margin >= 0),
    max_daily_loss NUMERIC(38,18) NOT NULL CHECK (max_daily_loss >= 0),
    max_drawdown_ratio NUMERIC(38,18) NOT NULL CHECK (max_drawdown_ratio >= 0 AND max_drawdown_ratio <= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, credential_id, account_scope, instrument_id, market_type, strategy_scope,
        policy_identity, policy_version, policy_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_qd_authoritative_risk_policies_select
    ON qd_authoritative_risk_policies
    (tenant_id, credential_id, account_scope, instrument_id, market_type, strategy_scope, observed_at DESC);

CREATE TABLE IF NOT EXISTS qd_authoritative_account_risk_facts (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL CHECK (contract_version = 'authoritative-risk-facts-v1'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    valuation_currency VARCHAR(32) NOT NULL CHECK (valuation_currency <> '' AND valuation_currency = UPPER(valuation_currency)),
    source_identity VARCHAR(160) NOT NULL CHECK (source_identity <> ''),
    source_version VARCHAR(160) NOT NULL CHECK (source_version <> ''),
    source_fingerprint VARCHAR(64) NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    max_age_seconds INTEGER NOT NULL CHECK (max_age_seconds >= 0),
    gross_notional NUMERIC(38,18) NOT NULL CHECK (gross_notional >= 0),
    net_notional NUMERIC(38,18) NOT NULL,
    instrument_notional NUMERIC(38,18) NOT NULL CHECK (instrument_notional >= 0),
    available_margin NUMERIC(38,18) NOT NULL CHECK (available_margin >= 0),
    equity NUMERIC(38,18) NOT NULL CHECK (equity > 0),
    peak_equity NUMERIC(38,18) NOT NULL CHECK (peak_equity >= equity),
    daily_realized_pnl NUMERIC(38,18) NOT NULL,
    account_facts_verified BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, credential_id, account_scope, instrument_id, market_type,
        source_identity, source_version, source_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_qd_authoritative_account_risk_facts_select
    ON qd_authoritative_account_risk_facts
    (tenant_id, credential_id, account_scope, instrument_id, market_type, observed_at DESC);

CREATE TABLE IF NOT EXISTS qd_authoritative_market_observations (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL CHECK (contract_version = 'authoritative-risk-facts-v1'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    valuation_currency VARCHAR(32) NOT NULL CHECK (valuation_currency <> '' AND valuation_currency = UPPER(valuation_currency)),
    price_type VARCHAR(8) NOT NULL CHECK (price_type IN ('LAST','MARK','INDEX')),
    price NUMERIC(38,18) NOT NULL CHECK (price > 0),
    source_identity VARCHAR(160) NOT NULL CHECK (source_identity <> ''),
    source_version VARCHAR(160) NOT NULL CHECK (source_version <> ''),
    source_fingerprint VARCHAR(64) NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    max_age_seconds INTEGER NOT NULL CHECK (max_age_seconds >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, credential_id, account_scope, instrument_id, market_type, valuation_currency,
        price_type, source_identity, source_version, source_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_qd_authoritative_market_observations_select
    ON qd_authoritative_market_observations
    (tenant_id, credential_id, account_scope, instrument_id, market_type, valuation_currency, price_type, observed_at DESC);

CREATE TABLE IF NOT EXISTS qd_authoritative_kill_switch_observations (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL CHECK (contract_version = 'authoritative-risk-facts-v1'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    strategy_scope VARCHAR(160) NOT NULL CHECK (strategy_scope <> ''),
    scope_kind VARCHAR(16) NOT NULL CHECK (scope_kind IN ('GLOBAL','ACCOUNT','STRATEGY')),
    source_identity VARCHAR(160) NOT NULL CHECK (source_identity <> ''),
    source_version VARCHAR(160) NOT NULL CHECK (source_version <> ''),
    source_fingerprint VARCHAR(64) NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    max_age_seconds INTEGER NOT NULL CHECK (max_age_seconds >= 0),
    switch_version BIGINT NOT NULL CHECK (switch_version >= 0),
    enabled BOOLEAN NOT NULL,
    mode VARCHAR(32) CHECK (mode IN ('OPEN_BLOCKED','ALL_NEW_COMMANDS_BLOCKED','EMERGENCY_REDUCE_ONLY')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((enabled AND mode IS NOT NULL) OR (NOT enabled AND mode IS NULL)),
    UNIQUE (tenant_id, credential_id, account_scope, strategy_scope, scope_kind,
        source_identity, source_version, source_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_qd_authoritative_kill_switch_select
    ON qd_authoritative_kill_switch_observations
    (tenant_id, credential_id, account_scope, strategy_scope, scope_kind, observed_at DESC);

CREATE TABLE IF NOT EXISTS qd_durable_risk_fact_provenance (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL CHECK (contract_version = 'authoritative-risk-facts-v1'),
    command_id UUID NOT NULL REFERENCES qd_durable_entry_specifications(command_id) ON DELETE RESTRICT,
    risk_input_snapshot_id UUID NOT NULL REFERENCES qd_durable_risk_input_snapshots(id) ON DELETE RESTRICT,
    risk_decision_id UUID NOT NULL REFERENCES qd_durable_risk_decisions(id) ON DELETE RESTRICT,
    source_kind VARCHAR(32) NOT NULL CHECK (source_kind IN ('POLICY','ACCOUNT','MARKET','KILL_SWITCH_GLOBAL','KILL_SWITCH_ACCOUNT','KILL_SWITCH_STRATEGY','RECONCILIATION','ACTIVE_RESERVATIONS')),
    source_identity VARCHAR(160) NOT NULL CHECK (source_identity <> ''),
    source_version VARCHAR(160) NOT NULL CHECK (source_version <> ''),
    source_fingerprint VARCHAR(64) NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    source_observed_at TIMESTAMPTZ NOT NULL,
    selection_anchor TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (source_observed_at <= selection_anchor),
    UNIQUE (risk_decision_id, source_kind),
    UNIQUE (command_id, risk_decision_id, source_kind, source_identity, source_version, source_fingerprint)
);

CREATE OR REPLACE FUNCTION qd_reject_authoritative_risk_fact_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'authoritative risk facts are append-only' USING ERRCODE = '55000';
END; $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_authoritative_risk_policies_append_only') THEN
        CREATE TRIGGER trg_qd_authoritative_risk_policies_append_only BEFORE UPDATE OR DELETE ON qd_authoritative_risk_policies FOR EACH ROW EXECUTE FUNCTION qd_reject_authoritative_risk_fact_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_authoritative_account_risk_facts_append_only') THEN
        CREATE TRIGGER trg_qd_authoritative_account_risk_facts_append_only BEFORE UPDATE OR DELETE ON qd_authoritative_account_risk_facts FOR EACH ROW EXECUTE FUNCTION qd_reject_authoritative_risk_fact_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_authoritative_market_observations_append_only') THEN
        CREATE TRIGGER trg_qd_authoritative_market_observations_append_only BEFORE UPDATE OR DELETE ON qd_authoritative_market_observations FOR EACH ROW EXECUTE FUNCTION qd_reject_authoritative_risk_fact_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_authoritative_kill_switch_observations_append_only') THEN
        CREATE TRIGGER trg_qd_authoritative_kill_switch_observations_append_only BEFORE UPDATE OR DELETE ON qd_authoritative_kill_switch_observations FOR EACH ROW EXECUTE FUNCTION qd_reject_authoritative_risk_fact_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_fact_provenance_append_only') THEN
        CREATE TRIGGER trg_qd_durable_risk_fact_provenance_append_only BEFORE UPDATE OR DELETE ON qd_durable_risk_fact_provenance FOR EACH ROW EXECUTE FUNCTION qd_reject_authoritative_risk_fact_mutation();
    END IF;
END $$;
