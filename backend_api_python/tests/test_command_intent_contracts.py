from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from tests.pr03_contract_loader import load_pr03_contracts


modules = load_pr03_contracts()
c = modules.contracts
d = modules.decimal_values
o = modules.order_contracts


def command(**changes):
    values = dict(
        command_id=uuid4(), tenant_id=10, user_id=10, credential_id=20,
        actor_type=o.Actor.STRATEGY, actor_id="strategy:10", source="strategy_v2",
        action=o.OrderAction.OPEN, account_scope="primary-account",
        request_payload={"instrument": "BTC-USDT", "mode": "paper"},
        idempotency_key="create-001", correlation_id="corr-001", strategy_id=30,
    )
    values.update(changes)
    return c.OrderCommand(**values)


def intent(command_value, **changes):
    values = dict(
        intent_id=uuid4(), economic_order_id=uuid4(), command_id=command_value.command_id,
        tenant_id=command_value.tenant_id, credential_id=command_value.credential_id,
        account_scope=command_value.account_scope, exchange_id="binance",
        instrument_id="btc-usdt", market_type="usdm", side="BUY",
        target_quantity=d.Quantity("1.25"), instrument_rule_snapshot_id=uuid4(),
        instrument_rule_version="rules-v1", order_type="LIMIT", execution_algo="DIRECT",
        rounding_mode="ROUND_DOWN", limit_price=d.Price("100"), quote_notional=d.QuoteAmount("125"),
    )
    values.update(changes)
    return c.OrderIntent(**values)


def reservation(command_value, intent_value, **changes):
    values = dict(
        reservation_id=uuid4(), command_id=command_value.command_id,
        economic_order_id=intent_value.economic_order_id, tenant_id=command_value.tenant_id,
        credential_id=command_value.credential_id, account_scope=command_value.account_scope,
        reservation_kind="initial_margin", currency="usdt", reserved_notional=d.QuoteAmount("125"),
        reserved_margin=d.QuoteAmount("25"), reserved_position_qty=d.Quantity("1.25"),
        limits_snapshot={"max_notional": "500"}, risk_input_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    values.update(changes)
    return c.RiskReservation(**values)


class CommandIntentContractTests(unittest.TestCase):
    def test_command_and_intent_are_deterministic_and_immutable(self):
        first = command()
        second = command(command_id=first.command_id)
        self.assertEqual(first.request_fingerprint, second.request_fingerprint)
        self.assertEqual(first.source, "strategy_v2")
        self.assertEqual(first.request_payload["instrument"], "BTC-USDT")
        with self.assertRaises(TypeError):
            first.request_payload["instrument"] = "ETH-USDT"
        order_intent = intent(first)
        self.assertEqual(len(order_intent.payload_hash), 64)
        self.assertEqual(order_intent.instrument_id, "BTC-USDT")
        self.assertEqual(order_intent.market_type, "usdm")

    def test_no_float_secret_or_noncanonical_identity_can_enter(self):
        with self.assertRaises(c.CommandIntentContractError):
            command(request_payload={"amount": 0.1})
        with self.assertRaises(c.CommandIntentContractError):
            command(request_payload={"api_key": "never-record-this"})
        with self.assertRaises(c.CommandIntentContractError):
            command(account_scope=" account")
        with self.assertRaises(c.CommandIntentContractError):
            command(idempotency_key="")
        with self.assertRaises(c.CommandIntentContractError):
            command(actor_type="HUMAN")

    def test_graph_enforces_command_intent_scope(self):
        value = command()
        valid = intent(value)
        graph = c.CommandGraph(value, valid)
        self.assertEqual(graph.intent.command_id, graph.command.command_id)
        with self.assertRaises(c.CommandIntentContractError):
            c.CommandGraph(value, intent(value, credential_id=999))

    def test_decimal_and_versioned_intent_requirements_fail_closed(self):
        value = command()
        with self.assertRaises(c.CommandIntentContractError):
            intent(value, target_quantity=Decimal("1"))
        with self.assertRaises(c.CommandIntentContractError):
            intent(value, intent_version=2)
        with self.assertRaises(c.CommandIntentContractError):
            intent(value, side="hold")
        with self.assertRaises(c.CommandIntentContractError):
            intent(value, limit_price=d.Quantity("1"))

    def test_reservation_facts_are_canonical_and_expiry_is_aware_utc(self):
        value = command()
        order_intent = intent(value)
        item = reservation(value, order_intent)
        self.assertEqual(item.currency, "USDT")
        self.assertEqual(item.reservation_kind, "INITIAL_MARGIN")
        self.assertEqual(item.expires_at.tzinfo, timezone.utc)
        self.assertEqual(len(item.immutable_fingerprint()), 64)
        with self.assertRaises(c.CommandIntentContractError):
            reservation(value, order_intent, expires_at=datetime.now())
        with self.assertRaises(c.CommandIntentContractError):
            reservation(value, order_intent, reserved_margin=d.FeeAmount("1"))
        with self.assertRaises(c.CommandIntentContractError):
            reservation(value, order_intent, limits_snapshot={"token": "secret"})

    def test_canonical_json_and_hash_do_not_depend_on_mapping_order(self):
        first = command(request_payload={"a": 1, "nested": {"z": "x", "b": True}})
        second = command(command_id=first.command_id, request_payload={"nested": {"b": True, "z": "x"}, "a": 1})
        self.assertEqual(first.canonical_request_json, second.canonical_request_json)
        self.assertEqual(first.request_fingerprint, second.request_fingerprint)


if __name__ == "__main__":
    unittest.main()
