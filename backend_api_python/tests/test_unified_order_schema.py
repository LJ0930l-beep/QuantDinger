"""Phase 0 PR-02 expand-only unified-order schema contracts."""

from __future__ import annotations

import os
import re
import unittest
import uuid
from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION = MIGRATIONS / "20260722_unified_order_expand_only.sql"
PRECONDITION_MIGRATION = MIGRATIONS / "20260723_state_recovery_ledger_preconditions.sql"
INCREMENTAL_MIGRATIONS = (MIGRATION, PRECONDITION_MIGRATION)
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
    "qd_venue_capability_snapshots",
    "qd_submission_recovery_policy_snapshots",
    "qd_submission_attempt_state_events",
    "qd_ledger_valuation_evidence",
    "qd_exchange_fill_fee_components",
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
        init_sql = INIT_SQL.read_text(encoding="utf-8")
        for migration in INCREMENTAL_MIGRATIONS:
            self.assertIn(migration.read_text(encoding="utf-8"), init_sql)

    def test_new_money_columns_use_numeric_38_18_without_float_types(self):
        schema = "\n".join(item.read_text(encoding="utf-8") for item in INCREMENTAL_MIGRATIONS)
        self.assertGreaterEqual(schema.count("NUMERIC(38,18)"), 29)
        self.assertIsNone(re.search(r"\b(?:FLOAT|REAL|DOUBLE(?:\s+PRECISION)?)\b", schema, re.I))

    def test_sql_files_reject_patch_markers_and_construction_output(self):
        diff_marker = re.compile(r"^(?:\+|@@|---|\*\*\*|-[—–])")
        construction_output = re.compile(
            r"^(?:Exit code:|Wall time:|Output:|Traceback\b|Script (?:error|failed|completed)\b|Command (?:failed|completed)\b)"
        )
        for sql_file in (*INCREMENTAL_MIGRATIONS, INIT_SQL):
            with self.subTest(sql_file=sql_file.name):
                for line_number, line in enumerate(sql_file.read_text(encoding="utf-8").splitlines(), 1):
                    self.assertIsNone(
                        diff_marker.match(line),
                        f"{sql_file.name}:{line_number} contains a literal patch marker",
                    )
                    self.assertFalse(
                        line.startswith(("-" + chr(0x2013), "-" + chr(0x2014))),
                        f"{sql_file.name}:{line_number} contains a literal patch marker",
                    )
                    self.assertIsNone(
                        construction_output.match(line),
                        f"{sql_file.name}:{line_number} contains non-SQL construction output",
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
        migration = "\n".join(item.read_text(encoding="utf-8") for item in INCREMENTAL_MIGRATIONS)
        self.assertNotIn("ON DELETE CASCADE", migration)
        for fragment in (
            "uq_qd_order_commands_idempotency",
            "UNIQUE(economic_order_id, child_seq, attempt_no)",
            "UNIQUE(exchange, credential_id, dedupe_key, key_version)",
            "UNIQUE(aggregate_id, aggregate_version, event_type)",
            "uq_qd_risk_reservations_active_command_kind",
            "FOREIGN KEY(intent_id, id, tenant_id, credential_id, account_scope, instrument_id, market_type)",
            "FOREIGN KEY(economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)",
            "FOREIGN KEY(attempt_id, economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)",
            "uq_qd_ledger_transactions_reversal_once",
            "uq_qd_exchange_order_observations_attempt_evidence",
            "uq_qd_position_projections_unassigned_scope",
            "uq_qd_order_state_events_idempotency",
            "qd_submission_attempt_state_events",
            "qd_exchange_fill_fee_components",
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
            "VALUES (%s, %s, 'spot', 'BTC-USDT', 'v1', '0.01', '0.001', '0', '0', 2, 3, 'v1')",
            (snapshot_id, f"schema-test-{suffix}"),
        )
        command_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO qd_order_commands "
            "(id, tenant_id, user_id, credential_id, actor_type, actor_id, source, action, account_scope, "
            "request_fingerprint, idempotency_key, status) "
            "VALUES (%s, %s, %s, %s, 'HUMAN', 'schema-test', 'SCHEMA_TEST', 'OPEN', 'account-a', "
            "'request-fingerprint', %s, 'ACCEPTED')",
            (command_id, user_id, user_id, credential_id, f"command-key-{suffix}"),
        )
        economic_order_id = str(uuid.uuid4())
        intent_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO qd_order_intents_v2 "
            "(id, command_id, tenant_id, credential_id, economic_order_id, intent_version, account_scope, "
            "instrument_id, market_type, side, order_type, execution_algo, target_quantity, "
            "instrument_rule_snapshot_id, instrument_rule_version, rounding_mode, payload_hash) "
            "VALUES (%s, %s, %s, %s, %s, 1, 'account-a', 'BTC-USDT', 'spot', 'BUY', 'LIMIT', 'DIRECT', "
            "'1', %s, 'v1', 'ROUND_DOWN', 'intent-payload')",
            (intent_id, command_id, user_id, credential_id, economic_order_id, snapshot_id),
        )
        cursor.execute(
            "INSERT INTO qd_economic_orders "
            "(id, intent_id, tenant_id, user_id, credential_id, account_scope, instrument_id, market_type, "
            "state, target_quantity) "
            "VALUES (%s, %s, %s, %s, %s, 'account-a', 'BTC-USDT', 'spot', 'CREATED', '1')",
            (economic_order_id, intent_id, user_id, user_id, credential_id),
        )
        return {
            "user_id": user_id,
            "credential_id": credential_id,
            "snapshot_id": snapshot_id,
            "command_id": command_id,
            "command_idempotency_key": f"command-key-{suffix}",
            "economic_order_id": economic_order_id,
            "intent_id": intent_id,
        }

    def _insert_checkpoint(self, cursor, graph, status, *, account_scope=None, instrument_id=None):
        cursor.execute(
            "INSERT INTO qd_reconciliation_checkpoints "
            "(id, tenant_id, credential_id, exchange, market_type, account_scope, instrument_id, status) "
            "VALUES (%s, %s, %s, 'schema-test', 'spot', %s, %s, %s)",
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
                for migration in INCREMENTAL_MIGRATIONS:
                    cursor.execute(migration.read_text(encoding="utf-8"))
                    cursor.execute(migration.read_text(encoding="utf-8"))
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
                        "VALUES (%s, %s, %s, 'schema-test', 'spot', %s, 'BTC-USDT', %s)",
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
                    "'request-fingerprint', %s, 'ACCEPTED')",
                    (str(uuid.uuid4()), graph["user_id"], graph["user_id"], graph["credential_id"], graph["command_idempotency_key"]),
                )

                attempt_id = str(uuid.uuid4())
                attempt_sql = (
                    "INSERT INTO qd_submission_attempts "
                    "(id, economic_order_id, exchange, tenant_id, credential_id, account_scope, instrument_id, market_type, "
                    "child_seq, attempt_no, role, canonical_client_order_id, venue_client_order_id, request_fingerprint, state) "
                    "VALUES (%s, %s, 'schema-test', %s, %s, 'account-a', 'BTC-USDT', 'spot', 1, 1, 'PRIMARY', 'canonical-1', 'venue-1', "
                    "'attempt-fingerprint', %s)"
                )
                cursor.execute(attempt_sql, (attempt_id, graph["economic_order_id"], graph["user_id"], graph["credential_id"], "READY"))
                self._assert_rejected(
                    cursor,
                    attempt_sql,
                    (str(uuid.uuid4()), graph["economic_order_id"], graph["user_id"], graph["credential_id"], "READY"),
                )
                self._assert_rejected(
                    cursor,
                    attempt_sql,
                    (str(uuid.uuid4()), graph["economic_order_id"], graph["user_id"], graph["credential_id"], "INVALID"),
                )
                self._assert_rejected(
                    cursor,
                    attempt_sql,
                    (str(uuid.uuid4()), graph["economic_order_id"], graph["user_id"], graph["credential_id"], None),
                )

                other_credential_id = cursor.execute(
                    "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (graph["user_id"], "schema-test-alt", "{}"),
                ) or cursor.fetchone()[0]
                scoped_attempt_sql = (
                    "INSERT INTO qd_submission_attempts "
                    "(id, economic_order_id, exchange, tenant_id, credential_id, account_scope, instrument_id, market_type, "
                    "child_seq, attempt_no, role, canonical_client_order_id, venue_client_order_id, request_fingerprint, state) "
                    "VALUES (%s, %s, 'schema-test', %s, %s, %s, %s, %s, %s, 1, 'PRIMARY', %s, %s, 'attempt-fingerprint', 'READY')"
                )
                for child_seq, credential_id, account_scope, instrument_id, market_type in (
                    (10, other_credential_id, "account-a", "BTC-USDT", "spot"),
                    (11, graph["credential_id"], "other-account", "BTC-USDT", "spot"),
                    (12, graph["credential_id"], "account-a", "ETH-USDT", "spot"),
                    (13, graph["credential_id"], "account-a", "BTC-USDT", "SWAP"),
                ):
                    self._assert_rejected(
                        cursor,
                        scoped_attempt_sql,
                        (
                            str(uuid.uuid4()),
                            graph["economic_order_id"],
                            graph["user_id"],
                            credential_id,
                            account_scope,
                            instrument_id,
                            market_type,
                            child_seq,
                            f"canonical-{child_seq}",
                            f"venue-{child_seq}",
                        ),
                    )

                exchange_order_sql = (
                    "INSERT INTO qd_exchange_orders "
                    "(id, attempt_id, economic_order_id, child_role, exchange, tenant_id, credential_id, market_type, "
                    "account_scope, instrument_id, venue_client_order_id, normalized_state, requested_qty) "
                    "VALUES (%s, %s, %s, 'PRIMARY', 'schema-test', %s, %s, %s, %s, %s, %s, %s, '1')"
                )
                cursor.execute(
                    exchange_order_sql,
                    (
                        str(uuid.uuid4()),
                        attempt_id,
                        graph["economic_order_id"],
                        graph["user_id"],
                        graph["credential_id"],
                        "spot",
                        "account-a",
                        "BTC-USDT",
                        "exchange-venue-1",
                        "SUBMITTED",
                    ),
                )
                for child_seq, normalized_state in enumerate(
                    (
                        "PARTIALLY_FILLED",
                        "FILLED",
                        "SUBMISSION_UNKNOWN",
                        "CANCEL_REQUESTED",
                        "CANCELLING",
                        "CANCELLED",
                        "REJECTED",
                        "RECONCILIATION_REQUIRED",
                    ),
                    start=30,
                ):
                    valid_attempt_id = str(uuid.uuid4())
                    cursor.execute(
                        scoped_attempt_sql,
                        (
                            valid_attempt_id,
                            graph["economic_order_id"],
                            graph["user_id"],
                            graph["credential_id"],
                            "account-a",
                            "BTC-USDT",
                            "spot",
                            child_seq,
                            f"canonical-{child_seq}",
                            f"venue-{child_seq}",
                        ),
                    )
                    cursor.execute(
                        exchange_order_sql,
                        (
                            str(uuid.uuid4()),
                            valid_attempt_id,
                            graph["economic_order_id"],
                            graph["user_id"],
                            graph["credential_id"],
                            "spot",
                            "account-a",
                            "BTC-USDT",
                            f"exchange-venue-{child_seq}",
                            normalized_state,
                        ),
                    )
                invalid_state_attempt_id = str(uuid.uuid4())
                cursor.execute(
                    scoped_attempt_sql,
                    (
                        invalid_state_attempt_id,
                        graph["economic_order_id"],
                        graph["user_id"],
                        graph["credential_id"],
                        "account-a",
                        "BTC-USDT",
                        "spot",
                        20,
                        "canonical-20",
                        "venue-20",
                    ),
                )
                self._assert_rejected(
                    cursor,
                    exchange_order_sql,
                    (
                        str(uuid.uuid4()),
                        invalid_state_attempt_id,
                        graph["economic_order_id"],
                        graph["user_id"],
                        graph["credential_id"],
                        "spot",
                        "account-a",
                        "BTC-USDT",
                        "exchange-venue-invalid",
                        "INVALID",
                    ),
                )
                for child_seq, credential_id, account_scope, instrument_id, market_type in (
                    (21, other_credential_id, "account-a", "BTC-USDT", "spot"),
                    (22, graph["credential_id"], "other-account", "BTC-USDT", "spot"),
                    (23, graph["credential_id"], "account-a", "ETH-USDT", "spot"),
                    (24, graph["credential_id"], "account-a", "BTC-USDT", "SWAP"),
                ):
                    attempt_scope_id = str(uuid.uuid4())
                    # The parent attempt is valid. The exchange order alone has
                    # a mismatched scope, so its composite foreign key must fail.
                    cursor.execute(
                        scoped_attempt_sql,
                        (
                            attempt_scope_id,
                            graph["economic_order_id"],
                            graph["user_id"],
                            graph["credential_id"],
                            "account-a",
                            "BTC-USDT",
                            "spot",
                            child_seq,
                            f"canonical-{child_seq}",
                            f"venue-{child_seq}",
                        ),
                    )
                    self._assert_rejected(
                        cursor,
                        exchange_order_sql,
                        (
                            str(uuid.uuid4()),
                            attempt_scope_id,
                            graph["economic_order_id"],
                            graph["user_id"],
                            credential_id,
                            market_type,
                            account_scope,
                            instrument_id,
                            f"exchange-venue-{child_seq}",
                            "SUBMITTED",
                        ),
                    )

                cross_attempt_id = str(uuid.uuid4())
                cursor.execute(
                    scoped_attempt_sql,
                    (
                        cross_attempt_id,
                        graph["economic_order_id"],
                        graph["user_id"],
                        graph["credential_id"],
                        "account-a",
                        "BTC-USDT",
                        "spot",
                        25,
                        "canonical-25",
                        "venue-25",
                    ),
                )
                other_graph = self._create_order_graph(cursor)
                self._assert_rejected(
                    cursor,
                    exchange_order_sql,
                    (
                        str(uuid.uuid4()),
                        cross_attempt_id,
                        other_graph["economic_order_id"],
                        other_graph["user_id"],
                        other_graph["credential_id"],
                        "spot",
                        "account-a",
                        "BTC-USDT",
                        "exchange-venue-cross-order",
                        "SUBMITTED",
                    ),
                )

                fill_sql = (
                    "INSERT INTO qd_exchange_fill_events "
                    "(id, key_version, dedupe_key, exchange, tenant_id, credential_id, account_scope, market_type, "
                    "economic_order_id, intent_id, instrument_id, side, price, quantity, quote_quantity, "
                    "exchange_event_at, received_at, source, raw_payload_hash, normalizer_version, instrument_rule_version) "
                    "VALUES (%s, 'v1', 'fill-dedupe', 'schema-test', %s, %s, 'account-a', 'spot', %s, %s, 'BTC-USDT', "
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

                ledger_sql = (
                    "INSERT INTO qd_ledger_transactions "
                    "(id, tenant_id, credential_id, transaction_type, source_event_type, source_event_id, "
                    "reverses_transaction_id, effective_at, valuation_ccy, policy_version, description_code) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), 'USDT', 'v1', 'schema-test')"
                )
                original_transaction_id = str(uuid.uuid4())
                cursor.execute(
                    ledger_sql,
                    (
                        original_transaction_id,
                        graph["user_id"],
                        graph["credential_id"],
                        "TRADE",
                        "SCHEMA_TEST",
                        str(uuid.uuid4()),
                        None,
                    ),
                )
                cursor.execute(
                    ledger_sql,
                    (
                        str(uuid.uuid4()),
                        graph["user_id"],
                        graph["credential_id"],
                        "REVERSAL",
                        "SCHEMA_TEST_REVERSAL",
                        str(uuid.uuid4()),
                        original_transaction_id,
                    ),
                )
                self._assert_rejected(
                    cursor,
                    ledger_sql,
                    (
                        str(uuid.uuid4()), graph["user_id"], graph["credential_id"], "REVERSAL",
                        "SCHEMA_TEST_REVERSAL", str(uuid.uuid4()), original_transaction_id,
                    ),
                )
                self._assert_rejected(
                    cursor,
                    ledger_sql,
                    (
                        str(uuid.uuid4()), graph["user_id"], graph["credential_id"], "REVERSAL",
                        "SCHEMA_TEST_REVERSAL", str(uuid.uuid4()), None,
                    ),
                )
                self._assert_rejected(
                    cursor,
                    ledger_sql,
                    (
                        str(uuid.uuid4()), graph["user_id"], graph["credential_id"], "TRADE",
                        "SCHEMA_TEST_TRADE", str(uuid.uuid4()), original_transaction_id,
                    ),
                )

                observation_sql = (
                    "INSERT INTO qd_exchange_order_observations "
                    "(id, attempt_id, observation_source, payload_hash, observed_at) "
                    "VALUES (%s, %s, 'REST', 'attempt-evidence', NOW())"
                )
                cursor.execute(observation_sql, (str(uuid.uuid4()), invalid_state_attempt_id))
                self._assert_rejected(
                    cursor,
                    observation_sql,
                    (str(uuid.uuid4()), invalid_state_attempt_id),
                )

                projection_sql = (
                    "INSERT INTO qd_position_projections "
                    "(id, tenant_id, credential_id, account_scope, instrument_id, side, projection_version, policy_version, rebuilt_at) "
                    "VALUES (%s, %s, %s, 'account-a', 'BTC-USDT', 'LONG', 1, 'v1', NOW())"
                )
                cursor.execute(projection_sql, (str(uuid.uuid4()), graph["user_id"], graph["credential_id"]))
                self._assert_rejected(
                    cursor,
                    projection_sql,
                    (str(uuid.uuid4()), graph["user_id"], graph["credential_id"]),
                )

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
                declared_economic_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO qd_order_intents_v2 "
                    "(id, command_id, tenant_id, credential_id, economic_order_id, intent_version, account_scope, "
                    "instrument_id, market_type, side, order_type, execution_algo, target_quantity, "
                    "instrument_rule_snapshot_id, instrument_rule_version, rounding_mode, payload_hash) "
                    "VALUES (%s, %s, %s, %s, %s, 2, 'account-a', 'BTC-USDT', 'spot', 'BUY', 'LIMIT', 'DIRECT', "
                    "'1', %s, 'v1', 'ROUND_DOWN', 'bad-intent')",
                    (bad_intent_id, graph["command_id"], graph["user_id"], graph["credential_id"], declared_economic_id, graph["snapshot_id"]),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_economic_orders "
                    "(id, intent_id, tenant_id, user_id, credential_id, account_scope, instrument_id, market_type, state, target_quantity) "
                    "VALUES (%s, %s, %s, %s, %s, 'account-a', 'BTC-USDT', 'spot', 'CREATED', '1')",
                    (str(uuid.uuid4()), bad_intent_id, graph["user_id"], graph["user_id"], graph["credential_id"]),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_economic_orders "
                    "(id, intent_id, tenant_id, user_id, credential_id, account_scope, instrument_id, market_type, state, target_quantity) "
                    "VALUES (%s, %s, %s, %s, %s, 'different-account', 'BTC-USDT', 'spot', 'CREATED', '1')",
                    (str(uuid.uuid4()), bad_intent_id, graph["user_id"], graph["user_id"], graph["credential_id"]),
                )

                invalid_state_intent_id = str(uuid.uuid4())
                invalid_state_economic_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO qd_order_intents_v2 "
                    "(id, command_id, tenant_id, credential_id, economic_order_id, intent_version, account_scope, "
                    "instrument_id, market_type, side, order_type, execution_algo, target_quantity, "
                    "instrument_rule_snapshot_id, instrument_rule_version, rounding_mode, payload_hash) "
                    "VALUES (%s, %s, %s, %s, %s, 3, 'account-a', 'BTC-USDT', 'spot', 'BUY', 'LIMIT', 'DIRECT', "
                    "'1', %s, 'v1', 'ROUND_DOWN', 'invalid-state-intent')",
                    (invalid_state_intent_id, graph["command_id"], graph["user_id"], graph["credential_id"], invalid_state_economic_id, graph["snapshot_id"]),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_economic_orders "
                    "(id, intent_id, tenant_id, user_id, credential_id, account_scope, instrument_id, market_type, state, target_quantity) "
                    "VALUES (%s, %s, %s, %s, %s, 'account-a', 'BTC-USDT', 'spot', 'INVALID', '1')",
                    (invalid_state_economic_id, invalid_state_intent_id, graph["user_id"], graph["user_id"], graph["credential_id"]),
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

    def test_state_recovery_and_multifee_preconditions_enforce_contracts(self):
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            connection.autocommit = False
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                for migration in INCREMENTAL_MIGRATIONS:
                    cursor.execute(migration.read_text(encoding="utf-8"))
                graph = self._create_order_graph(cursor)

                capability_snapshot_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO qd_venue_capability_snapshots "
                    "(id, exchange, market_type, capability_version, profile_hash, "
                    "accepts_external_client_order_id, can_generate_safe_client_order_id, "
                    "query_by_exchange_order_id, query_by_client_order_id, list_order_fills, stable_fill_id) "
                    "VALUES (%s, 'schema-test', 'spot', 'v1', 'capability-hash', TRUE, FALSE, TRUE, TRUE, TRUE, TRUE)",
                    (capability_snapshot_id,),
                )
                policy_snapshot_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO qd_submission_recovery_policy_snapshots "
                    "(id, exchange, market_type, policy_version, policy_hash, capability_snapshot_id, "
                    "capability_query_by_client_order_id, client_id_query_authoritative, "
                    "order_history_authoritative, fill_history_authoritative, not_found_min_query_count, "
                    "not_found_grace_seconds, not_found_action) "
                    "VALUES (%s, 'schema-test', 'spot', 'v1', 'policy-hash', %s, TRUE, TRUE, TRUE, TRUE, 2, 30, 'KEEP_UNKNOWN')",
                    (policy_snapshot_id, capability_snapshot_id),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_submission_recovery_policy_snapshots "
                    "(id, exchange, market_type, policy_version, policy_hash, capability_snapshot_id, "
                    "capability_query_by_client_order_id, client_id_query_authoritative, order_history_authoritative, "
                    "fill_history_authoritative, not_found_min_query_count, not_found_grace_seconds, not_found_action) "
                    "VALUES (%s, 'schema-test', 'spot', 'confirm-v1', 'confirm-hash', %s, TRUE, TRUE, TRUE, TRUE, 2, 30, 'CONFIRM_ABSENT')",
                    (str(uuid.uuid4()), capability_snapshot_id),
                )
                attempt_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO qd_submission_attempts "
                    "(id, economic_order_id, exchange, tenant_id, credential_id, account_scope, instrument_id, market_type, "
                    "child_seq, attempt_no, role, canonical_client_order_id, venue_client_order_id, request_fingerprint, state, "
                    "venue_capability_snapshot_id, recovery_policy_snapshot_id, client_id_algorithm_version, "
                    "broker_prefix_normalization_version, broker_prefix, canonical_contract_version) "
                    "VALUES (%s, %s, 'schema-test', %s, %s, 'account-a', 'BTC-USDT', 'spot', 1, 1, 'PRIMARY', "
                    "'canonical-1', 'venue-1', 'request-hash', 'READY', %s, %s, 'v1', 'v1', 'broker', 'attempt-contract-v1')",
                    (
                        attempt_id,
                        graph["economic_order_id"],
                        graph["user_id"],
                        graph["credential_id"],
                        capability_snapshot_id,
                        policy_snapshot_id,
                    ),
                )
                cursor.execute(
                    "INSERT INTO qd_submission_attempts "
                    "(id, economic_order_id, exchange, tenant_id, credential_id, account_scope, instrument_id, market_type, "
                    "child_seq, attempt_no, role, canonical_client_order_id, venue_client_order_id, request_fingerprint, state) "
                    "VALUES (%s, %s, 'schema-test', %s, %s, 'account-a', 'BTC-USDT', 'spot', 2, 1, 'PRIMARY', "
                    "'legacy-2', 'venue-legacy-2', 'legacy-request-hash', 'READY')",
                    (str(uuid.uuid4()), graph["economic_order_id"], graph["user_id"], graph["credential_id"]),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_submission_attempts "
                    "(id, economic_order_id, exchange, tenant_id, credential_id, account_scope, instrument_id, market_type, "
                    "child_seq, attempt_no, role, canonical_client_order_id, venue_client_order_id, request_fingerprint, state, "
                    "venue_capability_snapshot_id, canonical_contract_version) "
                    "VALUES (%s, %s, 'schema-test', %s, %s, 'account-a', 'BTC-USDT', 'spot', 3, 1, 'PRIMARY', "
                    "'partial-3', 'venue-partial-3', 'partial-request-hash', 'READY', %s, NULL)",
                    (
                        str(uuid.uuid4()), graph["economic_order_id"], graph["user_id"], graph["credential_id"],
                        capability_snapshot_id,
                    ),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_submission_attempts "
                    "(id, economic_order_id, exchange, tenant_id, credential_id, account_scope, instrument_id, market_type, "
                    "child_seq, attempt_no, role, canonical_client_order_id, venue_client_order_id, request_fingerprint, state, "
                    "venue_capability_snapshot_id, recovery_policy_snapshot_id, client_id_algorithm_version, "
                    "broker_prefix_normalization_version, broker_prefix, canonical_contract_version) "
                    "VALUES (%s, %s, 'schema-test', %s, %s, 'account-a', 'BTC-USDT', 'spot', 4, 1, 'PRIMARY', "
                    "'partial-4', 'venue-partial-4', 'partial-request-hash', 'READY', %s, %s, 'v1', 'v1', NULL, "
                    "'attempt-contract-v1')",
                    (
                        str(uuid.uuid4()), graph["economic_order_id"], graph["user_id"], graph["credential_id"],
                        capability_snapshot_id, policy_snapshot_id,
                    ),
                )
                self._assert_rejected(
                    cursor,
                    "UPDATE qd_submission_attempts SET market_type = 'swap' WHERE id = %s",
                    (attempt_id,),
                )
                self._assert_rejected(
                    cursor,
                    "UPDATE qd_submission_recovery_policy_snapshots "
                    "SET capability_query_by_client_order_id = FALSE WHERE id = %s",
                    (policy_snapshot_id,),
                )
                state_event_sql = (
                    "INSERT INTO qd_submission_attempt_state_events "
                    "(id, attempt_id, economic_order_id, event_seq, expected_version, resulting_version, "
                    "from_state, to_state, reason_code, actor_type, idempotency_key, event_fingerprint, occurred_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'READY', 'SUBMITTING', 'SCHEMA_TEST', 'HUMAN', %s, %s, NOW())"
                )
                cursor.execute(
                    state_event_sql,
                    (
                        str(uuid.uuid4()),
                        attempt_id,
                        graph["economic_order_id"],
                        1,
                        0,
                        1,
                        "attempt-event-1",
                        "attempt-fingerprint-1",
                    ),
                )
                self._assert_rejected(
                    cursor,
                    state_event_sql,
                    (
                        str(uuid.uuid4()),
                        attempt_id,
                        graph["economic_order_id"],
                        2,
                        1,
                        2,
                        "attempt-event-1",
                        "attempt-fingerprint-2",
                    ),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_order_state_events "
                    "(id, economic_order_id, event_seq, to_state, reason_code, actor_type, occurred_at, expected_version) "
                    "VALUES (%s, %s, 99, 'RISK_PENDING', 'SCHEMA_TEST', 'HUMAN', NOW(), 0)",
                    (str(uuid.uuid4()), graph["economic_order_id"]),
                )
                self._assert_rejected(
                    cursor,
                    state_event_sql,
                    (
                        str(uuid.uuid4()),
                        attempt_id,
                        graph["economic_order_id"],
                        2,
                        1,
                        3,
                        "attempt-event-2",
                        "attempt-fingerprint-2",
                    ),
                )
                other_graph = self._create_order_graph(cursor)
                self._assert_rejected(
                    cursor,
                    state_event_sql,
                    (
                        str(uuid.uuid4()),
                        attempt_id,
                        other_graph["economic_order_id"],
                        2,
                        1,
                        2,
                        "attempt-event-cross-order",
                        "attempt-fingerprint-cross-order",
                    ),
                )

                canonical_order_event_graph = self._create_order_graph(cursor)
                order_event_sql = (
                    "INSERT INTO qd_order_state_events "
                    "(id, economic_order_id, event_seq, to_state, reason_code, actor_type, occurred_at, "
                    "expected_version, resulting_version, idempotency_key, event_fingerprint, correlation_id, "
                    "canonical_payload_json) "
                    "VALUES (%s, %s, %s, 'RISK_PENDING', 'SCHEMA_TEST', 'HUMAN', NOW(), %s, %s, %s, %s, %s, "
                    "'{}'::jsonb)"
                )
                cursor.execute(
                    order_event_sql,
                    (
                        str(uuid.uuid4()), canonical_order_event_graph["economic_order_id"], 1, 0, 1,
                        "order-event-1", "order-fingerprint-1", "order-correlation-1",
                    ),
                )
                self._assert_rejected(
                    cursor,
                    order_event_sql,
                    (
                        str(uuid.uuid4()), canonical_order_event_graph["economic_order_id"], 3, 1, 2,
                        "order-event-2", "order-fingerprint-2", "order-correlation-2",
                    ),
                )

                fill_event_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO qd_exchange_fill_events "
                    "(id, key_version, dedupe_key, exchange, tenant_id, credential_id, account_scope, market_type, "
                    "economic_order_id, intent_id, instrument_id, side, price, quantity, quote_quantity, quote_quantity_origin, "
                    "quote_quantity_evidence_hash, fee_summary_state, exchange_event_at, received_at, source, raw_payload_hash, normalizer_version, instrument_rule_version) "
                    "VALUES (%s, 'venue-fill-id-v1', %s, 'schema-test', %s, %s, 'account-a', 'spot', %s, %s, "
                    "'BTC-USDT', 'SELL', '61000', '0.01', '610', 'VENUE', 'venue-quote-evidence', 'MULTI_COMPONENT', NOW(), NOW(), 'REST', "
                    "'fill-payload-hash', 'v1', 'v1')",
                    (
                        fill_event_id,
                        f"fill-dedupe-{uuid.uuid4().hex}",
                        graph["user_id"],
                        graph["credential_id"],
                        graph["economic_order_id"],
                        graph["intent_id"],
                    ),
                )
                valuation_evidence_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO qd_ledger_valuation_evidence "
                    "(id, fill_event_id, asset, valuation_ccy, price, evidence_source, policy_version, observed_at, payload_hash) "
                    "VALUES (%s, %s, 'USDT', 'USDT', '1', 'IDENTITY', 'identity-v1', NOW(), 'identity-usdt')",
                    (valuation_evidence_id, fill_event_id),
                )
                cursor.execute(
                    "INSERT INTO qd_ledger_valuation_evidence "
                    "(id, fill_event_id, asset, valuation_ccy, price, evidence_source, policy_version, observed_at, payload_hash) "
                    "VALUES (%s, %s, 'BNB', 'BNB', '1', 'IDENTITY', 'identity-v1', NOW(), 'identity-bnb')",
                    (str(uuid.uuid4()), fill_event_id),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_ledger_valuation_evidence "
                    "(id, fill_event_id, asset, valuation_ccy, price, evidence_source, policy_version, observed_at, payload_hash) "
                    "VALUES (%s, %s, 'BNB', 'USDT', '1', 'IDENTITY', 'identity-v1', NOW(), 'identity-cross-asset')",
                    (str(uuid.uuid4()), fill_event_id),
                )
                self._assert_rejected(
                    cursor,
                    "INSERT INTO qd_ledger_valuation_evidence "
                    "(id, fill_event_id, asset, valuation_ccy, price, evidence_source, policy_version, observed_at, payload_hash) "
                    "VALUES (%s, %s, 'BNB', 'BNB', '2', 'IDENTITY', 'identity-v1', NOW(), 'identity-wrong-price')",
                    (str(uuid.uuid4()), fill_event_id),
                )
                fee_sql = (
                    "INSERT INTO qd_exchange_fill_fee_components "
                    "(fill_event_id, fee_seq, asset, amount, fee_quote_amount, valuation_ccy, valuation_evidence_id, raw_component_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                )
                cursor.execute(fee_sql, (fill_event_id, 1, "USDT", "0.610", "0.610", "USDT", valuation_evidence_id, "fee-usdt"))
                cursor.execute(fee_sql, (fill_event_id, 2, "BNB", "0.0015", None, None, None, "fee-bnb"))
                self._assert_rejected(cursor, fee_sql, (fill_event_id, 3, "BNB", "0.0015", None, None, None, "fee-bnb"))
                self._assert_rejected(cursor, fee_sql, (fill_event_id, 3, "USDT", "0.1", "0.1", None, None, "fee-without-evidence"))
                self._assert_rejected(cursor, fee_sql, (fill_event_id, 3, "BNB", "0.1", "0.1", "USDT", valuation_evidence_id, "fee-cross-asset"))
                self._assert_rejected(
                    cursor,
                    "UPDATE qd_exchange_fill_events SET fee_amount = '1', fee_asset = 'USDT' WHERE id = %s",
                    (fill_event_id,),
                )
                self._assert_rejected(
                    cursor,
                    "UPDATE qd_exchange_fill_events SET quote_quantity_policy_version = 'derived-v1' WHERE id = %s",
                    (fill_event_id,),
                )
                self._assert_rejected(
                    cursor,
                    "UPDATE qd_exchange_fill_events SET quote_quantity_origin = 'DERIVED', quote_quantity_policy_version = NULL WHERE id = %s",
                    (fill_event_id,),
                )
                self._assert_rejected(
                    cursor,
                    "DELETE FROM qd_exchange_fill_events WHERE id = %s",
                    (fill_event_id,),
                )
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
