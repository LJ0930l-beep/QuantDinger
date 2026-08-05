from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_contracts():
    path = Path(__file__).resolve().parents[1] / "app" / "domain" / "order_contracts.py"
    spec = importlib.util.spec_from_file_location("pr00_order_contracts", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load order contracts")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load_contracts()
Actor = contracts.Actor
AmbiguousRiskEffectError = contracts.AmbiguousRiskEffectError
EconomicOrderState = contracts.EconomicOrderState
ExchangeOrderNormalizedState = contracts.ExchangeOrderNormalizedState
OrderAction = contracts.OrderAction
ReconciliationHealth = contracts.ReconciliationHealth
ReconciliationCheckpointStatus = contracts.ReconciliationCheckpointStatus
RiskEffect = contracts.RiskEffect
SubmissionAttemptState = contracts.SubmissionAttemptState


class EconomicOrderStateContractTests(unittest.TestCase):
    def test_enum_vocabulary_is_exact(self):
        self.assertEqual(
            {item.value for item in EconomicOrderState},
            {
                "CREATED",
                "RISK_PENDING",
                "RISK_RESERVED",
                "SUBMITTING",
                "SUBMITTED",
                "SUBMISSION_UNKNOWN",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCEL_REQUESTED",
                "CANCELLING",
                "CANCELLED",
                "REJECTED",
                "FAILED",
                "RECONCILIATION_REQUIRED",
            },
        )

    def test_every_transition_is_explicit_and_all_other_pairs_fail_closed(self):
        expected = {
            EconomicOrderState.CREATED: {"RISK_PENDING", "REJECTED", "FAILED"},
            EconomicOrderState.RISK_PENDING: {
                "RISK_RESERVED",
                "REJECTED",
                "FAILED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.RISK_RESERVED: {
                "SUBMITTING",
                "CANCELLED",
                "FAILED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.SUBMITTING: {
                "SUBMITTED",
                "SUBMISSION_UNKNOWN",
                "PARTIALLY_FILLED",
                "FILLED",
                "REJECTED",
                "FAILED",
            },
            EconomicOrderState.SUBMISSION_UNKNOWN: {
                "SUBMITTING",
                "SUBMITTED",
                "PARTIALLY_FILLED",
                "FILLED",
                "REJECTED",
                "FAILED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.SUBMITTED: {
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCEL_REQUESTED",
                "CANCELLED",
                "REJECTED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.PARTIALLY_FILLED: {
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCEL_REQUESTED",
                "CANCELLED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.CANCEL_REQUESTED: {
                "CANCELLING",
                "FILLED",
                "CANCELLED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.CANCELLING: {
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELLED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.RECONCILIATION_REQUIRED: {
                "SUBMITTED",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELLED",
                "REJECTED",
                "FAILED",
            },
            EconomicOrderState.FILLED: {"RECONCILIATION_REQUIRED"},
            EconomicOrderState.CANCELLED: {"RECONCILIATION_REQUIRED"},
            EconomicOrderState.REJECTED: {"RECONCILIATION_REQUIRED"},
            EconomicOrderState.FAILED: {"RECONCILIATION_REQUIRED"},
        }
        for current in EconomicOrderState:
            with self.subTest(current=current):
                self.assertEqual(
                    {item.value for item in contracts.allowed_transitions(current)},
                    expected[current],
                )
            for target in EconomicOrderState:
                with self.subTest(current=current, target=target):
                    self.assertEqual(
                        contracts.validate_transition(current, target),
                        target.value in expected[current],
                    )

    def test_business_terminal_states_only_exit_to_reconciliation(self):
        business_terminals = {
            EconomicOrderState.FILLED,
            EconomicOrderState.CANCELLED,
            EconomicOrderState.REJECTED,
            EconomicOrderState.FAILED,
        }
        for state in EconomicOrderState:
            expected = state in business_terminals
            self.assertEqual(contracts.is_business_terminal_state(state), expected)
            self.assertEqual(contracts.is_terminal_state(state), expected)
            self.assertFalse(contracts.is_absolute_terminal_state(state))
        for current in business_terminals:
            for target in EconomicOrderState:
                with self.subTest(current=current, target=target):
                    self.assertEqual(
                        contracts.validate_transition(current, target),
                        target is EconomicOrderState.RECONCILIATION_REQUIRED,
                    )

    def test_business_terminal_states_never_return_to_submission(self):
        for current in (
            EconomicOrderState.FILLED,
            EconomicOrderState.CANCELLED,
            EconomicOrderState.REJECTED,
            EconomicOrderState.FAILED,
        ):
            self.assertFalse(
                contracts.validate_transition(current, EconomicOrderState.SUBMITTING)
            )

    def test_retry_and_exchange_query_contracts(self):
        retryable = {
            EconomicOrderState.CREATED,
            EconomicOrderState.RISK_PENDING,
            EconomicOrderState.RISK_RESERVED,
            EconomicOrderState.SUBMISSION_UNKNOWN,
            EconomicOrderState.CANCEL_REQUESTED,
            EconomicOrderState.CANCELLING,
            EconomicOrderState.RECONCILIATION_REQUIRED,
        }
        query_required = {
            EconomicOrderState.SUBMISSION_UNKNOWN,
            EconomicOrderState.CANCELLING,
            EconomicOrderState.RECONCILIATION_REQUIRED,
        }
        for state in EconomicOrderState:
            self.assertEqual(contracts.may_retry(state), state in retryable)
            self.assertEqual(
                contracts.requires_exchange_query_before_retry(state),
                state in query_required,
            )
        self.assertFalse(contracts.may_retry("NOT_A_STATE"))
        self.assertTrue(contracts.requires_exchange_query_before_retry("NOT_A_STATE"))
        self.assertFalse(contracts.validate_transition("NOT_A_STATE", "CREATED"))


class SubmissionAttemptStateContractTests(unittest.TestCase):
    def test_attempt_vocabulary_is_exact(self):
        self.assertEqual(
            {item.value for item in SubmissionAttemptState},
            {
                "READY",
                "SUBMITTING",
                "ACKED",
                "UNKNOWN",
                "CONFIRMED_ABSENT",
                "REJECTED",
            },
        )

    def test_attempt_transition_graph_is_explicit_and_fail_closed(self):
        expected = {
            SubmissionAttemptState.READY: {"SUBMITTING"},
            SubmissionAttemptState.SUBMITTING: {"ACKED", "UNKNOWN", "REJECTED"},
            SubmissionAttemptState.UNKNOWN: {
                "ACKED",
                "CONFIRMED_ABSENT",
                "REJECTED",
            },
            SubmissionAttemptState.ACKED: set(),
            SubmissionAttemptState.CONFIRMED_ABSENT: set(),
            SubmissionAttemptState.REJECTED: set(),
        }
        for current in SubmissionAttemptState:
            self.assertEqual(
                {item.value for item in contracts.allowed_attempt_transitions(current)},
                expected[current],
            )
            for target in SubmissionAttemptState:
                with self.subTest(current=current, target=target):
                    self.assertEqual(
                        contracts.validate_attempt_transition(current, target),
                        target.value in expected[current],
                    )

    def test_unknown_attempt_requires_query_and_cannot_blindly_resubmit(self):
        self.assertTrue(
            contracts.attempt_requires_exchange_query(SubmissionAttemptState.UNKNOWN)
        )
        self.assertFalse(
            contracts.validate_attempt_transition(
                SubmissionAttemptState.UNKNOWN,
                SubmissionAttemptState.SUBMITTING,
            )
        )
        for state in SubmissionAttemptState:
            self.assertEqual(
                contracts.attempt_requires_exchange_query(state),
                state is SubmissionAttemptState.UNKNOWN,
            )
        self.assertTrue(contracts.attempt_requires_exchange_query("NOT_A_STATE"))
        self.assertFalse(
            contracts.validate_attempt_transition("NOT_A_STATE", "SUBMITTING")
        )


class ExchangeOrderNormalizedStateContractTests(unittest.TestCase):
    def test_normalized_exchange_order_state_vocabulary_is_exact(self):
        self.assertEqual(
            {item.value for item in ExchangeOrderNormalizedState},
            {
                "SUBMITTED",
                "PARTIALLY_FILLED",
                "FILLED",
                "SUBMISSION_UNKNOWN",
                "CANCEL_REQUESTED",
                "CANCELLING",
                "CANCELLED",
                "REJECTED",
                "RECONCILIATION_REQUIRED",
            },
        )


class ReconciliationCheckpointContractTests(unittest.TestCase):
    def test_checkpoint_status_vocabulary_is_exact(self):
        self.assertEqual(
            {item.value for item in ReconciliationCheckpointStatus},
            {"HEALTHY", "STALE", "FAILED", "CONFLICT"},
        )

    def test_health_is_derived_one_way_from_checkpoint_status(self):
        cases = {
            ReconciliationCheckpointStatus.HEALTHY: ReconciliationHealth.HEALTHY,
            ReconciliationCheckpointStatus.STALE: ReconciliationHealth.DEGRADED,
            ReconciliationCheckpointStatus.FAILED: ReconciliationHealth.UNHEALTHY,
            ReconciliationCheckpointStatus.CONFLICT: ReconciliationHealth.UNHEALTHY,
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertIs(contracts.derive_reconciliation_health(status), expected)

    def test_missing_or_unknown_checkpoint_status_fails_closed(self):
        self.assertIs(
            contracts.derive_reconciliation_health(None),
            ReconciliationHealth.UNHEALTHY,
        )
        self.assertIs(
            contracts.derive_reconciliation_health("UNKNOWN"),
            ReconciliationHealth.UNHEALTHY,
        )

    def test_expired_healthy_checkpoint_degrades_without_mutating_status(self):
        self.assertIs(
            contracts.derive_reconciliation_health(
                ReconciliationCheckpointStatus.HEALTHY,
                sla_expired=True,
            ),
            ReconciliationHealth.DEGRADED,
        )


class RiskActionContractTests(unittest.TestCase):
    def test_action_actor_and_risk_vocabularies_are_exact(self):
        self.assertEqual(
            {item.value for item in OrderAction},
            {"OPEN", "INCREASE", "REDUCE", "CLOSE", "CANCEL", "EMERGENCY_CLOSE", "PROTECTION"},
        )
        self.assertEqual(
            {item.value for item in Actor},
            {"STRATEGY", "HUMAN", "AGENT", "MCP", "GRID", "PROTECTION", "ADMIN"},
        )

    def test_risk_effect_classification(self):
        self.assertIs(contracts.classify_risk_effect(OrderAction.OPEN), RiskEffect.INCREASE_RISK)
        self.assertIs(contracts.classify_risk_effect(OrderAction.INCREASE), RiskEffect.INCREASE_RISK)
        for action in (OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE):
            self.assertIs(contracts.classify_risk_effect(action), RiskEffect.REDUCE_RISK)
        self.assertIs(contracts.classify_risk_effect(OrderAction.CANCEL), RiskEffect.NEUTRAL)
        with self.assertRaises(AmbiguousRiskEffectError):
            contracts.classify_risk_effect(OrderAction.PROTECTION)
        with self.assertRaises(ValueError):
            contracts.classify_risk_effect("UNKNOWN")

    def test_unhealthy_reconciliation_blocks_increase_for_every_actor(self):
        allowed_when_unhealthy = {
            OrderAction.REDUCE,
            OrderAction.CLOSE,
            OrderAction.CANCEL,
            OrderAction.EMERGENCY_CLOSE,
        }
        for health in (ReconciliationHealth.DEGRADED, ReconciliationHealth.UNHEALTHY):
            for actor in Actor:
                for action in OrderAction:
                    expected = action in allowed_when_unhealthy
                    with self.subTest(health=health, actor=actor, action=action):
                        self.assertEqual(
                            contracts.is_action_allowed(action, health, actor=actor),
                            expected,
                        )

    def test_protection_requires_explicit_effect_and_never_uses_actor_as_override(self):
        for actor in Actor:
            self.assertFalse(
                contracts.is_action_allowed(
                    OrderAction.PROTECTION,
                    ReconciliationHealth.DEGRADED,
                    actor=actor,
                )
            )
            self.assertTrue(
                contracts.is_action_allowed(
                    OrderAction.PROTECTION,
                    ReconciliationHealth.DEGRADED,
                    risk_effect=RiskEffect.REDUCE_RISK,
                    actor=actor,
                )
            )
            self.assertFalse(
                contracts.is_action_allowed(
                    OrderAction.PROTECTION,
                    ReconciliationHealth.DEGRADED,
                    risk_effect=RiskEffect.INCREASE_RISK,
                    actor=actor,
                )
            )

    def test_unknown_action_health_or_actor_fails_closed(self):
        self.assertFalse(contracts.is_action_allowed("UNKNOWN", ReconciliationHealth.HEALTHY))
        self.assertFalse(contracts.is_action_allowed(OrderAction.CANCEL, "UNKNOWN"))
        self.assertFalse(
            contracts.is_action_allowed(
                OrderAction.CANCEL,
                ReconciliationHealth.HEALTHY,
                actor="SUPERUSER",
            )
        )


if __name__ == "__main__":
    unittest.main()
