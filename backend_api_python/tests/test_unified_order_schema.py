"""Phase 0 PR-02 expand-only unified-order schema contracts."""

from __future__ import annotations

import os
import re
import unittest
import uuid
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
    "qd_strategy_broker_activities",
    "qd_strategy_equity_snapshots",
}


class UnifiedOrderSchemaTextTests(unittest.TestCase):
    def test_init_sql_contains_the_incremental_schema(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        init_sql = INIT_SQL.read_text(encoding="utf-8")
        self.assertIn(migration, init_sql)

    def test_new_money_columns_use_numeric_38_18_without_float_types(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertGreaterEqual(migration.count("NUMERIC(38,18)"), 25)
        self.assertIsNone(re.search(r"\b(?:FLOAT|REAL|DOUBLE(?:\s+PRECISION)?)\b", migration, re.I))

    def test_sql_files_reject_line_start_diff_markers(self):
        diff_marker = re.compile(r"^(?:\+|@@|---|\*\*\*|-[—–])")
        for sql_file in (MIGRATION, INIT_SQL):
            with self.subTest(sql_file=sql_file.name):
                for line_number, line in enumerate(sql_file.read_text(encoding="utf-8").splitlines(), 1):
                    self.assertIsNone(
                        diff_marker.match(line),
                        f"{sql_file.name}:{line_number} contains a literal patch marker",
                    )

    def test_pr00_and_checkpoint_status_contracts_are_encoded(self):
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("'CANCEL_REQUESTED','CANCELLING','CANCELLED'", migration)
        self.assertIn("'READY','SUBMITTING','ACKED','UNKNOWN','CONFIRMED_ABSENT','REJECTED'", migration)
        self.assertIn("status VARCHAR(16) NOT NULL CHECK (status IN ('HEALTHY','STALE','FAILED','CONFLICT'))", migration)
        self.assertNotIn("health_status", migration)
        self.assertNotIn("health_reason", migration)
        self.assertNotIn("reconcile_health", migration)

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
    def _assert_rejected(self, cursor, statement, parameters=()):
        import psycopg2

        cursor.execute("SAVEPOINT expected_rejection")
        try:
            with self.assertRaises(psycopg2.Error):
                cursor.execute(statement, parameters)
        finally:
            cursor.execute("ROLLBACK TO SAVEPOINT expected_rejection")
            cursor.execute("RELEASE SAVEPOINT expected_rejection")

    def _create_order_graph(self, cursor):
        suffix = uuid.uuid4().hex
        user_id = cursor.execute(
            "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
            (f"pr02_schema_{suffix}", "schema-test"),
        ) or cursor.fetchone()[0]
        credential_id = cursor.execute(
            "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) "
            "VALUES (%s, %s, %s) RETURNING id",
            (user_id, "schema-test", "{}"),
        ) or cursor.fetchone()[0]
        snapshot_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO qd_instrument_rule_snapshots "
            "(id, exchange, market_type, instrument_id, rule_version, tick_size, quantity_step, "
            "minimum_quantity, minimum_notional, price_scale, quantity_scale, rounding_policy_version) "
            "VALUES (%s, 'schema-test', 'SPOT', 'BTC-USDT', 'v1', '0.01', '0.001', '0', '0', 2, 3, 'v1')",
            (snapshot_id,),
        )
        command_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO qd_order_commands "
            "(id, tenant_id, user_id, credential_id, actor_type, actor_id, source, action, account_scope, "
            "request_fingerprint, idempotency_key, status) "
            "VALUES (%s, %s, %s, %s, 'HUMAN', 'schema-test', 'SCHEMA_TEST', 'OPEN', 'account-a', "
            "'request-fingerprint', 'command-key', 'ACCEPTED')",
            (command_id, user_id, user_id, credential_id),
        )
        economic_order_id = str(uuid.uuid4())
        intent_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO qd_order_intents_v2 "
            "(id, command_id, tenant_id, credential_id, economic_order_id, intent_version, account_scope, "
            "instrument_id, market_type, side, order_type, execution_algo, target_quantity, "
            "instrument_rule_snapshot_id, instrument_rule_version, rounding_mode, payload_hash) "
            "VALUES (%s, %s, %s, %s, %s, 1, 'account-a', 'BTC-USDT', 'SPOT', 'BUY', 'LIMIT', 'DIRECT', "
            "'1', %s, 'v1', 'ROUND_DOWN', 'intent-payload')",
            (intent_id, command_id, user_id, credential_id, economic_order_id, snapshot_id),
        )
        cursor.execute(
            "INSERT INTO qd_economic_orders "
            "(id, intent_id, tenant_id, user_id, credential_id, account_scope, instrument_id, market_type, "
            "state, target_quantity) "
            "VALUES (%s, %s, %s, %s, %s, 'account-a', 'BTC-USDT', 'SPOT', 'CREATED', '1')",
            (economic_order_id, intent_id, user_id, user_id, credential_id),
        )
        return {
            "user_id": user_id,
            "credential_id": credential_id,
            "snapshot_id": snapshot_id,
            "command_id": command_id,
            "economic_order_id": economic_order_id,
            "intent_id": intent_id,
        }

    def _insert_checkpoint(self, cursor, graph, status, *, account_scope=None, instrument_id=None):
        cursor.execute(
            "INSERT INTO qd_reconciliation_checkpoints "
            "(id, tenant_id, credential_id, exchange, market_type, account_scope, instrument_id, status) "
            "VALUES (%s, %s, %s, 'schema-test', 'SPOT', %s, %s, %s)",
            (
                str(uuid.uuid4()),
                graph["user_id"],
                graph["credential_id"],
                account_scope or f"checkpoint-{uuid.uuid4().hex}",
                instrument_id or "BTC-USDT",
                status,
            ),
        )

    def test_init_and_incremental_schema_enforce_database_contracts(self):
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            connection.autocommit = False
            with connection.cursor() as cursor:
                # CI initializes an empty PostgreSQL instance with init.sql before
                # running tests; execute it again here to enforce reentrancy.
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
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
                cursor.execute(
                    "SELECT is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'qd_reconciliation_checkpoints' "
                    "AND column_name = 'status'"
                )
                self.assertEqual(cursor.fetchone(), ("NO", None))

                graph = self._create_order_graph(cursor)
                for status in ("HEALTHY", "STALE", "FAILED", "CONFLICT"):
                    self._insert_checkpoint(cursor, graph, status)
                for invalid_status in ("DEGRADED", "UNHEALTHY", "UNKNOWN", None):
                    self._assert_rejected(
                        cursor,
                        "INSERT INTO qd_reconciliation_checkpoints "
                        "(id, tenant_id, credential_id, exchange, market_type, account_scope, instrument_id, status) "
                        "VALUES (%s, %s, %s, 'schema-test', 'SPOT', %s, 'BTC-USDT', %s)",
                        (
                            str(uuid.uuid4()),
                            graph["user_id"],
                            graph["credential_id"],
                            f"invalid-{uuid.uuid4().hex}",
                            invalid_status,
                        ),
                    )

                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_order_commands "
                    "(id, tenant_id, user_id, credential_id, actor_type, actor_id, source, action, account_scope, "
                    "request_fingerprint, idempotency_key, status) "
                    "VALUES (%s, %s, %s, %s, 'HUMAN', 'schema-test', 'SCHEMA_TEST', 'OPEN', 'account-b', "
                    "'request-fingerprint', 'command-key', 'ACCEPTED')",
                    (str(uuid.uuid4()), graph["user_id"], graph["user_id"], graph["credential_id"]),
                )

                attempt_id = str(uuid.uuid4())
                attempt_sql = (
                    "INSERT INTO qd_submission_attempts "
                    "(id, economic_order_id, exchange, credential_id, market_type, child_seq, attempt_no, role, "
                    "canonical_client_order_id, venue_client_order_id, request_fingerprint, state) "
                    "VALUES (%s, %s, 'schema-test', %s, 'SPOT', 1, 1, 'PRIMARY', 'canonical-1', 'venue-1', "
                    "'attempt-fingerprint', %s)"
                )
                cursor.execute(attempt_sql, (attempt_id, graph["economic_order_id"], graph["credential_id"], "READY"))
                self._assert_rejected(
                    cursor,
                    attempt_sql,
                    (str(uuid.uuid4()), graph["economic_order_id"], graph["credential_id"], "READY"),
                )
                self._assert_rejected(
                    cursor,
                    attempt_sql,
                    (str(uuid.uuid4()), graph["economic_order_id"], graph["credential_id"], "INVALID"),
                )
                self._assert_rejected(
                    cursor,
                    attempt_sql,
                    (str(uuid.uuid4()), graph["economic_order_id"], graph["credential_id"], None),
                )

                fill_sql = (
                    "INSERT INTO qd_exchange_fill_events "
                    "(id, key_version, dedupe_key, exchange, tenant_id, credential_id, account_scope, "
                    "economic_order_id, intent_id, instrument_id, side, price, quantity, quote_quantity, "
                    "exchange_event_at, received_at, source, raw_payload_hash, normalizer_version, instrument_rule_version) "
                    "VALUES (%s, 'v1', 'fill-dedupe', 'schema-test', %s, %s, 'account-a', %s, %s, 'BTC-USDT', "
                    "'BUY', '100', '1', '100', NOW(), NOW(), 'REST', 'payload-hash', 'v1', 'v1')"
                )
                cursor.execute(
                    fill_sql,
                    (str(uuid.uuid4()), graph["user_id"], graph["credential_id"], graph["economic_order_id"], graph["intent_id"]),
                )
                self._assert_rejected(
                    cursor,
                    fill_sql,
                    (str(uuid.uuid4()), graph["user_id"], graph["credential_id"], graph["economic_order_id"], graph["intent_id"]),
                )

                outbox_sql = (
                    "INSERT INTO qd_transactional_outbox "
                    "(event_id, aggregate_type, aggregate_id, aggregate_version, event_type, payload_json) "
                    "VALUES (%s, 'ECONOMIC_ORDER', %s, 1, 'ORDER_CREATED', '{}'::jsonb)"
                )
                aggregate_id = str(uuid.uuid4())
                cursor.execute(outbox_sql, (str(uuid.uuid4()), aggregate_id))
                self._assert_rejected(cursor, outbox_sql, (str(uuid.uuid4()), aggregate_id))

                risk_sql = (
                    "INSERT INTO qd_risk_reservations "
                    "(id, command_id, economic_order_id, tenant_id, credential_id, account_scope, reservation_kind, "
                    "currency, risk_input_hash, state) "
                    "VALUES (%s, %s, %s, %s, %s, 'account-a', 'OPENING', 'USDT', 'risk-hash', 'ACTIVE')"
                )
                cursor.execute(
                    risk_sql,
                    (str(uuid.uuid4()), graph["command_id"], graph["economic_order_id"], graph["user_id"], graph["credential_id"]),
                )
                self._assert_rejected(
                    cursor,
                    risk_sql,
                    (str(uuid.uuid4()), graph["command_id"], graph["economic_order_id"], graph["user_id"], graph["credential_id"]),
                )

                bad_intent_id = str(uuid.uuid4())
                bad_economic_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO qd_order_intents_v2 "
                    "(id, command_id, tenant_id, credential_id, economic_order_id, intent_version, account_scope, "
                    "instrument_id, market_type, side, order_type, execution_algo, target_quantity, "
                    "instrument_rule_snapshot_id, instrument_rule_version, rounding_mode, payload_hash) "
                    "VALUES (%s, %s, %s, %s, %s, 2, 'account-a', 'BTC-USDT', 'SPOT', 'BUY', 'LIMIT', 'DIRECT', "
                    "'1', %s, 'v1', 'ROUND_DOWN', 'bad-intent')",
                    (bad_intent_id, graph["command_id"], graph["user_id"], graph["credential_id"], bad_economic_id, graph["snapshot_id"]),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_economic_orders "
                    "(id, intent_id, tenant_id, user_id, credential_id, account_scope, instrument_id, market_type, state, target_quantity) "
                    "VALUES (%s, %s, %s, %s, %s, 'account-a', 'BTC-USDT', 'SPOT', 'INVALID', '1')",
                    (bad_economic_id, bad_intent_id, graph["user_id"], graph["user_id"], graph["credential_id"]),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_economic_orders "
                    "(id, intent_id, tenant_id, user_id, credential_id, account_scope, instrument_id, market_type, state, target_quantity) "
                    "VALUES (%s, %s, %s, %s, %s, 'different-account', 'BTC-USDT', 'SPOT', 'CREATED', '1')",
                    (str(uuid.uuid4()), bad_intent_id, graph["user_id"], graph["user_id"], graph["credential_id"]),
                )

                cursor.execute(
                    "INSERT INTO qd_order_state_events "
                    "(id, economic_order_id, event_seq, to_state, reason_code, actor_type, occurred_at) "
                    "VALUES (%s, %s, 1, 'CREATED', 'SCHEMA_TEST', 'HUMAN', NOW())",
                    (str(uuid.uuid4()), graph["economic_order_id"]),
                )
                self._assert_rejected(
                    cursor,
                    "DELETE FROM qd_economic_orders WHERE id = %s",
                    (graph["economic_order_id"],),
                )
                cursor.execute(
                    "SELECT COUNT(*) FROM qd_exchange_fill_events WHERE economic_order_id = %s",
                    (graph["economic_order_id"],),
                )
                self.assertEqual(cursor.fetchone()[0], 1)
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
