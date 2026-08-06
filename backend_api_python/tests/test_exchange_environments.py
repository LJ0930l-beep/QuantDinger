import json

import pytest

from app.routes.credentials import (
    GateCredentialProbeError,
    _crypto_credential_config,
    _gate_probe_error_code,
    _probe_crypto_credential,
    _safe_credential_error,
)
from app.services.exchange_execution import resolve_exchange_config
from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.factory import (
    create_client,
    exchange_market_scope,
    exchange_trading_environment,
    validate_exchange_environment,
)
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient


def _config(exchange_id, environment, market_scope="both"):
    config = {
        "exchange_id": exchange_id,
        "api_key": "key",
        "secret_key": "secret",
        "environment": environment,
        "market_scope": market_scope,
    }
    if exchange_id in ("okx", "bitget"):
        config["passphrase"] = "pass"
    return config


def test_exchange_environment_routes_match_official_demo_hosts():
    assert create_client(_config("binance", "demo", "spot"), market_type="spot").base_url == "https://demo-api.binance.com"
    assert create_client(_config("binance", "demo", "swap"), market_type="swap").base_url == "https://demo-fapi.binance.com"

    okx = create_client(_config("okx", "demo"), market_type="spot")
    assert okx.base_url == "https://openapi.okx.com"
    assert okx._headers("1", "sign")["x-simulated-trading"] == "1"

    bitget = create_client(_config("bitget", "demo"), market_type="spot")
    assert bitget.base_url == "https://api.bitget.com"
    assert bitget._headers("1", "sign", "/api/v2/spot/trade/place-order")["PAPTRADING"] == "1"

    assert create_client(_config("bybit", "demo"), market_type="spot").base_url == "https://api-demo.bybit.com"

    assert create_client(_config("gate", "testnet"), market_type="spot").base_url == "https://api-testnet.gateapi.io"
    assert create_client(_config("gate", "testnet"), market_type="swap").base_url == "https://api-testnet.gateapi.io"


def test_fee_clients_preserve_spot_vs_contract_market_routing():
    assert isinstance(create_client(_config("binance", "live"), market_type="spot"), BinanceSpotClient)
    assert isinstance(create_client(_config("binance", "live"), market_type="swap"), BinanceFuturesClient)
    assert isinstance(create_client(_config("okx", "live"), market_type="spot"), OkxClient)
    assert isinstance(create_client(_config("okx", "live"), market_type="swap"), OkxClient)
    assert isinstance(create_client(_config("bitget", "live"), market_type="spot"), BitgetSpotClient)
    assert isinstance(create_client(_config("bitget", "live"), market_type="swap"), BitgetMixClient)

    bybit_spot = create_client(_config("bybit", "live"), market_type="spot")
    bybit_swap = create_client(_config("bybit", "live"), market_type="swap")
    assert isinstance(bybit_spot, BybitClient) and bybit_spot.category == "spot"
    assert isinstance(bybit_swap, BybitClient) and bybit_swap.category == "linear"
    assert isinstance(create_client(_config("gate", "live"), market_type="spot"), GateSpotClient)
    assert isinstance(create_client(_config("gate", "live"), market_type="swap"), GateUsdtFuturesClient)

    htx_spot = create_client(_config("htx", "live"), market_type="spot")
    htx_swap = create_client(_config("htx", "live"), market_type="swap")
    assert isinstance(htx_spot, HtxClient) and htx_spot.market_type == "spot"
    assert isinstance(htx_swap, HtxClient) and htx_swap.market_type == "swap"


def test_legacy_demo_flags_map_to_exchange_specific_environment():
    assert exchange_trading_environment({"exchange_id": "binance", "enable_demo_trading": True}) == "demo"
    assert exchange_trading_environment({"exchange_id": "binance", "environment": "demo"}) == "demo"
    assert exchange_trading_environment({"exchange_id": "okx", "enable_demo_trading": True}) == "demo"
    assert exchange_trading_environment({"exchange_id": "bybit", "enable_demo_trading": True}) == "demo"
    assert exchange_trading_environment({"exchange_id": "gate", "enable_demo_trading": True}) == "testnet"


