"""Contract-lock tests for independent durable-entry hard-risk enforcement V2."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
from uuid import uuid4

from tests.pr11_contract_loader import load_pr11_contracts


m = load_pr11_contracts()
risk = m.durable_risk_v2
entry_v2 = m.entry_v2
entry = m.entry
OrderAction = m.order.OrderAction
Actor = m.order.Actor
RiskEffect = m.order.RiskEffect
Quantity = m.decimals.Quantity


COMMAND_ID = "11111111-1111-1111-1111-111111111111"
ECONOMIC_ORDER_ID = "22222222-2222-2222-2222-222222222222"


def graph(*, action: OrderAction = OrderAction.OPEN, correlation: str = "corr-1", quantity: str = "1"):
    effect = RiskEffect.INCREASE_RISK if action in (OrderAction.OPEN, OrderAction.INCREASE) else RiskEffect.REDUCE_RISK
    intent = entry_v2.CanonicalEconomicIntentV2(
        side=entry.OrderSide.BUY,
        quantity=Quantity(quantity) if effect is RiskEffect.INCREASE_RISK else None,
        quantity_semantics=entry_v2.QuantitySemantics.ABSOLUTE if effect is RiskEffect.INCREASE_RISK else None,
        execution_kind=entry.ExecutionKind.MARKET,
        reduce_only=effect is RiskEffect.REDUCE_RISK,
        target_position_id="position-1" if effect is RiskEffect.REDUCE_RISK else None,
        close_quantity=Quantity("1") if effect is RiskEffect.REDUCE_RISK else None,
    )
    actor = entry.EntryActorContext(
        Actor.PROTECTION if action is OrderAction.PROTECTION else Actor.HUMAN,
        "actor-1",
        entry.EntrySource.PROTECTION if action is OrderAction.PROTECTION else entry.EntrySource.REST,
    )
    specification = entry_v2.CanonicalEntryRequestV2(
        1, 2, "account-1", "BTCUSDT", "swap", action, intent, actor, effect,
        "case-1", correlation, datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    return entry_v2.DurableEntryGraphV2(COMMAND_ID, specification, entry_v2.EconomicOrderSubject(ECONOMIC_ORDER_ID))


class DurableRiskEnforcementV2ContractTests(unittest.TestCase):
    def test_contract_locks_independent_tables_typed_columns_and_statuses(self):
        self.assertEqual("durable-risk-enforcement-v2", risk.DURABLE_RISK_ENFORCEMENT_V2_CONTRACT_VERSION)
        self.assertEqual(
            (
                "qd_durable_risk_policy_snapshots", "qd_durable_risk_input_snapshots",
                "qd_durable_risk_decisions", "qd_durable_risk_reservations",
            ), risk.DURABLE_RISK_TABLES,
        )
        self.assertEqual(frozenset(risk.DURABLE_RISK_TABLES), risk.DURABLE_RISK_APPEND_ONLY_TABLES)
        self.assertEqual({"ALLOW", "DENY", "RECONCILIATION_REQUIRED"}, risk.DURABLE_RISK_DECISION_STATUSES)
        for columns in (
            risk.DURABLE_RISK_POLICY_SNAPSHOT_SQL_COLUMNS,
            risk.DURABLE_RISK_INPUT_SNAPSHOT_SQL_COLUMNS,
            risk.DURABLE_RISK_DECISION_SQL_COLUMNS,
            risk.DURABLE_RISK_RESERVATION_SQL_COLUMNS,
        ):
            self.assertIn("command_id", columns)
            self.assertIn("economic_order_id", columns)
            self.assertIn("scope_fingerprint", columns)
            self.assertIn("audit_fingerprint", columns)
        for column in (
            "max_gross_notional", "max_net_notional", "max_instrument_notional",
            "minimum_available_margin", "max_daily_loss", "max_drawdown_ratio",
        ):
            self.assertIn(column, risk.DURABLE_RISK_POLICY_SNAPSHOT_SQL_COLUMNS)
        for column in (
            "gross_notional", "net_notional", "instrument_notional", "available_margin",
            "equity", "peak_equity", "daily_realized_pnl", "reconciliation_health",
            "market_data_health", "account_facts_verified", "global_kill_switch_version",
        ):
            self.assertIn(column, risk.DURABLE_RISK_INPUT_SNAPSHOT_SQL_COLUMNS)
        for column in (
            "projected_gross_notional", "projected_net_notional",
            "projected_instrument_notional", "projected_available_margin",
            "projected_leverage", "projected_daily_loss", "projected_drawdown_ratio",
        ):
            self.assertIn(column, risk.DURABLE_RISK_DECISION_SQL_COLUMNS)

    def test_scope_fingerprint_is_economic_and_audit_is_separate(self):
        first = risk.build_durable_risk_scope_v2(graph(correlation="corr-1"))
        changed_audit = risk.build_durable_risk_scope_v2(graph(correlation="corr-2"))
        changed_economic = risk.build_durable_risk_scope_v2(graph(quantity="2"))
        self.assertEqual(first.scope_fingerprint, changed_audit.scope_fingerprint)
        self.assertNotEqual(first.audit_fingerprint, changed_audit.audit_fingerprint)
        self.assertNotEqual(first.scope_fingerprint, changed_economic.scope_fingerprint)
        self.assertEqual("canonical-entry-v2", first.durable_entry_contract_version)

    def test_cancel_is_rejected_before_risk_scope_or_sql(self):
        intent = entry_v2.CanonicalEconomicIntentV2(
            cancel_target_kind=entry_v2.CancelTargetKind.CLIENT_ORDER_ID,
            cancel_target_id="client-order-1",
        )
        specification = entry_v2.CanonicalEntryRequestV2(
            1, 2, "account-1", "BTCUSDT", "swap", OrderAction.CANCEL, intent,
            entry.EntryActorContext(Actor.HUMAN, "actor-1", entry.EntrySource.REST),
            RiskEffect.NEUTRAL, "case-1", "corr-1", datetime(2026, 7, 29, tzinfo=timezone.utc),
        )
        cancelled = entry_v2.DurableEntryGraphV2(
            uuid4(), specification,
            entry_v2.CancelTargetSubject(entry_v2.CancelTargetKind.CLIENT_ORDER_ID, "client-order-1"),
        )
        with self.assertRaises(risk.DurableRiskUnsupportedAction):
            risk.build_durable_risk_scope_v2(cancelled)

    def test_stable_ids_are_versioned_deterministic_and_ignore_audit_context(self):
        first = risk.build_durable_risk_scope_v2(graph(correlation="corr-1"))
        second = risk.build_durable_risk_scope_v2(graph(correlation="corr-2"))
        policy_hash = "a" * 64
        input_hash = "b" * 64
        demand_hash = "c" * 64
        self.assertEqual(risk.stable_policy_snapshot_id(first, policy_hash), risk.stable_policy_snapshot_id(second, policy_hash))
        self.assertEqual(risk.stable_input_snapshot_id(first, input_hash), risk.stable_input_snapshot_id(second, input_hash))
        decision_id = risk.stable_decision_id(first, policy_hash, input_hash)
        self.assertEqual(decision_id, risk.stable_decision_id(second, policy_hash, input_hash))
        self.assertEqual(risk.stable_reservation_id(decision_id, demand_hash), risk.stable_reservation_id(decision_id, demand_hash))
        self.assertNotEqual(decision_id, risk.stable_decision_id(first, "d" * 64, input_hash))


if __name__ == "__main__":
    unittest.main()
