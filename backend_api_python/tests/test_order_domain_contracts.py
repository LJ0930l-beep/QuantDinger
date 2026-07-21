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
OrderAction = contracts.OrderAction
ReconciliationHealth = contracts.ReconciliationHealth
RiskEffect = contracts.RiskEffect


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
                "CANCEL_PENDING",
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
                "CANCEL_PENDING",
                "CANCELLED",
                "REJECTED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.PARTIALLY_FILLED: {
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCEL_PENDING",
                "CANCELLED",
                "RECONCILIATION_REQUIRED",
            },
            EconomicOrderState.CANCEL_PENDING: {
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELLED",
                "FAILED",
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
            EconomicOrderState.FILLED: set(),
            EconomicOrderState.CANCELLED: set(),
            EconomicOrderState.REJECTED: set(),
            EconomicOrderState.FAILED: set(),
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

    def test_terminal_states_have_no_exit(self):
        terminals = {
            EconomicOrderState.FILLED,
            EconomicOrderState.CANCELLED,
            EconomicOrderState.REJECTED,
            EconomicOrderState.FAILED,
        }
        for state in EconomicOrderState:
            self.assertEqual(contracts.is_terminal_state(state), state in terminals)
        for current in terminals:
            for target in EconomicOrderState:
                self.assertFalse(contracts.validate_transition(current, target))

    def test_retry_and_exchange_query_contracts(self):
        retryable = {
            EconomicOrderState.CREATED,
            EconomicOrderState.RISK_PENDING,
            EconomicOrderState.RISK_RESERVED,
            EconomicOrderState.SUBMISSION_UNKNOWN,
            EconomicOrderState.CANCEL_PENDING,
            EconomicOrderState.RECONCILIATION_REQUIRED,
        }
        query_required = {
            EconomicOrderState.SUBMISSION_UNKNOWN,
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
