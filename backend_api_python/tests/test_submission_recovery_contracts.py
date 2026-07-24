from datetime import datetime, timezone
import json
import unittest

from tests.pr05_contract_loader import load_pr05_contracts

modules = load_pr05_contracts()
contracts, machine, recovery, venue = modules.contracts, modules.machine, modules.recovery, modules.venue
ORDER_ID, ATTEMPT_ID = "00000000-0000-0000-0000-000000000201", "00000000-0000-0000-0000-000000000202"
CAPABILITY_ID, POLICY_ID, EXCHANGE_PK = "00000000-0000-0000-0000-000000000203", "00000000-0000-0000-0000-000000000204", "00000000-0000-0000-0000-000000000205"
INVOCATION = "00000000-0000-0000-0000-000000000206"
NOW = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)

def facts(with_exchange=False, client_id="venue-client"):
    scope = machine.EconomicOrderScope(1, 2, "account-a", "BTCUSDT", "swap")
    order = recovery.EconomicOrderRecoveryFact(ORDER_ID, scope, contracts.EconomicOrderState.SUBMISSION_UNKNOWN, 7, 7)
    attempt_scope = machine.SubmissionAttemptScope(1, 2, "account-a", "BTCUSDT", "swap", ORDER_ID, "binance")
    attempt = recovery.SubmissionAttemptRecoveryFact(ATTEMPT_ID, attempt_scope, contracts.SubmissionAttemptState.UNKNOWN, 5, 5,
        CAPABILITY_ID, POLICY_ID, "canonical-client", client_id, "v1", "ascii-nonsensitive-v1", "Q")
    capability = recovery.VenueCapabilitySnapshotFact(CAPABILITY_ID, "binance", "swap", "cap-v1", "profile-hash", True, True)
    policy = recovery.RecoveryPolicySnapshotFact(POLICY_ID, CAPABILITY_ID, "binance", "swap", "policy-v1", "policy-hash", True, True, True, True, 1, 0)
    exchange = recovery.ExchangeOrderRecoveryFact(EXCHANGE_PK, ATTEMPT_ID, ORDER_ID, "binance", "swap", "account-a", "BTCUSDT", "exchange-1", client_id) if with_exchange else None
    return order, attempt, capability, policy, exchange

def query(status, normalized="", reference=None, client_id="venue-client", exchange_id="", account="account-a"):
    reference = reference or venue.OrderQueryReference.CLIENT_ORDER_ID
    if status is venue.OrderQueryStatus.FOUND and not exchange_id:
        exchange_id = "exchange-1"
    return venue.NormalizedOrderQuery(status, reference, "binance", "swap", account, "BTCUSDT", exchange_id, client_id, normalized,
                                      "RAW" if status is venue.OrderQueryStatus.FOUND else "")

def decide(*args, **kwargs):
    return recovery.decide_submission_recovery(*args, queried_at=NOW, correlation_id="corr-1", query_invocation_id=INVOCATION, **kwargs)

