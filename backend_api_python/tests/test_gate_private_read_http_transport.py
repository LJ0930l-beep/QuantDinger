from __future__ import annotations

import importlib.util
import os
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
        "app.domain.gate_readonly_contracts", "app.services", "app.services.gate_private_read_client",
        "app.services.gate_private_read_http_transport", "app.domain.gate_rate_limit_contracts",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        multi = _load(names[2], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        readonly = _load(names[3], ROOT / "app" / "domain" / "gate_readonly_contracts.py")
        rate_limit = _load(names[7], ROOT / "app" / "domain" / "gate_rate_limit_contracts.py")
        client = _load(names[5], ROOT / "app" / "services" / "gate_private_read_client.py")
        transport = _load(names[6], ROOT / "app" / "services" / "gate_private_read_http_transport.py")
        return multi, readonly, client, transport, rate_limit
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


MULTI, READONLY, CLIENT, TRANSPORT, RATE_LIMIT = _modules()


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        import json
        return json.dumps(self.payload).encode("utf-8")


class _Opener:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return _Response(self.payload)


class _SequenceOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        status, payload = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        response = _Response(payload)
        response.status = status
        return response


class _TimeoutOpener:
    def __init__(self):
        self.calls = 0

    def __call__(self, _request, timeout=None):
        del timeout
        self.calls += 1
        raise TimeoutError("fixture timeout")


class GatePrivateReadHttpTransportTests(unittest.TestCase):
    def _profile(self):
        return READONLY.GateReadCapabilityProfile(
            environment=READONLY.GateEnvironment.TESTNET,
            market_type=READONLY.GateMarketType.PERPETUAL,
            base_url=READONLY.GATE_TESTNET_FUTURES_REST_BASE_URL,
            credential_ref="credential-ref-1",
            supports_account_reads=True,
            supports_order_reads=True,
            supports_fill_reads=True,
        )

    def test_transport_is_get_only_and_uses_testnet_https(self):
        opener = _Opener({"status": "ok", "accounts": []})
        transport = TRANSPORT.GatePrivateReadHttpTransport(self._profile(), opener=opener)
        status, payload = transport.request("GET", "/api/v4/futures/usdt/accounts", "", "", {"KEY": "key"})
        self.assertEqual((status, payload["status"]), (200, "ok"))
        request, timeout = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("fx-api-testnet.gateio.ws/api/v4/futures/usdt/accounts", request.full_url)
        self.assertEqual(timeout, 10)

    def test_write_method_and_body_fail_closed(self):
        transport = TRANSPORT.GatePrivateReadHttpTransport(self._profile(), opener=_Opener({}))
        with self.assertRaises(TRANSPORT.GatePrivateReadHttpTransportError):
            transport.request("POST", "/api/v4/futures/usdt/orders", "", "{}", {})
        with self.assertRaises(TRANSPORT.GatePrivateReadHttpTransportError):
            transport.request("GET", "/api/v4/futures/usdt/accounts", "", "{}", {})

    def test_builder_keeps_credentials_in_typed_client_only(self):
        opener = _Opener([])
        credential = CLIENT.GatePrivateCredential("key-example", "secret-example", MULTI.CapabilityEnvironment.TESTNET)
        client = TRANSPORT.build_gate_private_read_client(
            credential=credential, profile=self._profile(), timestamp_provider=lambda: 1700000000, opener=opener,
        )
        self.assertNotIn("secret-example", repr(client))
        self.assertEqual(client.read_futures_accounts(), [])
        self.assertEqual(opener.requests[0][0].get_method(), "GET")

    def test_invalid_key_on_legacy_futures_host_retries_shared_testnet_host(self):
        opener = _SequenceOpener([
            (401, {"label": "INVALID_KEY"}),
            (200, {"status": "ok", "accounts": []}),
        ])
        transport = TRANSPORT.GatePrivateReadHttpTransport(self._profile(), opener=opener)

        status, payload = transport.request(
            "GET", "/api/v4/futures/usdt/accounts", "", "", {"KEY": "key"}
        )

        self.assertEqual((status, payload["status"]), (200, "ok"))
        self.assertEqual(len(opener.requests), 2)
        self.assertIn("fx-api-testnet.gateio.ws", opener.requests[0][0].full_url)
        self.assertIn("api-testnet.gateapi.io", opener.requests[1][0].full_url)

    def test_transient_statuses_retry_with_bounded_deterministic_delays(self):
        opener = _SequenceOpener([
            (429, {"label": "TOO_MANY_REQUESTS"}),
            (503, {"label": "SERVICE_UNAVAILABLE"}),
            (200, {"status": "ok", "accounts": []}),
        ])
        delays = []
        policy = RATE_LIMIT.GateRateLimitPolicy(max_attempts=3, base_delay_seconds=2, max_delay_seconds=3)
        transport = TRANSPORT.GatePrivateReadHttpTransport(
            self._profile(), opener=opener, retry_policy=policy, sleep=delays.append,
        )
        status, payload = transport.request(
            "GET", "/api/v4/futures/usdt/accounts", "", "", {"KEY": "key"}
        )
        self.assertEqual((status, payload["status"]), (200, "ok"))
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(delays, [2, 3])

    def test_timeout_retries_then_returns_typed_temporary_failure(self):
        opener = _TimeoutOpener()
        delays = []
        policy = RATE_LIMIT.GateRateLimitPolicy(max_attempts=2, base_delay_seconds=1, max_delay_seconds=1)
        transport = TRANSPORT.GatePrivateReadHttpTransport(
            self._profile(), opener=opener, retry_policy=policy, sleep=delays.append,
        )
        with self.assertRaises(CLIENT.GatePrivateReadTemporaryError):
            transport.request("GET", "/api/v4/futures/usdt/accounts", "", "", {"KEY": "key"})
        self.assertEqual(opener.calls, 2)
        self.assertEqual(delays, [1])

    def test_explicit_http_proxy_is_supported_for_private_reads(self):
        previous = os.environ.get("PROXY_URL")
        try:
            os.environ["PROXY_URL"] = "http://127.0.0.1:8080"
            self.assertEqual(TRANSPORT._configured_proxy_url(), "http://127.0.0.1:8080")
        finally:
            if previous is None:
                os.environ.pop("PROXY_URL", None)
            else:
                os.environ["PROXY_URL"] = previous

    def test_non_http_proxy_fails_closed(self):
        previous = os.environ.get("PROXY_URL")
        try:
            os.environ["PROXY_URL"] = "socks5h://127.0.0.1:1080"
            with self.assertRaises(TRANSPORT.GatePrivateReadHttpTransportError):
                TRANSPORT._configured_proxy_url()
        finally:
            if previous is None:
                os.environ.pop("PROXY_URL", None)
            else:
                os.environ["PROXY_URL"] = previous

    def test_open_circuit_blocks_network_until_cooldown(self):
        opener = _Opener({"status": "should-not-be-read"})
        snapshots = [RATE_LIMIT.GateCircuitSnapshot(
            RATE_LIMIT.GateCircuitState.OPEN,
            consecutive_failures=3,
            opened_at_seconds=100,
        )]
        policy = RATE_LIMIT.GateRateLimitPolicy(failure_threshold=3, cooldown_seconds=30)
        transport = TRANSPORT.GatePrivateReadHttpTransport(
            self._profile(),
            opener=opener,
            retry_policy=policy,
            circuit_snapshot_provider=lambda: snapshots[-1],
            circuit_update=snapshots.append,
            now_seconds=lambda: 129,
        )
        with self.assertRaises(CLIENT.GatePrivateReadTemporaryError):
            transport.request("GET", "/api/v4/futures/usdt/accounts", "", "", {})
        self.assertEqual(opener.requests, [])
        self.assertEqual(snapshots[-1].state, RATE_LIMIT.GateCircuitState.OPEN)

    def test_cooldown_probe_success_closes_circuit(self):
        opener = _Opener({"status": "ok", "accounts": []})
        snapshots = [RATE_LIMIT.GateCircuitSnapshot(
            RATE_LIMIT.GateCircuitState.OPEN,
            consecutive_failures=3,
            opened_at_seconds=100,
        )]
        policy = RATE_LIMIT.GateRateLimitPolicy(failure_threshold=3, cooldown_seconds=30)
        transport = TRANSPORT.GatePrivateReadHttpTransport(
            self._profile(),
            opener=opener,
            retry_policy=policy,
            circuit_snapshot_provider=lambda: snapshots[-1],
            circuit_update=snapshots.append,
            now_seconds=lambda: 130,
        )
        status, payload = transport.request("GET", "/api/v4/futures/usdt/accounts", "", "", {})
        self.assertEqual((status, payload["status"]), (200, "ok"))
        self.assertEqual(snapshots[-1], RATE_LIMIT.GateCircuitSnapshot())

    def test_transient_failures_open_circuit_but_auth_does_not(self):
        transient_opener = _SequenceOpener([
            (503, {"label": "SERVICE_UNAVAILABLE"}),
        ])
        transient_snapshots = [RATE_LIMIT.GateCircuitSnapshot()]
        policy = RATE_LIMIT.GateRateLimitPolicy(max_attempts=1, failure_threshold=1)
        transport = TRANSPORT.GatePrivateReadHttpTransport(
            self._profile(),
            opener=transient_opener,
            retry_policy=policy,
            circuit_snapshot_provider=lambda: transient_snapshots[-1],
            circuit_update=transient_snapshots.append,
            now_seconds=lambda: 200,
        )
        status, _payload = transport.request("GET", "/api/v4/futures/usdt/accounts", "", "", {})
        self.assertEqual(status, 503)
        self.assertEqual(transient_snapshots[-1].state, RATE_LIMIT.GateCircuitState.OPEN)

        auth_opener = _SequenceOpener([(401, {"label": "INVALID_KEY"})])
        auth_snapshots = [RATE_LIMIT.GateCircuitSnapshot()]
        auth_transport = TRANSPORT.GatePrivateReadHttpTransport(
            self._profile(),
            opener=auth_opener,
            retry_policy=policy,
            circuit_snapshot_provider=lambda: auth_snapshots[-1],
            circuit_update=auth_snapshots.append,
            now_seconds=lambda: 200,
        )
        status, _payload = auth_transport.request("GET", "/api/v4/futures/usdt/accounts", "", "", {})
        self.assertEqual(status, 401)
        self.assertEqual(auth_snapshots[-1], RATE_LIMIT.GateCircuitSnapshot())

    def test_circuit_callbacks_must_be_supplied_as_a_pair(self):
        with self.assertRaises(TRANSPORT.GatePrivateReadHttpTransportError):
            TRANSPORT.GatePrivateReadHttpTransport(
                self._profile(), opener=_Opener({}), circuit_snapshot_provider=lambda: RATE_LIMIT.GateCircuitSnapshot()
            )


if __name__ == "__main__":
    unittest.main()
