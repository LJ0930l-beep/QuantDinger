-- Phase 0 wave 2: hard-risk enforcement and outbox/projection persistence.
-- Expand-only. No runtime path is enabled by this migration.

-- qd_order_commands does not itself carry instrument/market facts.  This
-- non-partial unique index makes its command-level account scope referenceable
-- by an immutable risk decision without inventing those missing facts.
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_order_commands_command_scope
    ON qd_order_commands(id, tenant_id, credential_id, account_scope);

CREATE TABLE IF NOT EXISTS qd_risk_policy_snapshots (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    valuation_currency VARCHAR(20) NOT NULL,
    policy_version VARCHAR(96) NOT NULL,
    policy_hash VARCHAR(64) NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    max_gross_notional NUMERIC(38,18) NOT NULL CHECK (max_gross_notional >= 0),
    max_net_notional NUMERIC(38,18) NOT NULL CHECK (max_net_notional >= 0),
    max_instrument_notional NUMERIC(38,18) NOT NULL CHECK (max_instrument_notional >= 0),
    max_leverage NUMERIC(38,18) NOT NULL CHECK (max_leverage > 0),
    minimum_available_margin NUMERIC(38,18) NOT NULL CHECK (minimum_available_margin >= 0),
    max_daily_loss NUMERIC(38,18) NOT NULL CHECK (max_daily_loss >= 0),
    max_drawdown_ratio NUMERIC(38,18) NOT NULL CHECK (max_drawdown_ratio >= 0 AND max_drawdown_ratio <= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(id, tenant_id, credential_id, account_scope, instrument_id, market_type),
    UNIQUE(tenant_id, credential_id, account_scope, instrument_id, market_type, policy_version, policy_hash)
);

CREATE TABLE IF NOT EXISTS qd_risk_input_snapshots (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    input_version VARCHAR(96) NOT NULL,
    input_hash VARCHAR(64) NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    reconciliation_health VARCHAR(16) NOT NULL CHECK (reconciliation_health IN ('HEALTHY','DEGRADED','UNHEALTHY')),
    market_data_health VARCHAR(16) NOT NULL CHECK (market_data_health IN ('FRESH','STALE','UNKNOWN')),
    account_facts_verified BOOLEAN NOT NULL,
    global_kill_switch_version BIGINT NOT NULL CHECK (global_kill_switch_version >= 0),
    global_kill_switch_enabled BOOLEAN NOT NULL,
    global_kill_switch_mode VARCHAR(32),
    account_kill_switch_version BIGINT NOT NULL CHECK (account_kill_switch_version >= 0),
    account_kill_switch_enabled BOOLEAN NOT NULL,
    account_kill_switch_mode VARCHAR(32),
    strategy_kill_switch_version BIGINT NOT NULL CHECK (strategy_kill_switch_version >= 0),
    strategy_kill_switch_enabled BOOLEAN NOT NULL,
    strategy_kill_switch_mode VARCHAR(32),
    gross_notional NUMERIC(38,18) NOT NULL CHECK (gross_notional >= 0),
    net_notional NUMERIC(38,18) NOT NULL,
    instrument_notional NUMERIC(38,18) NOT NULL CHECK (instrument_notional >= 0),
    available_margin NUMERIC(38,18) NOT NULL CHECK (available_margin >= 0),
    equity NUMERIC(38,18) NOT NULL CHECK (equity > 0),
    peak_equity NUMERIC(38,18) NOT NULL CHECK (peak_equity >= equity),
    daily_realized_pnl NUMERIC(38,18) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(id, tenant_id, credential_id, account_scope, instrument_id, market_type),
    UNIQUE(tenant_id, credential_id, account_scope, instrument_id, market_type, input_version, input_hash)
);

CREATE TABLE IF NOT EXISTS qd_risk_decisions (
    id UUID PRIMARY KEY,
    command_id UUID NOT NULL REFERENCES qd_order_commands(id) ON DELETE RESTRICT,
    economic_order_id UUID NOT NULL REFERENCES qd_economic_orders(id) ON DELETE RESTRICT,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    action VARCHAR(20) NOT NULL CHECK (action IN ('OPEN','INCREASE','REDUCE','CLOSE','CANCEL','EMERGENCY_CLOSE','PROTECTION')),
    actor_type VARCHAR(16) NOT NULL CHECK (actor_type IN ('STRATEGY','HUMAN','AGENT','MCP','GRID','PROTECTION','ADMIN')),
    actor_id VARCHAR(160) NOT NULL,
    risk_effect VARCHAR(16) NOT NULL CHECK (risk_effect IN ('INCREASE_RISK','REDUCE_RISK','NEUTRAL')),
    policy_snapshot_id UUID NOT NULL,
    risk_input_snapshot_id UUID NOT NULL,
    decision VARCHAR(32) NOT NULL CHECK (decision IN ('ALLOW','DENY','ALLOW_RISK_REDUCING_ONLY','RECONCILIATION_REQUIRED')),
    decision_fingerprint VARCHAR(64) NOT NULL CHECK (decision_fingerprint ~ '^[0-9a-f]{64}$'),
    rejection_codes JSONB NOT NULL CHECK (jsonb_typeof(rejection_codes) = 'array'),
    projected_gross_notional NUMERIC(38,18) NOT NULL,
    projected_net_notional NUMERIC(38,18) NOT NULL,
    projected_instrument_notional NUMERIC(38,18) NOT NULL,
    projected_available_margin NUMERIC(38,18) NOT NULL,
    projected_leverage NUMERIC(38,18) NOT NULL CHECK (projected_leverage >= 0),
    projected_daily_loss NUMERIC(38,18) NOT NULL CHECK (projected_daily_loss >= 0),
    projected_drawdown_ratio NUMERIC(38,18) NOT NULL CHECK (projected_drawdown_ratio >= 0 AND projected_drawdown_ratio <= 1),
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    observed_at TIMESTAMPTZ NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(decision_fingerprint),
    UNIQUE(id, command_id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type),
    FOREIGN KEY(command_id, tenant_id, credential_id, account_scope)
        REFERENCES qd_order_commands(id, tenant_id, credential_id, account_scope) ON DELETE RESTRICT,
    FOREIGN KEY(economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_economic_orders(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT,
    FOREIGN KEY(policy_snapshot_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_risk_policy_snapshots(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT,
    FOREIGN KEY(risk_input_snapshot_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
        REFERENCES qd_risk_input_snapshots(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT
);

ALTER TABLE qd_risk_reservations
    ADD COLUMN IF NOT EXISTS decision_id UUID,
    ADD COLUMN IF NOT EXISTS instrument_id VARCHAR(100),
    ADD COLUMN IF NOT EXISTS market_type VARCHAR(20),
    ADD COLUMN IF NOT EXISTS action VARCHAR(20),
    ADD COLUMN IF NOT EXISTS policy_snapshot_id UUID,
    ADD COLUMN IF NOT EXISTS risk_input_snapshot_id UUID,
    ADD COLUMN IF NOT EXISTS enforcement_contract_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS reserved_gross_notional NUMERIC(38,18),
    ADD COLUMN IF NOT EXISTS reserved_net_notional NUMERIC(38,18),
    ADD COLUMN IF NOT EXISTS reserved_instrument_notional NUMERIC(38,18),
    ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(160);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_risk_reservations_enforcement_decision
    ON qd_risk_reservations(decision_id) WHERE decision_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_qd_risk_reservations_enforcement_complete') THEN
        ALTER TABLE qd_risk_reservations ADD CONSTRAINT chk_qd_risk_reservations_enforcement_complete CHECK (
            (decision_id IS NULL AND instrument_id IS NULL AND market_type IS NULL AND action IS NULL AND policy_snapshot_id IS NULL AND risk_input_snapshot_id IS NULL AND enforcement_contract_version IS NULL)
            OR (decision_id IS NOT NULL AND economic_order_id IS NOT NULL AND instrument_id IS NOT NULL AND market_type IS NOT NULL AND action IS NOT NULL AND policy_snapshot_id IS NOT NULL AND risk_input_snapshot_id IS NOT NULL AND enforcement_contract_version = 'hard-risk-enforcement-v1' AND reserved_gross_notional IS NOT NULL AND reserved_net_notional IS NOT NULL AND reserved_instrument_notional IS NOT NULL AND correlation_id IS NOT NULL AND correlation_id <> '')
        ) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_qd_risk_reservations_enforcement_decision') THEN
        ALTER TABLE qd_risk_reservations ADD CONSTRAINT fk_qd_risk_reservations_enforcement_decision
            FOREIGN KEY(decision_id, command_id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
            REFERENCES qd_risk_decisions(id, command_id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_qd_risk_reservations_enforcement_policy_snapshot') THEN
        ALTER TABLE qd_risk_reservations ADD CONSTRAINT fk_qd_risk_reservations_enforcement_policy_snapshot
            FOREIGN KEY(policy_snapshot_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
            REFERENCES qd_risk_policy_snapshots(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_qd_risk_reservations_enforcement_input_snapshot') THEN
        ALTER TABLE qd_risk_reservations ADD CONSTRAINT fk_qd_risk_reservations_enforcement_input_snapshot
            FOREIGN KEY(risk_input_snapshot_id, tenant_id, credential_id, account_scope, instrument_id, market_type)
            REFERENCES qd_risk_input_snapshots(id, tenant_id, credential_id, account_scope, instrument_id, market_type) ON DELETE RESTRICT NOT VALID;
    END IF;
END $$;

ALTER TABLE qd_transactional_outbox
    ADD COLUMN IF NOT EXISTS schema_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS payload_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS event_fingerprint VARCHAR(64),
    ADD COLUMN IF NOT EXISTS lease_fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (lease_fencing_token >= 0),
    ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    ADD COLUMN IF NOT EXISTS last_error VARCHAR(512);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_transactional_outbox_fingerprint
    ON qd_transactional_outbox(event_fingerprint) WHERE event_fingerprint IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_transactional_outbox_canonical_identity
    ON qd_transactional_outbox(aggregate_type, aggregate_id, aggregate_version, event_type, schema_version)
    WHERE schema_version IS NOT NULL;

CREATE TABLE IF NOT EXISTS qd_projection_generations (
    id UUID PRIMARY KEY,
    consumer_name VARCHAR(160) NOT NULL,
    build_fingerprint VARCHAR(64) NOT NULL CHECK (build_fingerprint ~ '^[0-9a-f]{64}$'),
    state VARCHAR(16) NOT NULL CHECK (state IN ('BUILDING','READY','FAILED')),
    source_high_watermark BIGINT NOT NULL CHECK (source_high_watermark >= 0),
    processed_high_watermark BIGINT NOT NULL DEFAULT -1 CHECK (processed_high_watermark >= -1),
    expected_event_count BIGINT NOT NULL DEFAULT 0 CHECK (expected_event_count >= 0),
    applied_event_count BIGINT NOT NULL DEFAULT 0 CHECK (applied_event_count >= 0),
    is_current BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    promoted_at TIMESTAMPTZ,
    failure_reason VARCHAR(512),
    UNIQUE(consumer_name, build_fingerprint),
    CHECK ((state = 'BUILDING' AND completed_at IS NULL AND promoted_at IS NULL AND failure_reason IS NULL)
        OR (state = 'READY' AND completed_at IS NOT NULL AND failure_reason IS NULL)
        OR (state = 'FAILED' AND completed_at IS NULL AND promoted_at IS NULL AND failure_reason IS NOT NULL)),
    CHECK (NOT is_current OR state = 'READY')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_projection_generations_current_consumer
    ON qd_projection_generations(consumer_name) WHERE is_current;

CREATE TABLE IF NOT EXISTS qd_projection_checkpoints (
    id UUID PRIMARY KEY,
    generation_id UUID NOT NULL REFERENCES qd_projection_generations(id) ON DELETE RESTRICT,
    consumer_name VARCHAR(160) NOT NULL,
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id UUID NOT NULL,
    last_applied_version BIGINT NOT NULL DEFAULT -1 CHECK (last_applied_version >= -1),
    last_event_id UUID,
    last_payload_hash VARCHAR(64),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(generation_id, consumer_name, aggregate_type, aggregate_id),
    CHECK ((last_applied_version = -1 AND last_event_id IS NULL AND last_payload_hash IS NULL) OR (last_applied_version >= 0 AND last_event_id IS NOT NULL AND last_payload_hash ~ '^[0-9a-f]{64}$'))
);

CREATE OR REPLACE FUNCTION qd_reject_wave2_risk_fact_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'immutable wave2 risk fact cannot be %', TG_OP USING ERRCODE = '55000';
END; $$;

CREATE OR REPLACE FUNCTION qd_guard_risk_reservation_enforcement_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    -- Existing non-enforcement reservations keep their current repository
    -- contract.  Once the enforcement facts exist, all later changes are
    -- constrained by the canonical state/version transition below.
    IF OLD.enforcement_contract_version IS NULL THEN
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.command_id,NEW.economic_order_id,NEW.tenant_id,NEW.credential_id,NEW.account_scope,NEW.reservation_kind,NEW.currency,NEW.reserved_notional,NEW.reserved_margin,NEW.reserved_position_qty,NEW.limits_snapshot_json,NEW.risk_input_hash,NEW.decision_id,NEW.instrument_id,NEW.market_type,NEW.action,NEW.policy_snapshot_id,NEW.risk_input_snapshot_id,NEW.enforcement_contract_version)
       IS DISTINCT FROM ROW(OLD.id,OLD.command_id,OLD.economic_order_id,OLD.tenant_id,OLD.credential_id,OLD.account_scope,OLD.reservation_kind,OLD.currency,OLD.reserved_notional,OLD.reserved_margin,OLD.reserved_position_qty,OLD.limits_snapshot_json,OLD.risk_input_hash,OLD.decision_id,OLD.instrument_id,OLD.market_type,OLD.action,OLD.policy_snapshot_id,OLD.risk_input_snapshot_id,OLD.enforcement_contract_version) THEN
        RAISE EXCEPTION 'risk reservation immutable facts cannot change' USING ERRCODE = '55000';
    END IF;
    IF NEW.state = OLD.state OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'risk reservation update requires one state transition and version increment' USING ERRCODE = '55000';
    END IF;
    IF NOT ((OLD.state = 'ACTIVE' AND NEW.state IN ('CONSUMED','RELEASED','EXPIRED'))) THEN
        RAISE EXCEPTION 'risk reservation state transition is invalid' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_guard_transactional_outbox_immutable_facts()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    IF ROW(NEW.event_id, NEW.aggregate_type, NEW.aggregate_id, NEW.aggregate_version,
           NEW.event_type, NEW.payload_json, NEW.schema_version, NEW.payload_hash,
           NEW.event_fingerprint)
       IS DISTINCT FROM ROW(OLD.event_id, OLD.aggregate_type, OLD.aggregate_id,
           OLD.aggregate_version, OLD.event_type, OLD.payload_json, OLD.schema_version,
           OLD.payload_hash, OLD.event_fingerprint) THEN
        RAISE EXCEPTION 'transactional outbox immutable facts cannot change' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_reject_transactional_outbox_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'transactional outbox facts are append-only' USING ERRCODE = '55000';
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_risk_policy_snapshots_append_only') THEN CREATE TRIGGER trg_qd_risk_policy_snapshots_append_only BEFORE UPDATE OR DELETE ON qd_risk_policy_snapshots FOR EACH ROW EXECUTE FUNCTION qd_reject_wave2_risk_fact_mutation(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_risk_input_snapshots_append_only') THEN CREATE TRIGGER trg_qd_risk_input_snapshots_append_only BEFORE UPDATE OR DELETE ON qd_risk_input_snapshots FOR EACH ROW EXECUTE FUNCTION qd_reject_wave2_risk_fact_mutation(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_risk_decisions_append_only') THEN CREATE TRIGGER trg_qd_risk_decisions_append_only BEFORE UPDATE OR DELETE ON qd_risk_decisions FOR EACH ROW EXECUTE FUNCTION qd_reject_wave2_risk_fact_mutation(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_risk_reservations_enforcement_guard') THEN CREATE TRIGGER trg_qd_risk_reservations_enforcement_guard BEFORE UPDATE ON qd_risk_reservations FOR EACH ROW EXECUTE FUNCTION qd_guard_risk_reservation_enforcement_update(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_transactional_outbox_immutable_facts') THEN CREATE TRIGGER trg_qd_transactional_outbox_immutable_facts BEFORE UPDATE ON qd_transactional_outbox FOR EACH ROW EXECUTE FUNCTION qd_guard_transactional_outbox_immutable_facts(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_transactional_outbox_append_only') THEN CREATE TRIGGER trg_qd_transactional_outbox_append_only BEFORE DELETE ON qd_transactional_outbox FOR EACH ROW EXECUTE FUNCTION qd_reject_transactional_outbox_delete(); END IF;
END $$;
