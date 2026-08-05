from datetime import datetime, timezone
from decimal import Decimal
import unittest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.domain.canonical_entry_contracts import EntryActorContext, EntryMode, EntrySource, ExecutionKind, OrderSide
from app.domain.canonical_entry_v2_contracts import CanonicalEconomicIntentV2, CanonicalEntryRequestV2, DurableEntryGraphV2, EconomicOrderSubject, TriggerDirection, TriggerPriceType, QuantitySemantics
from app.domain.decimal_values import Price, Quantity
from app.domain.gate_testnet_execution_contracts import GateExecutionKind
from app.domain.multi_asset_capability_contracts import AssetMarketType
from app.domain.order_contracts import Actor, OrderAction, RiskEffect
from app.services.gate_testnet_execution_service import (
    GateTestnetCancelRequest,
    GateTestnetExecutionServiceError,
    build_gate_testnet_execution_request,
    build_gate_testnet_execution_ledger_scopes,
    execute_gate_testnet_payload_caller_owned,
)
from app.domain.entry_admission_v2_contracts import EntryAdmissionDisposition


def _graph(kind=ExecutionKind.MARKET, *, limit=False):
    intent = CanonicalEconomicIntentV2(
        side=OrderSide.BUY,
        quantity=Quantity("0.001"),
        quantity_semantics=QuantitySemantics.ABSOLUTE,
        execution_kind=kind,
        limit_price=Price("60000") if limit else None,
        trigger_price=Price("59900") if kind in (ExecutionKind.STOP_MARKET, ExecutionKind.STOP_LIMIT) else None,
        trigger_direction=TriggerDirection.AT_OR_BELOW if kind in (ExecutionKind.STOP_MARKET, ExecutionKind.STOP_LIMIT) else None,
        trigger_price_type=TriggerPriceType.MARK if kind in (ExecutionKind.STOP_MARKET, ExecutionKind.STOP_LIMIT) else None,
    )
    request = CanonicalEntryRequestV2(
        1,
        2,
        "account",
        "BTC_USDT",
        "perpetual",
        OrderAction.OPEN,
        intent,
        EntryActorContext(Actor.HUMAN, "human", EntrySource.REST),
        RiskEffect.INCREASE_RISK,
        "case-1",
        "corr-1",
        datetime(2026, 8, 2, tzinfo=timezone.utc),
        EntryMode.PAPER,
    )
    return DurableEntryGraphV2(uuid4(), request, EconomicOrderSubject(uuid4()))


class GateTestnetExecutionServiceTests(unittest.TestCase):
    def test_builds_market_request_from_canonical_graph(self):
        request = build_gate_testnet_execution_request({"reference_price": "60000", "observed_at": "2026-08-02T00:00:00Z"}, _graph())
        self.assertEqual(request.execution_kind, GateExecutionKind.MARKET)
        self.assertEqual(str(request.quantity), "0.001")
        self.assertEqual(request.market_type.value, "perpetual")
        self.assertTrue(request.client_order_id.startswith("t-gate-v1-"))

    def test_builds_limit_request_and_preserves_explicit_client_id(self):
        request = build_gate_testnet_execution_request(
            {"reference_price": "60000", "observed_at": "2026-08-02T00:00:00Z", "client_order_id": "gate-case-1"},
            _graph(ExecutionKind.LIMIT, limit=True),
        )
        self.assertEqual(request.execution_kind, GateExecutionKind.LIMIT)
        self.assertEqual(str(request.limit_price), "60000")
        self.assertEqual(request.client_order_id, "t-gate-case-1")

    def test_stop_request_preserves_explicit_trigger_facts_and_invalid_client_id_fails_closed(self):
        request = build_gate_testnet_execution_request({"reference_price": "60000"}, _graph(ExecutionKind.STOP_MARKET))
        self.assertEqual(request.execution_kind, GateExecutionKind.STOP_MARKET)
        self.assertEqual(request.trigger_price, Decimal("59900"))
        self.assertEqual(request.trigger_direction.value, "AT_OR_BELOW")
        self.assertEqual(request.trigger_price_type.value, "MARK")
        with self.assertRaises(GateTestnetExecutionServiceError):
            build_gate_testnet_execution_request({"reference_price": "60000", "client_order_id": "bad id"}, _graph())
        with self.assertRaises(GateTestnetExecutionServiceError):
            build_gate_testnet_execution_request({"reference_price": "60000", "client_order_id": "x" * 29}, _graph())
        with self.assertRaises(GateTestnetExecutionServiceError):
            build_gate_testnet_execution_request({"reference_price": "60000", "client_order_id": "bad:id"}, _graph())

    def test_cancel_request_requires_typed_market_and_ascii_venue_order_id(self):
        request = GateTestnetCancelRequest("BTC_USDT", AssetMarketType.PERPETUAL, "account", "12345")
        self.assertEqual(request.exchange_order_id, "12345")
        with self.assertRaises(GateTestnetExecutionServiceError):
            GateTestnetCancelRequest("BTC_USDT", request.market_type, "account", "bad id")

    def test_testnet_submission_requires_ledger_scopes_before_client_creation(self):
        graph = _graph()
        admission = SimpleNamespace(
            disposition=EntryAdmissionDisposition.CREATED,
        )
        runtime_result = SimpleNamespace(admission=admission)
        client_factory = Mock()
        with patch(
            "app.services.gate_testnet_execution_service.admit_runtime_entry_payload_caller_owned",
            return_value=(runtime_result, graph),
        ):
            with self.assertRaisesRegex(GateTestnetExecutionServiceError, "ledger scopes"):
                execute_gate_testnet_payload_caller_owned(
                    object(),
                    {"reference_price": "60000", "observed_at": "2026-08-02T00:00:00Z"},
                    tenant_id=1,
                    actor_id="1",
                    client_factory=client_factory,
                )
        client_factory.assert_not_called()

    def test_builds_durable_entry_ledger_scopes_from_explicit_facts(self):
        graph = _graph()
        payload = {
            "credential_id": 2,
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "valuation_ccy": "USDT",
            "observed_at": "2026-08-02T00:00:00Z",
            "exchange_event_at": "2026-08-02T00:00:01Z",
            "received_at": "2026-08-02T00:00:02Z",
            "source": "REST",
            "normalizer_version": "gate-read-v1",
            "instrument_rule_version": "gate-rules-v1",
        }
        ledger_scope, persistence_scope = build_gate_testnet_execution_ledger_scopes(
            payload, graph, tenant_id=1, credential_id=2
        )
        self.assertEqual(ledger_scope.economic_order_id, str(graph.subject.economic_order_id))
        self.assertEqual(persistence_scope.durable_entry_command_id, str(graph.command_id))
        self.assertIsNone(persistence_scope.intent_id)

    def test_durable_entry_scope_rejects_missing_valuation_and_event_facts(self):
        graph = _graph()
        with self.assertRaises(GateTestnetExecutionServiceError):
            build_gate_testnet_execution_ledger_scopes(
                {"credential_id": 2, "base_asset": "BTC", "quote_asset": "USDT", "valuation_ccy": "USDT"},
                graph,
                tenant_id=1,
                credential_id=2,
            )


if __name__ == "__main__":
    unittest.main()
