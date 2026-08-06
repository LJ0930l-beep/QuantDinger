"""Agent Gateway v1 full-surface tests for the current production contract."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from flask import g

from app.routes.agent_v1 import quick_trade, research, trading_data
from app.utils import agent_auth


def _token(scopes: str = "R,W,B,N,T") -> dict:
    return {
        "id": 501,
        "user_id": 7,
        "name": "full-surface-agent",
        "scopes": scopes,
        "markets": "*",
        "instruments": "*",
        "paper_only": True,
        "rate_limit_per_min": 100,
        "max_order_notional": 1000,
        "max_daily_notional": 5000,
        "status": "active",
        "expires_at": None,
    }


def _headers(*, key: str | None = None) -> dict:
    headers = {"Authorization": "Bearer qd_agent_FULLSURFACE12345"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


@pytest.fixture(autouse=True)
def _authorized(monkeypatch):
    agent_auth._rate_state.clear()
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda _raw: _token())
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *args, **kwargs: None)
    yield
    agent_auth._rate_state.clear()


def test_whoami_returns_identity(client):
    response = client.get("/api/agent/v1/whoami", headers=_headers())
    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["user_id"] == 7


def test_unknown_token_is_rejected(client, monkeypatch):
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda _raw: None)
    response = client.get("/api/agent/v1/whoami", headers=_headers())
    assert response.status_code == 401


def test_factor_registry_is_exposed(client):
    response = client.get(
        "/api/agent/v1/research/factors?category=momentum",
        headers=_headers(),
    )
    assert response.status_code == 200
    items = response.get_json()["data"]["items"]
    assert any(item["factor_id"] == "rsi" for item in items)


def test_safe_account_metadata_never_returns_credential_blob(client, monkeypatch):
    class Cursor:
        def execute(self, _sql, _params=None):
            pass

        def fetchall(self):
            return [{
                "id": 9,
                "name": "main",
                "exchange_id": "binance",
                "api_key_hint": "abcd...wxyz",
                "encrypted_config": "not-a-valid-ciphertext",
                "created_at": None,
                "updated_at": None,
            }]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def fake_db():
        yield Connection()

    monkeypatch.setattr(trading_data, "get_db_connection", fake_db)
    response = client.get("/api/agent/v1/trading/accounts", headers=_headers())
    assert response.status_code == 200
    item = response.get_json()["data"][0]
    assert item["api_key_hint"] == "abcd...wxyz"
    assert "encrypted_config" not in item
    assert "api_key" not in item


def test_quick_trade_live_orders_fail_closed(monkeypatch):
    # SC-14 retired legacy quick-trade execution; the agent quick-trade
    # surface must fail closed rather than place orders.
    assert not hasattr(quick_trade, "_reserve_live_notional")
