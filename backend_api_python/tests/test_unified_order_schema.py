"""Phase 0 PR-02 expand-only unified-order schema contracts."""

from __future__ import annotations

import os
import re
import unittest
import uuid
import json
from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION = MIGRATIONS / "20260722_unified_order_expand_only.sql"
PRECONDITION_MIGRATION = MIGRATIONS / "20260723_state_recovery_ledger_preconditions.sql"
IMMUTABLE_LEDGER_MIGRATION = MIGRATIONS / "20260724_immutable_fill_ledger_guards.sql"
WAVE2_MIGRATION = MIGRATIONS / "20260725_wave2_persistence_schema.sql"
SHADOW_DIFF_MIGRATION = MIGRATIONS / "20260726_shadow_diff_schema.sql"
RECONCILIATION_MIGRATION = MIGRATIONS / "20260728_reconciliation_health_schema.sql"
DURABLE_ENTRY_MIGRATION = MIGRATIONS / "20260729_durable_entry_specifications.sql"
DURABLE_RISK_V2_MIGRATION = MIGRATIONS / "20260730_durable_risk_enforcement_v2.sql"
AUTHORITATIVE_RISK_FACTS_MIGRATION = MIGRATIONS / "20260731_authoritative_risk_fact_sources.sql"
INCREMENTAL_MIGRATIONS = (MIGRATION, PRECONDITION_MIGRATION, IMMUTABLE_LEDGER_MIGRATION, WAVE2_MIGRATION, SHADOW_DIFF_MIGRATION, RECONCILIATION_MIGRATION, DURABLE_ENTRY_MIGRATION, DURABLE_RISK_V2_MIGRATION, AUTHORITATIVE_RISK_FACTS_MIGRATION)
INIT_SQL = MIGRATIONS / "init.sql"

