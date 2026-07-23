"""PostgreSQL-only PR-05 atomicity checks; skipped locally without DATABASE_URL."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import unittest
from uuid import uuid4

from tests.pr05_contract_loader import load_pr05_contracts
from tests import test_unified_order_schema as schema_tests


modules = load_pr05_contracts()
contracts = modules.contracts
machine = modules.machine
recovery = modules.recovery
state_repository = modules.states
recovery_repository = modules.recovery_repo
venue = modules.venue


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class OrderStateRepositoryPostgresTests(unittest.TestCase):
    def _connection_and_graph(self):
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        connection.autocommit = False
        cursor = connection.cursor()
        cursor.execute(schema_tests.INIT_SQL.read_text(encoding="utf-8"))
        for migration in schema_tests.INCREMENTAL_MIGRATIONS:
            cursor.execute(migration.read_text(encoding="utf-8"))
        graph = schema_tests.UnifiedOrderSchemaPostgresTests()._create_order_graph(cursor)
        return connection, cursor, graph

    def _unknown_attempt(self, cursor, graph):
        capability_id, policy_id, attempt_id = str(uuid4()), str(uuid4()), str(uuid4())
        suffix = uuid4().hex
        cursor.execute(
            "INSERT INTO qd_venue_capability_snapshots "
            "(id, exchange, market_type, capability_version, profile_hash, accepts_external_client_order_id, "
            "can_generate_safe_client_order_id, query_by_exchange_order_id, query_by_client_order_id, list_order_fills, stable_fill_id) "
            "VALUES (%s, %s, 'spot', 'v1', %s, TRUE, FALSE, TRUE, TRUE, TRUE, TRUE)",
            (capability_id, f"pr05-{suffix}", f"cap-{suffix}"),
        )
        cursor.execute(
            "INSERT INTO qd_submission_recovery_policy_snapshots "
            "(id, exchange, market_type, policy_version, policy_hash, capability_snapshot_id, capability_query_by_client_order_id, "
            "client_id_query_authoritative, order_history_authoritative, fill_history_authoritative, not_found_min_query_count, "
            "not_found_grace_seconds, not_found_action) "
            "VALUES (%s, %s, 'spot', 'v1', %s, %s, TRUE, TRUE, TRUE, TRUE, 1, 0, 'KEEP_UNKNOWN')",
            (policy_id, f"pr05-{suffix}", f"policy-{suffix}", capability_id),
        )
        cursor.execute("UPDATE qd_economic_orders SET state = 'SUBMISSION_UNKNOWN' WHERE id = %s", (graph["economic_order_id"],))
        cursor.execute(
            "INSERT INTO qd_submission_attempts "
            "(id, economic_order_id, exchange, tenant_id, credential_id, account_scope, instrument_id, market_type, child_seq, attempt_no, "
            "role, canonical_client_order_id, venue_client_order_id, request_fingerprint, state, venue_capability_snapshot_id, "
            "recovery_policy_snapshot_id, client_id_algorithm_version, broker_prefix_normalization_version, broker_prefix, canonical_contract_version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'spot',1,1,'PRIMARY','Q-v1-id','Q-v1-id',%s,'UNKNOWN',%s,%s,'v1','ascii-nonsensitive-v1','Q','attempt-contract-v1')",
            (attempt_id, graph["economic_order_id"], f"pr05-{suffix}", graph["user_id"], graph["credential_id"],
             "account-a", "BTC-USDT", f"request-{suffix}", capability_id, policy_id),
        )
        return attempt_id, capability_id, policy_id, f"pr05-{suffix}"

    def test_recovery_updates_observation_attempt_and_order_atomically(self):
        connection, cursor, graph = self._connection_and_graph()
        try:
            attempt_id, capability_id, policy_id, exchange = self._unknown_attempt(cursor, graph)
            order = recovery.EconomicOrderRecoveryFact(graph["economic_order_id"], graph["user_id"], graph["credential_id"],
                                                        "account-a", "BTC-USDT", "spot",
                                                        contracts.EconomicOrderState.SUBMISSION_UNKNOWN, 0, 0)
            attempt = recovery.SubmissionAttemptRecoveryFact(attempt_id, graph["economic_order_id"], graph["user_id"], graph["credential_id"],
                                                               "account-a", "BTC-USDT", exchange, "spot",
                                                               contracts.SubmissionAttemptState.UNKNOWN, 0, 0, capability_id, policy_id,
                                                               "Q-v1-id", "v1", "ascii-nonsensitive-v1", "Q")
            policy = recovery.RecoveryPolicySnapshotFact(policy_id, capability_id, exchange, "spot", "v1")
            query = venue.NormalizedOrderQuery(venue.OrderQueryStatus.FOUND, venue.OrderQueryReference.CLIENT_ORDER_ID,
                                               exchange, "spot", "account-a", "BTC-USDT", "exchange-1", "Q-v1-id", "SUBMITTED", "NEW")
            decision = recovery.decide_submission_recovery(order=order, attempt=attempt, policy=policy, query=query,
                                                            queried_at=datetime(2026, 7, 23, tzinfo=timezone.utc), correlation_id="pg-recovery")
            result = recovery_repository.SubmissionRecoveryRepository().apply(connection, decision)
            self.assertEqual(recovery_repository.RecoveryDisposition.APPLIED, result.disposition)
            cursor.execute("SELECT state, version, last_event_seq FROM qd_economic_orders WHERE id = %s", (order.id,))
            self.assertEqual(("SUBMITTED", 1, 1), cursor.fetchone())
            cursor.execute("SELECT state, version, last_event_seq FROM qd_submission_attempts WHERE id = %s", (attempt.id,))
            self.assertEqual(("ACKED", 1, 1), cursor.fetchone())
            cursor.execute("SELECT COUNT(*) FROM qd_exchange_order_observations WHERE attempt_id = %s", (attempt.id,))
            self.assertEqual(1, cursor.fetchone()[0])
        finally:
            connection.rollback()
            connection.close()

    def test_not_found_only_appends_observation(self):
        connection, cursor, graph = self._connection_and_graph()
        try:
            attempt_id, capability_id, policy_id, exchange = self._unknown_attempt(cursor, graph)
            order = recovery.EconomicOrderRecoveryFact(graph["economic_order_id"], graph["user_id"], graph["credential_id"],
                                                        "account-a", "BTC-USDT", "spot",
                                                        contracts.EconomicOrderState.SUBMISSION_UNKNOWN, 0, 0)
            attempt = recovery.SubmissionAttemptRecoveryFact(attempt_id, graph["economic_order_id"], graph["user_id"], graph["credential_id"],
                                                               "account-a", "BTC-USDT", exchange, "spot",
                                                               contracts.SubmissionAttemptState.UNKNOWN, 0, 0, capability_id, policy_id,
                                                               "Q-v1-id", "v1", "ascii-nonsensitive-v1", "Q")
            policy = recovery.RecoveryPolicySnapshotFact(policy_id, capability_id, exchange, "spot", "v1")
            query = venue.NormalizedOrderQuery(venue.OrderQueryStatus.NOT_FOUND, venue.OrderQueryReference.CLIENT_ORDER_ID,
                                               exchange, "spot", "account-a", "BTC-USDT", client_order_id="Q-v1-id")
            decision = recovery.decide_submission_recovery(order=order, attempt=attempt, policy=policy, query=query,
                                                            queried_at=datetime(2026, 7, 23, tzinfo=timezone.utc), correlation_id="pg-not-found")
            recovery_repository.SubmissionRecoveryRepository().apply(connection, decision)
            cursor.execute("SELECT state, version FROM qd_economic_orders WHERE id = %s", (order.id,))
            self.assertEqual(("SUBMISSION_UNKNOWN", 0), cursor.fetchone())
            cursor.execute("SELECT state, version FROM qd_submission_attempts WHERE id = %s", (attempt.id,))
            self.assertEqual(("UNKNOWN", 0), cursor.fetchone())
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
