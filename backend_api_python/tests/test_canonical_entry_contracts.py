from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tests.pr11_contract_loader import load_pr11_contracts


modules = load_pr11_contracts()
o = modules.order
e = modules.entry
a = modules.adapters
d = modules.decimals


def market_buy(quantity="1"):
    return e.CanonicalEconomicIntent(
        side=e.OrderSide.BUY,
        quantity=d.Quantity(quantity),
        execution_kind=e.ExecutionKind.MARKET,
    )


def reducing_close(quantity="1"):
    return e.CanonicalEconomicIntent(
        side=e.OrderSide.SELL,
        quantity=d.Quantity(quantity),
        execution_kind=e.ExecutionKind.MARKET,
        reduce_only=True,
        target_position_id="position-1",
        close_quantity=d.Quantity(quantity),
    )


def entry_request(**changes):
    values = {
        "tenant_id": 1,
        "credential_id": 2,
        "account_scope": "primary",
        "instrument_id": "BTCUSDT",
        "market_type": "swap",
        "action": o.OrderAction.OPEN,
        "economic_intent": market_buy(),
        "actor": e.EntryActorContext(o.Actor.HUMAN, "human-42", e.EntrySource.REST),
        "idempotency_key": "case-1",
        "correlation_id": "corr-1",
        "occurred_at": datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
    }
    values.update(changes)
    return e.CanonicalEntryRequest(**values)


