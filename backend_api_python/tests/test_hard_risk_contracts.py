from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from tests.pr10_contract_loader import load_pr10_contracts


modules = load_pr10_contracts()
decimal = modules.decimal
contracts = modules.contracts
risk = modules.hard_risk


def policy():
    return risk.RiskLimitPolicy(
        "policy-1", "USDT", decimal.QuoteAmount("1000"), decimal.QuoteAmount("700"),
        decimal.QuoteAmount("600"), "4", decimal.QuoteAmount("100"), decimal.QuoteAmount("100"), "0.20",
    )


def snapshot(**changes):
    values = dict(
        account_scope="account-1", instrument_id="BTCUSDT", valuation_currency="USDT",
        gross_notional="100", net_notional="100", instrument_notional="100", available_margin="800",
        equity="500", peak_equity="500", daily_realized_pnl="0",
        reconciliation_health=contracts.ReconciliationHealth.HEALTHY,
        market_data_health=risk.MarketDataHealth.FRESH, account_facts_verified=True,
    )
    values.update(changes)
    return risk.RiskExposureSnapshot(**values)


def request(action=contracts.OrderAction.OPEN, **changes):
    values = dict(
        action=action, actor=contracts.Actor.AGENT, risk_effect=None,
        gross_notional="100", net_notional="100", instrument_notional="100", margin="25",
    )
    values.update(changes)
    return risk.HardRiskRequest(**values)


def switches(mode=None):
    state = risk.KillSwitchState(7, mode is not None, mode)
    return risk.KillSwitchSnapshot(state, state, state)