class SubmissionRecoveryContractTests(unittest.TestCase):
    def test_found_identity_mappings(self):
        expected = {"SUBMITTED": ("SUBMITTED", "ACKED"), "PARTIALLY_FILLED": ("PARTIALLY_FILLED", "ACKED"),
                    "FILLED": ("FILLED", "ACKED"), "REJECTED": ("REJECTED", "REJECTED"),
                    "CANCELLED": ("RECONCILIATION_REQUIRED", "ACKED")}
        for normalized, targets in expected.items():
            order, attempt, capability, policy, exchange = facts(True)
            decision = decide(order=order, attempt=attempt, capability=capability, policy=policy, exchange_order=exchange,
                              query=query(venue.OrderQueryStatus.FOUND, normalized))
            self.assertEqual(targets[0], decision.order_transition.target_state)
            self.assertEqual(targets[1], decision.attempt_transition.target_state)

    def test_exchange_order_lookup_requires_persisted_exchange_order_fact(self):
        order, attempt, capability, policy, exchange = facts(True)
        decision = decide(order=order, attempt=attempt, capability=capability, policy=policy, exchange_order=exchange,
                          query=query(venue.OrderQueryStatus.FOUND, "SUBMITTED", venue.OrderQueryReference.EXCHANGE_ORDER_ID, "venue-client", "exchange-1"))
        self.assertEqual("SUBMITTED", decision.order_transition.target_state)
        decision = decide(order=order, attempt=attempt, capability=capability, policy=policy, exchange_order=None,
                          query=query(venue.OrderQueryStatus.FOUND, "SUBMITTED", venue.OrderQueryReference.EXCHANGE_ORDER_ID, "venue-client", "exchange-1"))
        self.assertEqual("RECONCILIATION_REQUIRED", decision.order_transition.target_state)
        self.assertIsNone(decision.attempt_transition)

    def test_identity_or_capability_mismatch_never_acks(self):
        order, attempt, capability, policy, exchange = facts(True)
        wrong_client = decide(order=order, attempt=attempt, capability=capability, policy=policy, exchange_order=exchange,
                              query=query(venue.OrderQueryStatus.FOUND, "SUBMITTED", client_id="new-id"))
        unsupported = recovery.VenueCapabilitySnapshotFact(CAPABILITY_ID, "binance", "swap", "cap-v1", "profile-hash", True, False)
        unsupported_policy = recovery.RecoveryPolicySnapshotFact(POLICY_ID, CAPABILITY_ID, "binance", "swap", "policy-v1", "policy-hash", False, True, True, True, 1, 0)
        no_capability = decide(order=order, attempt=attempt, capability=unsupported, policy=unsupported_policy, exchange_order=exchange,
                               query=query(venue.OrderQueryStatus.FOUND, "SUBMITTED"))
        for decision in (wrong_client, no_capability):
            self.assertEqual("RECONCILIATION_REQUIRED", decision.order_transition.target_state)
            self.assertIsNone(decision.attempt_transition)

    def test_not_found_is_observation_only_and_invocations_are_distinct(self):
        order, attempt, capability, policy, exchange = facts(True)
        first = decide(order=order, attempt=attempt, capability=capability, policy=policy, exchange_order=exchange,
                       query=query(venue.OrderQueryStatus.NOT_FOUND))
        second = recovery.decide_submission_recovery(order=order, attempt=attempt, capability=capability, policy=policy,
            exchange_order=exchange, query=query(venue.OrderQueryStatus.NOT_FOUND), queried_at=NOW, correlation_id="corr-1",
            query_invocation_id="00000000-0000-0000-0000-000000000207")
        self.assertEqual("OBSERVATION_ONLY", first.disposition)
        self.assertNotEqual(first.observation.payload_hash, second.observation.payload_hash)
        self.assertEqual(first.observation.payload_hash, __import__("hashlib").sha256(first.observation.canonical_payload_json.encode()).hexdigest())

    def test_recovery_decision_cannot_be_manually_assembled(self):
        order, attempt, capability, policy, exchange = facts()
        with self.assertRaises(recovery.SubmissionRecoveryContractError):
            recovery.RecoveryDecision(order, attempt, capability, policy, exchange,
                                      recovery.RecoveryObservation(ATTEMPT_ID, INVOCATION, NOW, {}), None, None, "OBSERVATION_ONLY")

    def test_invalid_ingress_state_fails_before_observation(self):
        order, attempt, capability, policy, exchange = facts()
        changed = recovery.EconomicOrderRecoveryFact(ORDER_ID, order.scope, contracts.EconomicOrderState.SUBMITTED, 7, 7)
        with self.assertRaises(recovery.SubmissionRecoveryContractError):
            decide(order=changed, attempt=attempt, capability=capability, policy=policy, exchange_order=exchange,
                   query=query(venue.OrderQueryStatus.NOT_FOUND))

if __name__ == "__main__": unittest.main()
