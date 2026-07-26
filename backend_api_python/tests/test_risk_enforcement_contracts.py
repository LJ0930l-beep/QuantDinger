"""Pure scope and immutable-fact contracts for PR-10 enforcement storage."""

from __future__ import annotations

from uuid import uuid4
from datetime import datetime, timezone
import unittest

from tests.pr10_contract_loader import load_pr10_contracts


modules = load_pr10_contracts()
decimal = modules.decimal
contracts = modules.contracts
risk = modules.hard_risk
enforcement = modules.enforcement


def _scope(**changes):
    values = {
        "command_id": str(uuid4()), "economic_order_id": str(uuid4()),
        "tenant_id": 1, "credential_id": 2, "account_scope": "account-a",
        "instrument_id": "BTCUSDT", "market_type": "swap",
        "action": contracts.OrderAction.OPEN, "actor": contracts.Actor.STRATEGY,
        "actor_id": "strategy-a", "correlation_id": "correlation-a",
    }
    values.update(changes)
    return enforcement.RiskEnforcementScope(**values)


def _policy():
    return risk.RiskLimitPolicy(
        "policy-1", "USDT", decimal.QuoteAmount("1000"), decimal.QuoteAmount("800"),
        decimal.QuoteAmount("900"), "5", decimal.QuoteAmount("10"),
        decimal.QuoteAmount("100"), "0.50",
    )


def _exposure(**changes):
    values = {
        "account_scope": "account-a", "instrument_id": "BTCUSDT", "valuation_currency": "USDT",
        "gross_notional": "100", "net_notional": "100", "instrument_notional": "100",
        "available_margin": "900", "equity": "1000", "peak_equity": "1000",
        "daily_realized_pnl": "0", "reconciliation_health": contracts.ReconciliationHealth.HEALTHY,
        "market_data_health": risk.MarketDataHealth.FRESH, "account_facts_verified": True,
    }
    values.update(changes)
    return risk.RiskExposureSnapshot(**values)


def _request(**changes):
    values = {
        "action": contracts.OrderAction.OPEN, "actor": contracts.Actor.STRATEGY,
        "risk_effect": None, "gross_notional": "10", "net_notional": "10",
        "instrument_notional": "10", "margin": "2",
    }
    values.update(changes)
    return risk.HardRiskRequest(**values)


def _switches():
    disabled = risk.KillSwitchState(0, False)
    return risk.KillSwitchSnapshot(disabled, disabled, disabled)


NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


def _decision(scope, policy_snapshot, input_snapshot, request=None):
    evaluated = risk.evaluate_hard_risk(
        policy=policy_snapshot.policy, snapshot=input_snapshot.exposure,
        request=request or _request(), kill_switches=_switches(),
    )
    return enforcement.RiskDecisionFact(str(uuid4()), scope, policy_snapshot, input_snapshot, evaluated, NOW, NOW)


class RiskEnforcementContractTests(unittest.TestCase):
    def test_policy_input_and_decision_are_scope_bound_and_deterministic(self):
        scope = _scope()
        policy_snapshot = enforcement.RiskPolicySnapshotFact(str(uuid4()), scope, _policy())
        input_snapshot = enforcement.RiskInputSnapshotFact(str(uuid4()), scope, "input-1", _exposure(), _switches(), NOW, NOW)
        first = _decision(scope, policy_snapshot, input_snapshot)
        second = enforcement.RiskDecisionFact(first.decision_id, scope, policy_snapshot, input_snapshot, first.decision, NOW, NOW)
        self.assertEqual(first.decision_status, "ALLOW")
        self.assertEqual(first.decision_fingerprint, second.decision_fingerprint)
        self.assertEqual(len(first.decision_fingerprint), 64)

    def test_mixed_snapshot_scope_fails_closed(self):
        scope = _scope()
        policy_snapshot = enforcement.RiskPolicySnapshotFact(str(uuid4()), scope, _policy())
        wrong_scope = _scope(command_id=scope.command_id, economic_order_id=scope.economic_order_id, instrument_id="ETHUSDT")
        input_snapshot = enforcement.RiskInputSnapshotFact(str(uuid4()), wrong_scope, "input-1", _exposure(instrument_id="ETHUSDT"), _switches(), NOW, NOW)
        with self.assertRaises(enforcement.RiskEnforcementContractError):
            _decision(scope, policy_snapshot, input_snapshot)

    def test_denied_or_non_increasing_decision_never_reserves_capacity(self):
        scope = _scope()
        policy_snapshot = enforcement.RiskPolicySnapshotFact(str(uuid4()), scope, _policy())
        input_snapshot = enforcement.RiskInputSnapshotFact(
            str(uuid4()), scope, "input-1", _exposure(market_data_health=risk.MarketDataHealth.STALE), _switches(), NOW, NOW,
        )
        denied = _decision(scope, policy_snapshot, input_snapshot)
        self.assertFalse(denied.decision.allowed)
        self.assertIsNone(enforcement.build_risk_reservation_fact(
            reservation_id=str(uuid4()), decision=denied, request=_request(), reservation_kind="OPEN_CAPACITY",
        ))

    def test_allowed_increase_reservation_carries_decision_scope_and_decimal_demand(self):
        scope = _scope()
        policy_snapshot = enforcement.RiskPolicySnapshotFact(str(uuid4()), scope, _policy())
        input_snapshot = enforcement.RiskInputSnapshotFact(str(uuid4()), scope, "input-1", _exposure(), _switches(), NOW, NOW)
        decision = _decision(scope, policy_snapshot, input_snapshot)
        reservation = enforcement.build_risk_reservation_fact(
            reservation_id=str(uuid4()), decision=decision, request=_request(), reservation_kind="OPEN_CAPACITY",
        )
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation.demand.account_scope, scope.account_scope)
        self.assertEqual(reservation.demand.gross_notional, decimal.Decimal("10"))

    def test_scope_requires_canonical_market_and_instrument_values(self):
        with self.assertRaises(enforcement.RiskEnforcementContractError):
            _scope(market_type="SWAP")
        with self.assertRaises(enforcement.RiskEnforcementContractError):
            _scope(instrument_id="btcusdt")