def test_market_scope_alias_and_invalid_value_are_not_silently_ignored():
    assert exchange_market_scope({"marketScope": "futures"}) == "swap"
    assert exchange_market_scope({"marketScope": "spot_perpetual"}) == "both"
    assert exchange_market_scope({"marketScope": "spot_and_swap"}) == "both"
    with pytest.raises(LiveTradingError, match="INVALID_CREDENTIAL_MARKET_SCOPE"):
        validate_exchange_environment("okx", "live", exchange_market_scope({"marketScope": "margin"}))


def test_environment_and_market_scope_fail_closed():
    with pytest.raises(LiveTradingError, match="HTX_DEMO_NOT_SUPPORTED"):
        create_client(_config("htx", "demo"), market_type="spot")

    with pytest.raises(LiveTradingError, match="CREDENTIAL_MARKET_SCOPE_MISMATCH"):
        create_client(_config("bybit", "demo", "spot"), market_type="swap")

    with pytest.raises(LiveTradingError, match="UNSUPPORTED_TRADING_ENVIRONMENT"):
        create_client(_config("gate", "banana"), market_type="spot")


def test_binance_demo_credential_supports_spot_and_futures_scope():
    config = _crypto_credential_config(_config("binance", "demo"), "binance")
    assert config["environment"] == "demo"
    assert config["market_scope"] == "both"
    assert config["enable_demo_trading"] is True


def test_binance_testnet_environment_is_not_supported():
    with pytest.raises(LiveTradingError, match="UNSUPPORTED_TRADING_ENVIRONMENT"):
        _crypto_credential_config(_config("binance", "testnet"), "binance")


def test_bybit_testnet_environment_is_not_supported():
    with pytest.raises(LiveTradingError, match="UNSUPPORTED_TRADING_ENVIRONMENT"):
        _crypto_credential_config(_config("bybit", "testnet"), "bybit")


def test_strategy_cannot_override_credential_environment_or_secret(monkeypatch):
    monkeypatch.setattr(
        "app.services.exchange_execution._load_credential_config",
        lambda credential_id, user_id: {
            "exchange_id": "bybit",
            "api_key": "vault-key",
            "secret_key": "vault-secret",
            "environment": "demo",
            "market_scope": "spot",
        },
    )

    resolved = resolve_exchange_config(
        {
            "credential_id": 7,
            "exchange_id": "binance",
            "api_key": "override-key",
            "secret_key": "override-secret",
            "environment": "live",
            "market_scope": "swap",
            "margin_mode": "isolated",
        },
        user_id=3,
    )

    assert resolved["exchange_id"] == "bybit"
    assert resolved["api_key"] == "vault-key"
    assert resolved["secret_key"] == "vault-secret"
    assert resolved["environment"] == "demo"
    assert resolved["market_scope"] == "spot"
    assert resolved["margin_mode"] == "isolated"


def test_credential_probe_calls_private_account_endpoint(monkeypatch):
    calls = []

    class Client:
        def __init__(self, market_type):
            self.market_type = market_type

        def get_account(self):
            calls.append(self.market_type)
            return {"ok": True}

    monkeypatch.setattr("app.routes.credentials.create_client", lambda config, market_type: Client(market_type))
    tested = _probe_crypto_credential(_config("binance", "testnet", "spot"))

    assert tested == ["spot"]
    assert calls == ["spot"]

    tested = _probe_crypto_credential(_config("binance", "demo", "both"))

    assert tested == ["spot", "swap"]
    assert calls == ["spot", "spot", "swap"]


