from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _modules():
    names = (
        "app", "app.domain", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_vertical_read_contracts", "app.domain.gate_testnet_execution_contracts",
        "app.services", "app.services.gate_private_read_client", "app.services.gate_testnet_order_client",
        "app.services.gate_testnet_order_http_transport",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        multi = _load(names[2], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        vertical = _load(names[3], ROOT / "app" / "domain" / "gate_vertical_read_contracts.py")
        execution = _load(names[4], ROOT / "app" / "domain" / "gate_testnet_execution_contracts.py")
        private = _load(names[6], ROOT / "app" / "services" / "gate_private_read_client.py")
        client = _load(names[7], ROOT / "app" / "services" / "gate_testnet_order_client.py")
        http = _load(names[8], ROOT / "app" / "services" / "gate_testnet_order_http_transport.py")
        return multi, vertical, execution, private, client, http
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


MULTI, VERTICAL, EXECUTION, PRIVATE, CLIENT, HTTP = _modules()


class _Transport:
    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.calls = status, payload or {"id": "order-1", "text": "t-broker-v1"}, []

    def request(self, method, path, query, body, headers):
        self.calls.append((method, path, query, body, headers))
        return self.status, self.payload


class _SequenceTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, path, query, body, headers):
        self.calls.append((method, path, query, body, headers))
        return self.responses.pop(0)


class _Response:
    status = 200

    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, _limit):
        import json
        return json.dumps(self.payload).encode("utf-8")


class _Opener:
    def __init__(self): self.requests = []
    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return _Response({"id": "order-1", "text": "t-broker-v1", "status": "open"})


