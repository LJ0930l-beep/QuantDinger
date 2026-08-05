-- Durable Entry V2 -> immutable fill-ledger bridge.
-- Expand-only: canonical fills use their durable command identity and never
-- invent qd_order_intents_v2 or qd_economic_orders rows.

ALTER TABLE qd_ledger_entries
    ADD COLUMN IF NOT EXISTS durable_entry_command_id UUID
        REFERENCES qd_durable_entry_specifications(command_id) ON DELETE RESTRICT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_ledger_entries_single_order_identity'
    ) THEN
        ALTER TABLE qd_ledger_entries
            ADD CONSTRAINT chk_qd_ledger_entries_single_order_identity
            CHECK (durable_entry_command_id IS NULL OR economic_order_id IS NULL)
            NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_qd_ledger_entries_durable_command
    ON qd_ledger_entries(durable_entry_command_id, transaction_id)
    WHERE durable_entry_command_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS qd_durable_entry_fill_events (
    id UUID PRIMARY KEY,
    key_version VARCHAR(32) NOT NULL CHECK (key_version <> ''),
    dedupe_key VARCHAR(256) NOT NULL CHECK (dedupe_key <> ''),
    exchange VARCHAR(50) NOT NULL CHECK (exchange <> '' AND exchange = LOWER(exchange)),
    tenant_id INTEGER NOT NULL REFERENCES qd_users(id) ON DELETE RESTRICT,
    credential_id INTEGER NOT NULL REFERENCES qd_exchange_credentials(id) ON DELETE RESTRICT,
    account_scope VARCHAR(160) NOT NULL CHECK (account_scope <> ''),
    market_type VARCHAR(20) NOT NULL CHECK (market_type IN ('spot','perpetual')),
    command_id UUID NOT NULL REFERENCES qd_durable_entry_specifications(command_id) ON DELETE RESTRICT,
    economic_order_id UUID NOT NULL,
    exchange_order_id VARCHAR(160) NOT NULL DEFAULT '',
    exchange_fill_id VARCHAR(160) NOT NULL DEFAULT '',
    venue_trade_sequence VARCHAR(160) NOT NULL DEFAULT '',
    instrument_id VARCHAR(100) NOT NULL CHECK (instrument_id <> '' AND instrument_id = UPPER(instrument_id)),
    side VARCHAR(8) NOT NULL CHECK (side IN ('BUY','SELL')),
    position_side VARCHAR(12) NOT NULL DEFAULT '' CHECK (position_side IN ('','LONG','SHORT')),
    liquidity_role VARCHAR(16) NOT NULL DEFAULT '',
    price NUMERIC(38,18) NOT NULL CHECK (price > 0),
    quantity NUMERIC(38,18) NOT NULL CHECK (quantity > 0),
    quote_quantity NUMERIC(38,18) NOT NULL CHECK (quote_quantity >= 0),
    quote_quantity_origin VARCHAR(16) NOT NULL CHECK (quote_quantity_origin IN ('VENUE','DERIVED')),
    quote_quantity_policy_version VARCHAR(64),
    quote_quantity_evidence_hash VARCHAR(128) NOT NULL CHECK (quote_quantity_evidence_hash <> ''),
    fee_summary_state VARCHAR(24) NOT NULL CHECK (fee_summary_state IN ('NONE','SINGLE_COMPONENT','MULTI_COMPONENT')),
    fee_amount NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    fee_asset VARCHAR(20) NOT NULL DEFAULT '',
    fee_quote_amount NUMERIC(38,18),
    exchange_event_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    source VARCHAR(16) NOT NULL CHECK (source IN ('WS','REST','BACKFILL','MANUAL')),
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_payload_hash VARCHAR(128) NOT NULL CHECK (raw_payload_hash <> ''),
    normalizer_version VARCHAR(64) NOT NULL CHECK (normalizer_version <> ''),
    instrument_rule_version VARCHAR(100) NOT NULL CHECK (instrument_rule_version <> ''),
    quarantine_state VARCHAR(32) NOT NULL DEFAULT 'CLEAR'
        CHECK (quarantine_state IN ('CLEAR','QUARANTINED','RECONCILIATION_REQUIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(exchange, credential_id, dedupe_key, key_version),
    UNIQUE(id, command_id, tenant_id, credential_id, account_scope, instrument_id, market_type),
    CHECK (
        (quote_quantity_origin = 'VENUE' AND quote_quantity_policy_version IS NULL)
        OR (quote_quantity_origin = 'DERIVED' AND quote_quantity_policy_version IS NOT NULL
            AND btrim(quote_quantity_policy_version) <> '')
    ),
    CHECK (
        (fee_summary_state = 'NONE' AND fee_amount = 0 AND fee_asset = '' AND fee_quote_amount IS NULL)
        OR fee_summary_state = 'SINGLE_COMPONENT'
        OR (fee_summary_state = 'MULTI_COMPONENT' AND fee_amount = 0 AND fee_asset = ''
            AND fee_quote_amount IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_qd_durable_entry_fill_events_scope
    ON qd_durable_entry_fill_events(credential_id, account_scope, instrument_id, market_type, exchange_event_at);
CREATE INDEX IF NOT EXISTS idx_qd_durable_entry_fill_events_command
    ON qd_durable_entry_fill_events(command_id, exchange_event_at, id);

CREATE TABLE IF NOT EXISTS qd_durable_entry_ledger_valuation_evidence (
    id UUID PRIMARY KEY,
    fill_event_id UUID NOT NULL REFERENCES qd_durable_entry_fill_events(id) ON DELETE RESTRICT,
    asset VARCHAR(20) NOT NULL CHECK (asset <> '' AND asset = UPPER(asset)),
    valuation_ccy VARCHAR(20) NOT NULL CHECK (valuation_ccy <> '' AND valuation_ccy = UPPER(valuation_ccy)),
    price NUMERIC(38,18) NOT NULL CHECK (price > 0),
    evidence_source VARCHAR(32) NOT NULL CHECK (evidence_source IN ('VENUE','ORACLE','MANUAL_APPROVED','IDENTITY')),
    policy_version VARCHAR(64) NOT NULL CHECK (policy_version <> ''),
    observed_at TIMESTAMPTZ NOT NULL,
    payload_hash VARCHAR(128) NOT NULL CHECK (payload_hash <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (evidence_source = 'IDENTITY' AND asset = valuation_ccy AND price = 1)
        OR (evidence_source <> 'IDENTITY' AND asset <> valuation_ccy)
    ),
    UNIQUE(id, fill_event_id, asset, valuation_ccy),
    UNIQUE(fill_event_id, asset, valuation_ccy, evidence_source, policy_version, observed_at, payload_hash)
);

CREATE TABLE IF NOT EXISTS qd_durable_entry_fill_fee_components (
    fill_event_id UUID NOT NULL REFERENCES qd_durable_entry_fill_events(id) ON DELETE RESTRICT,
    fee_seq INTEGER NOT NULL CHECK (fee_seq >= 1),
    asset VARCHAR(20) NOT NULL CHECK (asset <> '' AND asset = UPPER(asset)),
    amount NUMERIC(38,18) NOT NULL CHECK (amount > 0),
    fee_quote_amount NUMERIC(38,18),
    valuation_ccy VARCHAR(20),
    valuation_evidence_id UUID,
    raw_component_hash VARCHAR(128) NOT NULL CHECK (raw_component_hash <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(fill_event_id, fee_seq),
    UNIQUE(fill_event_id, raw_component_hash),
    FOREIGN KEY(valuation_evidence_id, fill_event_id, asset, valuation_ccy)
        REFERENCES qd_durable_entry_ledger_valuation_evidence(id, fill_event_id, asset, valuation_ccy)
        ON DELETE RESTRICT,
    CHECK (valuation_ccy IS NULL OR valuation_ccy = UPPER(valuation_ccy)),
    CHECK (
        (fee_quote_amount IS NULL AND valuation_evidence_id IS NULL AND valuation_ccy IS NULL)
        OR (fee_quote_amount IS NOT NULL AND fee_quote_amount >= 0
            AND valuation_evidence_id IS NOT NULL AND valuation_ccy IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION qd_assert_durable_entry_fill_scope()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    entry_record qd_durable_entry_specifications%ROWTYPE;
    credential_user_id INTEGER;
    credential_exchange_id VARCHAR(50);
BEGIN
    SELECT * INTO entry_record
      FROM qd_durable_entry_specifications
     WHERE command_id = NEW.command_id;
    SELECT user_id, LOWER(exchange_id)
      INTO credential_user_id, credential_exchange_id
      FROM qd_exchange_credentials
     WHERE id = NEW.credential_id;
    IF entry_record.command_id IS NULL
       OR entry_record.action = 'CANCEL'
       OR entry_record.economic_order_id IS NULL
       OR entry_record.economic_order_id <> NEW.economic_order_id
       OR ROW(NEW.tenant_id, NEW.credential_id, NEW.account_scope,
              NEW.instrument_id, NEW.market_type)
          IS DISTINCT FROM ROW(entry_record.tenant_id, entry_record.credential_id,
              entry_record.account_scope, entry_record.instrument_id, entry_record.market_type)
       OR credential_user_id <> NEW.tenant_id
       OR credential_exchange_id <> NEW.exchange THEN
        RAISE EXCEPTION 'durable entry fill scope does not match canonical entry facts'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION qd_reject_durable_entry_fill_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'durable entry fill facts are append-only' USING ERRCODE = '55000';
END; $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_entry_fill_scope') THEN
        CREATE TRIGGER trg_qd_durable_entry_fill_scope
            BEFORE INSERT ON qd_durable_entry_fill_events
            FOR EACH ROW EXECUTE FUNCTION qd_assert_durable_entry_fill_scope();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_entry_fill_events_append_only') THEN
        CREATE TRIGGER trg_qd_durable_entry_fill_events_append_only
            BEFORE UPDATE OR DELETE ON qd_durable_entry_fill_events
            FOR EACH ROW EXECUTE FUNCTION qd_reject_durable_entry_fill_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_entry_fill_evidence_append_only') THEN
        CREATE TRIGGER trg_qd_durable_entry_fill_evidence_append_only
            BEFORE UPDATE OR DELETE ON qd_durable_entry_ledger_valuation_evidence
            FOR EACH ROW EXECUTE FUNCTION qd_reject_durable_entry_fill_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_qd_durable_entry_fee_components_append_only') THEN
        CREATE TRIGGER trg_qd_durable_entry_fee_components_append_only
            BEFORE UPDATE OR DELETE ON qd_durable_entry_fill_fee_components
            FOR EACH ROW EXECUTE FUNCTION qd_reject_durable_entry_fill_mutation();
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_qd_durable_entry_fill_fee_components_asset
    ON qd_durable_entry_fill_fee_components(asset, fill_event_id);
