from datetime import datetime, timezone
import unittest

from tests.pr05_contract_loader import load_pr05_contracts


modules = load_pr05_contracts()
contracts = modules.contracts
machine = modules.machine
repository = modules.states

ORDER_ID = "00000000-0000-0000-0000-000000000301"
NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, fetches):
        self.fetches = iter(fetches)
        self.executed = []
        self.closed = False

    def execute(self, query, params=()):
        self.executed.append((query, params))

    def fetchone(self):
        return next(self.fetches)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def transition(idempotency_key="event-1"):
    return machine.authorize_order_transition(
        aggregate_id=ORDER_ID, current_state=contracts.EconomicOrderState.SUBMISSION_UNKNOWN,
        target_state=contracts.EconomicOrderState.SUBMITTED, expected_version=2,
        cause=machine.TransitionCause.VENUE_OBSERVATION, actor=contracts.Actor.ADMIN,
        reason_code="VENUE_QUERY_FOUND", correlation_id="correlation-1", occurred_at=NOW,
        evidence_hash="a" * 64, canonical_payload={"source": "test"}, idempotency_key=idempotency_key,
    )


class OrderStateRepositoryTests(unittest.TestCase):
    def test_event_and_cas_update_commit_as_one_unit(self):
        cursor = FakeCursor([
            ("SUBMISSION_UNKNOWN", 2, 2),  # aggregate lock
            None,  # idempotency lookup
            ("SUBMITTED", 3),  # guarded update
        ])
        connection = FakeConnection(cursor)
        result = repository.OrderStateRepository().apply_order_transition(connection, transition())
        self.assertEqual(repository.StateEventDisposition.APPLIED, result.disposition)
        self.assertEqual(1, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        statements = "\n".join(query for query, _ in cursor.executed)
        self.assertIn("INSERT INTO qd_order_state_events", statements)
        self.assertIn("last_event_seq", statements)

    def test_same_idempotency_and_same_fingerprint_replays(self):
        event = transition()
        cursor = FakeCursor([
            ("SUBMISSION_UNKNOWN", 2, 2),
            ("SUBMITTED", 3, event.event_fingerprint, event.idempotency_key),
        ])
        connection = FakeConnection(cursor)
        result = repository.OrderStateRepository().apply_order_transition(connection, event)
        self.assertEqual(repository.StateEventDisposition.REPLAYED, result.disposition)
        self.assertEqual(1, connection.commits)
        self.assertFalse(any("INSERT INTO qd_order_state_events" in query for query, _ in cursor.executed))

    def test_same_idempotency_with_different_facts_rolls_back(self):
        cursor = FakeCursor([
            ("SUBMISSION_UNKNOWN", 2, 2),
            ("SUBMITTED", 3, "different", "event-1"),
        ])
        connection = FakeConnection(cursor)
        with self.assertRaises(machine.StateEventConflict):
            repository.OrderStateRepository().apply_order_transition(connection, transition())
        self.assertEqual(1, connection.rollbacks)

    def test_version_sequence_drift_fails_closed_and_rolls_back(self):
        cursor = FakeCursor([
            ("SUBMISSION_UNKNOWN", 2, 1),
            None,
        ])
        connection = FakeConnection(cursor)
        with self.assertRaises(machine.StateEventConflict):
            repository.OrderStateRepository().apply_order_transition(connection, transition())
        self.assertEqual(1, connection.rollbacks)

    def test_repository_rejects_bare_wrong_aggregate_transition(self):
        attempt = machine.authorize_attempt_transition(
            aggregate_id="00000000-0000-0000-0000-000000000302", current_state=contracts.SubmissionAttemptState.UNKNOWN,
            target_state=contracts.SubmissionAttemptState.ACKED, expected_version=0,
            cause=machine.TransitionCause.VENUE_OBSERVATION, actor=contracts.Actor.ADMIN,
            reason_code="TEST", correlation_id="c", occurred_at=NOW, evidence_hash="a",
            canonical_payload={}, idempotency_key="event-2",
        )
        with self.assertRaises(machine.StateEventConflict):
            repository.OrderStateRepository().apply_order_transition(FakeConnection(FakeCursor([])), attempt)


if __name__ == "__main__":
    unittest.main()
