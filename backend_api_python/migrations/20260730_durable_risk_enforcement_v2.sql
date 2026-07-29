-- Durable-entry hard-risk enforcement V2.  Expand-only and independent from
-- hard-risk-enforcement-v1: it references only durable-entry specifications.
-- Typed columns are the authoritative replay facts; JSON columns are audit
-- mirrors and never substitute for the typed columns below.

CREATE TABLE IF NOT EXISTS qd_durable_risk_policy_snapshots (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL
        CHECK (contract_version = 'durable-risk-enforcement-v2'),
    command_id UUID NOT NULL,
    economic_order_id UUID NOT NULL,
    durable_entry_contract_version VARCHAR(32) NOT NULL
        CHECK (durable_entry_contract_version = 'canonical-entry-v2'),
    economic_fingerprint VARCHAR(64) NOT NULL CHECK (economic_fingerprint ~ '^[0-9a-f]{64}$'),
    request_fingerprint VARCHAR(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    action VARCHAR(20) NOT NULL CHECK (action IN ('OPEN','INCREASE','REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION')),
    risk_effect VARCHAR(16) NOT NULL CHECK (risk_effect IN ('INCREASE_RISK','REDUCE_RISK')),
    actor_type VARCHAR(16) NOT NULL CHECK (actor_type IN ('STRATEGY','HUMAN','AGENT','MCP','GRID','PROTECTION','ADMIN')),
    actor_id VARCHAR(160) NOT NULL CHECK (actor_id <> ''),
    source VARCHAR(16) NOT NULL CHECK (source IN ('REST','MANUAL','STRATEGY','AGENT','MCP','GRID','PROTECTION')),
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('DISABLED','PAPER','SHADOW')),
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    entry_occurred_at TIMESTAMPTZ NOT NULL,
    scope_fingerprint VARCHAR(64) NOT NULL CHECK (scope_fingerprint ~ '^[0-9a-f]{64}$'),
    audit_fingerprint VARCHAR(64) NOT NULL CHECK (audit_fingerprint ~ '^[0-9a-f]{64}$'),

    policy_hash VARCHAR(64) NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    policy_version VARCHAR(160) NOT NULL CHECK (policy_version <> ''),
    valuation_currency VARCHAR(32) NOT NULL CHECK (valuation_currency <> '' AND valuation_currency = UPPER(valuation_currency)),
    max_gross_notional NUMERIC(38,18) NOT NULL CHECK (max_gross_notional >= 0),
    max_net_notional NUMERIC(38,18) NOT NULL CHECK (max_net_notional >= 0),
    max_instrument_notional NUMERIC(38,18) NOT NULL CHECK (max_instrument_notional >= 0),
    max_leverage NUMERIC(38,18) NOT NULL CHECK (max_leverage > 0),
    minimum_available_margin NUMERIC(38,18) NOT NULL CHECK (minimum_available_margin >= 0),
    max_daily_loss NUMERIC(38,18) NOT NULL CHECK (max_daily_loss >= 0),
    max_drawdown_ratio NUMERIC(38,18) NOT NULL CHECK (max_drawdown_ratio >= 0 AND max_drawdown_ratio <= 1),
    policy_payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    FOREIGN KEY (command_id) REFERENCES qd_durable_entry_specifications(command_id) ON DELETE RESTRICT,
    CHECK ((action IN ('OPEN','INCREASE') AND risk_effect = 'INCREASE_RISK')
        OR (action IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION') AND risk_effect = 'REDUCE_RISK')),
    CHECK ((source = 'REST' AND actor_type = 'HUMAN')
        OR (source = 'MANUAL' AND actor_type = 'HUMAN')
        OR (source = 'STRATEGY' AND actor_type = 'STRATEGY')
        OR (source = 'AGENT' AND actor_type = 'AGENT')
        OR (source = 'MCP' AND actor_type = 'MCP')
        OR (source = 'GRID' AND actor_type = 'GRID')
        OR (source = 'PROTECTION' AND actor_type = 'PROTECTION')),
    CHECK (source <> 'PROTECTION' OR (action IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION') AND risk_effect = 'REDUCE_RISK')),
    UNIQUE (id, contract_version, command_id, economic_order_id, durable_entry_contract_version,
        economic_fingerprint, request_fingerprint, tenant_id, credential_id, account_scope,
        instrument_id, market_type, action, risk_effect, actor_type, actor_id, source, mode,
        correlation_id, entry_occurred_at, scope_fingerprint, audit_fingerprint)
);

CREATE TABLE IF NOT EXISTS qd_durable_risk_input_snapshots (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL
        CHECK (contract_version = 'durable-risk-enforcement-v2'),
    command_id UUID NOT NULL,
    economic_order_id UUID NOT NULL,
    durable_entry_contract_version VARCHAR(32) NOT NULL
        CHECK (durable_entry_contract_version = 'canonical-entry-v2'),
    economic_fingerprint VARCHAR(64) NOT NULL CHECK (economic_fingerprint ~ '^[0-9a-f]{64}$'),
    request_fingerprint VARCHAR(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    action VARCHAR(20) NOT NULL CHECK (action IN ('OPEN','INCREASE','REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION')),
    risk_effect VARCHAR(16) NOT NULL CHECK (risk_effect IN ('INCREASE_RISK','REDUCE_RISK')),
    actor_type VARCHAR(16) NOT NULL CHECK (actor_type IN ('STRATEGY','HUMAN','AGENT','MCP','GRID','PROTECTION','ADMIN')),
    actor_id VARCHAR(160) NOT NULL CHECK (actor_id <> ''),
    source VARCHAR(16) NOT NULL CHECK (source IN ('REST','MANUAL','STRATEGY','AGENT','MCP','GRID','PROTECTION')),
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('DISABLED','PAPER','SHADOW')),
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    entry_occurred_at TIMESTAMPTZ NOT NULL,
    scope_fingerprint VARCHAR(64) NOT NULL CHECK (scope_fingerprint ~ '^[0-9a-f]{64}$'),
    audit_fingerprint VARCHAR(64) NOT NULL CHECK (audit_fingerprint ~ '^[0-9a-f]{64}$'),

    input_hash VARCHAR(64) NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    input_version VARCHAR(160) NOT NULL CHECK (input_version <> ''),
    valuation_currency VARCHAR(32) NOT NULL CHECK (valuation_currency <> '' AND valuation_currency = UPPER(valuation_currency)),
    gross_notional NUMERIC(38,18) NOT NULL CHECK (gross_notional >= 0),
    net_notional NUMERIC(38,18) NOT NULL,
    instrument_notional NUMERIC(38,18) NOT NULL CHECK (instrument_notional >= 0),
    available_margin NUMERIC(38,18) NOT NULL CHECK (available_margin >= 0),
    equity NUMERIC(38,18) NOT NULL CHECK (equity > 0),
    peak_equity NUMERIC(38,18) NOT NULL CHECK (peak_equity >= equity),
    daily_realized_pnl NUMERIC(38,18) NOT NULL,
    reconciliation_health VARCHAR(16) NOT NULL CHECK (reconciliation_health IN ('HEALTHY','DEGRADED','UNHEALTHY')),
    market_data_health VARCHAR(16) NOT NULL CHECK (market_data_health IN ('FRESH','STALE','UNKNOWN')),
    account_facts_verified BOOLEAN NOT NULL,
    global_kill_switch_version BIGINT NOT NULL CHECK (global_kill_switch_version >= 0),
    global_kill_switch_enabled BOOLEAN NOT NULL,
    global_kill_switch_mode VARCHAR(32) CHECK (global_kill_switch_mode IN ('OPEN_BLOCKED','ALL_NEW_COMMANDS_BLOCKED','EMERGENCY_REDUCE_ONLY')),
    account_kill_switch_version BIGINT NOT NULL CHECK (account_kill_switch_version >= 0),
    account_kill_switch_enabled BOOLEAN NOT NULL,
    account_kill_switch_mode VARCHAR(32) CHECK (account_kill_switch_mode IN ('OPEN_BLOCKED','ALL_NEW_COMMANDS_BLOCKED','EMERGENCY_REDUCE_ONLY')),
    strategy_kill_switch_version BIGINT NOT NULL CHECK (strategy_kill_switch_version >= 0),
    strategy_kill_switch_enabled BOOLEAN NOT NULL,
    strategy_kill_switch_mode VARCHAR(32) CHECK (strategy_kill_switch_mode IN ('OPEN_BLOCKED','ALL_NEW_COMMANDS_BLOCKED','EMERGENCY_REDUCE_ONLY')),
    exposure_payload_json JSONB NOT NULL,
    kill_switch_payload_json JSONB NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    FOREIGN KEY (command_id) REFERENCES qd_durable_entry_specifications(command_id) ON DELETE RESTRICT,
    CHECK ((action IN ('OPEN','INCREASE') AND risk_effect = 'INCREASE_RISK')
        OR (action IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION') AND risk_effect = 'REDUCE_RISK')),
    CHECK ((source = 'REST' AND actor_type = 'HUMAN')
        OR (source = 'MANUAL' AND actor_type = 'HUMAN')
        OR (source = 'STRATEGY' AND actor_type = 'STRATEGY')
        OR (source = 'AGENT' AND actor_type = 'AGENT')
        OR (source = 'MCP' AND actor_type = 'MCP')
        OR (source = 'GRID' AND actor_type = 'GRID')
        OR (source = 'PROTECTION' AND actor_type = 'PROTECTION')),
    CHECK (source <> 'PROTECTION' OR (action IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION') AND risk_effect = 'REDUCE_RISK')),
    CHECK ((global_kill_switch_enabled AND global_kill_switch_mode IS NOT NULL) OR (NOT global_kill_switch_enabled AND global_kill_switch_mode IS NULL)),
    CHECK ((account_kill_switch_enabled AND account_kill_switch_mode IS NOT NULL) OR (NOT account_kill_switch_enabled AND account_kill_switch_mode IS NULL)),
    CHECK ((strategy_kill_switch_enabled AND strategy_kill_switch_mode IS NOT NULL) OR (NOT strategy_kill_switch_enabled AND strategy_kill_switch_mode IS NULL)),
    UNIQUE (id, contract_version, command_id, economic_order_id, durable_entry_contract_version,
        economic_fingerprint, request_fingerprint, tenant_id, credential_id, account_scope,
        instrument_id, market_type, action, risk_effect, actor_type, actor_id, source, mode,
        correlation_id, entry_occurred_at, scope_fingerprint, audit_fingerprint)
);

CREATE TABLE IF NOT EXISTS qd_durable_risk_decisions (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL
        CHECK (contract_version = 'durable-risk-enforcement-v2'),
    command_id UUID NOT NULL REFERENCES qd_durable_entry_specifications(command_id) ON DELETE RESTRICT,
    economic_order_id UUID NOT NULL,
    durable_entry_contract_version VARCHAR(32) NOT NULL
        CHECK (durable_entry_contract_version = 'canonical-entry-v2'),
    economic_fingerprint VARCHAR(64) NOT NULL CHECK (economic_fingerprint ~ '^[0-9a-f]{64}$'),
    request_fingerprint VARCHAR(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    action VARCHAR(20) NOT NULL CHECK (action IN ('OPEN','INCREASE','REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION')),
    risk_effect VARCHAR(16) NOT NULL CHECK (risk_effect IN ('INCREASE_RISK','REDUCE_RISK')),
    actor_type VARCHAR(16) NOT NULL CHECK (actor_type IN ('STRATEGY','HUMAN','AGENT','MCP','GRID','PROTECTION','ADMIN')),
    actor_id VARCHAR(160) NOT NULL CHECK (actor_id <> ''),
    source VARCHAR(16) NOT NULL CHECK (source IN ('REST','MANUAL','STRATEGY','AGENT','MCP','GRID','PROTECTION')),
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('DISABLED','PAPER','SHADOW')),
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    entry_occurred_at TIMESTAMPTZ NOT NULL,
    scope_fingerprint VARCHAR(64) NOT NULL CHECK (scope_fingerprint ~ '^[0-9a-f]{64}$'),
    audit_fingerprint VARCHAR(64) NOT NULL CHECK (audit_fingerprint ~ '^[0-9a-f]{64}$'),

    policy_snapshot_id UUID NOT NULL,
    input_snapshot_id UUID NOT NULL,
    policy_hash VARCHAR(64) NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    input_hash VARCHAR(64) NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    decision_fingerprint VARCHAR(64) NOT NULL CHECK (decision_fingerprint ~ '^[0-9a-f]{64}$'),
    allowed BOOLEAN NOT NULL,
    decision_status VARCHAR(32) NOT NULL CHECK (decision_status IN ('ALLOW','DENY','RECONCILIATION_REQUIRED')),
    rejection_codes_json JSONB NOT NULL CHECK (jsonb_typeof(rejection_codes_json) = 'array'),
    projected_gross_notional NUMERIC(38,18) NOT NULL CHECK (projected_gross_notional >= 0),
    projected_net_notional NUMERIC(38,18) NOT NULL,
    projected_instrument_notional NUMERIC(38,18) NOT NULL CHECK (projected_instrument_notional >= 0),
    projected_available_margin NUMERIC(38,18) NOT NULL,
    projected_leverage NUMERIC(38,18) NOT NULL CHECK (projected_leverage >= 0),
    projected_daily_loss NUMERIC(38,18) NOT NULL CHECK (projected_daily_loss >= 0),
    projected_drawdown_ratio NUMERIC(38,18) NOT NULL CHECK (projected_drawdown_ratio >= 0 AND projected_drawdown_ratio <= 1),
    projected_risk_payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    FOREIGN KEY (policy_snapshot_id, contract_version, command_id, economic_order_id,
        durable_entry_contract_version, economic_fingerprint, request_fingerprint, tenant_id,
        credential_id, account_scope, instrument_id, market_type, action, risk_effect,
        actor_type, actor_id, source, mode, correlation_id, entry_occurred_at,
        scope_fingerprint, audit_fingerprint)
        REFERENCES qd_durable_risk_policy_snapshots (id, contract_version, command_id,
        economic_order_id, durable_entry_contract_version, economic_fingerprint,
        request_fingerprint, tenant_id, credential_id, account_scope, instrument_id,
        market_type, action, risk_effect, actor_type, actor_id, source, mode,
        correlation_id, entry_occurred_at, scope_fingerprint, audit_fingerprint) ON DELETE RESTRICT,
    FOREIGN KEY (input_snapshot_id, contract_version, command_id, economic_order_id,
        durable_entry_contract_version, economic_fingerprint, request_fingerprint, tenant_id,
        credential_id, account_scope, instrument_id, market_type, action, risk_effect,
        actor_type, actor_id, source, mode, correlation_id, entry_occurred_at,
        scope_fingerprint, audit_fingerprint)
        REFERENCES qd_durable_risk_input_snapshots (id, contract_version, command_id,
        economic_order_id, durable_entry_contract_version, economic_fingerprint,
        request_fingerprint, tenant_id, credential_id, account_scope, instrument_id,
        market_type, action, risk_effect, actor_type, actor_id, source, mode,
        correlation_id, entry_occurred_at, scope_fingerprint, audit_fingerprint) ON DELETE RESTRICT,
    CHECK ((action IN ('OPEN','INCREASE') AND risk_effect = 'INCREASE_RISK')
        OR (action IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION') AND risk_effect = 'REDUCE_RISK')),
    CHECK ((allowed AND decision_status = 'ALLOW')
        OR (NOT allowed AND decision_status IN ('DENY','RECONCILIATION_REQUIRED'))),
    UNIQUE (id, contract_version, command_id, economic_order_id, durable_entry_contract_version,
        economic_fingerprint, request_fingerprint, tenant_id, credential_id, account_scope,
        instrument_id, market_type, action, risk_effect, actor_type, actor_id, source, mode,
        correlation_id, entry_occurred_at, scope_fingerprint, audit_fingerprint)
);

CREATE TABLE IF NOT EXISTS qd_durable_risk_reservations (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL
        CHECK (contract_version = 'durable-risk-enforcement-v2'),
    command_id UUID NOT NULL,
    economic_order_id UUID NOT NULL,
    durable_entry_contract_version VARCHAR(32) NOT NULL
        CHECK (durable_entry_contract_version = 'canonical-entry-v2'),
    economic_fingerprint VARCHAR(64) NOT NULL CHECK (economic_fingerprint ~ '^[0-9a-f]{64}$'),
    request_fingerprint VARCHAR(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    action VARCHAR(20) NOT NULL CHECK (action IN ('OPEN','INCREASE')),
    risk_effect VARCHAR(16) NOT NULL CHECK (risk_effect = 'INCREASE_RISK'),
    actor_type VARCHAR(16) NOT NULL CHECK (actor_type IN ('STRATEGY','HUMAN','AGENT','MCP','GRID','PROTECTION','ADMIN')),
    actor_id VARCHAR(160) NOT NULL CHECK (actor_id <> ''),
    source VARCHAR(16) NOT NULL CHECK (source IN ('REST','MANUAL','STRATEGY','AGENT','MCP','GRID','PROTECTION')),
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('DISABLED','PAPER','SHADOW')),
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    entry_occurred_at TIMESTAMPTZ NOT NULL,
    scope_fingerprint VARCHAR(64) NOT NULL CHECK (scope_fingerprint ~ '^[0-9a-f]{64}$'),
    audit_fingerprint VARCHAR(64) NOT NULL CHECK (audit_fingerprint ~ '^[0-9a-f]{64}$'),

    decision_id UUID NOT NULL,
    reservation_hash VARCHAR(64) NOT NULL CHECK (reservation_hash ~ '^[0-9a-f]{64}$'),
    valuation_currency VARCHAR(32) NOT NULL CHECK (valuation_currency <> '' AND valuation_currency = UPPER(valuation_currency)),
    reserved_gross_notional NUMERIC(38,18) NOT NULL CHECK (reserved_gross_notional >= 0),
    reserved_net_notional NUMERIC(38,18) NOT NULL,
    reserved_instrument_notional NUMERIC(38,18) NOT NULL CHECK (reserved_instrument_notional >= 0),
    reserved_margin NUMERIC(38,18) NOT NULL CHECK (reserved_margin >= 0),
    state VARCHAR(16) NOT NULL CHECK (state = 'ACTIVE'),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    FOREIGN KEY (decision_id, contract_version, command_id, economic_order_id,
        durable_entry_contract_version, economic_fingerprint, request_fingerprint, tenant_id,
        credential_id, account_scope, instrument_id, market_type, action, risk_effect,
        actor_type, actor_id, source, mode, correlation_id, entry_occurred_at,
        scope_fingerprint, audit_fingerprint)
        REFERENCES qd_durable_risk_decisions (id, contract_version, command_id,
        economic_order_id, durable_entry_contract_version, economic_fingerprint,
        request_fingerprint, tenant_id, credential_id, account_scope, instrument_id,
        market_type, action, risk_effect, actor_type, actor_id, source, mode,
        correlation_id, entry_occurred_at, scope_fingerprint, audit_fingerprint) ON DELETE RESTRICT,
    CHECK (action IN ('OPEN','INCREASE') AND risk_effect = 'INCREASE_RISK')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_durable_risk_reservations_active_decision
    ON qd_durable_risk_reservations (decision_id) WHERE state = 'ACTIVE';

CREATE OR REPLACE FUNCTION qd_reject_durable_risk_v2_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'durable risk enforcement v2 facts are append-only' USING ERRCODE = '55000';
END; $$;

CREATE OR REPLACE FUNCTION qd_assert_durable_risk_v2_reservation_allowed()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    decision_allowed BOOLEAN;
    decision_status_value VARCHAR(32);
BEGIN
    SELECT allowed, decision_status
      INTO decision_allowed, decision_status_value
      FROM qd_durable_risk_decisions
     WHERE id = NEW.decision_id
     FOR KEY SHARE;
    IF NOT FOUND OR NOT decision_allowed OR decision_status_value <> 'ALLOW' THEN
        RAISE EXCEPTION 'durable risk reservation requires an ALLOW decision' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_assert_durable_risk_v2_scope_matches_entry()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1
      FROM qd_durable_entry_specifications entry_specification
     WHERE entry_specification.command_id = NEW.command_id
       AND entry_specification.contract_version = NEW.durable_entry_contract_version
       AND entry_specification.economic_order_id = NEW.economic_order_id
       AND entry_specification.economic_fingerprint = NEW.economic_fingerprint
       AND entry_specification.request_fingerprint = NEW.request_fingerprint
       AND entry_specification.tenant_id = NEW.tenant_id
       AND entry_specification.credential_id = NEW.credential_id
       AND entry_specification.account_scope = NEW.account_scope
       AND entry_specification.instrument_id = NEW.instrument_id
       AND entry_specification.market_type = NEW.market_type
       AND entry_specification.action = NEW.action
       AND entry_specification.risk_effect = NEW.risk_effect
       AND entry_specification.actor_type = NEW.actor_type
       AND entry_specification.actor_id = NEW.actor_id
       AND entry_specification.source = NEW.source
       AND entry_specification.mode = NEW.mode
       AND entry_specification.correlation_id = NEW.correlation_id
       AND entry_specification.occurred_at = NEW.entry_occurred_at;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'durable risk v2 scope does not match durable entry specification' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END; $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_policy_snapshots_append_only') THEN
        CREATE TRIGGER trg_qd_durable_risk_policy_snapshots_append_only
        BEFORE UPDATE OR DELETE ON qd_durable_risk_policy_snapshots
        FOR EACH ROW EXECUTE FUNCTION qd_reject_durable_risk_v2_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_input_snapshots_append_only') THEN
        CREATE TRIGGER trg_qd_durable_risk_input_snapshots_append_only
        BEFORE UPDATE OR DELETE ON qd_durable_risk_input_snapshots
        FOR EACH ROW EXECUTE FUNCTION qd_reject_durable_risk_v2_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_decisions_append_only') THEN
        CREATE TRIGGER trg_qd_durable_risk_decisions_append_only
        BEFORE UPDATE OR DELETE ON qd_durable_risk_decisions
        FOR EACH ROW EXECUTE FUNCTION qd_reject_durable_risk_v2_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_reservations_append_only') THEN
        CREATE TRIGGER trg_qd_durable_risk_reservations_append_only
        BEFORE UPDATE OR DELETE ON qd_durable_risk_reservations
        FOR EACH ROW EXECUTE FUNCTION qd_reject_durable_risk_v2_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_reservations_allow_decision') THEN
        CREATE TRIGGER trg_qd_durable_risk_reservations_allow_decision
        BEFORE INSERT ON qd_durable_risk_reservations
        FOR EACH ROW EXECUTE FUNCTION qd_assert_durable_risk_v2_reservation_allowed();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_policy_snapshots_scope_entry') THEN
        CREATE TRIGGER trg_qd_durable_risk_policy_snapshots_scope_entry
        BEFORE INSERT ON qd_durable_risk_policy_snapshots
        FOR EACH ROW EXECUTE FUNCTION qd_assert_durable_risk_v2_scope_matches_entry();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_input_snapshots_scope_entry') THEN
        CREATE TRIGGER trg_qd_durable_risk_input_snapshots_scope_entry
        BEFORE INSERT ON qd_durable_risk_input_snapshots
        FOR EACH ROW EXECUTE FUNCTION qd_assert_durable_risk_v2_scope_matches_entry();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_decisions_scope_entry') THEN
        CREATE TRIGGER trg_qd_durable_risk_decisions_scope_entry
        BEFORE INSERT ON qd_durable_risk_decisions
        FOR EACH ROW EXECUTE FUNCTION qd_assert_durable_risk_v2_scope_matches_entry();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_risk_reservations_scope_entry') THEN
        CREATE TRIGGER trg_qd_durable_risk_reservations_scope_entry
        BEFORE INSERT ON qd_durable_risk_reservations
        FOR EACH ROW EXECUTE FUNCTION qd_assert_durable_risk_v2_scope_matches_entry();
    END IF;
END $$;
