"""Focused caller-owned tests for RF-01B authoritative source selection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from uuid import UUID

from tests.pr12c_admission_loader import load_pr12c_admission


m = load_pr12c_admission()
anchor = datetime(2026, 7, 30, tzinfo=timezone.utc)
HASH = "a" * 64


def graph(action=None):
    action = action or m.order.OrderAction.OPEN
    reducing = action in {
        m.order.OrderAction.REDUCE, m.order.OrderAction.CLOSE,
        m.order.OrderAction.EMERGENCY_CLOSE, m.order.OrderAction.PROTECTION,
    }
    if action is m.order.OrderAction.CANCEL:
        intent = m.entry_v2.CanonicalEconomicIntentV2(
            cancel_target_kind=m.entry_v2.CancelTargetKind.CLIENT_ORDER_ID,
            cancel_target_id="venue-client-1",
        )
        actor = m.entry.EntryActorContext(m.order.Actor.HUMAN, "human-1", m.entry.EntrySource.REST)
        subject = m.entry_v2.CancelTargetSubject(
            m.entry_v2.CancelTargetKind.CLIENT_ORDER_ID, "venue-client-1",
        )
        effect = m.order.RiskEffect.NEUTRAL
    else:
        intent = m.entry_v2.CanonicalEconomicIntentV2(
            side=m.entry.OrderSide.BUY,
            quantity=None if reducing else m.decimal.Quantity("2"),
            quantity_semantics=None if reducing else m.entry_v2.QuantitySemantics.ABSOLUTE,
            execution_kind=m.entry.ExecutionKind.MARKET,
            reduce_only=reducing,
            target_position_id="position-1" if reducing else None,
            close_quantity=m.decimal.Quantity("2") if reducing else None,
            position_side=m.entry.PositionSide.NET,
        )
        protection = action is m.order.OrderAction.PROTECTION
        actor = m.entry.EntryActorContext(
            m.order.Actor.PROTECTION if protection else m.order.Actor.HUMAN,
            "protection-1" if protection else "human-1",
            m.entry.EntrySource.PROTECTION if protection else m.entry.EntrySource.REST,
        )
        subject = m.entry_v2.EconomicOrderSubject("22222222-2222-2222-2222-222222222222")
        effect = m.order.RiskEffect.REDUCE_RISK if reducing else m.order.RiskEffect.INCREASE_RISK
    request = m.entry_v2.CanonicalEntryRequestV2(
        1, 2, "account-1", "BTCUSDT", "swap", action, intent, actor,
        effect, "case-1", "corr-1", anchor, m.entry.EntryMode.PAPER,
    )
    return m.entry_v2.DurableEntryGraphV2("11111111-1111-1111-1111-111111111111", request, subject)


class _Cursor:
    def __init__(self, rows): self.rows, self.calls, self.closed = list(rows), [], False
    def execute(self, query, params=()): self.calls.append((query, params))
    def fetchall(self): return self.rows.pop(0)
    def close(self): self.closed = True


class _Connection:
    def __init__(self, cursor): self.cursor_value = cursor
    def cursor(self): return self.cursor_value


def source_rows(*, market_health="FRESH", reconciliation_identity="33333333-3333-3333-3333-333333333333"):
    switch = ("switch-source", "v1", HASH, anchor, 60, 1, False, None)
    return [
        [("policy-source", "policy-v1", HASH, anchor, 60, 30, "USDT", "1000", "700", "600", "4", "100", "100", "0.2")],
        [("rule-source", "rule-v1", HASH, anchor, 60, "USDT", "1", "0.25")],
        [("account-source", "facts-v1", HASH, anchor, 60, "USDT", "100", "100", "100", "800", "500", "500", "0", True)],
        [(reconciliation_identity, "HEALTHY", 1, HASH, anchor, 60, None)],
        [switch], [switch], [switch],
        [("market-source", "market-v1", HASH, anchor, 60, "USDT", "MARK", "50", market_health)],
        [],  # advisory lock has no row result
        [],  # active reservations
    ]


class AuthoritativeRiskFactsProviderTests(unittest.TestCase):
    def _prepare(self, rows, action=None):
        return m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(
            _Connection(_Cursor(rows)), graph(action),
        )

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

    def test_postgres_style_uuid_checkpoint_identity_is_canonicalized_for_provenance(self):
        checkpoint_id = UUID("33333333-3333-3333-3333-333333333333")
        result = self._prepare(source_rows(reconciliation_identity=checkpoint_id))
        reconciliation = next(
            item for item in result.provenance
            if item.source_kind is m.authoritative_risk_facts.RiskFactSourceKind.RECONCILIATION
        )
        self.assertEqual(str(checkpoint_id), reconciliation.source_identity)

    def test_missing_policy_fails_closed_and_closes_cursor(self):
        cursor = _Cursor([[]])
        with self.assertRaises(m.authoritative_risk_facts.RiskFactsUnavailable):
            m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(_Connection(cursor), graph())
        self.assertTrue(cursor.closed)

    def test_each_required_persisted_source_fails_closed_when_absent(self):
        cases = {
            "account": source_rows()[:2] + [[]],
            "reconciliation": source_rows()[:3] + [[]],
            "strategy kill switch": source_rows()[:6] + [[]],
            "market": source_rows()[:7] + [[]],
        }
        for label, rows in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(m.authoritative_risk_facts.RiskFactsUnavailable):
                    self._prepare(rows)

    def test_tied_or_future_or_wrong_valuation_source_is_rejected(self):
        ambiguous = source_rows()
        ambiguous[0] = ambiguous[0] * 2
        with self.assertRaises(m.authoritative_risk_facts.RiskFactsAmbiguous):
            self._prepare(ambiguous)

        future = source_rows()
        policy = list(future[0][0])
        policy[3] = anchor + timedelta(seconds=1)
        future[0] = [tuple(policy)]
        with self.assertRaises(m.authoritative_risk_facts.RiskFactsVersionConflict):
            self._prepare(future)

        wrong_currency = source_rows()
        rule = list(wrong_currency[1][0])
        rule[5] = "USD"
        wrong_currency[1] = [tuple(rule)]
        with self.assertRaises(m.authoritative_risk_facts.RiskFactsScopeConflict):
            self._prepare(wrong_currency)

    def test_expired_persisted_source_fails_closed_without_a_market_fallback(self):
        rows = source_rows()
        policy = list(rows[0][0])
        policy[3] = anchor - timedelta(seconds=61)
        rows[0] = [tuple(policy)]
        cursor = _Cursor(rows)
        with self.assertRaises(m.authoritative_risk_facts.RiskFactsStale):
            m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(_Connection(cursor), graph())
        self.assertTrue(cursor.closed)

    def test_expired_market_observation_fails_closed_instead_of_using_an_order_price(self):
        rows = source_rows()
        market = list(rows[7][0])
        market[3] = anchor - timedelta(seconds=61)
        rows[7] = [tuple(market)]
        with self.assertRaises(m.authoritative_risk_facts.RiskFactsStale):
            self._prepare(rows)

    def test_every_reducing_action_needs_no_market_and_never_requests_capacity(self):
        for action in (
            m.order.OrderAction.REDUCE, m.order.OrderAction.CLOSE,
            m.order.OrderAction.EMERGENCY_CLOSE, m.order.OrderAction.PROTECTION,
        ):
            with self.subTest(action=action):
                cursor = _Cursor(source_rows()[:7] + [[]])
                result = m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(
                    _Connection(cursor), graph(action),
                )
                self.assertEqual(m.decimal.Decimal("0"), result.request.gross_notional)
                self.assertIsNone(result.reservation_demand)
                self.assertIsNone(result.expires_at)
                self.assertEqual(8, len(result.provenance))
                self.assertNotIn("pg_advisory_xact_lock", "\n".join(call[0] for call in cursor.calls))

    def test_cancel_is_rejected_before_opening_a_provider_cursor(self):
        class NeverConnection:
            def cursor(self):
                raise AssertionError("CANCEL must not enter the facts provider")

        with self.assertRaises(m.admission.EntryAdmissionError):
            m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(NeverConnection(), graph(m.order.OrderAction.CANCEL))

    def test_unclassified_driver_error_is_wrapped_and_cursor_is_closed(self):
        class BrokenCursor(_Cursor):
            def execute(self, query, params=()):
                raise RuntimeError("driver fault")

        cursor = BrokenCursor([])
        with self.assertRaises(m.authoritative_risk_facts.RiskFactsRepositoryError):
            m.authoritative_risk_provider.AuthoritativeRiskFactsProvider().prepare(_Connection(cursor), graph())
        self.assertTrue(cursor.closed)


if __name__ == "__main__":
    unittest.main()
