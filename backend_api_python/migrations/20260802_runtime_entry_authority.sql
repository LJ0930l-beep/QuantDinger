-- REF-01C: persisted, immutable authority facts for Runtime Entry V1.
-- Expand-only.  These tables validate ingress scope and position subjects;
-- they neither create an exchange client nor authorise execution.

CREATE TABLE IF NOT EXISTS qd_runtime_entry_scope_bindings (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL
        CHECK (contract_version = 'runtime-entry-authority-v1'),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    exchange_id VARCHAR(50) NOT NULL
        CHECK (exchange_id <> '' AND exchange_id = LOWER(exchange_id)),
    source_identity VARCHAR(160) NOT NULL CHECK (source_identity <> ''),
    source_version VARCHAR(160) NOT NULL CHECK (source_version <> ''),
    source_fingerprint VARCHAR(64) NOT NULL
        CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, credential_id),
    UNIQUE (tenant_id, credential_id, account_scope, exchange_id,
        source_identity, source_version, source_fingerprint)
);

CREATE TABLE IF NOT EXISTS qd_runtime_entry_instrument_authorities (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL
        CHECK (contract_version = 'runtime-entry-authority-v1'),
    scope_binding_id UUID NOT NULL REFERENCES qd_runtime_entry_scope_bindings(id) ON DELETE RESTRICT,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    exchange_id VARCHAR(50) NOT NULL
        CHECK (exchange_id <> '' AND exchange_id = LOWER(exchange_id)),
    instrument_id VARCHAR(100) NOT NULL
        CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL
        CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    instrument_rule_snapshot_id UUID NOT NULL
        REFERENCES qd_instrument_rule_snapshots(id) ON DELETE RESTRICT,
    source_identity VARCHAR(160) NOT NULL CHECK (source_identity <> ''),
    source_version VARCHAR(160) NOT NULL CHECK (source_version <> ''),
    source_fingerprint VARCHAR(64) NOT NULL
        CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scope_binding_id, instrument_id, market_type),
    UNIQUE (id, tenant_id, credential_id, account_scope, instrument_id, market_type)
);

CREATE TABLE IF NOT EXISTS qd_runtime_entry_position_subjects (
    id UUID PRIMARY KEY,
    contract_version VARCHAR(64) NOT NULL
        CHECK (contract_version = 'runtime-entry-authority-v1'),
    scope_binding_id UUID NOT NULL REFERENCES qd_runtime_entry_scope_bindings(id) ON DELETE RESTRICT,
    instrument_authority_id UUID NOT NULL
        REFERENCES qd_runtime_entry_instrument_authorities(id) ON DELETE RESTRICT,
    reconciliation_checkpoint_id UUID NOT NULL
        REFERENCES qd_reconciliation_checkpoints(id) ON DELETE RESTRICT,
    position_projection_id UUID NOT NULL
        REFERENCES qd_position_projections(id) ON DELETE RESTRICT,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    exchange_id VARCHAR(50) NOT NULL
        CHECK (exchange_id <> '' AND exchange_id = LOWER(exchange_id)),
    instrument_id VARCHAR(100) NOT NULL
        CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL
        CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    position_side VARCHAR(8) NOT NULL CHECK (position_side IN ('LONG','SHORT')),
    source_fingerprint VARCHAR(64) NOT NULL
        CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instrument_authority_id, position_side, position_projection_id,
        reconciliation_checkpoint_id),
    UNIQUE (id, tenant_id, credential_id, account_scope, instrument_id, market_type, position_side)
);

