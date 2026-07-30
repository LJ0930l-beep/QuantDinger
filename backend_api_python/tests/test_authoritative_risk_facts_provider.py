"""Focused caller-owned tests for RF-01B authoritative source selection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from tests.pr12c_admission_loader import load_pr12c_admission


m = load_pr12c_admission()
anchor = datetime(2026, 7, 30, tzinfo=timezone.utc)
HASH = "a" * 64


def graph(action=None):
    action = action or m.order.OrderAction.OPEN
    intent = m.entry_v2.CanonicalEconomicIntentV2(
        side=m.entry.OrderSide.BUY,
        quantity=m.decimal.Quantity("2"),
        quantity_semantics=m.entry_v2.QuantitySemantics.ABSOLUTE,
        execution_kind=m.entry.ExecutionKind.MARKET,
        reduce_only=False,
        position_side=m.entry.PositionSide.NET,
    )
    actor = m.entry.EntryActorContext(m.order.Actor.HUMAN, "human-1", m.entry.EntrySource.REST)
    request = m.entry_v2.CanonicalEntryRequestV2(
        1, 2, "account-1", "BTCUSDT", "swap", action, intent, actor,
        m.order.RiskEffect.INCREASE_RISK, "case-1", "corr-1", anchor, m.entry.EntryMode.PAPER,
    )
    return m.entry_v2.DurableEntryGraphV2("11111111-1111-1111-1111-111111111111", request, m.entry_v2.EconomicOrderSubject("22222222-2222-2222-2222-222222222222"))


class _Cursor:
    def __init__(self, rows): self.rows, self.calls, self.closed = list(rows), [], False
    def execute(self, query, params=()): self.calls.append((query, params))
    def fetchall(self): return self.rows.pop(0)
    def close(self): self.closed = True


class _Connection:
    def __init__(self, cursor): self.cursor_value = cursor
    def cursor(self): return self.cursor_value


def source_rows(*, market_health="FRESH"):
    switch = ("switch-source", "v1", HASH, anchor, 60, 1, False, None)
    return [
        [("policy-source", "policy-v1", HASH, anchor, 60, 30, "USDT", "1000", "700", "600", "4", "100", "100", "0.2")],
        [("rule-source", "rule-v1", HASH, anchor, 60, "USDT", "1", "0.25")],
        [("account-source", "facts-v1", HASH, anchor, 60, "USDT", "100", "100", "100", "800", "500", "500", "0", True)],
        [("33333333-3333-3333-3333-333333333333", "HEALTHY", 1, HASH, anchor, 60, None)],
        [switch], [switch], [switch],
        [("market-source", "market-v1", HASH, anchor, 60, "USDT", "MARK", "50", market_health)],
        [],  # advisory lock has no row result
        [],  # active reservations
    ]


class AuthoritativeRiskFactsProviderTests(unittest.TestCase):
    def test_prepare_uses_one_connection_without_transaction_control_and_builds_demand(self):
        cursor = _Cursor(source_rows())
        result = m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(_Connection(cursor), graph())
        self.assertEqual(m.decimal.Decimal("100"), result.request.gross_notional)
        self.assertEqual(m.decimal.Decimal("25"), result.request.margin)
        self.assertEqual(m.decimal.Decimal("100"), result.reservation_demand.gross_notional)
        self.assertEqual(anchor.replace(second=30), result.expires_at)
        self.assertEqual(9, len(result.provenance))
        self.assertTrue(cursor.closed)
        source = Path(m.authoritative_risk_provider.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".commit(", source)
        self.assertNotIn(".rollback(", source)
        self.assertIn("pg_advisory_xact_lock", source)

    def test_persisted_stale_market_health_produces_typed_stale_input_not_a_price_fallback(self):
        result = m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(
            _Connection(_Cursor(source_rows(market_health="STALE"))), graph(),
        )
        self.assertIs(m.hard_risk.MarketDataHealth.STALE, result.exposure.market_data_health)
        self.assertEqual("100", result.request.gross_notional.to_integral_value().to_eng_string())
        _, _, decision, _ = m.durable_risk.build_durable_risk_facts_v2(
            graph(), policy=result.policy, exposure=result.exposure,
            kill_switches=result.kill_switches, request=result.request,
            observed_at=result.observed_at, active_reservations=result.active_reservations,
        )
        self.assertFalse(decision.decision.allowed)
        self.assertIn("MARKET_DATA_NOT_FRESH", {item.value for item in decision.decision.rejections})

    def test_missing_policy_fails_closed_and_closes_cursor(self):
        cursor = _Cursor([[]])
        with self.assertRaises(m.authoritative_risk_facts.RiskFactsUnavailable):
            m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(_Connection(cursor), graph())
        self.assertTrue(cursor.closed)


if __name__ == "__main__":
    unittest.main()
