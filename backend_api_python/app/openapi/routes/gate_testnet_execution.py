"""Explicit, authenticated Gate TestNet execution endpoint.

This endpoint is intentionally disabled unless the operator enables the
TestNet write flag.  It never accepts LIVE, never accepts credentials in the
request body, and only submits after Canonical Entry + Hard Risk admission.
"""

from __future__ import annotations

import os
import time
from typing import Any

from flask import current_app, jsonify, request

from app.utils.db import get_db_connection
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.exchange_execution import resolve_exchange_config
from app.services.gate_testnet_execution_service import (
    GateTestnetExecutionServiceError,
    GateTestnetCancelRequest,
    cancel_gate_testnet_payload_caller_owned,
    execute_gate_testnet_payload_caller_owned,
)
from app.services.gate_testnet_order_client_provider import build_gate_testnet_order_client_from_config
from app.services.gate_private_read_client import GatePrivateCredential
from app.services.gate_private_read_http_transport import build_gate_private_read_client
from app.domain.gate_readonly_contracts import (
    GateEnvironment,
    GateMarketType,
    GateReadCapabilityProfile,
    gate_testnet_base_url_for_market,
)
from app.domain.multi_asset_capability_contracts import CapabilityEnvironment
from app.services.gate_testnet_network_fill_settlement import (
    GateTestnetNetworkSettlementError,
    build_gate_testnet_settlement_scopes,
    read_and_settle_gate_testnet_order_fills_caller_owned,
)
from app.utils.auth import get_current_user_id, login_required


blp = Blueprint("gate_testnet_execution", __name__)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _public_admission(admission: Any) -> dict[str, Any]:
    """Expose durable admission identity, never credential material."""

    return {
        "command_id": str(admission.command_id),
        "action": admission.action.value,
        "disposition": admission.disposition.value,
        "economic_order_id": admission.economic_order_id,
        "economic_fingerprint": admission.economic_fingerprint,
        "request_fingerprint": admission.request_fingerprint,
        "risk_decision_id": admission.risk_decision_id,
        "risk_decision_status": admission.risk_decision_status,
        "reservation_id": admission.reservation_id,
        "outbox_event_id": admission.outbox_event_id,
        "outbox_payload_hash": admission.outbox_payload_hash,
        "outbox_event_fingerprint": admission.outbox_event_fingerprint,
    }


def _public_receipt(runtime_result: Any, execution_result: Any) -> dict[str, Any]:
    receipt = execution_result.receipt
    return {
        "status": runtime_result.disposition.value,
        "environment": "TESTNET",
        "network_access": True,
        "writes_enabled": True,
        "live_enabled": False,
        "admission": _public_admission(runtime_result.admission),
        "order": {
            "market_type": receipt.market_type.value,
            "account_scope": receipt.account_scope,
            "instrument_id": receipt.instrument_id,
            "client_order_id": receipt.client_order_id,
            "exchange_order_id": receipt.exchange_order_id,
            "state": receipt.raw_state,
            "status_code": receipt.status_code,
            "response_fingerprint": receipt.response_fingerprint,
        },
        "ledger": None if execution_result.ledger is None else execution_result.ledger.disposition.value,
    }


def _public_cancel_receipt(runtime_result: Any, receipt: Any) -> dict[str, Any]:
    return {
        "status": runtime_result.disposition.value,
        "environment": "TESTNET",
        "network_access": True,
        "writes_enabled": True,
        "live_enabled": False,
        "admission": _public_admission(runtime_result.admission),
        "order": {
            "market_type": receipt.market_type.value,
            "account_scope": receipt.account_scope,
            "instrument_id": receipt.instrument_id,
            "client_order_id": receipt.client_order_id,
            "exchange_order_id": receipt.exchange_order_id,
            "state": receipt.raw_state,
            "status_code": receipt.status_code,
            "response_fingerprint": receipt.response_fingerprint,
        },
        "ledger": None,
    }


def _public_query_receipt(receipt: Any) -> dict[str, Any]:
    return {
        "status": "FOUND",
        "environment": "TESTNET",
        "network_access": True,
        "writes_enabled": False,
        "live_enabled": False,
        "order": {
            "market_type": receipt.market_type.value,
            "account_scope": receipt.account_scope,
            "instrument_id": receipt.instrument_id,
            "client_order_id": receipt.client_order_id,
            "exchange_order_id": receipt.exchange_order_id,
            "state": receipt.raw_state,
            "status_code": receipt.status_code,
            "response_fingerprint": receipt.response_fingerprint,
        },
    }


