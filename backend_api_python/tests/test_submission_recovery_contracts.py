from datetime import datetime, timezone
import unittest

from tests.pr05_contract_loader import load_pr05_contracts


modules = load_pr05_contracts()
contracts = modules.contracts
recovery = modules.recovery
venue = modules.venue

ORDER_ID = "00000000-0000-0000-0000-000000000201"
ATTEMPT_ID = "00000000-0000-0000-0000-000000000202"
CAPABILITY_ID = "00000000-0000-0000-0000-000000000203"
POLICY_ID = "00000000-0000-0000-0000-000000000204"
NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


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
    policy = recovery.RecoveryPolicySnapshotFact(
        id=POLICY_ID, capability_snapshot_id=CAPABILITY_ID, exchange="binance", market_type="swap", policy_version="recovery-v1",
    )
    return order, attempt, policy


def query(status, normalized_state="", account_scope="account-a"):
    return venue.NormalizedOrderQuery(
        status=status, reference=venue.OrderQueryReference.CLIENT_ORDER_ID, venue="binance", market_type="swap",
        account_scope=account_scope, instrument="BTCUSDT", exchange_order_id="exchange-1" if status is venue.OrderQueryStatus.FOUND else "",
        client_order_id="Q-v1-id", normalized_state=normalized_state, raw_state="VENUE_STATE" if status is venue.OrderQueryStatus.FOUND else "",
    )


class SubmissionRecoveryContractTests(unittest.TestCase):
    def test_all_found_mappings(self):
        expected = {
            "SUBMITTED": ("SUBMITTED", "ACKED"), "PARTIALLY_FILLED": ("PARTIALLY_FILLED", "ACKED"),
            "FILLED": ("FILLED", "ACKED"), "REJECTED": ("REJECTED", "REJECTED"),
            "CANCEL_REQUESTED": ("RECONCILIATION_REQUIRED", "ACKED"),
            "CANCELLING": ("RECONCILIATION_REQUIRED", "ACKED"),
            "CANCELLED": ("RECONCILIATION_REQUIRED", "ACKED"),
            "RECONCILIATION_REQUIRED": ("RECONCILIATION_REQUIRED", "ACKED"),
        }
        for normalized, targets in expected.items():
            order, attempt, policy = facts()
            decision = recovery.decide_submission_recovery(
                order=order, attempt=attempt, policy=policy,
                query=query(venue.OrderQueryStatus.FOUND, normalized), queried_at=NOW, correlation_id="recovery-1",
            )
            self.assertEqual(targets[0], decision.order_transition.target_state)
            self.assertEqual(targets[1], decision.attempt_transition.target_state)

    def test_found_unknown_acks_attempt_but_does_not_fake_order_transition(self):
        order, attempt, policy = facts()
        decision = recovery.decide_submission_recovery(
            order=order, attempt=attempt, policy=policy,
            query=query(venue.OrderQueryStatus.FOUND, "SUBMISSION_UNKNOWN"), queried_at=NOW, correlation_id="recovery-1",
        )
        self.assertIsNone(decision.order_transition)
        self.assertEqual("ACKED", decision.attempt_transition.target_state)

    def test_non_found_and_non_authoritative_results_are_observation_only(self):
        for status in (venue.OrderQueryStatus.NOT_FOUND, venue.OrderQueryStatus.TEMPORARY_FAILURE,
                       venue.OrderQueryStatus.AUTH_OR_PERMISSION_FAILURE, venue.OrderQueryStatus.UNSUPPORTED,
                       venue.OrderQueryStatus.INVALID_RESPONSE):
            order, attempt, policy = facts()
            decision = recovery.decide_submission_recovery(
                order=order, attempt=attempt, policy=policy, query=query(status), queried_at=NOW, correlation_id="recovery-1",
            )
            self.assertIsNone(decision.order_transition)
            self.assertIsNone(decision.attempt_transition)
            self.assertEqual("OBSERVATION_ONLY", decision.disposition)

    def test_conflict_and_scope_mismatch_require_reconciliation_without_confirming_absence(self):
        order, attempt, policy = facts()
        decisions = (
            recovery.decide_submission_recovery(order=order, attempt=attempt, policy=policy,
                                                query=query(venue.OrderQueryStatus.CONFLICT), queried_at=NOW, correlation_id="recovery-1"),
            recovery.decide_submission_recovery(order=order, attempt=attempt, policy=policy,
                                                query=query(venue.OrderQueryStatus.FOUND, "SUBMITTED", "account-b"), queried_at=NOW, correlation_id="recovery-2"),
        )
        for decision in decisions:
            self.assertEqual("RECONCILIATION_REQUIRED", decision.order_transition.target_state)
            self.assertIsNone(decision.attempt_transition)

    def test_policy_cannot_claim_confirm_absent(self):
        with self.assertRaises(recovery.SubmissionRecoveryContractError):
            recovery.RecoveryPolicySnapshotFact(id=POLICY_ID, capability_snapshot_id=CAPABILITY_ID, exchange="binance",
                                                 market_type="swap", policy_version="recovery-v1", not_found_action="CONFIRM_ABSENT")

    def test_recovery_requires_strict_utc_and_complete_snapshot_scope(self):
        order, attempt, policy = facts()
        with self.assertRaises(Exception):
            recovery.decide_submission_recovery(order=order, attempt=attempt, policy=policy,
                                                query=query(venue.OrderQueryStatus.NOT_FOUND),
                                                queried_at=datetime(2026, 7, 23, 12, 0), correlation_id="recovery-1")
        wrong_policy = recovery.RecoveryPolicySnapshotFact(
            id=POLICY_ID, capability_snapshot_id="00000000-0000-0000-0000-000000000299",
            exchange="binance", market_type="swap", policy_version="recovery-v1",
        )
        with self.assertRaises(recovery.SubmissionRecoveryContractError):
            recovery.decide_submission_recovery(order=order, attempt=attempt, policy=wrong_policy,
                                                query=query(venue.OrderQueryStatus.NOT_FOUND),
                                                queried_at=NOW, correlation_id="recovery-2")


if __name__ == "__main__":
    unittest.main()