class CanonicalEntryContractTests(unittest.TestCase):
    def test_normalization_is_deterministic_and_preserves_economic_identity(self):
        first = entry_request()
        second = entry_request()
        strategy = entry_request(
            actor=e.EntryActorContext(o.Actor.STRATEGY, "strategy-42", e.EntrySource.STRATEGY),
            idempotency_key="case-2",
            correlation_id="corr-2",
        )

        self.assertEqual(first.request_fingerprint, second.request_fingerprint)
        self.assertEqual(first.economic_fingerprint, strategy.economic_fingerprint)
        self.assertNotEqual(first.request_fingerprint, strategy.request_fingerprint)
        self.assertEqual(
            e.normalize_entry(
                tenant_id=1,
                credential_id=2,
                account_scope="primary",
                instrument_id="BTCUSDT",
                market_type="swap",
                action=o.OrderAction.OPEN,
                economic_intent=market_buy(),
                actor=e.EntryActorContext(o.Actor.HUMAN, "human-42", e.EntrySource.REST),
                idempotency_key="case-1",
                correlation_id="corr-1",
                occurred_at=datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
            ).request,
            first,
        )

    def test_required_facts_and_utc_are_fail_closed(self):
        with self.assertRaises(e.EntryContractError):
            entry_request(correlation_id="")
        with self.assertRaises(e.EntryContractError):
            entry_request(occurred_at=datetime(2026, 7, 26, 9, 0))
        with self.assertRaises(e.EntryContractError):
            entry_request(occurred_at=datetime(2026, 7, 26, 17, 0, tzinfo=timezone(timedelta(hours=8))))
        stable = entry_request(occurred_at=datetime(2026, 7, 26, 9, 0, tzinfo=timezone(timedelta(0))))
        self.assertEqual(stable.occurred_at.tzinfo, timezone.utc)

    def test_restricted_sources_default_disabled_and_never_accept_live(self):
        for source, actor in ((e.EntrySource.AGENT, o.Actor.AGENT), (e.EntrySource.MCP, o.Actor.MCP), (e.EntrySource.GRID, o.Actor.GRID)):
            request = entry_request(actor=e.EntryActorContext(actor, f"{source.value.lower()}-1", source))
            self.assertEqual(request.mode, e.EntryMode.DISABLED)
            self.assertEqual(entry_request(actor=request.actor, mode=e.EntryMode.PAPER).mode, e.EntryMode.PAPER)
            self.assertEqual(entry_request(actor=request.actor, mode=e.EntryMode.SHADOW).mode, e.EntryMode.SHADOW)
        self.assertNotIn("LIVE", e.EntryMode.__members__)

    def test_source_actor_and_protection_semantics_are_fail_closed(self):
        with self.assertRaises(e.EntryContractError):
            e.EntryActorContext(o.Actor.AGENT, "agent-1", e.EntrySource.REST)
        protection = e.EntryActorContext(o.Actor.PROTECTION, "protect-1", e.EntrySource.PROTECTION)
        with self.assertRaises(e.EntryContractError):
            entry_request(actor=protection, action=o.OrderAction.OPEN, risk_effect=o.RiskEffect.INCREASE_RISK)
        with self.assertRaises(e.EntryContractError):
            entry_request(actor=protection, action=o.OrderAction.PROTECTION, risk_effect=o.RiskEffect.INCREASE_RISK)
        reduced = entry_request(
            actor=protection,
            action=o.OrderAction.EMERGENCY_CLOSE,
            economic_intent=reducing_close(),
            risk_effect=o.RiskEffect.REDUCE_RISK,
        )
        self.assertEqual(reduced.risk_effect, o.RiskEffect.REDUCE_RISK)

    def test_command_draft_has_typed_accept_and_reject_contracts(self):
        request = entry_request()
        accepted = e.CanonicalCommandDraft(request)
        self.assertEqual(accepted.disposition, e.EntryDisposition.ACCEPTED)
        rejected = e.CanonicalCommandDraft(
            request,
            disposition=e.EntryDisposition.REJECTED,
            rejection=e.EntryRejection.UNSAFE_MODE,
        )
        self.assertEqual(rejected.rejection, e.EntryRejection.UNSAFE_MODE)
        with self.assertRaises(e.EntryContractError):
            e.CanonicalCommandDraft(request, rejection=e.EntryRejection.UNSAFE_MODE)

    def test_source_adapters_own_actor_source_and_mode_boundary(self):
        facts = {
            "tenant_id": 1,
            "credential_id": 2,
            "account_scope": "primary",
            "instrument_id": "BTCUSDT",
            "market_type": "swap",
            "action": o.OrderAction.OPEN,
            "economic_intent": market_buy(),
            "idempotency_key": "case-1",
            "correlation_id": "corr-1",
            "occurred_at": datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
        }
        rest = a.adapt_rest("human-42", **facts)
        agent = a.adapt_agent("agent-42", **facts)
        paper_agent = a.adapt_agent("agent-42", mode=e.EntryMode.PAPER, **facts)
        self.assertEqual(rest.request.actor.entry_source, e.EntrySource.REST)
        self.assertEqual(agent.request.mode, e.EntryMode.DISABLED)
        self.assertEqual(paper_agent.request.mode, e.EntryMode.PAPER)
        with self.assertRaises(a.EntryAdapterError):
            a.adapt_rest("human-42", actor=rest.request.actor, **facts)

    def test_protection_adapter_preserves_risk_reducing_boundary(self):
        facts = {
            "tenant_id": 1,
            "credential_id": 2,
            "account_scope": "primary",
            "instrument_id": "BTCUSDT",
            "market_type": "swap",
            "economic_intent": reducing_close(),
            "idempotency_key": "case-1",
            "correlation_id": "corr-1",
            "occurred_at": datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
        }
        draft = a.adapt_protection(
            "protect-1",
            action=o.OrderAction.EMERGENCY_CLOSE,
            risk_effect=o.RiskEffect.REDUCE_RISK,
            **facts,
        )
        self.assertEqual(draft.request.actor.entry_source, e.EntrySource.PROTECTION)
        with self.assertRaises(e.EntryContractError):
            a.adapt_protection(
                "protect-1",
                action=o.OrderAction.OPEN,
                risk_effect=o.RiskEffect.INCREASE_RISK,
                **facts,
            )

    def test_economic_fingerprint_contains_all_typed_economic_facts(self):
        base = entry_request()
        self.assertNotEqual(base.economic_fingerprint, entry_request(economic_intent=market_buy("0.1")).economic_fingerprint)
        self.assertNotEqual(
            base.economic_fingerprint,
            entry_request(economic_intent=e.CanonicalEconomicIntent(
                side=e.OrderSide.SELL, quantity=d.Quantity("1"), execution_kind=e.ExecutionKind.MARKET,
            )).economic_fingerprint,
        )
        self.assertNotEqual(
            base.economic_fingerprint,
            entry_request(economic_intent=e.CanonicalEconomicIntent(
                side=e.OrderSide.BUY, quantity=d.Quantity("1"), execution_kind=e.ExecutionKind.LIMIT,
                limit_price=d.Price("100"),
            )).economic_fingerprint,
        )
        limit_101 = entry_request(economic_intent=e.CanonicalEconomicIntent(
            side=e.OrderSide.BUY, quantity=d.Quantity("1"), execution_kind=e.ExecutionKind.LIMIT,
            limit_price=d.Price("101"),
        ))
        stop_100 = entry_request(economic_intent=e.CanonicalEconomicIntent(
            side=e.OrderSide.BUY, quantity=d.Quantity("1"), execution_kind=e.ExecutionKind.STOP_MARKET,
            trigger_price=d.Price("100"),
        ))
        self.assertNotEqual(limit_101.economic_fingerprint, stop_100.economic_fingerprint)

    def test_cancel_and_closing_facts_are_typed_and_fail_closed(self):
        first = entry_request(
            action=o.OrderAction.CANCEL,
            economic_intent=e.CanonicalEconomicIntent(cancel_target_id="order-1"),
        )
        second = entry_request(
            action=o.OrderAction.CANCEL,
            economic_intent=e.CanonicalEconomicIntent(cancel_target_id="order-2"),
        )
        self.assertNotEqual(first.economic_fingerprint, second.economic_fingerprint)
        with self.assertRaises(e.EntryContractError):
            entry_request(action=o.OrderAction.CANCEL, economic_intent=e.CanonicalEconomicIntent())
        with self.assertRaises(e.EntryContractError):
            entry_request(action=o.OrderAction.CLOSE, economic_intent=market_buy())
        close_all = e.CanonicalEconomicIntent(
            side=e.OrderSide.SELL,
            quantity=d.Quantity("1"),
            execution_kind=e.ExecutionKind.MARKET,
            reduce_only=True,
            target_position_id="position-1",
            close_all=True,
        )
        self.assertEqual(entry_request(action=o.OrderAction.CLOSE, economic_intent=close_all).economic_intent.close_all, True)

    def test_equivalent_cross_entry_intents_share_economic_but_not_request_identity(self):
        facts = {
            "tenant_id": 1, "credential_id": 2, "account_scope": "primary",
            "instrument_id": "BTCUSDT", "market_type": "swap", "action": o.OrderAction.OPEN,
            "economic_intent": market_buy(), "idempotency_key": "case-1", "correlation_id": "corr-1",
            "occurred_at": datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
        }
        rest = a.adapt_rest("human-42", **facts)
        strategy = a.adapt_strategy("strategy-42", **facts)
        self.assertEqual(rest.request.economic_fingerprint, strategy.request.economic_fingerprint)
        self.assertNotEqual(rest.request.request_fingerprint, strategy.request.request_fingerprint)


if __name__ == "__main__":
    unittest.main()