@blp.route("/api/quant/gate/testnet/order", methods=["POST"])
@login_required
def submit_gate_testnet_order():
    """Submit one explicitly enabled Gate TestNet order after admission."""

    if not _enabled("GATE_TESTNET_WRITE_ENABLED") or _enabled("AGENT_LIVE_TRADING_ENABLED"):
        return jsonify({"status": "DISABLED", "environment": "TESTNET", "live_enabled": False, "writes_enabled": False}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422
    try:
        user_id = int(get_current_user_id())
        credential_id = int(payload["credential_id"])
        config = resolve_exchange_config({"credential_id": credential_id}, user_id=user_id)

        def client_factory(order_request):
            return build_gate_testnet_order_client_from_config(
                config,
                market_type=order_request.market_type,
                account_scope=order_request.account_scope,
                # Gate API v4 signing uses Unix seconds, not milliseconds.
                timestamp_provider=lambda: int(time.time()),
                client_order_id_validator=lambda value: value,
                environ=os.environ,
                allow_writes=True,
            )

        with get_db_connection() as connection:
            execution_payload = {**payload, "credential_id": credential_id}
            runtime_result, execution_result = execute_gate_testnet_payload_caller_owned(
                connection,
                execution_payload,
                tenant_id=user_id,
                actor_id=str(user_id),
                client_factory=client_factory,
            )
            connection.commit()
        return jsonify(_public_receipt(runtime_result, execution_result)), 200
    except (KeyError, ValueError, TypeError, GateTestnetExecutionServiceError):
        return jsonify({"status": "REJECTED", "code": "TESTNET_ORDER_CONTRACT_INVALID", "live_enabled": False}), 422
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "TESTNET_ORDER_UNAVAILABLE", "live_enabled": False}), 503


@blp.route("/api/quant/gate/testnet/order", methods=["GET"])
@login_required
def query_gate_testnet_order():
    """Read one Gate TestNet order by its stable exchange order id."""

    try:
        user_id = int(get_current_user_id())
        credential_id = int(request.args.get("credential_id", ""))
        instrument_id = str(request.args.get("instrument_id", "")).strip()
        market_type = str(request.args.get("market_type", "spot")).strip().lower()
        exchange_order_id = str(request.args.get("exchange_order_id", "")).strip()
        if not instrument_id or not exchange_order_id:
            return jsonify({"status": "REJECTED", "code": "ORDER_QUERY_SCOPE_REQUIRED", "live_enabled": False}), 422
        config = resolve_exchange_config({"credential_id": credential_id}, user_id=user_id)
        from app.domain.multi_asset_capability_contracts import AssetMarketType

        typed_market = AssetMarketType.SPOT if market_type == "spot" else AssetMarketType.PERPETUAL if market_type in {"perpetual", "perp", "swap", "futures"} else None
        if typed_market is None:
            return jsonify({"status": "REJECTED", "code": "ORDER_QUERY_MARKET_INVALID", "live_enabled": False}), 422
        client = build_gate_testnet_order_client_from_config(
            config,
            market_type=typed_market,
            account_scope=str(request.args.get("account_scope", "query")).strip() or "query",
            timestamp_provider=lambda: int(time.time()),
            client_order_id_validator=lambda value: value,
            environ=os.environ,
            allow_writes=False,
        )
        return jsonify(_public_query_receipt(client.query(instrument_id=instrument_id, exchange_order_id=exchange_order_id))), 200
    except (KeyError, ValueError, TypeError, GateTestnetExecutionServiceError):
        return jsonify({"status": "REJECTED", "code": "TESTNET_ORDER_QUERY_INVALID", "live_enabled": False}), 422
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "TESTNET_ORDER_QUERY_UNAVAILABLE", "live_enabled": False}), 503


@blp.route("/api/quant/gate/testnet/order/cancel", methods=["POST"])
@login_required
def cancel_gate_testnet_order():
    """Cancel one admitted Gate TestNet venue order by stable venue id."""

    if not _enabled("GATE_TESTNET_WRITE_ENABLED") or _enabled("AGENT_LIVE_TRADING_ENABLED"):
        return jsonify({"status": "DISABLED", "environment": "TESTNET", "live_enabled": False, "writes_enabled": False}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422
    try:
        user_id = int(get_current_user_id())
        credential_id = int(payload["credential_id"])
        config = resolve_exchange_config({"credential_id": credential_id}, user_id=user_id)

        def client_factory(cancel_request: GateTestnetCancelRequest):
            return build_gate_testnet_order_client_from_config(
                config,
                market_type=cancel_request.market_type,
                account_scope=cancel_request.account_scope,
                timestamp_provider=lambda: int(time.time()),
                client_order_id_validator=lambda value: value,
                environ=os.environ,
                allow_writes=True,
            )

        with get_db_connection() as connection:
            runtime_result, receipt = cancel_gate_testnet_payload_caller_owned(
                connection,
                payload,
                tenant_id=user_id,
                actor_id=str(user_id),
                client_factory=client_factory,
            )
            connection.commit()
        return jsonify(_public_cancel_receipt(runtime_result, receipt)), 200
    except (KeyError, ValueError, TypeError, GateTestnetExecutionServiceError):
        return jsonify({"status": "REJECTED", "code": "TESTNET_CANCEL_CONTRACT_INVALID", "live_enabled": False}), 422
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "TESTNET_CANCEL_UNAVAILABLE", "live_enabled": False}), 503