class GateTestnetOrderClientTests(unittest.TestCase):
    def _request(self, **changes):
        values = dict(
            instrument_id="BTC_USDT",
            market_type=MULTI.AssetMarketType.PERPETUAL,
            account_scope="testnet-account",
            side=VERTICAL.GateOrderSide.BUY,
            quantity=Decimal("1"),
            reference_price=Decimal("100"),
            execution_kind=EXECUTION.GateExecutionKind.MARKET,
            client_order_id="t-broker-v1",
            environment=MULTI.CapabilityEnvironment.TESTNET,
        )
        values.update(changes)
        return EXECUTION.GateTestnetExecutionRequest(**values)

    def _client(self, transport=None):
        credential = PRIVATE.GatePrivateCredential(
            "key-example", "secret-example", MULTI.CapabilityEnvironment.TESTNET,
        )
        return CLIENT.GateTestnetOrderClient(
            credential=credential,
            transport=transport or _Transport(),
            timestamp_provider=lambda: 1700000000,
            market_type=MULTI.AssetMarketType.PERPETUAL,
            account_scope="testnet-account",
            client_order_id_validator=lambda value: value,
        )

    def test_submit_is_signed_testnet_only_and_typed(self):
        transport = _Transport(payload={"id": "order-1", "text": "t-broker-v1", "status": "open"})
        receipt = self._client(transport).submit(self._request())
        self.assertEqual(receipt.exchange_order_id, "order-1")
        self.assertFalse(receipt.live_enabled)
        method, path, _query, body, headers = transport.calls[0]
        self.assertEqual((method, path), ("POST", "/api/v4/futures/usdt/orders"))
        self.assertIn('"size":"1"', body)
        self.assertEqual(headers["KEY"], "key-example")

    def test_submit_rejects_noncanonical_or_overlong_gate_client_id(self):
        for value in ("broker-v1", "t-" + "x" * 29):
            with self.subTest(value=value):
                with self.assertRaises(CLIENT.GateTestnetOrderCapabilityError):
                    self._client().submit(self._request(client_order_id=value))

    def test_spot_market_order_uses_ioc_time_in_force(self):
        credential = PRIVATE.GatePrivateCredential(
            "key-example", "secret-example", MULTI.CapabilityEnvironment.TESTNET,
        )
        transport = _Transport(payload={"id": "order-1", "text": "t-broker-v1", "status": "open"})
        client = CLIENT.GateTestnetOrderClient(
            credential=credential,
            transport=transport,
            timestamp_provider=lambda: 1700000000,
            market_type=MULTI.AssetMarketType.SPOT,
            account_scope="testnet-account",
            client_order_id_validator=lambda value: value,
        )
        client.submit(self._request(market_type=MULTI.AssetMarketType.SPOT))
        self.assertIn('"time_in_force":"ioc"', transport.calls[0][3])

    def test_spot_market_buy_uses_reference_quote_notional(self):
        credential = PRIVATE.GatePrivateCredential(
            "key-example", "secret-example", MULTI.CapabilityEnvironment.TESTNET,
        )
        transport = _Transport(payload={"id": "order-1", "text": "t-broker-v1", "status": "open"})
        client = CLIENT.GateTestnetOrderClient(
            credential=credential,
            transport=transport,
            timestamp_provider=lambda: 1700000000,
            market_type=MULTI.AssetMarketType.SPOT,
            account_scope="testnet-account",
            client_order_id_validator=lambda value: value,
        )
        client.submit(self._request(
            market_type=MULTI.AssetMarketType.SPOT,
            quantity=Decimal("0.00011"),
            reference_price=Decimal("63000"),
        ))
        self.assertIn('"amount":"6.93"', transport.calls[0][3])

    def test_spot_limit_order_preserves_base_quantity_and_price(self):
        credential = PRIVATE.GatePrivateCredential(
            "key-example", "secret-example", MULTI.CapabilityEnvironment.TESTNET,
        )
        transport = _Transport(payload={"id": "order-1", "text": "t-broker-v1", "status": "open"})
        client = CLIENT.GateTestnetOrderClient(
            credential=credential,
            transport=transport,
            timestamp_provider=lambda: 1700000000,
            market_type=MULTI.AssetMarketType.SPOT,
            account_scope="testnet-account",
            client_order_id_validator=lambda value: value,
        )
        client.submit(self._request(
            market_type=MULTI.AssetMarketType.SPOT,
            quantity=Decimal("0.01"),
            execution_kind=EXECUTION.GateExecutionKind.LIMIT,
            limit_price=Decimal("60000"),
        ))
        body = transport.calls[0][3]
        self.assertIn('"amount":"0.01"', body)
        self.assertIn('"price":"60000"', body)
        self.assertIn('"time_in_force":"gtc"', body)

    def test_perpetual_limit_order_preserves_signed_size_and_price(self):
        transport = _Transport(payload={"id": "order-1", "text": "t-broker-v1", "status": "open"})
        self._client(transport).submit(self._request(
            quantity=Decimal("2"),
            side=VERTICAL.GateOrderSide.SELL,
            execution_kind=EXECUTION.GateExecutionKind.LIMIT,
            limit_price=Decimal("60000"),
        ))
        body = transport.calls[0][3]
        self.assertIn('"size":"-2"', body)
        self.assertIn('"price":"60000"', body)
        self.assertIn('"tif":"gtc"', body)

    def test_price_triggered_submission_does_not_fall_back_to_limit_payload(self):
        request = self._request(
            execution_kind=EXECUTION.GateExecutionKind.STOP_LIMIT,
            limit_price=Decimal("60000"),
            trigger_price=Decimal("59900"),
            trigger_direction=EXECUTION.GateTriggerDirection.AT_OR_BELOW,
            trigger_price_type=EXECUTION.GateTriggerPriceType.MARK,
        )
        transport = _Transport()
        with self.assertRaises(CLIENT.GateTestnetOrderCapabilityError):
            self._client(transport).submit(request)
        self.assertEqual(transport.calls, [])

    def test_terminal_state_uses_finish_reason_to_distinguish_fill(self):
        filled = self._client(_Transport(payload={"id": "order-1", "text": "t-broker-v1", "status": "closed", "finish_as": "filled"})).query(
            instrument_id="BTC_USDT", exchange_order_id="order-1"
        )
        self.assertEqual(filled.raw_state, "filled")
        cancelled = self._client(_Transport(payload={"id": "order-1", "text": "t-broker-v1", "status": "finished", "finish_as": "cancelled"})).query(
            instrument_id="BTC_USDT", exchange_order_id="order-1"
        )
        self.assertEqual(cancelled.raw_state, "cancelled")

    def test_get_only_transport_marks_order_receipt_write_disabled(self):
        credential = PRIVATE.GatePrivateCredential(
            "key-example", "secret-example", MULTI.CapabilityEnvironment.TESTNET,
        )
        transport = HTTP.GateTestnetOrderHttpTransport(opener=_Opener(), allow_testnet_writes=False)
        client = CLIENT.GateTestnetOrderClient(
            credential=credential,
            transport=transport,
            timestamp_provider=lambda: 1700000000,
            market_type=MULTI.AssetMarketType.PERPETUAL,
            account_scope="testnet-account",
            client_order_id_validator=lambda value: value,
        )
        receipt = client.query(instrument_id="BTC_USDT", exchange_order_id="order-1")
        self.assertTrue(receipt.network_access)
        self.assertFalse(receipt.writes_enabled)
        self.assertFalse(receipt.live_enabled)

    def test_auth_and_temporary_failures_are_typed(self):
        with self.assertRaises(CLIENT.GateTestnetOrderAuthError):
            self._client(_Transport(status=403)).submit(self._request())
        with self.assertRaises(CLIENT.GateTestnetOrderTemporaryError):
            self._client(_Transport(status=503)).submit(self._request())

    def test_reduce_only_is_an_explicit_futures_request_fact(self):
        transport = _Transport(payload={"id": "order-1", "text": "t-broker-v1", "status": "open"})
        self._client(transport).submit(self._request(reduce_only=True))
        self.assertIn('"reduce_only":true', transport.calls[0][3])

    def test_spot_query_and_cancel_include_currency_pair_scope(self):
        credential = PRIVATE.GatePrivateCredential(
            "key-example", "secret-example", MULTI.CapabilityEnvironment.TESTNET,
        )
        transport = _Transport(payload={"id": "order-1", "text": "query", "status": "open"})
        client = CLIENT.GateTestnetOrderClient(
            credential=credential,
            transport=transport,
            timestamp_provider=lambda: 1700000000,
            market_type=MULTI.AssetMarketType.SPOT,
            account_scope="testnet-account",
            client_order_id_validator=lambda value: value,
        )
        client.query(instrument_id="BTC_USDT", exchange_order_id="order-1")
        self.assertEqual(transport.calls[-1][2], "currency_pair=BTC_USDT")
        client.cancel(instrument_id="BTC_USDT", exchange_order_id="order-1")
        self.assertEqual(transport.calls[-1][2], "currency_pair=BTC_USDT")

    def test_cancel_and_confirm_queries_after_delete(self):
        transport = _SequenceTransport([
            (200, {"id": "order-1", "text": "cancel", "status": "cancelled"}),
            (200, {"id": "order-1", "text": "t-broker-v1", "status": "open"}),
            (200, {"id": "order-1", "text": "t-broker-v1", "status": "finished"}),
        ])
        client = self._client(transport)
        receipt = client.cancel_and_confirm(instrument_id="BTC_USDT", exchange_order_id="order-1")
        self.assertEqual(receipt.raw_state, "finished")
        self.assertEqual([item[0] for item in transport.calls], ["DELETE", "GET", "GET"])

    def test_cancel_and_confirm_fails_closed_when_query_remains_open(self):
        transport = _SequenceTransport([
            (200, {"id": "order-1", "text": "cancel", "status": "cancelled"}),
            (200, {"id": "order-1", "text": "t-broker-v1", "status": "open"}),
            (200, {"id": "order-1", "text": "t-broker-v1", "status": "open"}),
        ])
        client = self._client(transport)
        with self.assertRaises(CLIENT.GateTestnetOrderTemporaryError):
            client.cancel_and_confirm(instrument_id="BTC_USDT", exchange_order_id="order-1", max_attempts=2)
        self.assertEqual([item[0] for item in transport.calls], ["DELETE", "GET", "GET"])

    def test_disabled_transport_and_live_credential_fail_closed(self):
        credential = PRIVATE.GatePrivateCredential(
            "key-example", "secret-example", MULTI.CapabilityEnvironment.TESTNET,
        )
        with self.assertRaises(CLIENT.GateTestnetOrderCapabilityError):
            CLIENT.GateTestnetOrderClient.disabled(
                credential, market_type=MULTI.AssetMarketType.PERPETUAL,
                account_scope="testnet-account",
                client_order_id_validator=lambda value: value,
            ).submit(self._request())

    def test_http_transport_requires_explicit_testnet_write_opt_in(self):
        opener = _Opener()
        disabled = HTTP.GateTestnetOrderHttpTransport(opener=opener)
        with self.assertRaises(CLIENT.GateTestnetOrderCapabilityError):
            disabled.request("POST", "/api/v4/futures/usdt/orders", "", "{}", {})
        enabled = HTTP.GateTestnetOrderHttpTransport(opener=opener, allow_testnet_writes=True)
        status, payload = enabled.request("POST", "/api/v4/futures/usdt/orders", "", "{}", {})
        self.assertEqual((status, payload["id"]), (200, "order-1"))
        self.assertEqual(opener.requests[-1][0].get_method(), "POST")


if __name__ == "__main__":
    unittest.main()
