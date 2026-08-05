"""Pure coverage for the PR-13 source-to-Durable-Entry binding contract."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
from uuid import UUID

from tests.pr12c_admission_loader import load_pr12c_admission


m = load_pr12c_admission()
entry = m.entry
entry_v2 = m.entry_v2
order = m.order
decimals = m.decimal


b = m.entrypoint_bindings

COMMAND_ID = "11111111-1111-1111-1111-111111111111"
ECONOMIC_ORDER_ID = "22222222-2222-2222-2222-222222222222"


def _intent(*, cancel: bool = False, protection: bool = False):
    if cancel:
        return entry_v2.CanonicalEconomicIntentV2(
            cancel_target_kind=entry_v2.CancelTargetKind.CLIENT_ORDER_ID,
            cancel_target_id="client-order-1",
        )
    if protection:
        return entry_v2.CanonicalEconomicIntentV2(
            side=entry.OrderSide.SELL,
            execution_kind=entry.ExecutionKind.STOP_MARKET,
            trigger_price=decimals.Price("99"),
            trigger_direction=entry_v2.TriggerDirection.AT_OR_BELOW,
            trigger_price_type=entry_v2.TriggerPriceType.MARK,
            reduce_only=True,
            target_position_id="position-1",
            close_all=True,
        )
    return entry_v2.CanonicalEconomicIntentV2(
        side=entry.OrderSide.BUY,
        quantity=decimals.Quantity("1"),
        quantity_semantics=entry_v2.QuantitySemantics.ABSOLUTE,
        execution_kind=entry.ExecutionKind.LIMIT,
        limit_price=decimals.Price("100"),
    )


def _request(source=entry.EntrySource.REST, actor=order.Actor.HUMAN, *, action=order.OrderAction.OPEN):
    cancel = action is order.OrderAction.CANCEL
    protection = action is order.OrderAction.PROTECTION
    effect = order.RiskEffect.NEUTRAL if cancel else (
        order.RiskEffect.INCREASE_RISK if action in (order.OrderAction.OPEN, order.OrderAction.INCREASE)
        else order.RiskEffect.REDUCE_RISK
    )
    if protection:
        source, actor = entry.EntrySource.PROTECTION, order.Actor.PROTECTION
    return entry_v2.CanonicalEntryRequestV2(
        tenant_id=1,
        credential_id=2,
        account_scope="account-1",
        instrument_id="BTCUSDT",
        market_type="swap",
        action=action,
        economic_intent=_intent(cancel=cancel, protection=protection),
        actor=entry.EntryActorContext(actor, f"{source.value.lower()}-1", source),
        risk_effect=effect,
        idempotency_key="case-1",
        correlation_id="corr-1",
        occurred_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )


class EntryPointV2BindingContractTests(unittest.TestCase):
    def test_each_entry_source_has_a_lossless_typed_binding(self):
        bindings = {
            entry.EntrySource.REST: b.bind_rest_v2,
            entry.EntrySource.MANUAL: b.bind_manual_v2,
            entry.EntrySource.STRATEGY: b.bind_strategy_v2,
            entry.EntrySource.PROTECTION: b.bind_protection_v2,
            entry.EntrySource.AGENT: b.bind_agent_v2,
            entry.EntrySource.MCP: b.bind_mcp_v2,
            entry.EntrySource.GRID: b.bind_grid_v2,
        }
        actors = {
            entry.EntrySource.REST: order.Actor.HUMAN,
            entry.EntrySource.MANUAL: order.Actor.HUMAN,
            entry.EntrySource.STRATEGY: order.Actor.STRATEGY,
            entry.EntrySource.AGENT: order.Actor.AGENT,
            entry.EntrySource.MCP: order.Actor.MCP,
            entry.EntrySource.GRID: order.Actor.GRID,
        }
        identity = b.DurableEntryIdentityV2(COMMAND_ID, ECONOMIC_ORDER_ID)
        for source, bind in bindings.items():
            request = _request(source, actors.get(source, order.Actor.PROTECTION), action=(
                order.OrderAction.PROTECTION if source is entry.EntrySource.PROTECTION else order.OrderAction.OPEN
            ))
            graph = bind(request, identity)
            self.assertEqual(graph.specification, request)
            self.assertEqual(graph.specification.economic_fingerprint, request.economic_fingerprint)
            self.assertEqual(graph.command_id, COMMAND_ID)
            self.assertEqual(graph.subject.economic_order_id, ECONOMIC_ORDER_ID)
            if source in {entry.EntrySource.AGENT, entry.EntrySource.MCP, entry.EntrySource.GRID}:
                self.assertEqual(request.mode, entry.EntryMode.DISABLED)

    def test_cancel_uses_only_the_typed_cancel_subject(self):
        request = _request(action=order.OrderAction.CANCEL)
        graph = b.bind_rest_v2(request, b.DurableEntryIdentityV2(COMMAND_ID))
        self.assertIsInstance(graph.subject, entry_v2.CancelTargetSubject)
        self.assertEqual(graph.subject.cancel_target_id, "client-order-1")
        with self.assertRaises(b.EntryPointBindingError):
            b.bind_rest_v2(request, b.DurableEntryIdentityV2(COMMAND_ID, ECONOMIC_ORDER_ID))

    def test_non_cancel_requires_explicit_immutable_economic_order_identity(self):
        request = _request()
        with self.assertRaises(b.EntryPointBindingError):
            b.bind_rest_v2(request, b.DurableEntryIdentityV2(COMMAND_ID))
        graph = b.bind_rest_v2(request, b.DurableEntryIdentityV2(UUID(COMMAND_ID), UUID(ECONOMIC_ORDER_ID)))
        self.assertEqual(graph.command_id, COMMAND_ID)
        self.assertEqual(graph.subject.economic_order_id, ECONOMIC_ORDER_ID)

    def test_source_mismatch_and_untyped_inputs_fail_closed(self):
        request = _request()
        with self.assertRaises(b.EntryPointBindingError):
            b.bind_strategy_v2(request, b.DurableEntryIdentityV2(COMMAND_ID, ECONOMIC_ORDER_ID))
        with self.assertRaises(b.EntryPointBindingError):
            b.bind_entrypoint_v2("REST", request, b.DurableEntryIdentityV2(COMMAND_ID, ECONOMIC_ORDER_ID))
        with self.assertRaises(b.EntryPointBindingError):
            b.bind_rest_v2(object(), b.DurableEntryIdentityV2(COMMAND_ID, ECONOMIC_ORDER_ID))
        with self.assertRaises(b.EntryPointBindingError):
            b.bind_rest_v2(request, object())

    def test_binding_module_has_no_runtime_or_persistence_dependency(self):
        from pathlib import Path

        import ast

        source = (Path(__file__).resolve().parents[1] / "app" / "domain" / "entrypoint_v2_binding_contracts.py").read_text(encoding="utf-8")
        imports = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        haystack = " ".join(imports).lower()
        for forbidden in ("service", "repository", "route", "worker", "executor", "exchange"):
            self.assertNotIn(forbidden, haystack)
        self.assertNotIn("commit(", source)
        self.assertNotIn("rollback(", source)
        self.assertNotIn("LIVE", source)


if __name__ == "__main__":
    unittest.main()
