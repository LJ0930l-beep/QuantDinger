import pytest

from app.services import gate_private_read_provider as provider_module


def _config(environment="testnet"):
    return {
        "exchange_id": "gate",
        "environment": environment,
        "market_scope": "spot",
        "api_key": "opaque-test-key",
        "secret_key": "opaque-test-secret",
    }


def test_database_provider_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("QUANT_GATE_PRIVATE_READ_ENABLED", raising=False)
    provider = provider_module.provider_from_database(
        credential_resolver=lambda _credential_id, _user_id: pytest.fail("disabled provider must not resolve credentials")
    )

    with pytest.raises(provider_module.GatePrivateReadProviderError, match="disabled"):
        provider(7, 42, "spot", "gate-testnet", "BTC_USDT", None)


def test_database_provider_rejects_live_before_client_construction(monkeypatch):
    monkeypatch.setenv("QUANT_GATE_PRIVATE_READ_ENABLED", "1")
    build_calls = []
    provider = provider_module.provider_from_database(
        credential_resolver=lambda _credential_id, _user_id: _config("live"),
        client_builder=lambda **kwargs: build_calls.append(kwargs),
    )

    with pytest.raises(provider_module.GatePrivateReadProviderError, match="TestNet"):
        provider(7, 42, "spot", "gate-testnet", "BTC_USDT", None)
    assert build_calls == []


def test_database_provider_builds_testnet_read_only_client_with_explicit_scope(monkeypatch):
    monkeypatch.setenv("QUANT_GATE_PRIVATE_READ_ENABLED", "1")
    captured = {}

    class FakeSnapshotService:
        def read_snapshot(self, client, **kwargs):
            captured["client"] = client
            captured["kwargs"] = kwargs
            return "typed-snapshot"

    class FakeClient:
        pass

    def build_client(**kwargs):
        captured["builder"] = kwargs
        return FakeClient()

    monkeypatch.setattr(provider_module, "GatePrivateReadAccountService", FakeSnapshotService)
    def resolve(credential_id, user_id):
        captured["resolver"] = (credential_id, user_id)
        return _config("testnet")

    provider = provider_module.provider_from_database(
        credential_resolver=resolve,
        client_builder=build_client,
    )

    result = provider(7, 42, "spot", "gate-testnet", "BTC_USDT", None)

    assert result == "typed-snapshot"
    assert captured["resolver"] == (42, 7)
    assert captured["kwargs"]["account_scope"] == "gate-testnet"
    assert captured["kwargs"]["instrument_id"] == "BTC_USDT"
    credential = captured["builder"]["credential"]
    assert credential.environment.value == "testnet"
    assert "opaque-test-key" not in repr(credential)
    assert "opaque-test-secret" not in repr(credential)
    assert captured["builder"]["profile"].credential_ref == "credential-42"
    assert callable(captured["builder"]["circuit_snapshot_provider"])
    assert callable(captured["builder"]["circuit_update"])


def test_circuit_store_is_typed_and_scope_keyed_without_secret_material():
    store = provider_module.GatePrivateReadCircuitStore()
    first = store.read("credential-42:spot")
    assert first.state.value == "CLOSED"
    opened = provider_module.GateCircuitSnapshot(
        provider_module.GateCircuitState.OPEN,
        consecutive_failures=3,
        opened_at_seconds=123,
    )
    store.write("credential-42:spot", opened)
    assert store.read("credential-42:spot") == opened
    assert store.read("credential-42:perpetual") != opened

    with pytest.raises(provider_module.GatePrivateReadProviderError):
        store.read("key secret")
    with pytest.raises(provider_module.GatePrivateReadProviderError):
        store.write("credential-42:spot", object())


@pytest.mark.parametrize(
    ("error_type", "expected_code"),
    [
        (provider_module.GatePrivateReadAuthError, "GATE_TESTNET_AUTH_REJECTED"),
        (provider_module.GatePrivateReadPermissionError, "GATE_TESTNET_PERMISSION_OR_IP_REJECTED"),
        (provider_module.GatePrivateReadTemporaryError, "GATE_TESTNET_NETWORK_UNAVAILABLE"),
        (provider_module.GatePrivateReadInvalidResponse, "GATE_TESTNET_INVALID_RESPONSE"),
    ],
)
def test_database_provider_classifies_private_read_failures_without_payload_leak(monkeypatch, error_type, expected_code):
    monkeypatch.setenv("QUANT_GATE_PRIVATE_READ_ENABLED", "1")

    class FailingSnapshotService:
        def read_snapshot(self, client, **kwargs):
            raise error_type("opaque provider payload with secret-value")

    monkeypatch.setattr(provider_module, "GatePrivateReadAccountService", FailingSnapshotService)
    provider = provider_module.provider_from_database(
        credential_resolver=lambda _credential_id, _user_id: _config("testnet"),
        client_builder=lambda **kwargs: object(),
    )

    with pytest.raises(provider_module.GatePrivateReadProviderError) as captured:
        provider(7, 42, "spot", "gate-testnet", "BTC_USDT", None)
    assert captured.value.code == expected_code
    assert "secret-value" not in str(captured.value)
