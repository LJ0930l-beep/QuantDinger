from datetime import datetime, timedelta, timezone
import unittest
from uuid import UUID

from tests.pr05_contract_loader import load_pr05_contracts


modules = load_pr05_contracts()
contracts = modules.contracts
machine = modules.machine

ORDER_ID = "00000000-0000-0000-0000-000000000101"
ATTEMPT_ID = "00000000-0000-0000-0000-000000000102"
NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
ORDER_SCOPE = machine.EconomicOrderScope(1, 2, "account-a", "BTCUSDT", "swap")
ATTEMPT_SCOPE = machine.SubmissionAttemptScope(1, 2, "account-a", "BTCUSDT", "swap", ORDER_ID, "binance")


def order_event(current, target, cause=machine.TransitionCause.VENUE_OBSERVATION):
    return machine.authorize_order_transition(
        aggregate_id=ORDER_ID, current_state=current, target_state=target, expected_version=3,
        cause=cause, actor=contracts.Actor.ADMIN, reason_code="TEST", correlation_id="correlation-1",
        occurred_at=NOW, evidence_hash="a" * 64, canonical_payload={"source": "test"}, idempotency_key="case-1", aggregate_scope=ORDER_SCOPE,
    )


def attempt_event(current, target, cause=machine.TransitionCause.VENUE_OBSERVATION):
    return machine.authorize_attempt_transition(
        aggregate_id=ATTEMPT_ID, current_state=current, target_state=target, expected_version=3,
        cause=cause, actor=contracts.Actor.ADMIN, reason_code="TEST", correlation_id="correlation-1",
        occurred_at=NOW, evidence_hash="b" * 64, canonical_payload={"source": "test"}, idempotency_key="case-2", aggregate_scope=ATTEMPT_SCOPE,
    )