@blp.route("/api/quant/gate/testnet/order/settle-fills", methods=["POST"])
@login_required
def settle_gate_testnet_order_fills():
    """Read and persist fills for one already-known Gate TestNet order.

    This is an explicit, opt-in recovery seam.  It accepts only a credential
    reference and caller-owned immutable scope facts; it never accepts raw
    keys, opens Live, submits, cancels, or guesses valuation/timestamps.
    """

    if not _enabled("GATE_TESTNET_FILL_SETTLEMENT_ENABLED") or _enabled("AGENT_LIVE_TRADING_ENABLED"):
        return jsonify({"status": "DISABLED", "environment": "TESTNET", "live_enabled": False, "writes_enabled": False}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422
    try:
        user_id = int(get_current_user_id())
        credential_id = int(payload["credential_id"])
        exchange_order_id = str(payload["exchange_order_id"]).strip()
        instrument_id = str(payload["instrument_id"]).strip()
        account_scope = str(payload["account_scope"]).strip()
        if not exchange_order_id or not instrument_id or not account_scope:
            raise GateTestnetNetworkSettlementError("order scope is required")
        config = resolve_exchange_config({"credential_id": credential_id}, user_id=user_id)
        environment = str(config.get("environment") or config.get("network") or config.get("env") or "").strip().lower()
        if str(config.get("exchange_id") or config.get("exchangeId") or "").strip().lower() != "gate" or environment not in {"testnet", "sandbox", "test"}:
            raise GateTestnetNetworkSettlementError("Gate TestNet credential is required")
        market_raw = str(payload.get("market_type") or "").strip().lower()
        from app.domain.multi_asset_capability_contracts import AssetMarketType
        typed_market = AssetMarketType.SPOT if market_raw == "spot" else AssetMarketType.PERPETUAL if market_raw in {"perpetual", "perp", "swap", "futures", "future"} else None
        if typed_market is None:
            raise GateTestnetNetworkSettlementError("market_type must be spot or perpetual")

        scopes = build_gate_testnet_settlement_scopes(payload, tenant_id=user_id, credential_id=credential_id)
        order_client = build_gate_testnet_order_client_from_config(
            config,
            market_type=typed_market,
            account_scope=account_scope,
            timestamp_provider=lambda: int(time.time()),
            client_order_id_validator=lambda value: value,
            environ=os.environ,
            allow_writes=False,
        )
        receipt = order_client.query(instrument_id=instrument_id, exchange_order_id=exchange_order_id)
        api_key = str(config.get("api_key") or config.get("apiKey") or "").strip()
        api_secret = str(config.get("secret_key") or config.get("secret") or "").strip()
        gate_market = GateMarketType.SPOT if typed_market is AssetMarketType.SPOT else GateMarketType.PERPETUAL
        read_profile = GateReadCapabilityProfile(
            environment=GateEnvironment.TESTNET,
            market_type=gate_market,
            base_url=gate_testnet_base_url_for_market(gate_market),
            credential_ref=f"credential-{credential_id}",
            supports_account_reads=True,
            supports_order_reads=True,
            supports_fill_reads=True,
        )
        read_client = build_gate_private_read_client(
            credential=GatePrivateCredential(api_key, api_secret, CapabilityEnvironment.TESTNET),
            profile=read_profile,
            timestamp_provider=lambda: int(scopes.observed_at.timestamp()),
        )
        with get_db_connection() as connection:
            result = read_and_settle_gate_testnet_order_fills_caller_owned(
                connection,
                receipt,
                read_client=read_client,
                observed_at=scopes.observed_at,
                ledger_scope=scopes.ledger_scope,
                persistence_scope=scopes.persistence_scope,
            )
            connection.commit()
        return jsonify({
            "status": result.disposition,
            "environment": "TESTNET",
            "network_access": True,
            "writes_enabled": False,
            "live_enabled": False,
            "order": {
                "market_type": receipt.market_type.value,
                "account_scope": receipt.account_scope,
                "instrument_id": receipt.instrument_id,
                "exchange_order_id": receipt.exchange_order_id,
                "client_order_id": receipt.client_order_id,
                "state": receipt.raw_state,
            },
            "fills": [
                {
                    "venue_fill_id": fill.venue_fill_id,
                    "exchange_order_id": fill.exchange_order_id,
                    "quantity": str(fill.quantity),
                    "price": str(fill.price),
                    "fee_asset": fill.fee_asset,
                    "fee_amount": None if fill.fee_amount is None else str(fill.fee_amount),
                }
                for fill in result.fills
            ],
            "ledger": {
                "disposition": result.ledger.disposition,
                "fills": [item.fill_event_id for item in result.ledger.fills],
            },
        }), 200
    except (KeyError, ValueError, TypeError, GateTestnetNetworkSettlementError):
        return jsonify({"status": "REJECTED", "code": "TESTNET_FILL_SETTLEMENT_INVALID", "live_enabled": False}), 422
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "TESTNET_FILL_SETTLEMENT_UNAVAILABLE", "live_enabled": False}), 503


__all__ = ["blp"]
