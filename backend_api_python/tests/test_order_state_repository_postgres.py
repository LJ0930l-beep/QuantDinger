"""CI PostgreSQL concurrency coverage for PR-05's durable state boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import unittest
import uuid

from tests.pr05_contract_loader import load_pr05_contracts
from tests import test_unified_order_schema as schema_tests

modules = load_pr05_contracts()
contracts, machine, states, recovery, recovery_repo, venue = (
    modules.contracts, modules.machine, modules.states, modules.recovery, modules.recovery_repo, modules.venue,
)

@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class OrderStateRepositoryPostgresTests(unittest.TestCase):
    def _setup_graph(self):
        import psycopg2
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        connection.autocommit = False
        cursor = connection.cursor()
        cursor.execute(schema_tests.INIT_SQL.read_text(encoding="utf-8"))
        for migration in schema_tests.INCREMENTAL_MIGRATIONS:
            cursor.execute(migration.read_text(encoding="utf-8"))
        graph = schema_tests.UnifiedOrderSchemaPostgresTests()._create_order_graph(cursor)
        cursor.execute("UPDATE qd_economic_orders SET state='SUBMISSION_UNKNOWN',version=0,last_event_seq=0 WHERE id=%s", (graph["economic_order_id"],))
        connection.commit(); cursor.close(); connection.close()
        return graph

    def _transition(self, graph, key, payload):
        scope = machine.EconomicOrderScope(graph["user_id"], graph["credential_id"], "account-a", "BTC-USDT", "spot")
        return machine.authorize_order_transition(aggregate_id=graph["economic_order_id"], aggregate_scope=scope,
            current_state=contracts.EconomicOrderState.SUBMISSION_UNKNOWN, target_state=contracts.EconomicOrderState.SUBMITTED,
            expected_version=0, cause=machine.TransitionCause.VENUE_OBSERVATION, actor=contracts.Actor.ADMIN,
            reason_code="PG_TEST", correlation_id="pg-correlation", occurred_at=datetime(2026,7,24,tzinfo=timezone.utc),
            evidence_hash="a"*64, canonical_payload=payload, idempotency_key=key)

    def _concurrent(self, graph, first, second):
        import psycopg2
        barrier, results, errors = threading.Barrier(2, timeout=10), [], []
        def worker(transition):
            connection = psycopg2.connect(os.environ["DATABASE_URL"])
            try:
                barrier.wait(timeout=10)
                results.append(states.OrderStateRepository().apply_order_transition(connection, transition))
            except Exception as exc: errors.append(exc)
            finally: connection.close()
        threads = [threading.Thread(target=worker, args=(item,), daemon=True) for item in (first, second)]
        [thread.start() for thread in threads]; [thread.join(15) for thread in threads]
        self.assertTrue(all(not thread.is_alive() for thread in threads), "concurrency test timed out")
        return results, errors

    def _concurrent_calls(self, first, second):
        barrier, results, errors = threading.Barrier(2, timeout=10), [], []
        def worker(call):
            try:
                barrier.wait(timeout=10)
                results.append(call())
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(call,), daemon=True) for call in (first, second)]
        [thread.start() for thread in threads]; [thread.join(15) for thread in threads]
        self.assertTrue(all(not thread.is_alive() for thread in threads), "concurrency test timed out")
        return results, errors

    def _setup_recovery_graph(self, *, client_query_authoritative=True):
        import psycopg2
        graph = self._setup_graph()
        connection = psycopg2.connect(os.environ["DATABASE_URL"]); connection.autocommit = False
        cursor = connection.cursor()
        capability_id, policy_id, attempt_id, exchange_pk = (str(uuid.uuid4()) for _ in range(4))
        capability_version = f"cap-{graph['economic_order_id']}"
        policy_version = f"policy-{graph['economic_order_id']}"
        profile_hash = f"profile-{graph['economic_order_id']}"
        policy_hash = f"policy-hash-{graph['economic_order_id']}"
        cursor.execute("""INSERT INTO qd_venue_capability_snapshots
            (id,exchange,market_type,capability_version,profile_hash,accepts_external_client_order_id,
             can_generate_safe_client_order_id,query_by_exchange_order_id,query_by_client_order_id,list_order_fills,stable_fill_id)
            VALUES (%s,'schema-test','spot',%s,%s,true,true,true,true,true,true)""", (capability_id, capability_version, profile_hash))
        cursor.execute("""INSERT INTO qd_submission_recovery_policy_snapshots
            (id,exchange,market_type,policy_version,policy_hash,capability_snapshot_id,capability_query_by_client_order_id,
             client_id_query_authoritative,order_history_authoritative,fill_history_authoritative,not_found_min_query_count,
             not_found_grace_seconds,not_found_action)
            VALUES (%s,'schema-test','spot',%s,%s,%s,true,%s,true,true,1,0,'KEEP_UNKNOWN')""", (policy_id, policy_version, policy_hash, capability_id, client_query_authoritative))
        cursor.execute("""INSERT INTO qd_submission_attempts
            (id,economic_order_id,exchange,tenant_id,credential_id,account_scope,instrument_id,market_type,child_seq,attempt_no,
             role,canonical_client_order_id,venue_client_order_id,request_fingerprint,state,venue_capability_snapshot_id,
             recovery_policy_snapshot_id,client_id_algorithm_version,broker_prefix_normalization_version,broker_prefix,canonical_contract_version)
            VALUES (%s,%s,'schema-test',%s,%s,'account-a','BTC-USDT','spot',1,1,'PRIMARY','canonical-1','venue-1',
                    'attempt-recovery','UNKNOWN',%s,%s,'v1','norm-v1','Q','attempt-contract-v1')""",
                       (attempt_id, graph["economic_order_id"], graph["user_id"], graph["credential_id"], capability_id, policy_id))
        cursor.execute("""INSERT INTO qd_exchange_orders
            (id,attempt_id,economic_order_id,child_role,exchange,tenant_id,credential_id,market_type,account_scope,instrument_id,
             exchange_order_id,venue_client_order_id,normalized_state,requested_qty)
            VALUES (%s,%s,%s,'PRIMARY','schema-test',%s,%s,'spot','account-a','BTC-USDT','exchange-1','venue-1','SUBMITTED','1')""",
                       (exchange_pk, attempt_id, graph["economic_order_id"], graph["user_id"], graph["credential_id"]))
        connection.commit(); cursor.close(); connection.close()
        return {**graph, "capability_id": capability_id, "policy_id": policy_id, "attempt_id": attempt_id, "exchange_pk": exchange_pk,
                "capability_version": capability_version, "profile_hash": profile_hash, "policy_version": policy_version, "policy_hash": policy_hash,
                "client_query_authoritative": client_query_authoritative}

    def _decision(self, graph, invocation, status=venue.OrderQueryStatus.FOUND, normalized="SUBMITTED", exchange_fact=None):
        scope = machine.EconomicOrderScope(graph["user_id"], graph["credential_id"], "account-a", "BTC-USDT", "spot")
        order = recovery.EconomicOrderRecoveryFact(graph["economic_order_id"], scope, contracts.EconomicOrderState.SUBMISSION_UNKNOWN, 0, 0)
        attempt_scope = machine.SubmissionAttemptScope(graph["user_id"], graph["credential_id"], "account-a", "BTC-USDT", "spot", graph["economic_order_id"], "schema-test")
        attempt = recovery.SubmissionAttemptRecoveryFact(graph["attempt_id"], attempt_scope, contracts.SubmissionAttemptState.UNKNOWN, 0, 0,
            graph["capability_id"], graph["policy_id"], "canonical-1", "venue-1", "v1", "norm-v1", "Q")
        capability = recovery.VenueCapabilitySnapshotFact(graph["capability_id"], "schema-test", "spot", graph["capability_version"], graph["profile_hash"], True, True)
        policy = recovery.RecoveryPolicySnapshotFact(graph["policy_id"], graph["capability_id"], "schema-test", "spot", graph["policy_version"], graph["policy_hash"], True, graph["client_query_authoritative"], True, True, 1, 0)
        exchange = exchange_fact or recovery.ExchangeOrderRecoveryFact(graph["exchange_pk"], graph["attempt_id"], graph["economic_order_id"], "schema-test", graph["user_id"], graph["credential_id"], "spot", "account-a", "BTC-USDT", "exchange-1", "venue-1")
        raw_state = "RAW" if status is venue.OrderQueryStatus.FOUND else ""
        query = venue.NormalizedOrderQuery(status, venue.OrderQueryReference.CLIENT_ORDER_ID, "schema-test", "spot", "account-a", "BTC-USDT", "exchange-1", "venue-1", normalized, raw_state)
        return recovery.decide_submission_recovery(order=order, attempt=attempt, capability=capability, policy=policy,
            exchange_order=exchange, query=query, queried_at=datetime(2026,7,25,tzinfo=timezone.utc), correlation_id="pg-recovery", query_invocation_id=invocation)

    @staticmethod
    def _apply_recovery(decision):
        import psycopg2
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            return recovery_repo.SubmissionRecoveryRepository().apply(connection, decision)
        finally:
            connection.close()

    def test_same_order_event_two_connections_apply_then_replay(self):
        graph = self._setup_graph(); event = self._transition(graph, "pg-event-1", {"case":"same"})
        results, errors = self._concurrent(graph, event, event)
        self.assertEqual([], errors)
        self.assertEqual(sorted(item.disposition.value for item in results), ["APPLIED", "REPLAYED"])

    def test_same_version_different_order_events_fail_closed(self):
        graph = self._setup_graph()
        results, errors = self._concurrent(graph, self._transition(graph, "pg-event-a", {"case":"a"}),
                                           self._transition(graph, "pg-event-b", {"case":"b"}))
        self.assertEqual(1, len(results)); self.assertEqual("APPLIED", results[0].disposition.value)
        self.assertEqual(1, len(errors)); self.assertIsInstance(errors[0], machine.StateEventConflict)

    def test_same_attempt_event_two_connections_apply_then_replay(self):
        graph = self._setup_recovery_graph()
        scope = machine.SubmissionAttemptScope(graph["user_id"], graph["credential_id"], "account-a", "BTC-USDT", "spot", graph["economic_order_id"], "schema-test")
        event = machine.authorize_attempt_transition(aggregate_id=graph["attempt_id"], aggregate_scope=scope,
            current_state=contracts.SubmissionAttemptState.UNKNOWN, target_state=contracts.SubmissionAttemptState.ACKED,
            expected_version=0, cause=machine.TransitionCause.VENUE_OBSERVATION, actor=contracts.Actor.ADMIN,
            reason_code="PG_TEST", correlation_id="pg-attempt", occurred_at=datetime(2026,7,25,tzinfo=timezone.utc),
            evidence_hash="b"*64, canonical_payload={"case":"same-attempt"}, idempotency_key="pg-attempt-1")
        results, errors = self._concurrent_calls(
            lambda: self._apply_attempt(event), lambda: self._apply_attempt(event))
        self.assertEqual([], errors); self.assertEqual(["APPLIED", "REPLAYED"], sorted(item.disposition.value for item in results))

    def test_same_attempt_version_with_different_events_returns_typed_conflict(self):
        graph = self._setup_recovery_graph()
        scope = machine.SubmissionAttemptScope(graph["user_id"], graph["credential_id"], "account-a", "BTC-USDT", "spot", graph["economic_order_id"], "schema-test")
        def event(key, payload):
            return machine.authorize_attempt_transition(aggregate_id=graph["attempt_id"], aggregate_scope=scope,
                current_state=contracts.SubmissionAttemptState.UNKNOWN, target_state=contracts.SubmissionAttemptState.ACKED,
                expected_version=0, cause=machine.TransitionCause.VENUE_OBSERVATION, actor=contracts.Actor.ADMIN,
                reason_code="PG_TEST", correlation_id="pg-attempt", occurred_at=datetime(2026,7,25,tzinfo=timezone.utc),
                evidence_hash="b"*64, canonical_payload=payload, idempotency_key=key)
        results, errors = self._concurrent_calls(
            lambda: self._apply_attempt(event("pg-attempt-a", {"case":"a"})),
            lambda: self._apply_attempt(event("pg-attempt-b", {"case":"b"})),
        )
        self.assertEqual(1, len(results)); self.assertEqual("APPLIED", results[0].disposition.value)
        self.assertEqual(1, len(errors)); self.assertIsInstance(errors[0], machine.StateEventConflict)

    @staticmethod
    def _apply_attempt(event):
        import psycopg2
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try: return states.OrderStateRepository().apply_attempt_transition(connection, event)
        finally: connection.close()

    def test_same_recovery_decision_two_connections_apply_then_replay(self):
        graph = self._setup_recovery_graph(); decision = self._decision(graph, str(uuid.uuid4()))
        results, errors = self._concurrent_calls(lambda: self._apply_recovery(decision), lambda: self._apply_recovery(decision))
        self.assertEqual([], errors); self.assertEqual(["APPLIED", "REPLAYED"], sorted(item.disposition.value for item in results))

    def test_same_invocation_with_different_observation_facts_keeps_one_observation(self):
        import psycopg2
        graph = self._setup_recovery_graph(); invocation = str(uuid.uuid4())
        first = self._decision(graph, invocation, venue.OrderQueryStatus.NOT_FOUND, "")
        second = self._decision(graph, invocation, venue.OrderQueryStatus.TEMPORARY_FAILURE, "")
        results, errors = self._concurrent_calls(lambda: self._apply_recovery(first), lambda: self._apply_recovery(second))
        self.assertEqual(1, len(results)); self.assertEqual("OBSERVATION_ONLY", results[0].disposition.value)
        self.assertEqual(1, len(errors)); self.assertIsInstance(errors[0], machine.StateEventConflict)
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM qd_exchange_order_observations WHERE attempt_id=%s", (graph["attempt_id"],))
                self.assertEqual(1, cursor.fetchone()[0])
        finally: connection.close()

    def test_same_not_found_persistence_replay_does_not_append_again(self):
        import psycopg2
        graph = self._setup_recovery_graph(); decision = self._decision(graph, str(uuid.uuid4()), venue.OrderQueryStatus.NOT_FOUND, "")
        self.assertEqual("OBSERVATION_ONLY", self._apply_recovery(decision).disposition.value)
        self.assertEqual("REPLAYED", self._apply_recovery(decision).disposition.value)
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM qd_exchange_order_observations WHERE attempt_id=%s", (graph["attempt_id"],))
                self.assertEqual(1, cursor.fetchone()[0])
        finally: connection.close()

    def test_found_recovery_commits_observation_and_both_state_events_atomically(self):
        import psycopg2
        graph = self._setup_recovery_graph(); result = self._apply_recovery(self._decision(graph, str(uuid.uuid4())))
        self.assertEqual("APPLIED", result.disposition.value)
        self.assertEqual("APPLIED", result.order_event.disposition.value); self.assertEqual("APPLIED", result.attempt_event.disposition.value)
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                for table, column, identifier in (("qd_exchange_order_observations", "attempt_id", graph["attempt_id"]),
                                                  ("qd_order_state_events", "economic_order_id", graph["economic_order_id"]),
                                                  ("qd_submission_attempt_state_events", "attempt_id", graph["attempt_id"])):
                    cursor.execute(f"SELECT count(*) FROM {table} WHERE {column}=%s", (identifier,))
                    self.assertEqual(1, cursor.fetchone()[0])
        finally: connection.close()

    def test_non_authoritative_client_found_transitions_only_to_reconciliation(self):
        import psycopg2
        graph = self._setup_recovery_graph(client_query_authoritative=False)
        result = self._apply_recovery(self._decision(graph, str(uuid.uuid4())))
        self.assertEqual("APPLIED", result.disposition.value)
        self.assertIsNotNone(result.order_event); self.assertIsNone(result.attempt_event)
        self.assertEqual("RECONCILIATION_REQUIRED", result.order_event.resulting_state)
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT state,version FROM qd_submission_attempts WHERE id=%s", (graph["attempt_id"],))
                self.assertEqual(("UNKNOWN", 0), cursor.fetchone())
        finally: connection.close()

    def test_replay_after_later_legal_order_event_is_replayed_without_regression(self):
        import psycopg2
        graph = self._setup_recovery_graph(); decision = self._decision(graph, str(uuid.uuid4()))
        self.assertEqual("APPLIED", self._apply_recovery(decision).disposition.value)
        scope = machine.EconomicOrderScope(graph["user_id"], graph["credential_id"], "account-a", "BTC-USDT", "spot")
        later = machine.authorize_order_transition(aggregate_id=graph["economic_order_id"], aggregate_scope=scope,
            current_state=contracts.EconomicOrderState.SUBMITTED, target_state=contracts.EconomicOrderState.PARTIALLY_FILLED,
            expected_version=1, cause=machine.TransitionCause.VENUE_OBSERVATION, actor=contracts.Actor.ADMIN,
            reason_code="LATER_VENUE_FACT", correlation_id="later", occurred_at=datetime(2026,7,25,tzinfo=timezone.utc),
            evidence_hash="c"*64, canonical_payload={"case":"later"}, idempotency_key="later-event")
        self.assertEqual("APPLIED", self._apply_order(later).disposition.value)
        replay = self._apply_recovery(decision)
        self.assertEqual("REPLAYED", replay.disposition.value)
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT state,version,last_event_seq FROM qd_economic_orders WHERE id=%s", (graph["economic_order_id"],))
                self.assertEqual(("PARTIALLY_FILLED", 2, 2), cursor.fetchone())
        finally: connection.close()

    @staticmethod
    def _apply_order(event):
        import psycopg2
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try: return states.OrderStateRepository().apply_order_transition(connection, event)
        finally: connection.close()

    def test_partial_history_observation_cannot_auto_complete_state_events(self):
        import psycopg2
        graph = self._setup_recovery_graph(); decision = self._decision(graph, str(uuid.uuid4()))
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("""INSERT INTO qd_exchange_order_observations
                    (id,attempt_id,observation_source,payload_hash,payload_json,observed_at)
                    VALUES (%s,%s,'REST',%s,%s::jsonb,%s)""",
                    (str(uuid.uuid4()), graph["attempt_id"], decision.observation.payload_hash,
                     decision.observation.canonical_payload_json, decision.observation.observed_at))
            connection.commit()
        finally: connection.close()
        with self.assertRaises(machine.StateEventConflict):
            self._apply_recovery(decision)
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM qd_order_state_events WHERE economic_order_id=%s", (graph["economic_order_id"],))
                self.assertEqual(0, cursor.fetchone()[0])
                cursor.execute("SELECT count(*) FROM qd_submission_attempt_state_events WHERE attempt_id=%s", (graph["attempt_id"],))
                self.assertEqual(0, cursor.fetchone()[0])
        finally: connection.close()

    def test_distinct_not_found_invocations_append_two_observations_without_state_change(self):
        import psycopg2
        graph = self._setup_recovery_graph()
        first = self._decision(graph, str(uuid.uuid4()), venue.OrderQueryStatus.NOT_FOUND, "")
        second = self._decision(graph, str(uuid.uuid4()), venue.OrderQueryStatus.NOT_FOUND, "")
        self.assertEqual("OBSERVATION_ONLY", self._apply_recovery(first).disposition.value)
        self.assertEqual("OBSERVATION_ONLY", self._apply_recovery(second).disposition.value)
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM qd_exchange_order_observations WHERE attempt_id=%s", (graph["attempt_id"],))
                self.assertEqual(2, cursor.fetchone()[0])
                cursor.execute("SELECT state,version,last_event_seq FROM qd_economic_orders WHERE id=%s", (graph["economic_order_id"],))
                self.assertEqual(("SUBMISSION_UNKNOWN", 0, 0), cursor.fetchone())
        finally: connection.close()

if __name__ == "__main__": unittest.main()