CREATE TABLE IF NOT EXISTS qd_runtime_entry_ingresses (
    command_id UUID PRIMARY KEY
        REFERENCES qd_durable_entry_specifications(command_id) ON DELETE RESTRICT,
    contract_version VARCHAR(64) NOT NULL
        CHECK (contract_version = 'runtime-entry-authority-v1'),
    scope_binding_id UUID NOT NULL REFERENCES qd_runtime_entry_scope_bindings(id) ON DELETE RESTRICT,
    instrument_authority_id UUID NOT NULL
        REFERENCES qd_runtime_entry_instrument_authorities(id) ON DELETE RESTRICT,
    position_subject_id UUID REFERENCES qd_runtime_entry_position_subjects(id) ON DELETE RESTRICT,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    instrument_id VARCHAR(100) NOT NULL
        CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    market_type VARCHAR(20) NOT NULL
        CHECK (market_type <> '' AND market_type = LOWER(market_type)),
    action VARCHAR(20) NOT NULL CHECK (action IN (
        'OPEN','INCREASE','REDUCE','CLOSE','CANCEL','EMERGENCY_CLOSE','PROTECTION')),
    actor_type VARCHAR(16) NOT NULL CHECK (actor_type IN (
        'STRATEGY','HUMAN','AGENT','MCP','GRID','PROTECTION','ADMIN')),
    actor_id VARCHAR(160) NOT NULL CHECK (actor_id <> ''),
    source VARCHAR(16) NOT NULL CHECK (source IN (
        'REST','MANUAL','STRATEGY','AGENT','MCP','GRID','PROTECTION')),
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('DISABLED','PAPER','SHADOW')),
    idempotency_key VARCHAR(160) NOT NULL CHECK (idempotency_key <> ''),
    economic_fingerprint VARCHAR(64) NOT NULL
        CHECK (economic_fingerprint ~ '^[0-9a-f]{64}$'),
    request_fingerprint VARCHAR(64) NOT NULL
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, credential_id, account_scope, idempotency_key, contract_version)
);

CREATE INDEX IF NOT EXISTS idx_qd_runtime_entry_instrument_authorities_scope
    ON qd_runtime_entry_instrument_authorities
        (tenant_id, credential_id, account_scope, instrument_id, market_type);
CREATE INDEX IF NOT EXISTS idx_qd_runtime_entry_position_subjects_lookup
    ON qd_runtime_entry_position_subjects
        (tenant_id, credential_id, account_scope, instrument_id, market_type, position_side);
CREATE INDEX IF NOT EXISTS idx_qd_runtime_entry_ingresses_scope
    ON qd_runtime_entry_ingresses
        (tenant_id, credential_id, account_scope, created_at DESC);

CREATE OR REPLACE FUNCTION qd_reject_runtime_entry_authority_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'runtime entry authority facts are append-only' USING ERRCODE = '55000';
END; $$;

CREATE OR REPLACE FUNCTION qd_assert_runtime_entry_scope_binding()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    credential_user_id INTEGER;
    credential_exchange_id VARCHAR(50);
BEGIN
    SELECT user_id, LOWER(exchange_id)
      INTO credential_user_id, credential_exchange_id
      FROM qd_exchange_credentials
     WHERE id = NEW.credential_id;
    IF NOT FOUND
       OR credential_user_id <> NEW.tenant_id
       OR credential_exchange_id <> NEW.exchange_id THEN
        RAISE EXCEPTION 'runtime entry scope binding does not match credential ownership'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_assert_runtime_entry_instrument_authority()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    binding_tenant_id INTEGER;
    binding_credential_id INTEGER;
    binding_account_scope VARCHAR(160);
    binding_exchange_id VARCHAR(50);
    rule_exchange VARCHAR(50);
    rule_market_type VARCHAR(20);
    rule_instrument_id VARCHAR(100);