def test_gate_probe_reports_partial_market_results_without_provider_payload(monkeypatch):
    calls = []

    def probe(config, market_type):
        calls.append(market_type)
        if market_type == "swap":
            raise LiveTradingError("Gate HTTP 401: INVALID_KEY")

    monkeypatch.setattr("app.routes.credentials._probe_gate_testnet_readonly", probe)

    with pytest.raises(GateCredentialProbeError) as caught:
        _probe_crypto_credential(_config("gate", "testnet", "both"))

    error = caught.value
    assert error.code == "GATE_TESTNET_AUTH_REJECTED"
    assert error.tested_markets == ("spot",)
    assert error.failed_markets == ({"market_type": "swap", "code": "GATE_TESTNET_AUTH_REJECTED"},)
    assert calls == ["spot", "swap"]


def test_gate_testnet_probe_uses_get_only_adapter_for_each_market(monkeypatch):
    calls = []

    class FakeClient:
        def read_spot_accounts(self):
            calls.append("spot-read")

        def read_futures_accounts(self):
            calls.append("swap-read")

    def build(*, credential, profile, timestamp_provider, opener=None):
        calls.append((profile.market_type.value, profile.base_url, profile.writes_enabled))
        return FakeClient()

    monkeypatch.setattr("app.routes.credentials.build_gate_private_read_client", build)
    config = _config("gate", "testnet", "both")
    config.update({"api_key": "probe-key", "secret_key": "probe-secret"})

    assert _probe_crypto_credential(config) == ["spot", "swap"]
    assert calls == [
        ("spot", "https://api-testnet.gateapi.io", False),
        "spot-read",
        ("perpetual", "https://fx-api-testnet.gateio.ws", False),
        "swap-read",
    ]


def test_credential_error_contract_never_reflects_provider_payload():
    opaque_payload = "opaque-test-secret"

    assert _safe_credential_error(
        RuntimeError(opaque_payload),
        "CREDENTIAL_CONNECTION_FAILED",
    ) == "CREDENTIAL_CONNECTION_FAILED"
    assert _safe_credential_error(
        LiveTradingError("CREDENTIAL_MARKET_SCOPE_MISMATCH"),
        "CREDENTIAL_CONNECTION_FAILED",
    ) == "CREDENTIAL_MARKET_SCOPE_MISMATCH"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTPSConnectionPool: connection refused", "GATE_TESTNET_NETWORK_UNAVAILABLE"),
        ("[WinError 10061] target machine actively refused the connection", "GATE_TESTNET_NETWORK_UNAVAILABLE"),
        ("Gate HTTP 401: INVALID_KEY", "GATE_TESTNET_AUTH_REJECTED"),
        ("Gate HTTP 403: IP_FORBIDDEN", "GATE_TESTNET_PERMISSION_OR_IP_REJECTED"),
        ("Gate private read permission denied", "GATE_TESTNET_PERMISSION_OR_IP_REJECTED"),
        ("unexpected Gate response", "CREDENTIAL_CONNECTION_FAILED"),
    ],
)
def test_gate_probe_error_is_typed_without_provider_payload(message, expected):
    assert _gate_probe_error_code(RuntimeError(message)) == expected


def test_credential_create_error_contract_never_reflects_provider_payload():
    """Create-time validation must use the same stable public error boundary."""

    assert _safe_credential_error(
        RuntimeError("provider payload leaked secret-material"),
        "CREDENTIAL_CREATE_FAILED",
    ) == "CREDENTIAL_CREATE_FAILED"


@pytest.mark.parametrize(
    "fallback",
    ["CREDENTIAL_LIST_FAILED", "CREDENTIAL_UPDATE_FAILED", "CREDENTIAL_DELETE_FAILED"],
)
def test_credential_crud_error_contract_uses_fallback(fallback):
    assert _safe_credential_error(RuntimeError("opaque provider payload"), fallback) == fallback


def _saved_credential_auth_payload():
    return {
        "user_id": 7,
        "_verified_username": "test-user",
        "_verified_user_role": "admin",
    }


