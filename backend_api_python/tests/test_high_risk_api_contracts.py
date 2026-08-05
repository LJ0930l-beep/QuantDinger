"""Contract tests for security-sensitive human API mutations."""

import pytest
from marshmallow import ValidationError

from app.openapi.schemas.high_risk import (
    CredentialCreateRequestSchema,
    QuickTradeOrderRequestSchema,
)


HIGH_RISK_REQUESTS = (
    ("/api/auth/login", "post"),
    ("/api/auth/register", "post"),
    ("/api/auth/reset-password", "post"),
    ("/api/auth/change-password", "post"),
    ("/api/strategies/{strategy_id}/start", "post"),
    ("/api/strategies/{strategy_id}/stop", "post"),
    ("/api/strategies/{strategy_id}", "delete"),
    ("/api/credentials/create", "post"),
    ("/api/credentials/delete", "delete"),
    ("/api/billing/usdt/create", "post"),
    ("/api/quick-trade/place-order", "post"),
    ("/api/quick-trade/close-position", "post"),
)


def test_high_risk_mutations_have_typed_requests(app):
    from app.openapi import get_openapi_api
    from app.openapi.register import enrich_spec

    api = get_openapi_api(app)
    with app.app_context():
        paths = enrich_spec(api.spec.to_dict())["paths"]

    for path, method in HIGH_RISK_REQUESTS:
        operation = paths[path][method]
        assert "requestBody" in operation or operation.get("parameters"), path


def test_login_validation_uses_human_error_envelope(client):
    response = client.post("/api/auth/login", json={"username": "demo"})

    assert response.status_code == 400
    assert response.get_json() == {
        "code": 0,
        "msg": "Invalid request data",
        "data": {"errors": {"json": {"password": ["Missing data for required field."]}}},
    }


def test_login_unknown_user_is_typed_401_not_internal_error(client, monkeypatch):
    """A missing local user must never surface as an HTTP 500 from login."""

    class _Security:
        def verify_turnstile_or_clearance(self, **kwargs):
            return True, "ok"

        def check_login_allowed(self, username, ip_address):
            return True, "ok"

        def record_login_attempt(self, *args, **kwargs):
            return None

        def log_security_event(self, *args, **kwargs):
            return None

    class _Users:
        def authenticate(self, username, password, update_last_login=False):
            return None

    monkeypatch.setattr(
        "app.services.security_service.get_security_service",
        lambda: _Security(),
    )
    monkeypatch.setattr(
        "app.services.user_service.get_user_service",
        lambda: _Users(),
    )
    monkeypatch.setattr("app.routes.auth._is_single_user_mode", lambda: False)

    response = client.post(
        "/api/auth/login",
        json={"username": "missing-local-user", "password": "not-a-real-secret"},
    )

    assert response.status_code == 401
    assert response.get_json() == {
        "code": 0,
        "msg": "Invalid credentials",
        "data": None,
    }


def test_login_dependency_failure_is_safe_503_without_raw_error(client, monkeypatch):
    """Infrastructure failures must not leak DB/driver text as HTTP 500."""

    class _Security:
        def verify_turnstile_or_clearance(self, **kwargs):
            return True, "ok"

        def check_login_allowed(self, username, ip_address):
            raise RuntimeError("permission denied for table security_login_attempts")

    monkeypatch.setattr(
        "app.services.security_service.get_security_service",
        lambda: _Security(),
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "known-user", "password": "not-a-real-secret"},
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "code": 503,
        "msg": "Authentication service temporarily unavailable",
        "data": None,
    }
    assert "permission denied" not in response.get_data(as_text=True).lower()


def test_quick_trade_contract_normalizes_legacy_values():
    loaded = QuickTradeOrderRequestSchema().load(
        {
            "credential_id": 7,
            "symbol": "BTC/USDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "amount": "50.5",
            "price": "60000",
            "market_type": "PERP",
            "marginMode": "ISOLATED",
        }
    )

    assert loaded["side"] == "buy"
    assert loaded["order_type"] == "limit"
    assert loaded["amount"] == 50.5
    assert loaded["market_type"] == "perp"
    assert loaded["marginMode"] == "isolated"


def test_credential_contract_requires_secrets_except_ibkr():
    with pytest.raises(ValidationError):
        CredentialCreateRequestSchema().load({"exchange_id": "binance"})

    loaded = CredentialCreateRequestSchema().load(
        {"exchange_id": "IBKR", "ibkr_port": 7497}
    )
    assert loaded["exchange_id"] == "ibkr"