class OrderStateMachineTests(unittest.TestCase):
    def test_existing_structural_graph_accepts_every_declared_economic_transition(self):
        for current, targets in contracts._TRANSITIONS.items():
            for target in targets:
                self.assertTrue(contracts.validate_transition(current, target))

    def test_existing_structural_graph_accepts_every_declared_attempt_transition(self):
        for current, targets in contracts._ATTEMPT_TRANSITIONS.items():
            for target in targets:
                self.assertTrue(contracts.validate_attempt_transition(current, target))

    def test_overlay_rejects_structurally_allowed_unknown_resubmit(self):
        self.assertTrue(contracts.validate_transition(contracts.EconomicOrderState.SUBMISSION_UNKNOWN, contracts.EconomicOrderState.SUBMITTING))
        with self.assertRaises(machine.OperationalAuthorizationError):
            order_event(contracts.EconomicOrderState.SUBMISSION_UNKNOWN, contracts.EconomicOrderState.SUBMITTING)

    def test_overlay_rejects_confirmed_absent(self):
        self.assertTrue(contracts.validate_attempt_transition(contracts.SubmissionAttemptState.UNKNOWN, contracts.SubmissionAttemptState.CONFIRMED_ABSENT))
        with self.assertRaises(machine.OperationalAuthorizationError):
            attempt_event(contracts.SubmissionAttemptState.UNKNOWN, contracts.SubmissionAttemptState.CONFIRMED_ABSENT)

    def test_authorized_transition_is_versioned_and_fingerprinted_deterministically(self):
        first = order_event(contracts.EconomicOrderState.SUBMISSION_UNKNOWN, contracts.EconomicOrderState.SUBMITTED)
        second = order_event(contracts.EconomicOrderState.SUBMISSION_UNKNOWN, contracts.EconomicOrderState.SUBMITTED)
        self.assertEqual(4, first.resulting_version)
        self.assertEqual(4, first.event_seq)
        self.assertEqual(first.event_fingerprint, second.event_fingerprint)
        self.assertEqual(ORDER_ID, str(UUID(first.aggregate_id)))
        self.assertNotIn('"contract_version"', first.canonical_payload_json)

    def test_strict_utc_rejects_naive_and_non_utc(self):
        for value in (datetime(2026, 7, 23, 12, 0), datetime(2026, 7, 23, 20, 0, tzinfo=timezone(timedelta(hours=8)))):
            with self.assertRaises(machine.StateMachineContractError):
                machine.strict_utc(value)

    def test_strict_utc_normalizes_zero_offset(self):
        value = datetime(2026, 7, 23, 12, 0, tzinfo=timezone(timedelta(0)))
        self.assertIs(timezone.utc, machine.strict_utc(value).tzinfo)

    def test_unknown_contract_values_fail_closed(self):
        with self.assertRaises(machine.UnknownStateError):
            order_event("INVENTED", contracts.EconomicOrderState.SUBMITTED)
        with self.assertRaises(machine.UnknownActorError):
            machine.authorize_order_transition(
                aggregate_id=ORDER_ID, current_state=contracts.EconomicOrderState.SUBMISSION_UNKNOWN,
                target_state=contracts.EconomicOrderState.SUBMITTED, expected_version=0,
                cause=machine.TransitionCause.VENUE_OBSERVATION, actor="INVENTED", reason_code="TEST",
                correlation_id="c", occurred_at=NOW, evidence_hash="a", canonical_payload={}, idempotency_key="i", aggregate_scope=ORDER_SCOPE,
            )

    def test_binary_float_payload_fails_closed(self):
        with self.assertRaises(machine.StateMachineContractError):
            machine.authorize_order_transition(
                aggregate_id=ORDER_ID, current_state=contracts.EconomicOrderState.SUBMISSION_UNKNOWN,
                target_state=contracts.EconomicOrderState.SUBMITTED, expected_version=0,
                cause=machine.TransitionCause.VENUE_OBSERVATION, actor=contracts.Actor.ADMIN, reason_code="TEST",
                correlation_id="c", occurred_at=NOW, evidence_hash="a", canonical_payload={"price": 1.1}, idempotency_key="i", aggregate_scope=ORDER_SCOPE,
            )

    def test_actor_cause_matrix_fails_closed_when_no_runtime_principal_is_approved(self):
        with self.assertRaises(machine.OperationalAuthorizationError):
            machine.authorize_order_transition(
                aggregate_id=ORDER_ID, aggregate_scope=ORDER_SCOPE, current_state=contracts.EconomicOrderState.SUBMISSION_UNKNOWN,
                target_state=contracts.EconomicOrderState.SUBMITTED, expected_version=0,
                cause=machine.TransitionCause.VENUE_OBSERVATION, actor=contracts.Actor.HUMAN, reason_code="TEST",
                correlation_id="c", occurred_at=NOW, evidence_hash="a", canonical_payload={}, idempotency_key="i",
            )
        with self.assertRaises(machine.OperationalAuthorizationError):
            machine.authorize_order_transition(
                aggregate_id=ORDER_ID, aggregate_scope=ORDER_SCOPE, current_state=contracts.EconomicOrderState.CREATED,
                target_state=contracts.EconomicOrderState.RISK_PENDING, expected_version=0,
                cause=machine.TransitionCause.RISK_DECISION, actor=contracts.Actor.PROTECTION, reason_code="TEST",
                correlation_id="c", occurred_at=NOW, evidence_hash="a", canonical_payload={}, idempotency_key="i2",
            )

    def test_authorized_transition_cannot_be_constructed_as_a_bare_repository_input(self):
        with self.assertRaises(machine.StateMachineContractError):
            machine.AuthorizedTransition(
                aggregate_id=ORDER_ID, aggregate_type=machine.AggregateType.ECONOMIC_ORDER,
                current_state="SUBMISSION_UNKNOWN", target_state="SUBMITTED", expected_version=0,
                resulting_version=1, event_seq=1, transition_cause=machine.TransitionCause.VENUE_OBSERVATION,
                actor=contracts.Actor.ADMIN, reason_code="TEST", correlation_id="c", occurred_at=NOW,
                evidence_hash="a", canonical_payload={}, idempotency_key="i", aggregate_scope=ORDER_SCOPE,
            )


if __name__ == "__main__":
    unittest.main()
