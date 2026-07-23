from datetime import datetime, timezone
import unittest

from tests.pr05_contract_loader import load_pr05_contracts


modules = load_pr05_contracts()
contracts = modules.contracts
recovery = modules.recovery
recovery_repository = modules.recovery_repo
venue = modules.venue

ORDER_ID = "00000000-0000-0000-0000-000000000401"
ATTEMPT_ID = "00000000-0000-0000-0000-000000000402"
CAPABILITY_ID = "00000000-0000-0000-0000-000000000403"
POLICY_ID = "00000000-0000-0000-0000-000000000404"
OBSERVATION_ID = "00000000-0000-0000-0000-000000000405"
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


def facts():
    order = recovery.EconomicOrderRecoveryFact(
        id=ORDER_ID, tenant_id=1, credential_id=2, account_scope="account-a", instrument_id="BTCUSDT",
        market_type="swap", state=contracts.EconomicOrderState.SUBMISSION_UNKNOWN, version=7, last_event_seq=7,
    )
    attempt = recovery.SubmissionAttemptRecoveryFact(
        id=ATTEMPT_ID, economic_order_id=ORDER_ID, tenant_id=1, credential_id=2, account_scope="account-a",
        instrument_id="BTCUSDT", exchange="binance", market_type="swap", state=contracts.SubmissionAttemptState.UNKNOWN,
        version=5, last_event_seq=5, venue_capability_snapshot_id=CAPABILITY_ID, recovery_policy_snapshot_id=POLICY_ID,
        canonical_client_order_id="Q-v1-id", client_id_algorithm_version="v1",
        broker_prefix_normalization_version="ascii-nonsensitive-v1", broker_prefix="Q",
    )
    policy = recovery.RecoveryPolicySnapshotFact(id=POLICY_ID, capability_snapshot_id=CAPABILITY_ID, exchange="binance",
                                                  market_type="swap", policy_version="recovery-v1")
    return order, attempt, policy


def found_query():
    return venue.NormalizedOrderQuery(
        status=venue.OrderQueryStatus.FOUND, reference=venue.OrderQueryReference.CLIENT_ORDER_ID,
        venue="binance", market_type="swap", account_scope="account-a", instrument="BTCUSDT",
        exchange_order_id="exchange-1", client_order_id="Q-v1-id", normalized_state="SUBMITTED", raw_state="NEW",
    )


def order_row():
    return (ORDER_ID, 1, 2, "account-a", "BTCUSDT", "swap", "SUBMISSION_UNKNOWN", 7, 7)


def attempt_row():
    return (ATTEMPT_ID, ORDER_ID, 1, 2, "account-a", "BTCUSDT", "binance", "swap", "UNKNOWN", 5, 5,
            CAPABILITY_ID, POLICY_ID, "Q-v1-id", "v1", "ascii-nonsensitive-v1", "Q")


class SubmissionRecoveryRepositoryTests(unittest.TestCase):
    def test_recovery_observation_and_two_aggregate_events_commit_once(self):
        order, attempt, policy = facts()
        decision = recovery.decide_submission_recovery(order=order, attempt=attempt, policy=policy,
                                                        query=found_query(), queried_at=NOW, correlation_id="recovery-1")
        cursor = FakeCursor([
            order_row(), attempt_row(), (POLICY_ID, CAPABILITY_ID, "binance", "swap", CAPABILITY_ID, "binance", "swap"),
            (OBSERVATION_ID,),  # outer lock/append
            ("SUBMISSION_UNKNOWN", 7, 7), None, ("SUBMITTED", 8),  # order event
            (ORDER_ID, "UNKNOWN", 5, 5), None, ("ACKED", 6),  # attempt event
        ])
        connection = FakeConnection(cursor)
        result = recovery_repository.SubmissionRecoveryRepository().apply(connection, decision)
        self.assertEqual(recovery_repository.RecoveryDisposition.APPLIED, result.disposition)
        self.assertEqual(1, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        sql = "\n".join(query for query, _ in cursor.executed)
        self.assertLess(sql.index("FROM qd_economic_orders"), sql.index("FROM qd_submission_attempts"))
        self.assertIn("INSERT INTO qd_exchange_order_observations", sql)
        self.assertIn("INSERT INTO qd_order_state_events", sql)
        self.assertIn("INSERT INTO qd_submission_attempt_state_events", sql)

    def test_not_found_appends_only_observation_without_self_transition(self):
        order, attempt, policy = facts()
        query = venue.NormalizedOrderQuery(status=venue.OrderQueryStatus.NOT_FOUND,
                                           reference=venue.OrderQueryReference.CLIENT_ORDER_ID, venue="binance",
                                           market_type="swap", account_scope="account-a", instrument="BTCUSDT",
                                           client_order_id="Q-v1-id")
        decision = recovery.decide_submission_recovery(order=order, attempt=attempt, policy=policy,
                                                        query=query, queried_at=NOW, correlation_id="recovery-1")
        cursor = FakeCursor([
            order_row(), attempt_row(), (POLICY_ID, CAPABILITY_ID, "binance", "swap", CAPABILITY_ID, "binance", "swap"),
            (OBSERVATION_ID,),
        ])
        connection = FakeConnection(cursor)
        result = recovery_repository.SubmissionRecoveryRepository().apply(connection, decision)
        self.assertEqual(recovery_repository.RecoveryDisposition.OBSERVATION_ONLY, result.disposition)
        sql = "\n".join(query for query, _ in cursor.executed)
        self.assertNotIn("INSERT INTO qd_order_state_events", sql)
        self.assertNotIn("INSERT INTO qd_submission_attempt_state_events", sql)

    def test_scope_mismatch_rolls_back_before_observation(self):
        order, attempt, policy = facts()
        decision = recovery.decide_submission_recovery(order=order, attempt=attempt, policy=policy,
                                                        query=found_query(), queried_at=NOW, correlation_id="recovery-1")
        wrong_order = list(order_row())
        wrong_order[4] = "ETHUSDT"
        cursor = FakeCursor([tuple(wrong_order)])
        connection = FakeConnection(cursor)
        with self.assertRaises(recovery.SubmissionRecoveryContractError):
            recovery_repository.SubmissionRecoveryRepository().apply(connection, decision)
        self.assertEqual(1, connection.rollbacks)
        self.assertFalse(any("INSERT INTO qd_exchange_order_observations" in query for query, _ in cursor.executed))


if __name__ == "__main__":
    unittest.main()
