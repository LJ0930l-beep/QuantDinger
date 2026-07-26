-- Phase 0 PR-06: immutable fill-ledger storage guards.
-- This migration is expand-only. It does not wire any runtime trading path.

ALTER TABLE qd_ledger_transactions
    ADD COLUMN IF NOT EXISTS account_scope VARCHAR(160),
    ADD COLUMN IF NOT EXISTS source_fingerprint VARCHAR(128);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_ledger_transactions_canonical_scope'
    ) THEN
        ALTER TABLE qd_ledger_transactions
            ADD CONSTRAINT chk_qd_ledger_transactions_canonical_scope
            CHECK (account_scope IS NOT NULL AND btrim(account_scope) <> '') NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_ledger_transactions_source_fingerprint'
    ) THEN
        ALTER TABLE qd_ledger_transactions
            ADD CONSTRAINT chk_qd_ledger_transactions_source_fingerprint
            CHECK (source_fingerprint IS NOT NULL AND source_fingerprint ~ '^[0-9a-f]{64}$') NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_ledger_entries_book_shape'
    ) THEN
        ALTER TABLE qd_ledger_entries
            ADD CONSTRAINT chk_qd_ledger_entries_book_shape
            CHECK (
                (book = 'QUANTITY' AND value_in_valuation_ccy IS NULL)
                OR (book = 'MONETARY' AND value_in_valuation_ccy IS NOT NULL)
            ) NOT VALID;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_qd_ledger_transactions_source_fingerprint
    ON qd_ledger_transactions(source_fingerprint)
    WHERE source_fingerprint IS NOT NULL;

CREATE OR REPLACE FUNCTION qd_reject_immutable_fill_ledger_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'immutable fill-ledger facts cannot be %', TG_OP
        USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION qd_assert_immutable_ledger_transaction_balanced()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    ledger_transaction_id UUID;
BEGIN
    IF TG_TABLE_NAME = 'qd_ledger_entries' THEN
        ledger_transaction_id := NEW.transaction_id;
    ELSE
        ledger_transaction_id := NEW.id;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM qd_ledger_entries
        WHERE transaction_id = ledger_transaction_id
    ) THEN
        RAISE EXCEPTION 'ledger transaction % requires entries before commit', ledger_transaction_id
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM qd_ledger_entries
        WHERE transaction_id = ledger_transaction_id
        GROUP BY book, asset
        HAVING SUM(signed_amount) <> 0
    ) THEN
        RAISE EXCEPTION 'ledger transaction % is unbalanced by book and asset', ledger_transaction_id
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM qd_ledger_entries
        WHERE transaction_id = ledger_transaction_id
          AND book = 'MONETARY'
        GROUP BY book
        HAVING SUM(value_in_valuation_ccy) <> 0
    ) THEN
        RAISE EXCEPTION 'ledger transaction % is monetarily unbalanced', ledger_transaction_id
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_qd_exchange_fill_events_append_only'
          AND tgrelid = 'qd_exchange_fill_events'::regclass
    ) THEN
        CREATE TRIGGER trg_qd_exchange_fill_events_append_only
            BEFORE UPDATE OR DELETE ON qd_exchange_fill_events
            FOR EACH ROW EXECUTE FUNCTION qd_reject_immutable_fill_ledger_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_qd_exchange_fill_fee_components_append_only'
          AND tgrelid = 'qd_exchange_fill_fee_components'::regclass
    ) THEN
        CREATE TRIGGER trg_qd_exchange_fill_fee_components_append_only
            BEFORE UPDATE OR DELETE ON qd_exchange_fill_fee_components
            FOR EACH ROW EXECUTE FUNCTION qd_reject_immutable_fill_ledger_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_qd_ledger_valuation_evidence_append_only'
          AND tgrelid = 'qd_ledger_valuation_evidence'::regclass
    ) THEN
        CREATE TRIGGER trg_qd_ledger_valuation_evidence_append_only
            BEFORE UPDATE OR DELETE ON qd_ledger_valuation_evidence
            FOR EACH ROW EXECUTE FUNCTION qd_reject_immutable_fill_ledger_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_qd_ledger_transactions_append_only'
          AND tgrelid = 'qd_ledger_transactions'::regclass
    ) THEN
        CREATE TRIGGER trg_qd_ledger_transactions_append_only
            BEFORE UPDATE OR DELETE ON qd_ledger_transactions
            FOR EACH ROW EXECUTE FUNCTION qd_reject_immutable_fill_ledger_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_qd_ledger_entries_append_only'
          AND tgrelid = 'qd_ledger_entries'::regclass
    ) THEN
        CREATE TRIGGER trg_qd_ledger_entries_append_only
            BEFORE UPDATE OR DELETE ON qd_ledger_entries
            FOR EACH ROW EXECUTE FUNCTION qd_reject_immutable_fill_ledger_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'ctrg_qd_ledger_transactions_balanced'
          AND tgrelid = 'qd_ledger_transactions'::regclass
    ) THEN
        CREATE CONSTRAINT TRIGGER ctrg_qd_ledger_transactions_balanced
            AFTER INSERT ON qd_ledger_transactions
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION qd_assert_immutable_ledger_transaction_balanced();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'ctrg_qd_ledger_entries_balanced'
          AND tgrelid = 'qd_ledger_entries'::regclass
    ) THEN
        CREATE CONSTRAINT TRIGGER ctrg_qd_ledger_entries_balanced
            AFTER INSERT ON qd_ledger_entries
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION qd_assert_immutable_ledger_transaction_balanced();
    END IF;
END $$;