def test_saved_gate_testnet_probe_returns_read_only_contract_without_secrets(client, monkeypatch):
    monkeypatch.setattr("app.utils.auth.verify_token", lambda _: _saved_credential_auth_payload())
    monkeypatch.setattr(
        "app.routes.credentials.resolve_exchange_config",
        lambda payload, user_id: {
            "exchange_id": "gate",
            "environment": "testnet",
            "market_scope": "spot",
            "api_key": "not-returned",
            "secret_key": "not-returned",
        },
    )
    monkeypatch.setattr("app.routes.credentials._probe_crypto_credential", lambda _: ["spot"])

    response = client.post(
        "/api/credentials/test-saved",
        json={"credential_id": 42},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == 1
    assert body["data"] == {
        "exchange_id": "gate",
        "environment": "testnet",
        "market_scope": "spot",
        "tested_markets": ["spot"],
        "live_enabled": False,
        "writes_enabled": False,
    }
    assert "api_key" not in body["data"]
    assert "secret_key" not in body["data"]


def test_saved_gate_probe_returns_safe_per_market_failure_details(client, monkeypatch):
    monkeypatch.setattr("app.utils.auth.verify_token", lambda _: _saved_credential_auth_payload())
    monkeypatch.setattr(
        "app.routes.credentials.resolve_exchange_config",
        lambda payload, user_id: {
            "exchange_id": "gate",
            "environment": "testnet",
            "market_scope": "both",
        },
    )
    monkeypatch.setattr(
        "app.routes.credentials._probe_crypto_credential",
        lambda _: (_ for _ in ()).throw(
            GateCredentialProbeError(
                "GATE_TESTNET_AUTH_REJECTED",
                tested_markets=("spot",),
                failed_markets=(
                    {"market_type": "swap", "code": "GATE_TESTNET_AUTH_REJECTED"},
                ),
            )
        ),
    )

    response = client.post(
        "/api/credentials/test-saved",
        json={"credential_id": 42},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["msg"] == "GATE_TESTNET_AUTH_REJECTED"
    assert body["data"]["tested_markets"] == ["spot"]
    assert body["data"]["failed_markets"] == [
        {"market_type": "swap", "code": "GATE_TESTNET_AUTH_REJECTED"},
    ]
    assert "api_key" not in body["data"]
    assert "secret_key" not in body["data"]


def test_saved_credential_probe_rejects_live_before_probe(client, monkeypatch):
    monkeypatch.setattr("app.utils.auth.verify_token", lambda _: _saved_credential_auth_payload())
    monkeypatch.setattr(
        "app.routes.credentials.resolve_exchange_config",
        lambda payload, user_id: {"exchange_id": "gate", "environment": "live", "market_scope": "spot"},
    )
    probe_calls = []
    monkeypatch.setattr("app.routes.credentials._probe_crypto_credential", lambda _: probe_calls.append(True))

    response = client.post(
        "/api/credentials/test-saved",
        json={"credential_id": 42},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 400
    assert response.get_json()["msg"] == "GATE_TESTNET_CREDENTIAL_REQUIRED"
    assert probe_calls == []


def test_saved_credential_probe_requires_credential_id(client, monkeypatch):
    monkeypatch.setattr("app.utils.auth.verify_token", lambda _: _saved_credential_auth_payload())

    response = client.post(
        "/api/credentials/test-saved",
        json={},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 400
    assert response.get_json()["msg"] == "CREDENTIAL_ID_REQUIRED"


def test_credential_list_exposes_testnet_metadata_without_credential_material(client, monkeypatch):
    """The dashboard may select TestNet credentials without receiving secrets."""

    class Cursor:
        def execute(self, *_args):
            return None

        def fetchall(self):
            return [
                {
                    "id": 42,
                    "user_id": 7,
                    "name": "Gate sandbox",
                    "exchange_id": "gate",
                    "api_key_hint": "test...1234",
                    "encrypted_config": "ciphertext",
                    "created_at": "2026-08-03T00:00:00Z",
                    "updated_at": "2026-08-03T00:00:00Z",
                }
            ]

        def close(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr("app.utils.auth.verify_token", lambda _: _saved_credential_auth_payload())
    monkeypatch.setattr("app.routes.credentials.get_db_connection", lambda: Connection())
    monkeypatch.setattr(
        "app.routes.credentials.decrypt_credential_blob",
        lambda _: json.dumps(
            {
                "exchange_id": "gate",
                "environment": "testnet",
                "market_scope": "spot",
                "api_key": "must-not-cross-http",
                "secret_key": "must-not-cross-http",
            }
        ),
    )

    response = client.get(
        "/api/credentials/list",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    item = response.get_json()["data"]["items"][0]
    assert item["environment"] == "testnet"
    assert item["market_scope"] == "spot"
    assert item["api_key_hint"] == "test...1234"
    assert "encrypted_config" not in item
    assert "api_key" not in item
    assert "secret_key" not in item


def test_gate_readonly_route_passes_saved_credential_scope_without_live_authority(client, monkeypatch):
    """The account view uses the saved credential identity and cannot enable Live."""

    calls = []

    class Service:
        def read_response(self, **kwargs):
            calls.append(kwargs)
            return 200, {
                "status": "READY",
                "environment": "TESTNET",
                "account_scope": kwargs["account_scope"],
                "live_enabled": False,
                "network_access": True,
            }

    monkeypatch.setattr("app.utils.auth.verify_token", lambda _: _saved_credential_auth_payload())
    monkeypatch.setattr(
        "app.openapi.routes.quant_readonly.gate_account_service_from_app",
        lambda _app: Service(),
    )

    response = client.get(
        "/api/quant/gate/account/readonly?credential_id=42&market_type=spot&account_scope=gate-testnet&instrument_id=BTC_USDT",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "READY",
        "environment": "TESTNET",
        "account_scope": "gate-testnet",
        "live_enabled": False,
        "network_access": True,
    }
    assert calls[0]["user_id"] == 7
    assert calls[0]["credential_id"] == 42
    assert calls[0]["market_type"] == "spot"
    assert calls[0]["instrument_id"] == "BTC_USDT"


def test_gate_testnet_environment_route_accepts_saved_credential_without_write_authority(client, monkeypatch):
    """The dashboard's TestNet account view may use the saved encrypted credential."""

    calls = []

    class Service:
        def read_response(self, **kwargs):
            calls.append(kwargs)
            return 200, {
                "status": "READY",
                "environment": "TESTNET",
                "account_scope": kwargs["account_scope"],
                "live_enabled": False,
                "network_access": True,
                "balances": [],
                "orders": [],
                "fills": [],
            }

    monkeypatch.setattr("app.utils.auth.verify_token", lambda _: _saved_credential_auth_payload())
    monkeypatch.setattr(
        "app.openapi.routes.quant_readonly.gate_account_service_from_app",
        lambda _app: Service(),
    )

    response = client.get(
        "/api/quant/gate/testnet/account?credential_id=42&market_type=spot&account_scope=gate-testnet&instrument_id=BTC_USDT",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.get_json()["environment"] == "TESTNET"
    assert response.get_json()["live_enabled"] is False
    assert response.get_json()["order_source"] == "saved_credential"
    assert calls[0]["user_id"] == 7
    assert calls[0]["credential_id"] == 42
    assert calls[0]["market_type"] == "spot"
    assert calls[0]["account_scope"] == "gate-testnet"


def test_gate_testnet_environment_route_rejects_invalid_saved_credential_id(client, monkeypatch):
    monkeypatch.setattr("app.utils.auth.verify_token", lambda _: _saved_credential_auth_payload())
    response = client.get(
        "/api/quant/gate/testnet/account?credential_id=0&market_type=spot&account_scope=gate-testnet",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 503
    assert response.get_json()["live_enabled"] is False
