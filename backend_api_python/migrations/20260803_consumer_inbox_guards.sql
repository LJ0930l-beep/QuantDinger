-- SC-14 R02: append-only and canonical consumer inbox facts.
-- This migration is expand-only.  It does not wire a projection consumer or
-- change any runtime trading path.

-- The domain contract permits 160 canonical ASCII characters.  Widening the
-- original VARCHAR(96) column is non-destructive for existing rows.
ALTER TABLE qd_consumer_inbox
    ALTER COLUMN consumer_name TYPE VARCHAR(160);

ALTER TABLE qd_consumer_inbox
    ALTER COLUMN result_hash DROP DEFAULT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_consumer_inbox_consumer_name_canonical'
    ) THEN
        ALTER TABLE qd_consumer_inbox
            ADD CONSTRAINT chk_qd_consumer_inbox_consumer_name_canonical
            CHECK (
                consumer_name <> ''
                AND consumer_name = btrim(consumer_name)
                AND consumer_name = lower(consumer_name)
                AND consumer_name ~ '^[a-z0-9][a-z0-9._:/-]*$'
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_qd_consumer_inbox_result_hash_sha256'
    ) THEN
        ALTER TABLE qd_consumer_inbox
            ADD CONSTRAINT chk_qd_consumer_inbox_result_hash_sha256
            CHECK (result_hash ~ '^[0-9a-f]{64}$') NOT VALID;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION qd_reject_consumer_inbox_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'consumer inbox facts are append-only'
        USING ERRCODE = '55000';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_qd_consumer_inbox_append_only'
    ) THEN
        CREATE TRIGGER trg_qd_consumer_inbox_append_only
            BEFORE UPDATE OR DELETE ON qd_consumer_inbox
            FOR EACH ROW EXECUTE FUNCTION qd_reject_consumer_inbox_mutation();
    END IF;
END $$;
