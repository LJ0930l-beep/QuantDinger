from datetime import datetime, timezone
import unittest

from tests.pr05_contract_loader import load_pr05_contracts

modules = load_pr05_contracts()
contracts, machine, recovery, repository, venue = modules.contracts, modules.machine, modules.recovery, modules.recovery_repo, modules.venue
ORDER_ID, ATTEMPT_ID = "00000000-0000-0000-0000-000000000401", "00000000-0000-0000-0000-000000000402"
CAPABILITY_ID, POLICY_ID, INVOCATION = "00000000-0000-0000-0000-000000000403", "00000000-0000-0000-0000-000000000404", "00000000-0000-0000-0000-000000000405"
NOW = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)

class Cursor:
    def __init__(self, rows): self.rows, self.executed = iter(rows), []
    def execute(self, sql, params=()): self.executed.append((sql, params))
    def fetchone(self): return next(self.rows)
    def close(self): pass
class Connection:
    def __init__(self, cursor): self.cursor_value, self.commits, self.rollbacks = cursor, 0, 0
    def cursor(self): return self.cursor_value
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1

def decision():
    scope = machine.EconomicOrderScope(1, 2, "account-a", "BTCUSDT", "swap")
    order = recovery.EconomicOrderRecoveryFact(ORDER_ID, scope, contracts.EconomicOrderState.SUBMISSION_UNKNOWN, 0, 0)
    attempt_scope = machine.SubmissionAttemptScope(1, 2, "account-a", "BTCUSDT", "swap", ORDER_ID, "binance")
    attempt = recovery.SubmissionAttemptRecoveryFact(ATTEMPT_ID, attempt_scope, contracts.SubmissionAttemptState.UNKNOWN, 0, 0, CAPABILITY_ID, POLICY_ID, "canonical", "venue", "v1", "norm-v1", "Q")
    capability = recovery.VenueCapabilitySnapshotFact(CAPABILITY_ID, "binance", "swap", "cap-v1", "profile", True, True)
    policy = recovery.RecoveryPolicySnapshotFact(POLICY_ID, CAPABILITY_ID, "binance", "swap", "policy-v1", "policy", True, True, True, True, 1, 0)
    query = venue.NormalizedOrderQuery(venue.OrderQueryStatus.NOT_FOUND, venue.OrderQueryReference.CLIENT_ORDER_ID, "binance", "swap", "account-a", "BTCUSDT", client_order_id="venue")
    return recovery.decide_submission_recovery(order=order, attempt=attempt, capability=capability, policy=policy, exchange_order=None,
        query=query, queried_at=NOW, correlation_id="corr", query_invocation_id=INVOCATION)

class RecoveryRepositoryTests(unittest.TestCase):
    def test_new_not_found_observation_commits_without_state_event(self):
        item = decision(); c, p = item.capability, item.policy
        snapshot = (c.id,c.exchange,c.market_type,c.capability_version,c.profile_hash,c.query_by_exchange_order_id,c.query_by_client_order_id,
                    p.id,p.capability_snapshot_id,p.exchange,p.market_type,p.policy_version,p.policy_hash,p.capability_query_by_client_order_id,
                    p.client_id_query_authoritative,p.order_history_authoritative,p.fill_history_authoritative,p.not_found_min_query_count,p.not_found_grace_seconds,p.not_found_action)
        cursor = Cursor([(ORDER_ID,), (ATTEMPT_ID,), snapshot, None, ("00000000-0000-0000-0000-000000000499",),
                         ("SUBMISSION_UNKNOWN",0,0), ("UNKNOWN",0,0)])
        connection = Connection(cursor)
        result = repository.SubmissionRecoveryRepository().apply(connection, item)
        self.assertEqual(repository.RecoveryDisposition.OBSERVATION_ONLY, result.disposition)
        self.assertEqual(1, connection.commits)
        self.assertFalse(any("state_events" in sql for sql, _ in cursor.executed))

    def test_exact_observation_replay_does_not_require_old_aggregate_state(self):
        item = decision(); c, p = item.capability, item.policy
        snapshot = (c.id,c.exchange,c.market_type,c.capability_version,c.profile_hash,c.query_by_exchange_order_id,c.query_by_client_order_id,
                    p.id,p.capability_snapshot_id,p.exchange,p.market_type,p.policy_version,p.policy_hash,p.capability_query_by_client_order_id,
                    p.client_id_query_authoritative,p.order_history_authoritative,p.fill_history_authoritative,p.not_found_min_query_count,p.not_found_grace_seconds,p.not_found_action)
        cursor = Cursor([(ORDER_ID,), (ATTEMPT_ID,), snapshot, ("00000000-0000-0000-0000-000000000499", item.observation.canonical_payload_json)])
        connection = Connection(cursor)
        result = repository.SubmissionRecoveryRepository().apply(connection, item)
        self.assertEqual(repository.RecoveryDisposition.REPLAYED, result.disposition)
        self.assertEqual(1, connection.commits)

if __name__ == "__main__": unittest.main()
