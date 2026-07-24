"""CI PostgreSQL concurrency coverage for PR-05's durable state boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import unittest

from tests.pr05_contract_loader import load_pr05_contracts
from tests import test_unified_order_schema as schema_tests

modules = load_pr05_contracts()
contracts, machine, states = modules.contracts, modules.machine, modules.states

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

if __name__ == "__main__": unittest.main()
