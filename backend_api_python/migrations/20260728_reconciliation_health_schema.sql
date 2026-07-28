-- Phase 0 wave 4: deterministic reconciliation facts and derived health only.
-- Expand-only. No exchange client, worker, scheduler, runtime or order mutation is enabled.

CREATE TABLE IF NOT EXISTS qd_reconciliation_runs (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    venue VARCHAR(64) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    instrument_id VARCHAR(100),
    asset_scope VARCHAR(24),
    reconciliation_contract_version VARCHAR(64) NOT NULL CHECK (reconciliation_contract_version = 'reconciliation-v1'),
    local_generation_id UUID NOT NULL REFERENCES qd_projection_generations(id) ON DELETE RESTRICT,
    local_consumer_name VARCHAR(160) NOT NULL,
    local_generation_build_fingerprint VARCHAR(64) NOT NULL CHECK (local_generation_build_fingerprint ~ '^[0-9a-f]{64}$'),
    local_checkpoint_watermark BIGINT NOT NULL CHECK (local_checkpoint_watermark >= 0),
    external_observation_identity VARCHAR(64) NOT NULL,
    external_observation_version VARCHAR(64) NOT NULL,
    external_observation_fingerprint VARCHAR(64) NOT NULL CHECK (external_observation_fingerprint ~ '^[0-9a-f]{64}$'),
    local_observed_at TIMESTAMPTZ NOT NULL,
    external_observed_at TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    policy_version VARCHAR(64) NOT NULL,
    warning_degrades_health BOOLEAN NOT NULL,
    quantity_absolute NUMERIC(38,18) NOT NULL CHECK (quantity_absolute >= 0),
    monetary_absolute NUMERIC(38,18) NOT NULL CHECK (monetary_absolute >= 0),
    max_observation_age_seconds BIGINT NOT NULL CHECK (max_observation_age_seconds >= 0),
    policy_fingerprint VARCHAR(64) NOT NULL CHECK (policy_fingerprint ~ '^[0-9a-f]{64}$'),
    build_fingerprint VARCHAR(64) NOT NULL CHECK (build_fingerprint ~ '^[0-9a-f]{64}$'),
    replay_fingerprint VARCHAR(64) CHECK (replay_fingerprint ~ '^[0-9a-f]{64}$'),
    discrepancy_count INTEGER NOT NULL DEFAULT 0 CHECK (discrepancy_count >= 0),
    state VARCHAR(16) NOT NULL CHECK (state IN ('BUILDING','COMPLETE','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    failure_reason VARCHAR(512),
    CHECK (instrument_id IS NOT NULL OR asset_scope IS NOT NULL),
    CHECK (local_observed_at <= as_of AND external_observed_at <= as_of),
    CHECK ((state = 'BUILDING' AND replay_fingerprint IS NULL AND completed_at IS NULL AND failure_reason IS NULL)
        OR (state = 'COMPLETE' AND replay_fingerprint IS NOT NULL AND completed_at IS NOT NULL AND failure_reason IS NULL)
        OR (state = 'FAILED' AND replay_fingerprint IS NULL AND completed_at IS NOT NULL AND failure_reason IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_reconciliation_runs_authoritative_identity
    ON qd_reconciliation_runs (tenant_id, credential_id, account_scope, venue, market_type,
        COALESCE(instrument_id, ''), COALESCE(asset_scope, ''), build_fingerprint);

CREATE TABLE IF NOT EXISTS qd_reconciliation_discrepancies (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES qd_reconciliation_runs(id) ON DELETE RESTRICT,
    fact_name VARCHAR(100) NOT NULL,
    discrepancy_kind VARCHAR(32) NOT NULL CHECK (discrepancy_kind IN (
        'MISSING_LOCAL','MISSING_EXTERNAL','ORDER_STATE_MISMATCH','UNKNOWN_SUBMISSION',
        'FILL_MISSING','FILL_UNEXPECTED','POSITION_MISMATCH','BALANCE_MISMATCH',
        'FEE_MISMATCH','STALE_LOCAL','STALE_EXTERNAL','SCOPE_MISMATCH','VERSION_MISMATCH','UNSUPPORTED_FACT')),
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('INFO','WARNING','BLOCKING')),
    local_value NUMERIC(38,18),
    local_value_kind VARCHAR(16) CHECK (local_value_kind IN ('QUANTITY','MONETARY','RATIO')),
    local_asset VARCHAR(24),
    external_value NUMERIC(38,18),
    external_value_kind VARCHAR(16) CHECK (external_value_kind IN ('QUANTITY','MONETARY','RATIO')),
    external_asset VARCHAR(24),
    detail VARCHAR(160) NOT NULL,
    discrepancy_fingerprint VARCHAR(64) NOT NULL CHECK (discrepancy_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, discrepancy_fingerprint),
    CHECK ((local_value IS NULL AND local_value_kind IS NULL AND local_asset IS NULL)
       OR (local_value IS NOT NULL AND local_value_kind IS NOT NULL
           AND ((local_value_kind = 'RATIO' AND local_asset IS NULL)
             OR (local_value_kind IN ('QUANTITY','MONETARY') AND local_asset IS NOT NULL)))),
    CHECK ((external_value IS NULL AND external_value_kind IS NULL AND external_asset IS NULL)
       OR (external_value IS NOT NULL AND external_value_kind IS NOT NULL
           AND ((external_value_kind = 'RATIO' AND external_asset IS NULL)
             OR (external_value_kind IN ('QUANTITY','MONETARY') AND external_asset IS NOT NULL))))
);

-- qd_reconciliation_checkpoints already exists from PR-02.  These canonical
-- columns are additive and leave legacy cursor checkpoints readable.
ALTER TABLE qd_reconciliation_checkpoints
    ADD COLUMN IF NOT EXISTS reconciliation_run_id UUID,
    ADD COLUMN IF NOT EXISTS reconciliation_checkpoint_version BIGINT,
    ADD COLUMN IF NOT EXISTS result_fingerprint VARCHAR(64),
    ADD COLUMN IF NOT EXISTS policy_fingerprint VARCHAR(64);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_qd_reconciliation_checkpoints_canonical_run') THEN
        ALTER TABLE qd_reconciliation_checkpoints
            ADD CONSTRAINT fk_qd_reconciliation_checkpoints_canonical_run
            FOREIGN KEY (reconciliation_run_id) REFERENCES qd_reconciliation_runs(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_qd_reconciliation_checkpoints_canonical_complete') THEN
        ALTER TABLE qd_reconciliation_checkpoints
            ADD CONSTRAINT chk_qd_reconciliation_checkpoints_canonical_complete CHECK (
                (reconciliation_run_id IS NULL AND reconciliation_checkpoint_version IS NULL
                    AND result_fingerprint IS NULL AND policy_fingerprint IS NULL)
                OR (reconciliation_run_id IS NOT NULL AND reconciliation_checkpoint_version IS NOT NULL
                    AND reconciliation_checkpoint_version >= 0
                    AND result_fingerprint ~ '^[0-9a-f]{64}$'
                    AND policy_fingerprint ~ '^[0-9a-f]{64}$')
            ) NOT VALID;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_reconciliation_checkpoints_canonical_version
    ON qd_reconciliation_checkpoints (reconciliation_run_id, reconciliation_checkpoint_version)
    WHERE reconciliation_run_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_reconciliation_checkpoints_canonical_result
    ON qd_reconciliation_checkpoints (reconciliation_run_id, result_fingerprint)
    WHERE reconciliation_run_id IS NOT NULL;

CREATE OR REPLACE FUNCTION qd_guard_reconciliation_run_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE actual_count INTEGER;
BEGIN
    IF ROW(NEW.id, NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.venue,
           NEW.instrument_id, NEW.asset_scope, NEW.reconciliation_contract_version,
           NEW.local_generation_id, NEW.local_consumer_name, NEW.local_generation_build_fingerprint,
           NEW.local_checkpoint_watermark, NEW.external_observation_identity,
           NEW.external_observation_version, NEW.external_observation_fingerprint,
           NEW.local_observed_at, NEW.external_observed_at, NEW.as_of, NEW.correlation_id,
           NEW.policy_version, NEW.warning_degrades_health, NEW.quantity_absolute,
           NEW.monetary_absolute, NEW.max_observation_age_seconds, NEW.policy_fingerprint,
           NEW.build_fingerprint, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.tenant_id, OLD.credential_id, OLD.account_scope, OLD.venue,
           OLD.instrument_id, OLD.asset_scope, OLD.reconciliation_contract_version,
           OLD.local_generation_id, OLD.local_consumer_name, OLD.local_generation_build_fingerprint,
           OLD.local_checkpoint_watermark, OLD.external_observation_identity,
           OLD.external_observation_version, OLD.external_observation_fingerprint,
           OLD.local_observed_at, OLD.external_observed_at, OLD.as_of, OLD.correlation_id,
           OLD.policy_version, OLD.warning_degrades_health, OLD.quantity_absolute,
           OLD.monetary_absolute, OLD.max_observation_age_seconds, OLD.policy_fingerprint,
           OLD.build_fingerprint, OLD.created_at) THEN
        RAISE EXCEPTION 'reconciliation run immutable facts cannot change' USING ERRCODE = '55000';
    END IF;
    IF OLD.state <> 'BUILDING' OR NEW.state NOT IN ('COMPLETE','FAILED') THEN
        RAISE EXCEPTION 'reconciliation run transition is invalid' USING ERRCODE = '55000';
    END IF;
    IF NEW.state = 'COMPLETE' THEN
        SELECT COUNT(*) INTO actual_count FROM qd_reconciliation_discrepancies WHERE run_id = NEW.id;
        IF actual_count <> NEW.discrepancy_count THEN
            RAISE EXCEPTION 'reconciliation discrepancy count is incomplete' USING ERRCODE = '55000';
        END IF;
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_reject_reconciliation_run_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'reconciliation runs are append-only' USING ERRCODE = '55000';
END; $$;

CREATE OR REPLACE FUNCTION qd_guard_reconciliation_discrepancy_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ DECLARE current_state VARCHAR(16); BEGIN
    SELECT state INTO current_state FROM qd_reconciliation_runs WHERE id = NEW.run_id FOR KEY SHARE;
    IF current_state IS NULL OR current_state <> 'BUILDING' THEN
        RAISE EXCEPTION 'reconciliation discrepancies require a BUILDING run' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_reject_reconciliation_discrepancy_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'reconciliation discrepancies are append-only' USING ERRCODE = '55000';
END; $$;

CREATE OR REPLACE FUNCTION qd_reject_reconciliation_checkpoint_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP = 'DELETE' AND OLD.reconciliation_run_id IS NOT NULL THEN
        RAISE EXCEPTION 'canonical reconciliation checkpoints are append-only' USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    IF OLD.reconciliation_run_id IS NOT NULL THEN
        IF ROW(NEW.tenant_id, NEW.credential_id, NEW.exchange, NEW.market_type,
               NEW.account_scope, NEW.instrument_id)
           IS DISTINCT FROM ROW(OLD.tenant_id, OLD.credential_id, OLD.exchange, OLD.market_type,
               OLD.account_scope, OLD.instrument_id) THEN
            RAISE EXCEPTION 'canonical reconciliation checkpoint scope cannot change' USING ERRCODE = '55000';
        END IF;
        IF NEW.version <> OLD.version + 1
           OR NEW.reconciliation_checkpoint_version <> OLD.reconciliation_checkpoint_version + 1 THEN
            RAISE EXCEPTION 'canonical reconciliation checkpoint version must increase by one' USING ERRCODE = '55000';
        END IF;
    END IF;
    RETURN NEW;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_reconciliation_runs_guard') THEN CREATE TRIGGER trg_qd_reconciliation_runs_guard BEFORE UPDATE ON qd_reconciliation_runs FOR EACH ROW EXECUTE FUNCTION qd_guard_reconciliation_run_update(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_reconciliation_runs_append_only') THEN CREATE TRIGGER trg_qd_reconciliation_runs_append_only BEFORE DELETE ON qd_reconciliation_runs FOR EACH ROW EXECUTE FUNCTION qd_reject_reconciliation_run_delete(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_reconciliation_discrepancies_building_only') THEN CREATE TRIGGER trg_qd_reconciliation_discrepancies_building_only BEFORE INSERT ON qd_reconciliation_discrepancies FOR EACH ROW EXECUTE FUNCTION qd_guard_reconciliation_discrepancy_insert(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_reconciliation_discrepancies_append_only') THEN CREATE TRIGGER trg_qd_reconciliation_discrepancies_append_only BEFORE UPDATE OR DELETE ON qd_reconciliation_discrepancies FOR EACH ROW EXECUTE FUNCTION qd_reject_reconciliation_discrepancy_mutation(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_reconciliation_checkpoints_append_only') THEN CREATE TRIGGER trg_qd_reconciliation_checkpoints_append_only BEFORE UPDATE OR DELETE ON qd_reconciliation_checkpoints FOR EACH ROW EXECUTE FUNCTION qd_reject_reconciliation_checkpoint_mutation(); END IF;
END $$;
