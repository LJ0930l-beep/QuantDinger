-- RF-01B: persisted, versioned conversion rules for authoritative risk demand.
-- Expand-only.  Provider code supports only explicit linear quote conversion.

CREATE TABLE IF NOT EXISTS qd_authoritative_instrument_risk_rules (
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
    quantity_to_quote_multiplier NUMERIC(38,18) NOT NULL CHECK (quantity_to_quote_multiplier > 0),
    initial_margin_ratio NUMERIC(38,18) NOT NULL CHECK (initial_margin_ratio > 0 AND initial_margin_ratio <= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, credential_id, account_scope, instrument_id, market_type,
        source_identity, source_version, source_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_qd_authoritative_instrument_risk_rules_select
    ON qd_authoritative_instrument_risk_rules
    (tenant_id, credential_id, account_scope, instrument_id, market_type, observed_at DESC);

ALTER TABLE qd_reconciliation_checkpoints
    ADD COLUMN IF NOT EXISTS risk_max_age_seconds INTEGER;
ALTER TABLE qd_reconciliation_checkpoints
    DROP CONSTRAINT IF EXISTS chk_qd_reconciliation_checkpoints_risk_max_age;
ALTER TABLE qd_reconciliation_checkpoints
    ADD CONSTRAINT chk_qd_reconciliation_checkpoints_risk_max_age
    CHECK (risk_max_age_seconds IS NULL OR risk_max_age_seconds >= 0);

ALTER TABLE qd_authoritative_market_observations
    ADD COLUMN IF NOT EXISTS market_data_health VARCHAR(16);
ALTER TABLE qd_authoritative_market_observations
    DROP CONSTRAINT IF EXISTS chk_qd_authoritative_market_observations_health;
ALTER TABLE qd_authoritative_market_observations
    ADD CONSTRAINT chk_qd_authoritative_market_observations_health
    CHECK (market_data_health IS NULL OR market_data_health IN ('FRESH','STALE','UNKNOWN'));

ALTER TABLE qd_durable_risk_fact_provenance
    DROP CONSTRAINT IF EXISTS qd_durable_risk_fact_provenance_source_kind_check;
ALTER TABLE qd_durable_risk_fact_provenance
    ADD CONSTRAINT qd_durable_risk_fact_provenance_source_kind_check
    CHECK (source_kind IN ('POLICY','ACCOUNT','MARKET','KILL_SWITCH_GLOBAL','KILL_SWITCH_ACCOUNT','KILL_SWITCH_STRATEGY','RECONCILIATION','ACTIVE_RESERVATIONS','INSTRUMENT_RULES'));

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_authoritative_instrument_risk_rules_append_only') THEN
        CREATE TRIGGER trg_qd_authoritative_instrument_risk_rules_append_only
        BEFORE UPDATE OR DELETE ON qd_authoritative_instrument_risk_rules
        FOR EACH ROW EXECUTE FUNCTION qd_reject_authoritative_risk_fact_mutation();
    END IF;
END $$;