BEGIN
    SELECT tenant_id, credential_id, account_scope, exchange_id
      INTO binding_tenant_id, binding_credential_id, binding_account_scope, binding_exchange_id
      FROM qd_runtime_entry_scope_bindings WHERE id = NEW.scope_binding_id;
    SELECT LOWER(exchange), market_type, instrument_id
      INTO rule_exchange, rule_market_type, rule_instrument_id
      FROM qd_instrument_rule_snapshots WHERE id = NEW.instrument_rule_snapshot_id;
    IF binding_tenant_id IS NULL
       OR rule_exchange IS NULL
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.exchange_id)
          IS DISTINCT FROM ROW(binding_tenant_id, binding_credential_id, binding_account_scope, binding_exchange_id)
       OR ROW(NEW.exchange_id, NEW.market_type, NEW.instrument_id)
          IS DISTINCT FROM ROW(rule_exchange, rule_market_type, rule_instrument_id) THEN
        RAISE EXCEPTION 'runtime entry instrument authority scope does not match binding or rule snapshot'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_assert_runtime_entry_position_subject()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    binding_record qd_runtime_entry_scope_bindings%ROWTYPE;
    authority_record qd_runtime_entry_instrument_authorities%ROWTYPE;
    checkpoint_record qd_reconciliation_checkpoints%ROWTYPE;
    projection_record qd_position_projections%ROWTYPE;
BEGIN
    SELECT * INTO binding_record FROM qd_runtime_entry_scope_bindings WHERE id = NEW.scope_binding_id;
    SELECT * INTO authority_record FROM qd_runtime_entry_instrument_authorities WHERE id = NEW.instrument_authority_id;
    SELECT * INTO checkpoint_record FROM qd_reconciliation_checkpoints WHERE id = NEW.reconciliation_checkpoint_id;
    SELECT * INTO projection_record FROM qd_position_projections WHERE id = NEW.position_projection_id;
    IF binding_record.id IS NULL
       OR authority_record.id IS NULL
       OR checkpoint_record.id IS NULL
       OR projection_record.id IS NULL
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.exchange_id)
          IS DISTINCT FROM ROW(binding_record.tenant_id, binding_record.credential_id, binding_record.account_scope, binding_record.exchange_id)
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.exchange_id, NEW.instrument_id, NEW.market_type)
          IS DISTINCT FROM ROW(authority_record.tenant_id, authority_record.credential_id, authority_record.account_scope, authority_record.exchange_id, authority_record.instrument_id, authority_record.market_type)
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.exchange_id, NEW.instrument_id, NEW.market_type)
          IS DISTINCT FROM ROW(checkpoint_record.tenant_id, checkpoint_record.credential_id, checkpoint_record.account_scope, LOWER(checkpoint_record.exchange), checkpoint_record.instrument_id, checkpoint_record.market_type)
       OR checkpoint_record.status <> 'HEALTHY'
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.instrument_id, NEW.position_side)
          IS DISTINCT FROM ROW(projection_record.tenant_id, projection_record.credential_id, projection_record.account_scope, projection_record.instrument_id, projection_record.side)
       OR projection_record.quantity <= 0 THEN
        RAISE EXCEPTION 'runtime entry position subject does not match persisted position and reconciliation facts'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_assert_runtime_entry_ingress()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    entry_record qd_durable_entry_specifications%ROWTYPE;
    binding_record qd_runtime_entry_scope_bindings%ROWTYPE;
    authority_record qd_runtime_entry_instrument_authorities%ROWTYPE;
    position_record qd_runtime_entry_position_subjects%ROWTYPE;
