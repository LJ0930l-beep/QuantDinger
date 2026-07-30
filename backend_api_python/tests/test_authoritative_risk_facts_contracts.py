"""Pure Contract Lock checks for RF-01 authoritative risk facts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from tests.pr12c_admission_loader import load_pr12c_admission


m = load_pr12c_admission()
c = m.authoritative_risk_facts
UTC = timezone.utc
HEX = hashlib.sha256(b"risk-facts").hexdigest()


def graph(action=None, execution=None):
    action = action or m.order.OrderAction.OPEN
    execution = execution or m.entry.ExecutionKind.MARKET
    reducing = action in {m.order.OrderAction.REDUCE, m.order.OrderAction.CLOSE, m.order.OrderAction.EMERGENCY_CLOSE, m.order.OrderAction.PROTECTION}
    stop = execution in {m.entry.ExecutionKind.STOP_MARKET, m.entry.ExecutionKind.STOP_LIMIT}
    intent = m.entry_v2.CanonicalEconomicIntentV2(
        side=m.entry.OrderSide.BUY,
        quantity=None if reducing else m.decimal.Quantity("1"),
        quantity_semantics=None if reducing else m.entry_v2.QuantitySemantics.ABSOLUTE,
        execution_kind=execution,
        trigger_price=m.decimal.Price("100") if stop else None,
        trigger_direction=m.entry_v2.TriggerDirection.AT_OR_ABOVE if stop else None,
        trigger_price_type=m.entry_v2.TriggerPriceType.MARK if stop else None,
        reduce_only=reducing,
        target_position_id="position-1" if reducing else None,
        close_quantity=m.decimal.Quantity("1") if reducing else None,
    )
    request = m.entry_v2.CanonicalEntryRequestV2(
        1, 2, "account-1", "BTC-USDT", "usdm", action, intent,
        m.entry.EntryActorContext(m.order.Actor.HUMAN, "human-1", m.entry.EntrySource.REST),
        m.order.RiskEffect.REDUCE_RISK if reducing else m.order.RiskEffect.INCREASE_RISK,
        "case-1", "corr-1", datetime(2026, 7, 30, tzinfo=UTC), m.entry.EntryMode.PAPER,
    )
    return m.entry_v2.DurableEntryGraphV2("11111111-1111-1111-1111-111111111111", request, m.entry_v2.EconomicOrderSubject("22222222-2222-2222-2222-222222222222"))


class AuthoritativeRiskFactsContractTests(unittest.TestCase):
    def test_scope_is_exact_and_non_strategy_scope_is_persisted_constant(self):
        scope = c.AuthoritativeRiskFactScope.from_graph(graph())
        self.assertEqual(c.NON_STRATEGY_SCOPE, scope.strategy_scope)
        self.assertEqual("BTC-USDT", scope.instrument_id)

    def test_provenance_rejects_future_and_stale_observations(self):
        anchor = datetime(2026, 7, 30, 12, tzinfo=UTC)
        future = c.RiskFactProvenance(c.RiskFactSourceKind.POLICY, "policy-a", "1", HEX, anchor + timedelta(seconds=1), 60)
        with self.assertRaises(c.RiskFactsVersionConflict):
            future.validate_selection_anchor(anchor)
        stale = c.RiskFactProvenance(c.RiskFactSourceKind.POLICY, "policy-a", "1", HEX, anchor - timedelta(seconds=61), 60)
        with self.assertRaises(c.RiskFactsStale):
            stale.validate_selection_anchor(anchor)

    def test_market_selection_has_no_limit_or_last_price_fallback(self):
        self.assertIs(c.MarketPriceType.MARK, c.required_market_price_type(graph()))
        stop = graph(execution=m.entry.ExecutionKind.STOP_MARKET)
        self.assertIs(c.MarketPriceType.MARK, c.required_market_price_type(stop))

    def test_cancel_cannot_construct_authoritative_risk_scope(self):
        intent = m.entry_v2.CanonicalEconomicIntentV2(cancel_target_kind=m.entry_v2.CancelTargetKind.CLIENT_ORDER_ID, cancel_target_id="client-1")
        request = m.entry_v2.CanonicalEntryRequestV2(1, 2, "account-1", "BTC-USDT", "usdm", m.order.OrderAction.CANCEL, intent, m.entry.EntryActorContext(m.order.Actor.HUMAN, "human-1", m.entry.EntrySource.REST), m.order.RiskEffect.NEUTRAL, "case-2", "corr-2", datetime(2026, 7, 30, tzinfo=UTC), m.entry.EntryMode.PAPER)
        cancel = m.entry_v2.DurableEntryGraphV2("33333333-3333-3333-3333-333333333333", request, m.entry_v2.CancelTargetSubject(m.entry_v2.CancelTargetKind.CLIENT_ORDER_ID, "client-1"))
        with self.assertRaises(c.RiskFactsScopeConflict):
            c.AuthoritativeRiskFactScope.from_graph(cancel)


if __name__ == "__main__":
    unittest.main()
