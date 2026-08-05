"""Authenticated PAPER order persistence through the canonical admission chain.

The route creates durable PAPER order facts only after Canonical Entry V2,
Hard Risk, reservation, and admission outbox have completed on the same
caller-owned transaction.  It never creates a venue client or submits an
exchange order.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any

from flask import jsonify, request

from app.domain.paper_execution_contracts import (
    PaperExecutionContractError,
    PaperExecutionFill,
    PaperExecutionOrder,
    PaperExecutionOrderEvent,
    PaperExecutionEventType,
    PaperExecutionStatus,
)
from app.domain.runtime_entry_admission_contracts import RuntimeEntryAdmissionDisposition
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.paper_execution_repository import (
    PaperExecutionConflict,
    PaperExecutionRepository,
    PaperExecutionRepositoryError,
)
from app.services.runtime_entry_admission_http_service import (
    RuntimeEntryAdmissionApiError,
    admit_runtime_entry_payload_caller_owned,
    result_to_public_dict,
)
from app.utils.auth import get_current_user_id, login_required
from app.utils.db import get_db_connection


blp = Blueprint("paper_execution", __name__)


def _text(payload: dict[str, Any], name: str, default: str | None = None) -> str:
    value = payload.get(name, default)
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise RuntimeEntryAdmissionApiError(f"{name} must be canonical ASCII text")
    return value


def _public_result(runtime_result: Any, paper_result: Any | None, order: PaperExecutionOrder | None) -> dict[str, Any]:
    body = result_to_public_dict(runtime_result)
    body.update({
        "paper": True,
        "network_access": False,
        "live_enabled": False,
        "paper_order": None if order is None else {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "market_type": order.market_type,
            "side": order.side,
            "order_type": order.order_type,
            "quantity": format(order.quantity, "f"),
            "limit_price": None if order.limit_price is None else format(order.limit_price, "f"),
            "status": order.status.value,
            "disposition": None if paper_result is None else paper_result.disposition.value,
        },
    })
    return body


@blp.route("/api/quant/paper/order", methods=["POST"])
@login_required
def submit_paper_order():
    """Persist one PAPER order after canonical admission and hard risk."""

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422

    try:
        if str(payload.get("mode", "PAPER")).upper() != "PAPER":
            raise RuntimeEntryAdmissionApiError("PAPER endpoint requires mode=PAPER")
        execution_kind = str(payload.get("execution_kind", "")).upper()
        if execution_kind not in {"MARKET", "LIMIT"}:
            raise RuntimeEntryAdmissionApiError("PAPER execution supports MARKET and LIMIT only")
        action = str(payload.get("action", "")).upper()
        if action == "CANCEL":
            raise RuntimeEntryAdmissionApiError("PAPER order creation does not accept CANCEL")

        user_id = int(get_current_user_id())
        instrument_id = _text(payload, "instrument_id").upper()
        market_type = _text(payload, "market_type").lower()
        side = _text(payload, "side").upper()
        order_type = execution_kind
        quantity = Decimal(str(payload.get("quantity")))
        limit_price = None if order_type == "MARKET" else Decimal(str(payload.get("limit_price")))
        occurred_at = payload.get("occurred_at")
        if not isinstance(occurred_at, str):
            raise RuntimeEntryAdmissionApiError("occurred_at is required")
        try:
            created_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeEntryAdmissionApiError("occurred_at must be an ISO-8601 UTC string") from exc
        if created_at.tzinfo is None or created_at.utcoffset() != timezone.utc.utcoffset(created_at):
            raise RuntimeEntryAdmissionApiError("occurred_at must use a zero UTC offset")
        created_at = created_at.astimezone(timezone.utc)

        with get_db_connection() as connection:
            runtime_result, _graph = admit_runtime_entry_payload_caller_owned(
                connection,
                payload,
                tenant_id=user_id,
                actor_id=str(user_id),
            )

            if runtime_result.disposition is RuntimeEntryAdmissionDisposition.DISABLED:
                return jsonify(_public_result(runtime_result, None, None)), 200

            paper_result = None
            order = None
            if runtime_result.disposition in {
                RuntimeEntryAdmissionDisposition.CREATED,
                RuntimeEntryAdmissionDisposition.REPLAYED,
            }:
                if runtime_result.admission is None or not runtime_result.admission.economic_order_id:
                    raise RuntimeEntryAdmissionApiError("PAPER admission did not return an economic order")
                order = PaperExecutionOrder(
                    order_id=runtime_result.admission.economic_order_id,
                    user_id=user_id,
                    idempotency_key=_text(payload, "idempotency_key"),
                    request_fingerprint=runtime_result.admission.request_fingerprint,
                    market=str(payload.get("market", "gate")).lower(),
                    symbol=instrument_id,
                    market_type=market_type,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    limit_price=limit_price,
                    status=PaperExecutionStatus.SUBMITTED,
                    created_at=created_at,
                )
                repository = PaperExecutionRepository()
                paper_result = repository.persist_order(connection, order)
                event = PaperExecutionOrderEvent(
                    event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"paper-submitted:{order.order_id}")),
                    order_id=order.order_id,
                    event_seq=1,
                    event_type=PaperExecutionEventType.SUBMITTED,
                    occurred_at=order.created_at,
                )
                repository.append_order_event(connection, event)

            connection.commit()
            return jsonify(_public_result(runtime_result, paper_result, order)), 200
    except RuntimeEntryAdmissionApiError as exc:
        return jsonify({"status": "REJECTED", "code": "PAPER_ORDER_CONTRACT_INVALID", "message": str(exc), "live_enabled": False}), 422
    except (PaperExecutionConflict, PaperExecutionRepositoryError) as exc:
        return jsonify({"status": "CONFLICT", "code": "PAPER_ORDER_CONFLICT", "message": str(exc), "live_enabled": False}), 409
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        return jsonify({"status": "REJECTED", "code": "PAPER_ORDER_CONTRACT_INVALID", "message": str(exc), "live_enabled": False}), 422
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "PAPER_ORDER_UNAVAILABLE", "live_enabled": False}), 503


def _utc_timestamp(payload: dict[str, Any], name: str) -> datetime:
    value = payload.get(name)
    if not isinstance(value, str):
        raise RuntimeEntryAdmissionApiError(f"{name} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeEntryAdmissionApiError(f"{name} must be an ISO-8601 UTC string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RuntimeEntryAdmissionApiError(f"{name} must use a zero UTC offset")
    return parsed.astimezone(timezone.utc)


def _decimal_field(payload: dict[str, Any], name: str) -> Decimal:
    value = payload.get(name)
    if isinstance(value, (bool, float)) or value is None:
        raise RuntimeEntryAdmissionApiError(f"{name} must be a decimal string")
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise RuntimeEntryAdmissionApiError(f"{name} must be a decimal string") from exc


@blp.route("/api/quant/paper/order/<order_id>/fill", methods=["POST"])
@login_required
def append_paper_fill(order_id: str):
    """Append a caller-supplied PAPER fill without contacting a venue.

    This endpoint is a deterministic rehearsal seam: the fill identity and
    occurred time are explicit inputs, and the authenticated user is checked
    while the order row is locked.  It cannot create a Gate request or enable
    live trading.
    """

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422
    try:
        user_id = int(get_current_user_id())
        fill = PaperExecutionFill(
            fill_id=_text(payload, "fill_id"),
            order_id=_text({"order_id": order_id}, "order_id"),
            quantity=_decimal_field(payload, "quantity"),
            price=_decimal_field(payload, "price"),
            fee_amount=_decimal_field(payload, "fee_amount"),
            fee_asset=_text(payload, "fee_asset").upper(),
            occurred_at=_utc_timestamp(payload, "occurred_at"),
        )
        with get_db_connection() as connection:
            repository = PaperExecutionRepository()
            disposition = repository.append_fill(connection, fill, user_id=user_id)
            orders = repository.read_orders(connection, user_id=user_id, limit=500)
            order = next((candidate for candidate in orders if candidate.order_id == fill.order_id), None)
            if order is None:
                raise PaperExecutionRepositoryError("paper fill order could not be re-read")
            connection.commit()
            return jsonify({
                "status": "OK",
                "live_enabled": False,
                "network_access": False,
                "disposition": disposition.value,
                "fill": {
                    "fill_id": fill.fill_id,
                    "order_id": fill.order_id,
                    "quantity": format(fill.quantity, "f"),
                    "price": format(fill.price, "f"),
                    "fee_amount": format(fill.fee_amount, "f"),
                    "fee_asset": fill.fee_asset,
                    "occurred_at": fill.occurred_at.isoformat(),
                },
                "order": {
                    "status": order.status.value,
                    "fill_quantity": format(order.fill_quantity, "f"),
                    "fill_price": None if order.fill_price is None else format(order.fill_price, "f"),
                    "fee_amount": format(order.fee_amount, "f"),
                    "fee_asset": order.fee_asset,
                },
            }), 200
    except PaperExecutionConflict as exc:
        return jsonify({"status": "CONFLICT", "code": "PAPER_FILL_CONFLICT", "message": str(exc), "live_enabled": False}), 409
    except (PaperExecutionContractError, RuntimeEntryAdmissionApiError, PaperExecutionRepositoryError) as exc:
        return jsonify({"status": "REJECTED", "code": "PAPER_FILL_CONTRACT_INVALID", "message": str(exc), "live_enabled": False}), 422
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        return jsonify({"status": "REJECTED", "code": "PAPER_FILL_CONTRACT_INVALID", "message": str(exc), "live_enabled": False}), 422
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "PAPER_FILL_UNAVAILABLE", "live_enabled": False}), 503


@blp.route("/api/quant/paper/order/<order_id>/cancel", methods=["POST"])
@login_required
def cancel_paper_order(order_id: str):
    """Record a deterministic PAPER cancellation without venue access."""

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422
    try:
        user_id = int(get_current_user_id())
        canonical_order_id = _text({"order_id": order_id}, "order_id")
        occurred_at = _utc_timestamp(payload, "occurred_at")
        event = PaperExecutionOrderEvent(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"paper-cancelled:{canonical_order_id}")),
            order_id=canonical_order_id,
            event_seq=2,
            event_type=PaperExecutionEventType.CANCELLED,
            occurred_at=occurred_at,
        )
        with get_db_connection() as connection:
            repository = PaperExecutionRepository()
            orders = repository.read_orders(connection, user_id=user_id, limit=500)
            order = next((candidate for candidate in orders if candidate.order_id == event.order_id), None)
            if order is None:
                raise PaperExecutionRepositoryError("paper cancel references an unknown order")
            if order.status is PaperExecutionStatus.FILLED:
                raise PaperExecutionConflict("filled PAPER order cannot be cancelled")
            disposition = repository.append_order_event(connection, event, user_id=user_id)
            updated_orders = repository.read_orders(connection, user_id=user_id, limit=500)
            updated = next((candidate for candidate in updated_orders if candidate.order_id == event.order_id), None)
            if updated is None:
                raise PaperExecutionRepositoryError("paper cancellation could not be re-read")
            connection.commit()
            return jsonify({
                "status": "OK",
                "live_enabled": False,
                "network_access": False,
                "disposition": disposition.value,
                "order": {
                    "order_id": updated.order_id,
                    "status": updated.status.value,
                    "fill_quantity": format(updated.fill_quantity, "f"),
                    "fill_price": None if updated.fill_price is None else format(updated.fill_price, "f"),
                },
            }), 200
    except PaperExecutionConflict as exc:
        return jsonify({"status": "CONFLICT", "code": "PAPER_CANCEL_CONFLICT", "message": str(exc), "live_enabled": False}), 409
    except (PaperExecutionContractError, RuntimeEntryAdmissionApiError, PaperExecutionRepositoryError) as exc:
        return jsonify({"status": "REJECTED", "code": "PAPER_CANCEL_CONTRACT_INVALID", "message": str(exc), "live_enabled": False}), 422
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        return jsonify({"status": "REJECTED", "code": "PAPER_CANCEL_CONTRACT_INVALID", "message": str(exc), "live_enabled": False}), 422
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "PAPER_CANCEL_UNAVAILABLE", "live_enabled": False}), 503


__all__ = ["blp"]