BEGIN
    SELECT * INTO entry_record FROM qd_durable_entry_specifications WHERE command_id = NEW.command_id;
    SELECT * INTO binding_record FROM qd_runtime_entry_scope_bindings WHERE id = NEW.scope_binding_id;
    SELECT * INTO authority_record FROM qd_runtime_entry_instrument_authorities WHERE id = NEW.instrument_authority_id;
    IF entry_record.command_id IS NULL
       OR binding_record.id IS NULL
       OR authority_record.id IS NULL
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope)
          IS DISTINCT FROM ROW(binding_record.tenant_id, binding_record.credential_id, binding_record.account_scope)
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.instrument_id, NEW.market_type)
          IS DISTINCT FROM ROW(authority_record.tenant_id, authority_record.credential_id, authority_record.account_scope, authority_record.instrument_id, authority_record.market_type)
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.instrument_id, NEW.market_type,
           NEW.action, NEW.actor_type, NEW.actor_id, NEW.source, NEW.mode, NEW.idempotency_key,
           NEW.economic_fingerprint, NEW.request_fingerprint, NEW.correlation_id, NEW.occurred_at)
          IS DISTINCT FROM ROW(entry_record.tenant_id, entry_record.credential_id, entry_record.account_scope, entry_record.instrument_id, entry_record.market_type,
              entry_record.action, entry_record.actor_type, entry_record.actor_id, entry_record.source, entry_record.mode, entry_record.idempotency_key,
              entry_record.economic_fingerprint, entry_record.request_fingerprint, entry_record.correlation_id, entry_record.occurred_at)
       OR (entry_record.action IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION') AND NEW.position_subject_id IS NULL)
       OR (entry_record.action NOT IN ('REDUCE','CLOSE','EMERGENCY_CLOSE','PROTECTION') AND NEW.position_subject_id IS NOT NULL) THEN
        RAISE EXCEPTION 'runtime entry ingress does not match durable entry authority facts'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.position_subject_id IS NOT NULL THEN
        SELECT * INTO position_record FROM qd_runtime_entry_position_subjects WHERE id = NEW.position_subject_id;
        IF position_record.id IS NULL
           OR ROW(position_record.tenant_id, position_record.credential_id, position_record.account_scope,
               position_record.instrument_id, position_record.market_type)
              IS DISTINCT FROM ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.instrument_id, NEW.market_type)
           OR position_record.id::text <> entry_record.target_position_id THEN
            RAISE EXCEPTION 'runtime entry ingress position subject does not match durable entry'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END; $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_runtime_entry_scope_bindings_validate') THEN
        CREATE TRIGGER trg_qd_runtime_entry_scope_bindings_validate BEFORE INSERT ON qd_runtime_entry_scope_bindings FOR EACH ROW EXECUTE FUNCTION qd_assert_runtime_entry_scope_binding();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_runtime_entry_instrument_authorities_validate') THEN
        CREATE TRIGGER trg_qd_runtime_entry_instrument_authorities_validate BEFORE INSERT ON qd_runtime_entry_instrument_authorities FOR EACH ROW EXECUTE FUNCTION qd_assert_runtime_entry_instrument_authority();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_runtime_entry_position_subjects_validate') THEN
        CREATE TRIGGER trg_qd_runtime_entry_position_subjects_validate BEFORE INSERT ON qd_runtime_entry_position_subjects FOR EACH ROW EXECUTE FUNCTION qd_assert_runtime_entry_position_subject();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_runtime_entry_ingresses_validate') THEN
        CREATE TRIGGER trg_qd_runtime_entry_ingresses_validate BEFORE INSERT ON qd_runtime_entry_ingresses FOR EACH ROW EXECUTE FUNCTION qd_assert_runtime_entry_ingress();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_runtime_entry_scope_bindings_append_only') THEN
        CREATE TRIGGER trg_qd_runtime_entry_scope_bindings_append_only BEFORE UPDATE OR DELETE ON qd_runtime_entry_scope_bindings FOR EACH ROW EXECUTE FUNCTION qd_reject_runtime_entry_authority_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_runtime_entry_instrument_authorities_append_only') THEN
        CREATE TRIGGER trg_qd_runtime_entry_instrument_authorities_append_only BEFORE UPDATE OR DELETE ON qd_runtime_entry_instrument_authorities FOR EACH ROW EXECUTE FUNCTION qd_reject_runtime_entry_authority_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_runtime_entry_position_subjects_append_only') THEN
        CREATE TRIGGER trg_qd_runtime_entry_position_subjects_append_only BEFORE UPDATE OR DELETE ON qd_runtime_entry_position_subjects FOR EACH ROW EXECUTE FUNCTION qd_reject_runtime_entry_authority_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_runtime_entry_ingresses_append_only') THEN
        CREATE TRIGGER trg_qd_runtime_entry_ingresses_append_only BEFORE UPDATE OR DELETE ON qd_runtime_entry_ingresses FOR EACH ROW EXECUTE FUNCTION qd_reject_runtime_entry_authority_mutation();
    END IF;
END $$;