EXPECTED_TABLES = {
    "qd_order_commands",
    "qd_instrument_rule_snapshots",
    "qd_order_intents_v2",
    "qd_economic_orders",
    "qd_risk_reservations",
    "qd_risk_policy_snapshots",
    "qd_risk_input_snapshots",
    "qd_risk_decisions",
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
    "qd_projection_checkpoints",
    "qd_projection_generations",
    "qd_projection_generation_events",
    "qd_consumer_inbox",
    "qd_projection_snapshots",
    "qd_venue_capability_snapshots",
    "qd_submission_recovery_policy_snapshots",
    "qd_submission_attempt_state_events",
    "qd_ledger_valuation_evidence",
    "qd_exchange_fill_fee_components",
    "qd_shadow_comparison_runs",
    "qd_shadow_diff_facts",
    "qd_reconciliation_runs",
    "qd_reconciliation_discrepancies",
    "qd_durable_entry_specifications",
    "qd_durable_risk_policy_snapshots",
    "qd_durable_risk_input_snapshots",
    "qd_durable_risk_decisions",
    "qd_durable_risk_reservations",
    "qd_authoritative_risk_policies",
    "qd_authoritative_account_risk_facts",
    "qd_authoritative_market_observations",
    "qd_authoritative_kill_switch_observations",
    "qd_durable_risk_fact_provenance",
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
            "uq_qd_ledger_transactions_source_fingerprint",
            "qd_reject_immutable_fill_ledger_mutation",
            "qd_assert_immutable_ledger_transaction_balanced",
            "ctrg_qd_ledger_transactions_balanced",
            "ctrg_qd_ledger_entries_balanced",
        ):
            self.assertIn(fragment, migration)

    def test_immutable_ledger_schema_is_expand_only_and_commit_time_guarded(self):
        migration = IMMUTABLE_LEDGER_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS account_scope VARCHAR(160)", migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS source_fingerprint VARCHAR(128)", migration)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", migration)
        self.assertIn("GROUP BY book, asset", migration)
        for table in (
            "qd_exchange_fill_events",
            "qd_exchange_fill_fee_components",
            "qd_ledger_valuation_evidence",
            "qd_ledger_transactions",
            "qd_ledger_entries",
        ):
            self.assertIn(f"{table}_append_only", migration)

    def test_wave2_schema_has_immutable_risk_facts_and_canonical_outbox_identity(self):
        migration = WAVE2_MIGRATION.read_text(encoding="utf-8")
        for table in (
            "qd_risk_policy_snapshots", "qd_risk_input_snapshots", "qd_risk_decisions",
            "qd_projection_checkpoints", "qd_projection_generations", "qd_projection_generation_events",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
        for fragment in (
            "decision_fingerprint VARCHAR(64) NOT NULL",
            "uq_qd_order_commands_command_scope",
            "FOREIGN KEY(command_id, tenant_id, credential_id, account_scope)",
            "FOREIGN KEY(economic_order_id, tenant_id, credential_id, account_scope, instrument_id, market_type)",
            "chk_qd_risk_reservations_enforcement_complete",
            "fk_qd_risk_reservations_enforcement_decision",
            "fk_qd_risk_reservations_enforcement_policy_snapshot",
            "fk_qd_risk_reservations_enforcement_input_snapshot",
            "global_kill_switch_version BIGINT NOT NULL",
            "actor_id VARCHAR(160) NOT NULL",
            "risk_effect VARCHAR(16) NOT NULL",
            "rejection_codes JSONB NOT NULL",
            "projected_gross_notional NUMERIC(38,18) NOT NULL",
            "reserved_gross_notional NUMERIC(38,18)",
            "trg_qd_transactional_outbox_immutable_facts",
            "uq_qd_transactional_outbox_canonical_identity",
            "lease_fencing_token BIGINT NOT NULL DEFAULT 0",
            "qd_guard_risk_reservation_enforcement_update",
            "uq_qd_projection_generations_current_consumer",
            "UNIQUE(generation_id, event_id)",
            "qd_reject_projection_generation_event_mutation",
        ):
            self.assertIn(fragment, migration)
        self.assertNotIn("ON DELETE CASCADE", migration)

    def test_shadow_diff_schema_is_expand_only_and_append_only(self):
        migration = SHADOW_DIFF_MIGRATION.read_text(encoding="utf-8")
        for table in ("qd_shadow_comparison_runs", "qd_shadow_diff_facts"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
        for fragment in (
            "comparison_contract_version = 'shadow-diff-v1'",
            "candidate_consumer_name VARCHAR(160) NOT NULL",
            "candidate_generation_build_fingerprint VARCHAR(64) NOT NULL",
            "tolerance_policy_fingerprint VARCHAR(64) NOT NULL",
            "state IN ('BUILDING','COMPLETE','FAILED')",
            "UNIQUE(tenant_id, credential_id, account_scope, instrument_id, market_type, build_fingerprint)",
            "UNIQUE(run_id, diff_fingerprint)",
            "NUMERIC(38,18)",
            "qd_guard_shadow_comparison_run_update",
            "qd_guard_shadow_diff_fact_insert",
            "trg_qd_shadow_diff_facts_append_only",
        ):
            self.assertIn(fragment, migration)
        self.assertNotIn("ON DELETE CASCADE", migration)

    def test_reconciliation_schema_is_expand_only_and_uses_persisted_checkpoint_facts(self):
        migration = RECONCILIATION_MIGRATION.read_text(encoding="utf-8")
        for table in ("qd_reconciliation_runs", "qd_reconciliation_discrepancies"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
        for fragment in (
            "reconciliation_contract_version = 'reconciliation-v1'",
            "state IN ('BUILDING','COMPLETE','FAILED')",
            "UNIQUE(run_id, discrepancy_fingerprint)",
            "uq_qd_reconciliation_checkpoints_canonical_result",
            "NUMERIC(38,18)",
            "qd_guard_reconciliation_run_update",
            "qd_guard_reconciliation_discrepancy_insert",
            "trg_qd_reconciliation_checkpoints_append_only",
            "ALTER TABLE qd_reconciliation_checkpoints",
            "reconciliation_run_id UUID",
            "reconciliation_discrepancy_count INTEGER",
        ):
            self.assertIn(fragment, migration)
        self.assertNotIn("ON DELETE CASCADE", migration)

    def test_durable_entry_schema_uses_typed_v2_facts_and_append_only_guards(self):
        migration = DURABLE_ENTRY_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS qd_durable_entry_specifications", migration)
        self.assertIn("contract_version = 'canonical-entry-v2'", migration)
        self.assertIn(
            "UNIQUE (tenant_id, credential_id, account_scope, idempotency_key, contract_version)",
            migration,
        )
        self.assertNotIn("UNIQUE (economic_order_id)", migration)
        for column in (
            "command_id UUID PRIMARY KEY", "economic_order_id UUID", "quantity NUMERIC(38,18)",
            "limit_price NUMERIC(38,18)", "trigger_price NUMERIC(38,18)",
            "close_quantity NUMERIC(38,18)", "cancel_target_kind VARCHAR(24)",
            "target_position_id VARCHAR(160)", "economic_fingerprint VARCHAR(64)",
            "request_fingerprint VARCHAR(64)", "correlation_id VARCHAR(160)",
            "occurred_at TIMESTAMPTZ NOT NULL", "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "qd_reject_durable_entry_specification_mutation",
            "trg_qd_durable_entry_specifications_append_only",
        ):
            self.assertIn(column, migration)
        for action in ("'CANCEL'", "'OPEN'", "'INCREASE'", "'REDUCE'", "'CLOSE'", "'EMERGENCY_CLOSE'", "'PROTECTION'"):
            self.assertIn(action, migration)
        for execution in ("'MARKET'", "'LIMIT'", "'STOP_MARKET'", "'STOP_LIMIT'"):
            self.assertIn(execution, migration)
        self.assertNotIn("ON DELETE CASCADE", migration)

    def test_durable_risk_v2_schema_is_independent_typed_and_append_only(self):
        migration = DURABLE_RISK_V2_MIGRATION.read_text(encoding="utf-8")
        for table in (
            "qd_durable_risk_policy_snapshots",
            "qd_durable_risk_input_snapshots",
            "qd_durable_risk_decisions",
            "qd_durable_risk_reservations",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
            self.assertIn(f"{table}_append_only", migration)
        for column in (
            "durable-risk-enforcement-v2", "command_id UUID NOT NULL",
            "economic_order_id UUID NOT NULL", "economic_fingerprint VARCHAR(64) NOT NULL",
            "request_fingerprint VARCHAR(64) NOT NULL", "scope_fingerprint VARCHAR(64) NOT NULL",
            "audit_fingerprint VARCHAR(64) NOT NULL", "max_gross_notional NUMERIC(38,18) NOT NULL",
            "gross_notional NUMERIC(38,18) NOT NULL", "projected_gross_notional NUMERIC(38,18) NOT NULL",
            "reserved_gross_notional NUMERIC(38,18) NOT NULL", "global_kill_switch_version BIGINT NOT NULL",
            "qd_reject_durable_risk_v2_mutation", "qd_assert_durable_risk_v2_reservation_allowed",
            "qd_assert_durable_risk_v2_scope_matches_entry",
            "trg_qd_durable_risk_reservations_allow_decision", "uq_qd_durable_risk_reservations_active_decision",
        ):
            self.assertIn(column, migration)
        self.assertIn("REFERENCES qd_durable_entry_specifications(command_id) ON DELETE RESTRICT", migration)
        self.assertNotIn("qd_order_commands", migration)
        self.assertNotIn("qd_order_intents_v2", migration)
        self.assertNotIn("qd_economic_orders", migration)
        self.assertNotIn("ON DELETE CASCADE", migration)

    def test_authoritative_risk_fact_sources_are_typed_scoped_and_append_only(self):
        migration = AUTHORITATIVE_RISK_FACTS_MIGRATION.read_text(encoding="utf-8")
        for table in (
            "qd_authoritative_risk_policies",
            "qd_authoritative_account_risk_facts",
            "qd_authoritative_market_observations",
            "qd_authoritative_kill_switch_observations",
            "qd_durable_risk_fact_provenance",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
            self.assertIn(f"{table}_append_only", migration)
        for fragment in (
            "contract_version = 'authoritative-risk-facts-v1'",
            "strategy_scope VARCHAR(160) NOT NULL",
            "source_identity VARCHAR(160) NOT NULL",
            "source_version VARCHAR(160) NOT NULL",
            "source_fingerprint VARCHAR(64) NOT NULL",
            "observed_at TIMESTAMPTZ NOT NULL",
            "max_age_seconds INTEGER NOT NULL",
            "selection_anchor TIMESTAMPTZ NOT NULL",
            "source_observed_at <= selection_anchor",
            "NUMERIC(38,18)",
            "ON DELETE RESTRICT",
        ):
            self.assertIn(fragment, migration)
        self.assertNotIn("ON DELETE CASCADE", migration)

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

    def _create_durable_entry_scope(self, cursor):
        """Create only a typed durable-entry row; V2 risk never needs legacy orders."""

        suffix = uuid.uuid4().hex
        cursor.execute(
            "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
            (f"durable_risk_v2_{suffix}", "schema-test"),
        )
        tenant_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) "
            "VALUES (%s, %s, %s) RETURNING id",
            (tenant_id, "durable-risk-v2", "{}"),
        )
        credential_id = cursor.fetchone()[0]
        command_id = str(uuid.uuid4())
        economic_order_id = str(uuid.uuid4())
        economic_fingerprint = "a" * 64
        request_fingerprint = "b" * 64
        cursor.execute(
            "INSERT INTO qd_durable_entry_specifications ("
            "command_id, contract_version, tenant_id, credential_id, account_scope, instrument_id, "
            "market_type, action, risk_effect, side, quantity, quantity_semantics, execution_kind, "
            "limit_price, trigger_price, trigger_direction, trigger_price_type, reduce_only, position_side, "
            "cancel_target_kind, cancel_target_id, target_position_id, close_quantity, close_all, "
            "economic_order_id, economic_fingerprint, request_fingerprint, actor_type, actor_id, source, "
            "mode, idempotency_key, correlation_id, occurred_at) VALUES ("
            "%s, 'canonical-entry-v2', %s, %s, 'account-a', 'BTC-USDT', 'spot', 'OPEN', 'INCREASE_RISK', "
            "'BUY', '1', 'ABSOLUTE', 'MARKET', NULL, NULL, NULL, NULL, FALSE, 'NET', NULL, NULL, NULL, "
            "NULL, FALSE, %s, %s, %s, 'HUMAN', 'human-a', 'REST', 'PAPER', %s, 'correlation-a', "
            "'2026-07-30T00:00:00+00:00')",
            (command_id, tenant_id, credential_id, economic_order_id, economic_fingerprint,
             request_fingerprint, f"durable-risk-key-{suffix}"),
        )
        return {
            "contract_version": "durable-risk-enforcement-v2",
            "command_id": command_id,
            "economic_order_id": economic_order_id,
            "durable_entry_contract_version": "canonical-entry-v2",
            "economic_fingerprint": economic_fingerprint,
            "request_fingerprint": request_fingerprint,
            "tenant_id": tenant_id,
            "credential_id": credential_id,
            "account_scope": "account-a",
            "instrument_id": "BTC-USDT",
            "market_type": "spot",
            "action": "OPEN",
            "risk_effect": "INCREASE_RISK",
            "actor_type": "HUMAN",
            "actor_id": "human-a",
            "source": "REST",
            "mode": "PAPER",
            "correlation_id": "correlation-a",
            "entry_occurred_at": "2026-07-30T00:00:00+00:00",
            "scope_fingerprint": "c" * 64,
            "audit_fingerprint": "d" * 64,
        }

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
                    "(id, tenant_id, credential_id, account_scope, transaction_type, source_event_type, source_event_id, source_fingerprint, "
                    "reverses_transaction_id, effective_at, valuation_ccy, policy_version, description_code) "
                    "VALUES (%s, %s, %s, 'account-a', %s, %s, %s, %s, %s, NOW(), 'USDT', 'v1', 'schema-test')"
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
                        uuid.uuid4().hex + uuid.uuid4().hex,
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
                        uuid.uuid4().hex + uuid.uuid4().hex,
                        original_transaction_id,
                    ),
                )
                self._assert_rejected(
                    cursor,
                    ledger_sql,
                    (
                        str(uuid.uuid4()), graph["user_id"], graph["credential_id"], "REVERSAL",
                        "SCHEMA_TEST_REVERSAL", str(uuid.uuid4()), uuid.uuid4().hex + uuid.uuid4().hex, original_transaction_id,
                    ),
                )
                self._assert_rejected(
                    cursor,
                    ledger_sql,
                    (
                        str(uuid.uuid4()), graph["user_id"], graph["credential_id"], "REVERSAL",
                        "SCHEMA_TEST_REVERSAL", str(uuid.uuid4()), uuid.uuid4().hex + uuid.uuid4().hex, None,
                    ),
                )
                self._assert_rejected(
                    cursor,
                    ledger_sql,
                    (
                        str(uuid.uuid4()), graph["user_id"], graph["credential_id"], "TRADE",
                        "SCHEMA_TEST_TRADE", str(uuid.uuid4()), uuid.uuid4().hex + uuid.uuid4().hex, original_transaction_id,
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

    def test_durable_entry_specifications_enforce_v2_action_matrix_and_append_only(self):
        import psycopg2

        columns = (
            "command_id", "contract_version", "tenant_id", "credential_id", "account_scope",
            "instrument_id", "market_type", "action", "risk_effect", "side", "quantity",
            "quantity_semantics", "execution_kind", "limit_price", "trigger_price",
            "trigger_direction", "trigger_price_type", "reduce_only", "position_side",
            "cancel_target_kind", "cancel_target_id", "target_position_id", "close_quantity",
            "close_all", "economic_order_id", "economic_fingerprint", "request_fingerprint",
            "actor_type", "actor_id", "source", "mode", "idempotency_key", "correlation_id",
            "occurred_at",
        )
        statement = (
            "INSERT INTO qd_durable_entry_specifications (" + ", ".join(columns) + ") VALUES ("
            + ", ".join(["%s"] * len(columns)) + ")"
        )
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            connection.autocommit = False
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                cursor.execute(DURABLE_ENTRY_MIGRATION.read_text(encoding="utf-8"))
                cursor.execute(DURABLE_ENTRY_MIGRATION.read_text(encoding="utf-8"))
                graph = self._create_order_graph(cursor)
                sequence = 0

                def insert_specification(action, execution=None, *, close_all=False, cancel_kind=None,
                                         cancel_target_id=None, command_id=None, idempotency_key=None,
                                         write=True, **overrides):
                    nonlocal sequence
                    sequence += 1
                    is_cancel = action == "CANCEL"
                    is_reducing = action in {"REDUCE", "CLOSE", "EMERGENCY_CLOSE", "PROTECTION"}
                    is_stop = execution in {"STOP_MARKET", "STOP_LIMIT"}
                    is_limit = execution in {"LIMIT", "STOP_LIMIT"}
                    token = f"{sequence:064x}"
                    values = {
                        "command_id": command_id or str(uuid.uuid4()),
                        "contract_version": "canonical-entry-v2",
                        "tenant_id": graph["user_id"],
                        "credential_id": graph["credential_id"],
                        "account_scope": "account-a",
                        "instrument_id": "BTC-USDT",
                        "market_type": "spot",
                        "action": action,
                        "risk_effect": "NEUTRAL" if is_cancel else "REDUCE_RISK" if is_reducing else "INCREASE_RISK",
                        "side": None if is_cancel else "SELL" if is_reducing else "BUY",
                        "quantity": "1" if not is_cancel and not is_reducing else None,
                        "quantity_semantics": "ABSOLUTE" if not is_cancel and not is_reducing else None,
                        "execution_kind": execution,
                        "limit_price": "100" if is_limit else None,
                        "trigger_price": "99" if is_stop else None,
                        "trigger_direction": "AT_OR_BELOW" if is_stop else None,
                        "trigger_price_type": "MARK" if is_stop else None,
                        "reduce_only": False if not is_reducing else True,
                        "position_side": "NET",
                        "cancel_target_kind": cancel_kind if is_cancel else None,
                        "cancel_target_id": cancel_target_id if is_cancel else None,
                        "target_position_id": "position-a" if is_reducing else None,
                        "close_quantity": None if not is_reducing or close_all else "1",
                        "close_all": close_all if is_reducing else False,
                        "economic_order_id": None if is_cancel else str(uuid.uuid4()),
                        "economic_fingerprint": token,
                        "request_fingerprint": token,
                        "actor_type": "PROTECTION" if action == "PROTECTION" else "HUMAN",
                        "actor_id": "protection-a" if action == "PROTECTION" else "human-a",
                        "source": "PROTECTION" if action == "PROTECTION" else "REST",
                        "mode": "PAPER",
                        "idempotency_key": idempotency_key or f"durable-entry-{sequence}",
                        "correlation_id": f"correlation-{sequence}",
                        "occurred_at": "2026-07-29T00:00:00+00:00",
                    }
                    values.update(overrides)
                    if write:
                        cursor.execute(statement, tuple(values[column] for column in columns))
                    return values

                for action in ("OPEN", "INCREASE"):
                    for execution in ("MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT"):
                        insert_specification(action, execution)
                for action in ("REDUCE", "CLOSE", "EMERGENCY_CLOSE", "PROTECTION"):
                    for close_all in (False, True):
                        for execution in ("MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT"):
                            insert_specification(action, execution, close_all=close_all)
                for kind, target in (
                    ("ECONOMIC_ORDER_ID", str(uuid.uuid4())),
                    ("CLIENT_ORDER_ID", "client-order-a"),
                    ("VENUE_ORDER_ID", "venue-order-a"),
                ):
                    insert_specification("CANCEL", cancel_kind=kind, cancel_target_id=target)

                invalid_cancel = insert_specification(
                    "CANCEL", cancel_kind="CLIENT_ORDER_ID", cancel_target_id="cancel-invalid",
                    economic_order_id=str(uuid.uuid4()), write=False,
                )
                self._assert_rejected(cursor, statement, tuple(invalid_cancel[column] for column in columns))
                invalid_close_all = insert_specification(
                    "CLOSE", "MARKET", close_all=True, quantity="1", write=False,
                )
                self._assert_rejected(cursor, statement, tuple(invalid_close_all[column] for column in columns))
                invalid_stop = insert_specification(
                    "OPEN", "STOP_MARKET", trigger_direction=None, write=False,
                )
                self._assert_rejected(cursor, statement, tuple(invalid_stop[column] for column in columns))
                invalid_risk = insert_specification("OPEN", "MARKET", risk_effect="REDUCE_RISK", write=False)
                self._assert_rejected(cursor, statement, tuple(invalid_risk[column] for column in columns))
                invalid_subject = insert_specification(
                    "CANCEL", cancel_kind="ECONOMIC_ORDER_ID", cancel_target_id="not-a-uuid", write=False,
                )
                self._assert_rejected(cursor, statement, tuple(invalid_subject[column] for column in columns))

                replay = insert_specification("OPEN", "MARKET")
                duplicate = dict(replay)
                duplicate["command_id"] = str(uuid.uuid4())
                self._assert_rejected(cursor, statement, tuple(duplicate[column] for column in columns))
                conflict = dict(replay)
                conflict["command_id"] = str(uuid.uuid4())
                conflict["correlation_id"] = "different-audit-fact"
                self._assert_rejected(cursor, statement, tuple(conflict[column] for column in columns))

                self._assert_rejected(
                    cursor,
                    "UPDATE qd_durable_entry_specifications SET actor_id = 'changed' WHERE command_id = %s",
                    (replay["command_id"],),
                )
                self._assert_rejected(
                    cursor,
                    "DELETE FROM qd_durable_entry_specifications WHERE command_id = %s",
                    (replay["command_id"],),
                )

                rollback_key = f"rollback-{uuid.uuid4().hex}"
                rolled_back = insert_specification("OPEN", "MARKET", idempotency_key=rollback_key)
                connection.rollback()
                with connection.cursor() as reuse_cursor:
                    reuse_cursor.execute(
                        "SELECT COUNT(*) FROM qd_durable_entry_specifications WHERE idempotency_key = %s",
                        (rollback_key,),
                    )
                    self.assertEqual(reuse_cursor.fetchone()[0], 0)
                    # The same connection remains usable after caller-owned rollback.
                    reuse_cursor.execute("SELECT 1")
                    self.assertEqual(reuse_cursor.fetchone()[0], 1)
                self.assertIsNotNone(rolled_back["command_id"])
        finally:
            connection.rollback()
            connection.close()

    def test_durable_risk_v2_is_independent_scoped_and_append_only(self):
        import psycopg2

        scope_columns = (
            "contract_version", "command_id", "economic_order_id", "durable_entry_contract_version",
            "economic_fingerprint", "request_fingerprint", "tenant_id", "credential_id",
            "account_scope", "instrument_id", "market_type", "action", "risk_effect",
            "actor_type", "actor_id", "source", "mode", "correlation_id",
            "entry_occurred_at", "scope_fingerprint", "audit_fingerprint",
        )

        def statement(table, columns):
            return (
                f"INSERT INTO {table} (" + ", ".join(columns) + ") VALUES ("
                + ", ".join(["%s"] * len(columns)) + ")"
            )

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            connection.autocommit = False
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                cursor.execute(DURABLE_RISK_V2_MIGRATION.read_text(encoding="utf-8"))
                cursor.execute(DURABLE_RISK_V2_MIGRATION.read_text(encoding="utf-8"))
                scope = self._create_durable_entry_scope(cursor)

                policy_columns = (*scope_columns, "id", "policy_hash", "policy_version", "valuation_currency",
                                  "max_gross_notional", "max_net_notional", "max_instrument_notional",
                                  "max_leverage", "minimum_available_margin", "max_daily_loss",
                                  "max_drawdown_ratio", "policy_payload_json")
                input_columns = (*scope_columns, "id", "input_hash", "input_version", "valuation_currency",
                                 "gross_notional", "net_notional", "instrument_notional", "available_margin",
                                 "equity", "peak_equity", "daily_realized_pnl", "reconciliation_health",
                                 "market_data_health", "account_facts_verified", "global_kill_switch_version",
                                 "global_kill_switch_enabled", "global_kill_switch_mode",
                                 "account_kill_switch_version", "account_kill_switch_enabled", "account_kill_switch_mode",
                                 "strategy_kill_switch_version", "strategy_kill_switch_enabled", "strategy_kill_switch_mode",
                                 "exposure_payload_json", "kill_switch_payload_json", "observed_at")
                decision_columns = (*scope_columns, "id", "policy_snapshot_id", "input_snapshot_id",
                                    "policy_hash", "input_hash", "decision_fingerprint", "allowed",
                                    "decision_status", "rejection_codes_json", "projected_gross_notional",
                                    "projected_net_notional", "projected_instrument_notional",
                                    "projected_available_margin", "projected_leverage", "projected_daily_loss",
                                    "projected_drawdown_ratio", "projected_risk_payload_json")
                reservation_columns = (*scope_columns, "id", "decision_id", "reservation_hash",
                                       "valuation_currency", "reserved_gross_notional", "reserved_net_notional",
                                       "reserved_instrument_notional", "reserved_margin", "state", "expires_at")

                policy = {
                    **scope, "id": str(uuid.uuid4()), "policy_hash": "e" * 64, "policy_version": "policy-v1",
                    "valuation_currency": "USDT", "max_gross_notional": "100", "max_net_notional": "100",
                    "max_instrument_notional": "100", "max_leverage": "2", "minimum_available_margin": "1",
                    "max_daily_loss": "50", "max_drawdown_ratio": "0.5", "policy_payload_json": json.dumps({"audit": True}),
                }
                input_snapshot = {
                    **scope, "id": str(uuid.uuid4()), "input_hash": "f" * 64, "input_version": "input-v1",
                    "valuation_currency": "USDT", "gross_notional": "1", "net_notional": "1",
                    "instrument_notional": "1", "available_margin": "99", "equity": "100",
                    "peak_equity": "100", "daily_realized_pnl": "0", "reconciliation_health": "HEALTHY",
                    "market_data_health": "FRESH", "account_facts_verified": True,
                    "global_kill_switch_version": 0, "global_kill_switch_enabled": False, "global_kill_switch_mode": None,
                    "account_kill_switch_version": 0, "account_kill_switch_enabled": False, "account_kill_switch_mode": None,
                    "strategy_kill_switch_version": 0, "strategy_kill_switch_enabled": False, "strategy_kill_switch_mode": None,
                    "exposure_payload_json": json.dumps({"audit": True}), "kill_switch_payload_json": json.dumps({"audit": True}),
                    "observed_at": "2026-07-30T00:00:00+00:00",
                }
                cursor.execute(statement("qd_durable_risk_policy_snapshots", policy_columns), tuple(policy[name] for name in policy_columns))
                cursor.execute(statement("qd_durable_risk_input_snapshots", input_columns), tuple(input_snapshot[name] for name in input_columns))
                decision = {
                    **scope, "id": str(uuid.uuid4()), "policy_snapshot_id": policy["id"],
                    "input_snapshot_id": input_snapshot["id"], "policy_hash": policy["policy_hash"],
                    "input_hash": input_snapshot["input_hash"], "decision_fingerprint": "1" * 64,
                    "allowed": True, "decision_status": "ALLOW", "rejection_codes_json": json.dumps([]),
                    "projected_gross_notional": "1", "projected_net_notional": "1",
                    "projected_instrument_notional": "1", "projected_available_margin": "99",
                    "projected_leverage": "0.01", "projected_daily_loss": "0",
                    "projected_drawdown_ratio": "0", "projected_risk_payload_json": json.dumps({"audit": True}),
                }
                cursor.execute(statement("qd_durable_risk_decisions", decision_columns), tuple(decision[name] for name in decision_columns))
                reservation = {
                    **scope, "id": str(uuid.uuid4()), "decision_id": decision["id"], "reservation_hash": "2" * 64,
                    "valuation_currency": "USDT", "reserved_gross_notional": "1", "reserved_net_notional": "1",
                    "reserved_instrument_notional": "1", "reserved_margin": "1", "state": "ACTIVE", "expires_at": None,
                }
                cursor.execute(statement("qd_durable_risk_reservations", reservation_columns), tuple(reservation[name] for name in reservation_columns))

                invalid_cancel = dict(policy)
                invalid_cancel["id"] = str(uuid.uuid4())
                invalid_cancel["action"] = "CANCEL"
                invalid_cancel["risk_effect"] = "NEUTRAL"
                self._assert_rejected(cursor, statement("qd_durable_risk_policy_snapshots", policy_columns), tuple(invalid_cancel[name] for name in policy_columns))
                invalid_entry_scope = dict(policy)
                invalid_entry_scope["id"] = str(uuid.uuid4())
                invalid_entry_scope["actor_id"] = "different-human"
                self._assert_rejected(cursor, statement("qd_durable_risk_policy_snapshots", policy_columns), tuple(invalid_entry_scope[name] for name in policy_columns))
                invalid_scope = dict(decision)
                invalid_scope["id"] = str(uuid.uuid4())
                invalid_scope["account_scope"] = "other-account"
                self._assert_rejected(cursor, statement("qd_durable_risk_decisions", decision_columns), tuple(invalid_scope[name] for name in decision_columns))
                invalid_reservation = dict(reservation)
                invalid_reservation["id"] = str(uuid.uuid4())
                invalid_reservation["action"] = "REDUCE"
                invalid_reservation["risk_effect"] = "REDUCE_RISK"
                self._assert_rejected(cursor, statement("qd_durable_risk_reservations", reservation_columns), tuple(invalid_reservation[name] for name in reservation_columns))
                denied_decision = dict(decision)
                denied_decision["id"] = str(uuid.uuid4())
                denied_decision["decision_fingerprint"] = "3" * 64
                denied_decision["allowed"] = False
                denied_decision["decision_status"] = "DENY"
                denied_decision["rejection_codes_json"] = json.dumps(["KILL_SWITCH"])
                cursor.execute(statement("qd_durable_risk_decisions", decision_columns), tuple(denied_decision[name] for name in decision_columns))
                denied_reservation = dict(reservation)
                denied_reservation["id"] = str(uuid.uuid4())
                denied_reservation["decision_id"] = denied_decision["id"]
                denied_reservation["reservation_hash"] = "4" * 64
                self._assert_rejected(cursor, statement("qd_durable_risk_reservations", reservation_columns), tuple(denied_reservation[name] for name in reservation_columns))
                duplicate_reservation = dict(reservation)
                duplicate_reservation["id"] = str(uuid.uuid4())
                self._assert_rejected(cursor, statement("qd_durable_risk_reservations", reservation_columns), tuple(duplicate_reservation[name] for name in reservation_columns))
                self._assert_rejected(cursor, "UPDATE qd_durable_risk_policy_snapshots SET policy_version = 'changed' WHERE id = %s", (policy["id"],))
                self._assert_rejected(cursor, "UPDATE qd_durable_risk_input_snapshots SET input_version = 'changed' WHERE id = %s", (input_snapshot["id"],))
                self._assert_rejected(cursor, "UPDATE qd_durable_risk_decisions SET allowed = FALSE WHERE id = %s", (decision["id"],))
                self._assert_rejected(cursor, "UPDATE qd_durable_risk_reservations SET state = 'RELEASED' WHERE id = %s", (reservation["id"],))
                self._assert_rejected(cursor, "DELETE FROM qd_durable_risk_decisions WHERE id = %s", (decision["id"],))
                self._assert_rejected(cursor, "DELETE FROM qd_durable_risk_reservations WHERE id = %s", (reservation["id"],))
                missing_command = dict(policy)
                missing_command["id"] = str(uuid.uuid4())
                missing_command["command_id"] = str(uuid.uuid4())
                self._assert_rejected(cursor, statement("qd_durable_risk_policy_snapshots", policy_columns), tuple(missing_command[name] for name in policy_columns))
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
