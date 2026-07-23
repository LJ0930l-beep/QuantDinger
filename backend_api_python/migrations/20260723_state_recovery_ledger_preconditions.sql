-- Phase 0 precondition: append-only state recovery and lossless fill-fee facts.
-- This migration is expand-only. It does not wire any runtime trading path.

ALTER TABLE qd_order_state_events
    ADD COLUMN IF NOT EXISTS expected_version BIGINT,
    ADD COLUMN IF NOT EXISTS resulting_version BIGINT,
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(180),
    ADD COLUMN IF NOT EXISTS event_fingerprint VARCHAR(128),
    ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(160),
    ADD COLUMN IF NOT EXISTS canonical_payload_json JSONB;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_order_state_events_versions'
    ) THEN
        ALTER TABLE qd_order_state_events
            ADD CONSTRAINT chk_qd_order_state_events_versions
            CHECK (
                (expected_version IS NULL AND resulting_version IS NULL)
                OR (
                    expected_version >= 0
                    AND resulting_version = expected_version + 1
                )
            ) NOT VALID;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_order_state_events_idempotency
    ON qd_order_state_events(economic_order_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_order_state_events_fingerprint
    ON qd_order_state_events(economic_order_id, event_fingerprint)
    WHERE event_fingerprint IS NOT NULL;

CREATE TABLE IF NOT EXISTS qd_venue_capability_snapshots (
    id UUID PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    capability_version VARCHAR(64) NOT NULL CHECK (capability_version <> ''),
    profile_hash VARCHAR(128) NOT NULL CHECK (profile_hash <> ''),
    accepts_external_client_order_id BOOLEAN NOT NULL,
    can_generate_safe_client_order_id BOOLEAN NOT NULL,
    query_by_exchange_order_id BOOLEAN NOT NULL,
    query_by_client_order_id BOOLEAN NOT NULL,
    list_order_fills BOOLEAN NOT NULL,
    stable_fill_id BOOLEAN NOT NULL,
    client_id_max_length INTEGER CHECK (client_id_max_length IS NULL OR client_id_max_length > 0),
    client_id_pattern VARCHAR(256),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(exchange, market_type, capability_version, profile_hash)
);

CREATE TABLE IF NOT EXISTS qd_submission_recovery_policy_snapshots (
    id UUID PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    policy_version VARCHAR(64) NOT NULL CHECK (policy_version <> ''),
    policy_hash VARCHAR(128) NOT NULL CHECK (policy_hash <> ''),
    client_id_query_authoritative BOOLEAN NOT NULL,
    order_history_authoritative BOOLEAN NOT NULL,
    fill_history_authoritative BOOLEAN NOT NULL,
    not_found_min_query_count INTEGER NOT NULL CHECK (not_found_min_query_count >= 1),
    not_found_grace_seconds INTEGER NOT NULL CHECK (not_found_grace_seconds >= 0),
    not_found_action VARCHAR(24) NOT NULL CHECK (not_found_action IN ('KEEP_UNKNOWN','CONFIRM_ABSENT')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(exchange, market_type, policy_version, policy_hash)
);

ALTER TABLE qd_submission_attempts
    ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_event_seq BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS venue_capability_snapshot_id UUID,
    ADD COLUMN IF NOT EXISTS recovery_policy_snapshot_id UUID,
    ADD COLUMN IF NOT EXISTS client_id_algorithm_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS broker_prefix_normalization_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS broker_prefix VARCHAR(64),
    ADD COLUMN IF NOT EXISTS recovery_evidence_hash VARCHAR(128) NOT NULL DEFAULT '';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_submission_attempts_version_sequence'
    ) THEN
        ALTER TABLE qd_submission_attempts
            ADD CONSTRAINT chk_qd_submission_attempts_version_sequence
            CHECK (version >= 0 AND last_event_seq >= 0) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_qd_submission_attempts_id_economic_order'
    ) THEN
        ALTER TABLE qd_submission_attempts
            ADD CONSTRAINT uq_qd_submission_attempts_id_economic_order
            UNIQUE (id, economic_order_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_qd_submission_attempts_capability_snapshot'
    ) THEN
        ALTER TABLE qd_submission_attempts
            ADD CONSTRAINT fk_qd_submission_attempts_capability_snapshot
            FOREIGN KEY (venue_capability_snapshot_id)
            REFERENCES qd_venue_capability_snapshots(id) ON DELETE RESTRICT NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_qd_submission_attempts_recovery_policy_snapshot'
    ) THEN
        ALTER TABLE qd_submission_attempts
            ADD CONSTRAINT fk_qd_submission_attempts_recovery_policy_snapshot
            FOREIGN KEY (recovery_policy_snapshot_id)
            REFERENCES qd_submission_recovery_policy_snapshots(id) ON DELETE RESTRICT NOT VALID;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS qd_submission_attempt_state_events (
    id UUID PRIMARY KEY,
    attempt_id UUID NOT NULL,
    economic_order_id UUID NOT NULL REFERENCES qd_economic_orders(id) ON DELETE RESTRICT,
    event_seq BIGINT NOT NULL CHECK (event_seq >= 1),
    expected_version BIGINT NOT NULL CHECK (expected_version >= 0),
    resulting_version BIGINT NOT NULL CHECK (resulting_version = expected_version + 1),
    from_state VARCHAR(20) CHECK (from_state IS NULL OR from_state IN ('READY','SUBMITTING','ACKED','UNKNOWN','CONFIRMED_ABSENT','REJECTED')),
    to_state VARCHAR(20) NOT NULL CHECK (to_state IN ('READY','SUBMITTING','ACKED','UNKNOWN','CONFIRMED_ABSENT','REJECTED')),
    reason_code VARCHAR(64) NOT NULL CHECK (reason_code <> ''),
    actor_type VARCHAR(16) NOT NULL CHECK (actor_type <> ''),
    correlation_id VARCHAR(160) NOT NULL DEFAULT '',
    idempotency_key VARCHAR(180) NOT NULL CHECK (idempotency_key <> ''),
    event_fingerprint VARCHAR(128) NOT NULL CHECK (event_fingerprint <> ''),
    evidence_hash VARCHAR(128) NOT NULL DEFAULT '',
    canonical_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (attempt_id, economic_order_id)
        REFERENCES qd_submission_attempts(id, economic_order_id) ON DELETE RESTRICT,
    UNIQUE(attempt_id, event_seq),
    UNIQUE(attempt_id, idempotency_key),
    UNIQUE(attempt_id, event_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_qd_submission_attempt_state_events_order
    ON qd_submission_attempt_state_events(economic_order_id, occurred_at);

CREATE TABLE IF NOT EXISTS qd_ledger_valuation_evidence (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    exchange VARCHAR(50) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    asset VARCHAR(20) NOT NULL CHECK (asset <> ''),
    valuation_ccy VARCHAR(20) NOT NULL CHECK (valuation_ccy <> ''),
    price NUMERIC(38,18) NOT NULL CHECK (price > 0),
    evidence_source VARCHAR(32) NOT NULL CHECK (evidence_source IN ('VENUE','ORACLE','MANUAL_APPROVED')),
    observed_at TIMESTAMPTZ NOT NULL,
    payload_hash VARCHAR(128) NOT NULL CHECK (payload_hash <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, credential_id, account_scope, exchange, market_type, asset, valuation_ccy, evidence_source, observed_at, payload_hash)
);

ALTER TABLE qd_exchange_fill_events
    ADD COLUMN IF NOT EXISTS quote_quantity_origin VARCHAR(16),
    ADD COLUMN IF NOT EXISTS fee_summary_state VARCHAR(24) NOT NULL DEFAULT 'UNSPECIFIED';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_exchange_fill_events_quote_quantity_origin'
    ) THEN
        ALTER TABLE qd_exchange_fill_events
            ADD CONSTRAINT chk_qd_exchange_fill_events_quote_quantity_origin
            CHECK (quote_quantity_origin IS NULL OR quote_quantity_origin IN ('VENUE','DERIVED')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_exchange_fill_events_fee_summary_state'
    ) THEN
        ALTER TABLE qd_exchange_fill_events
            ADD CONSTRAINT chk_qd_exchange_fill_events_fee_summary_state
            CHECK (fee_summary_state IN ('UNSPECIFIED','NONE','SINGLE_COMPONENT','MULTI_COMPONENT')) NOT VALID;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS qd_exchange_fill_fee_components (
    fill_event_id UUID NOT NULL REFERENCES qd_exchange_fill_events(id) ON DELETE RESTRICT,
    fee_seq INTEGER NOT NULL CHECK (fee_seq >= 1),
    asset VARCHAR(20) NOT NULL CHECK (asset <> ''),
    amount NUMERIC(38,18) NOT NULL CHECK (amount > 0),
    fee_quote_amount NUMERIC(38,18),
    valuation_evidence_id UUID REFERENCES qd_ledger_valuation_evidence(id) ON DELETE RESTRICT,
    raw_component_hash VARCHAR(128) NOT NULL CHECK (raw_component_hash <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(fill_event_id, fee_seq),
    UNIQUE(fill_event_id, raw_component_hash),
    CHECK (fee_quote_amount IS NULL OR fee_quote_amount >= 0)
);
CREATE INDEX IF NOT EXISTS idx_qd_exchange_fill_fee_components_asset
    ON qd_exchange_fill_fee_components(asset, fill_event_id);
