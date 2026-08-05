"""Public Gate TestNet receipt contract tests."""

from types import SimpleNamespace
from pathlib import Path

from app.openapi.routes.gate_testnet_execution import _public_receipt


def test_gate_v4_route_signing_uses_unix_seconds():
    source = (Path(__file__).resolve().parents[1] / "app" / "openapi" / "routes" / "gate_testnet_execution.py").read_text(encoding="utf-8")
    assert "time.time() * 1000" not in source
    assert "timestamp_provider=lambda: int(time.time())" in source


def test_public_receipt_keeps_durable_admission_identity_without_credentials():
    admission = SimpleNamespace(
        command_id="command-1",
        action=SimpleNamespace(value="OPEN"),
        disposition=SimpleNamespace(value="CREATED"),
        economic_order_id="order-1",
        economic_fingerprint="e" * 64,
        request_fingerprint="r" * 64,
        risk_decision_id="risk-1",
        risk_decision_status="ALLOW",
        reservation_id="reservation-1",
        outbox_event_id="event-1",
        outbox_payload_hash="p" * 64,
        outbox_event_fingerprint="o" * 64,
    )
    receipt = SimpleNamespace(
        market_type=SimpleNamespace(value="spot"),
        account_scope="testnet-account",
        instrument_id="BTC_USDT",
        client_order_id="client-1",
        exchange_order_id="exchange-1",
        raw_state="closed",
        status_code=200,
        response_fingerprint="x" * 64,
    )
    runtime = SimpleNamespace(disposition=SimpleNamespace(value="CREATED"), admission=admission)
    execution = SimpleNamespace(receipt=receipt, ledger=None)

    body = _public_receipt(runtime, execution)

    assert body["admission"]["economic_order_id"] == "order-1"
    assert body["admission"]["economic_fingerprint"] == "e" * 64
    assert body["admission"]["request_fingerprint"] == "r" * 64
    assert body["admission"]["outbox_event_id"] == "event-1"
    assert "api_key" not in body
    assert "secret_key" not in body
