-- Phase 0 wave 3: immutable shadow-comparison persistence only.
-- Expand-only.  No runtime consumer, trading decision, or read cutover is enabled.

CREATE TABLE IF NOT EXISTS qd_shadow_comparison_runs (
    id UUID PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL,
    instrument_id VARCHAR(100) NOT NULL,
    market_type VARCHAR(20) NOT NULL,
    comparison_contract_version VARCHAR(64) NOT NULL CHECK (comparison_contract_version = 'shadow-diff-v1'),
    legacy_source_identity VARCHAR(32) NOT NULL,
    legacy_source_version VARCHAR(64) NOT NULL,
    legacy_source_fingerprint VARCHAR(64) NOT NULL CHECK (legacy_source_fingerprint ~ '^[0-9a-f]{64}$'),
    candidate_source_fingerprint VARCHAR(64) NOT NULL CHECK (candidate_source_fingerprint ~ '^[0-9a-f]{64}$'),
    candidate_generation_id UUID NOT NULL REFERENCES qd_projection_generations(id) ON DELETE RESTRICT,
    candidate_consumer_name VARCHAR(160) NOT NULL,
    candidate_generation_build_fingerprint VARCHAR(64) NOT NULL CHECK (candidate_generation_build_fingerprint ~ '^[0-9a-f]{64}$'),
    candidate_checkpoint_watermark BIGINT NOT NULL CHECK (candidate_checkpoint_watermark >= 0),
    as_of TIMESTAMPTZ NOT NULL,
    correlation_id VARCHAR(160) NOT NULL CHECK (correlation_id <> ''),
    tolerance_policy_version VARCHAR(64) NOT NULL,
    quantity_absolute NUMERIC(38,18) NOT NULL CHECK (quantity_absolute >= 0),
    quantity_relative NUMERIC(38,18) NOT NULL CHECK (quantity_relative >= 0),
    monetary_absolute NUMERIC(38,18) NOT NULL CHECK (monetary_absolute >= 0),
    monetary_relative NUMERIC(38,18) NOT NULL CHECK (monetary_relative >= 0),
    ratio_absolute NUMERIC(38,18) NOT NULL CHECK (ratio_absolute >= 0),
    tolerance_policy_fingerprint VARCHAR(64) NOT NULL CHECK (tolerance_policy_fingerprint ~ '^[0-9a-f]{64}$'),
    build_fingerprint VARCHAR(64) NOT NULL CHECK (build_fingerprint ~ '^[0-9a-f]{64}$'),
    replay_fingerprint VARCHAR(64) CHECK (replay_fingerprint ~ '^[0-9a-f]{64}$'),
    state VARCHAR(16) NOT NULL CHECK (state IN ('BUILDING','COMPLETE','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    failure_reason VARCHAR(512),
    UNIQUE(tenant_id, credential_id, account_scope, instrument_id, market_type, build_fingerprint),
    CHECK ((state = 'BUILDING' AND replay_fingerprint IS NULL AND completed_at IS NULL AND failure_reason IS NULL)
        OR (state = 'COMPLETE' AND replay_fingerprint IS NOT NULL AND completed_at IS NOT NULL AND failure_reason IS NULL)
        OR (state = 'FAILED' AND replay_fingerprint IS NULL AND completed_at IS NOT NULL AND failure_reason IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS qd_shadow_diff_facts (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES qd_shadow_comparison_runs(id) ON DELETE RESTRICT,
    fact_name VARCHAR(100) NOT NULL,
    diff_kind VARCHAR(32) NOT NULL CHECK (diff_kind IN ('MISSING_LEGACY','MISSING_CANDIDATE','VALUE_MISMATCH','VERSION_MISMATCH','STALE_SOURCE','UNSUPPORTED_FACT','SCOPE_MISMATCH','VALUATION_REQUIRED')),
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('INFO','WARNING','BLOCKING')),
    legacy_value NUMERIC(38,18),
    legacy_value_kind VARCHAR(16) CHECK (legacy_value_kind IN ('QUANTITY','MONETARY','RATIO')),
    legacy_asset VARCHAR(24),
    candidate_value NUMERIC(38,18),
    candidate_value_kind VARCHAR(16) CHECK (candidate_value_kind IN ('QUANTITY','MONETARY','RATIO')),
    candidate_asset VARCHAR(24),
    detail VARCHAR(160) NOT NULL,
    diff_fingerprint VARCHAR(64) NOT NULL CHECK (diff_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, diff_fingerprint),
    CHECK ((legacy_value IS NULL AND legacy_value_kind IS NULL AND legacy_asset IS NULL)
        OR (legacy_value IS NOT NULL AND legacy_value_kind IS NOT NULL
            AND ((legacy_value_kind = 'RATIO' AND legacy_asset IS NULL)
                OR (legacy_value_kind IN ('QUANTITY','MONETARY') AND legacy_asset IS NOT NULL)))),
    CHECK ((candidate_value IS NULL AND candidate_value_kind IS NULL AND candidate_asset IS NULL)
        OR (candidate_value IS NOT NULL AND candidate_value_kind IS NOT NULL
            AND ((candidate_value_kind = 'RATIO' AND candidate_asset IS NULL)
                OR (candidate_value_kind IN ('QUANTITY','MONETARY') AND candidate_asset IS NOT NULL))))
);

CREATE OR REPLACE FUNCTION qd_guard_shadow_comparison_run_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    IF ROW(NEW.id, NEW.tenant_id, NEW.credential_id, NEW.account_scope, NEW.instrument_id,
           NEW.market_type, NEW.comparison_contract_version, NEW.legacy_source_fingerprint,
           NEW.legacy_source_identity, NEW.legacy_source_version, NEW.candidate_source_fingerprint,
           NEW.candidate_generation_id, NEW.candidate_consumer_name, NEW.candidate_generation_build_fingerprint,
           NEW.candidate_checkpoint_watermark, NEW.as_of, NEW.correlation_id, NEW.tolerance_policy_version,
           NEW.quantity_absolute, NEW.quantity_relative, NEW.monetary_absolute, NEW.monetary_relative,
           NEW.ratio_absolute, NEW.tolerance_policy_fingerprint, NEW.build_fingerprint,
           NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.tenant_id, OLD.credential_id, OLD.account_scope,
           OLD.instrument_id, OLD.market_type, OLD.comparison_contract_version,
           OLD.legacy_source_fingerprint, OLD.legacy_source_identity, OLD.legacy_source_version,
           OLD.candidate_source_fingerprint, OLD.candidate_generation_id, OLD.candidate_consumer_name,
           OLD.candidate_generation_build_fingerprint, OLD.candidate_checkpoint_watermark, OLD.as_of,
           OLD.correlation_id, OLD.tolerance_policy_version, OLD.quantity_absolute, OLD.quantity_relative,
           OLD.monetary_absolute, OLD.monetary_relative, OLD.ratio_absolute,
           OLD.tolerance_policy_fingerprint, OLD.build_fingerprint, OLD.created_at) THEN
        RAISE EXCEPTION 'shadow comparison immutable facts cannot change' USING ERRCODE = '55000';
    END IF;
    IF OLD.state <> 'BUILDING' OR NEW.state NOT IN ('COMPLETE','FAILED') THEN
        RAISE EXCEPTION 'shadow comparison run transition is invalid' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_reject_shadow_comparison_run_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'shadow comparison runs are append-only' USING ERRCODE = '55000';
END; $$;

CREATE OR REPLACE FUNCTION qd_guard_shadow_diff_fact_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ DECLARE current_state VARCHAR(16); BEGIN
    SELECT state INTO current_state FROM qd_shadow_comparison_runs WHERE id = NEW.run_id FOR KEY SHARE;
    IF current_state IS NULL OR current_state <> 'BUILDING' THEN
        RAISE EXCEPTION 'shadow diff facts require a BUILDING run' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_reject_shadow_diff_fact_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'shadow diff facts are append-only' USING ERRCODE = '55000';
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_shadow_comparison_runs_guard') THEN CREATE TRIGGER trg_qd_shadow_comparison_runs_guard BEFORE UPDATE ON qd_shadow_comparison_runs FOR EACH ROW EXECUTE FUNCTION qd_guard_shadow_comparison_run_update(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_shadow_comparison_runs_append_only') THEN CREATE TRIGGER trg_qd_shadow_comparison_runs_append_only BEFORE DELETE ON qd_shadow_comparison_runs FOR EACH ROW EXECUTE FUNCTION qd_reject_shadow_comparison_run_delete(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_shadow_diff_facts_building_only') THEN CREATE TRIGGER trg_qd_shadow_diff_facts_building_only BEFORE INSERT ON qd_shadow_diff_facts FOR EACH ROW EXECUTE FUNCTION qd_guard_shadow_diff_fact_insert(); END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_qd_shadow_diff_facts_append_only') THEN CREATE TRIGGER trg_qd_shadow_diff_facts_append_only BEFORE UPDATE OR DELETE ON qd_shadow_diff_facts FOR EACH ROW EXECUTE FUNCTION qd_reject_shadow_diff_fact_mutation(); END IF;
END $$;
