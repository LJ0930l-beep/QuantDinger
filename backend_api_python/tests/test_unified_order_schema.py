"""Phase 0 PR-02 expand-only unified-order schema contracts."""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION = MIGRATIONS / "20260722_unified_order_expand_only.sql"
INIT_SQL = MIGRATIONS / "init.sql"

EXPECTED_TABLES = {
    "qd_order_commands",
    "qd_instrument_rule_snapshots",
    "qd_order_intents_v2",
    "qd_economic_orders",
    "qd_risk_reservations",
    "qd_order_state_events",
    "qd_submission_attempts",
    "qd_exchange_orders",
    "qd_exchange_order_observations",
    "qd_exchange_fill_events",
    "qd_ledger_transactions",
    "qd_ledger_entries",
    "qd_position_projections",
    "qd_pnl_projections",
    "qd_reconciliation_checkpoints",
    "qd_reconciliation_issues",
    "qd_transactional_outbox",
    "qd_consumer_inbox",
    "qd_projection_snapshots",
}

# These are representative pre-existing upstream tables whose availability is
# required by the additive schema's foreign keys and adjacent trading flows.
REQUIRED_UPSTREAM_TABLES = {
    "qd_users",
    "qd_exchange_credentials",
    "qd_strategies_trading",
    "qd_strategy_trades",
    "qd_strategy_positions",
    "qd_strategy_funding_fees",
}


class UnifiedOrderSchemaTextTests(unittest.TestCase):
    def test_init_sql_contains_the_incremental_schema(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        init_sql = INIT_SQL.read_text(encoding="utf-8")
        self.assertIn(migration, init_sql)

    def test_new_money_columns_use_numeric_38_18_without_float_types(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertGreaterEqual(migration.count("NUMERIC(38,18)"), 25)
        self.assertIsNone(re.search(r"\\b(?:FLOAT|REAL|DOUBLE(?:\\s+PRECISION)?)\\b", migration, re.I))

    def test_pr00_status_contracts_and_confirmed_health_mapping_are_encoded(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("'CANCEL_REQUESTED','CANCELLING','CANCELLED'", migration)
        self.assertIn("'READY','SUBMITTING','ACKED','UNKNOWN','CONFIRMED_ABSENT','REJECTED'", migration)
        self.assertIn("health_status IN ('HEALTHY','DEGRADED','UNHEALTHY')", migration)
        self.assertIn("health_reason IN ('','STALE','FAILED','CONFLICT')", migration)
        self.assertIn("health_status = 'DEGRADED' AND health_reason = 'STALE'", migration)
        self.assertIn("health_status = 'UNHEALTHY' AND health_reason IN ('FAILED','CONFLICT')", migration)

    def test_immutable_fact_foreign_keys_restrict_deletes_and_idempotency_is_database_backed(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("ON DELETE CASCADE", migration)
        for fragment in (
            "uq_qd_order_commands_idempotency",
            "UNIQUE(economic_order_id, child_seq, attempt_no)",
            "UNIQUE(exchange, credential_id, dedupe_key, key_version)",
            "UNIQUE(aggregate_id, aggregate_version, event_type)",
            "uq_qd_risk_reservations_active_command_kind",
            "FOREIGN KEY(economic_order_id, tenant_id, credential_id, account_scope, instrument_id)",
            "FOREIGN KEY(intent_id, tenant_id, credential_id, account_scope, instrument_id)",
        ):
            self.assertIn(fragment, migration)

    def test_init_sql_retains_representative_upstream_trading_tables(self):
        init_sql = INIT_SQL.read_text(encoding="utf-8")
        for table in REQUIRED_UPSTREAM_TABLES:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", init_sql)


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class UnifiedOrderSchemaPostgresTests(unittest.TestCase):
    def test_incremental_migration_is_reentrant_and_creates_contract_tables(self):
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            connection.autocommit = False
            with connection.cursor() as cursor:
                cursor.execute(MIGRATION.read_text(encoding="utf-8"))
                cursor.execute(MIGRATION.read_text(encoding="utf-8"))
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(%s)",
                    (sorted(EXPECTED_TABLES | REQUIRED_UPSTREAM_TABLES),),
                )
                self.assertEqual(
                    {row[0] for row in cursor.fetchall()},
                    EXPECTED_TABLES | REQUIRED_UPSTREAM_TABLES,
                )
                cursor.execute(
                    "SELECT table_name, column_name, numeric_precision, numeric_scale "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND data_type = 'numeric' "
                    "AND table_name = ANY(%s)",
                    (sorted(EXPECTED_TABLES),),
                )
                for table, column, precision, scale in cursor.fetchall():
                    self.assertEqual(
                        (precision, scale),
                        (38, 18),
                        f"{table}.{column} must remain NUMERIC(38,18)",
                    )
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