class HardRiskContractTests(unittest.TestCase):
    def test_healthy_increase_with_explicit_facts_is_allowed(self):
        decision = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshot(), request=request(), kill_switches=switches())
        self.assertTrue(decision.allowed)
        self.assertEqual(Decimal("200"), decision.projected.gross_notional)
        self.assertEqual(Decimal("0.4"), decision.projected.leverage)

    def test_reconciliation_stale_market_data_and_unverified_facts_fail_closed_for_open(self):
        decision = risk.evaluate_hard_risk(
            policy=policy(),
            snapshot=snapshot(reconciliation_health=contracts.ReconciliationHealth.DEGRADED, market_data_health=risk.MarketDataHealth.STALE, account_facts_verified=False),
            request=request(), kill_switches=switches(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual({risk.RiskRejectionCode.RECONCILIATION_UNHEALTHY, risk.RiskRejectionCode.MARKET_DATA_NOT_FRESH, risk.RiskRejectionCode.ACCOUNT_FACTS_UNVERIFIED}, set(decision.rejections))

    def test_reducing_action_remains_available_during_degraded_health(self):
        decision = risk.evaluate_hard_risk(
            policy=policy(), snapshot=snapshot(reconciliation_health=contracts.ReconciliationHealth.UNHEALTHY, market_data_health=risk.MarketDataHealth.UNKNOWN, account_facts_verified=False),
            request=request(contracts.OrderAction.CLOSE, gross_notional="0", net_notional="0", instrument_notional="0", margin="0"),
            kill_switches=switches(),
        )
        self.assertTrue(decision.allowed)

    def test_non_increasing_actions_cannot_smuggle_a_reservation_demand(self):
        with self.assertRaises(risk.HardRiskContractError):
            request(contracts.OrderAction.REDUCE, gross_notional="1", net_notional="0", instrument_notional="0", margin="0")

    def test_all_actor_types_are_subject_to_the_same_kill_switch(self):
        for actor in contracts.Actor:
            with self.subTest(actor=actor):
                decision = risk.evaluate_hard_risk(
                    policy=policy(), snapshot=snapshot(), request=request(actor=actor),
                    kill_switches=switches(risk.KillSwitchMode.OPEN_BLOCKED),
                )
                self.assertFalse(decision.allowed)
                self.assertIn(risk.RiskRejectionCode.KILL_SWITCH, decision.rejections)

    def test_switch_modes_are_fail_closed_and_emergency_only_allows_reduction(self):
        blocked = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshot(), request=request(), kill_switches=risk.KillSwitchSnapshot(risk.KillSwitchState(1, True, risk.KillSwitchMode.ALL_NEW_COMMANDS_BLOCKED), risk.KillSwitchState(1, True, risk.KillSwitchMode.OPEN_BLOCKED), risk.KillSwitchState(1, True, risk.KillSwitchMode.OPEN_BLOCKED)))
        self.assertIn(risk.RiskRejectionCode.KILL_SWITCH, blocked.rejections)
        emergency = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshot(), request=request(contracts.OrderAction.EMERGENCY_CLOSE, gross_notional="0", net_notional="0", instrument_notional="0", margin="0"), kill_switches=switches(risk.KillSwitchMode.EMERGENCY_REDUCE_ONLY))
        self.assertTrue(emergency.allowed)

    def test_all_limit_dimensions_are_checked_for_new_risk(self):
        cases = {
            "gross_notional": risk.RiskRejectionCode.GROSS_NOTIONAL_LIMIT,
            "net_notional": risk.RiskRejectionCode.NET_NOTIONAL_LIMIT,
            "instrument_notional": risk.RiskRejectionCode.INSTRUMENT_NOTIONAL_LIMIT,
            "available_margin": risk.RiskRejectionCode.AVAILABLE_MARGIN_LIMIT,
            "daily_realized_pnl": risk.RiskRejectionCode.DAILY_LOSS_LIMIT,
            "peak_equity": risk.RiskRejectionCode.DRAWDOWN_LIMIT,
        }
        snapshots = {
            "gross_notional": snapshot(gross_notional="950"),
            "net_notional": snapshot(net_notional="650"),
            "instrument_notional": snapshot(instrument_notional="550"),
            "available_margin": snapshot(available_margin="110"),
            "daily_realized_pnl": snapshot(daily_realized_pnl="-101"),
            "peak_equity": snapshot(equity="500", peak_equity="700"),
        }
        for key, code in cases.items():
            with self.subTest(key=key):
                decision = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshots[key], request=request(), kill_switches=switches())
                self.assertIn(code, decision.rejections)

    def test_active_reservations_reduce_capacity_and_are_scope_safe(self):
        held = risk.RiskReservationDemand("reservation-1", "account-1", "BTCUSDT", "USDT", "850", "0", "0", "0")
        decision = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshot(), request=request(), kill_switches=switches(), active_reservations=[held])
        self.assertIn(risk.RiskRejectionCode.GROSS_NOTIONAL_LIMIT, decision.rejections)
        with self.assertRaises(risk.ReservationReducerError):
            risk.reduce_active_reservations([held], account_scope="other", instrument_id="BTCUSDT", valuation_currency="USDT")
        with self.assertRaises(risk.ReservationReducerError):
            risk.reduce_active_reservations([held, held], account_scope="account-1", instrument_id="BTCUSDT", valuation_currency="USDT")

    def test_decimal_contract_rejects_binary_float_and_invalid_drawdown(self):
        with self.assertRaises(risk.HardRiskContractError):
            request(gross_notional=0.1)
        with self.assertRaises(risk.HardRiskContractError):
            risk.KillSwitchState(1, True, None)
        with self.assertRaises(risk.HardRiskContractError):
            risk.RiskLimitPolicy("policy-1", "USDT", decimal.QuoteAmount("1"), decimal.QuoteAmount("1"), decimal.QuoteAmount("1"), "1", decimal.QuoteAmount("0"), decimal.QuoteAmount("0"), "1.1")

    def test_deterministic_fingerprint_includes_projected_state(self):
        first = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshot(), request=request(), kill_switches=switches())
        second = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshot(), request=request(), kill_switches=switches())
        changed = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshot(), request=request(net_notional="-100"), kill_switches=switches())
        self.assertEqual(first.canonical_fingerprint, second.canonical_fingerprint)
        self.assertNotEqual(first.canonical_fingerprint, changed.canonical_fingerprint)
        different_scope = risk.evaluate_hard_risk(policy=policy(), snapshot=snapshot(account_scope="account-2"), request=request(), kill_switches=switches())
        self.assertNotEqual(first.canonical_fingerprint, different_scope.canonical_fingerprint)

    def test_module_has_no_flask_database_or_exchange_dependency(self):
        source = Path(risk.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("flask", "psycopg", "sqlalchemy", "get_db_connection", "exchange client", "requests"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
