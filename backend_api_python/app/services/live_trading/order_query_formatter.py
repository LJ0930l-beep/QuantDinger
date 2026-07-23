"""Read-only Binance order-query formatting for the future recovery boundary.

This module performs no HTTP calls and makes no recovery decision.  Callers
must supply the resolved credential/account scope, then pass the raw response
from an already completed read-only adapter query.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.venue_order_contracts import (
    FillFee,
    NormalizedOrderQuery,
    OrderQueryRequest,
    VenueContractError,
    VenueFillIdentity,
    VenueOrderScope,
    VenueQueryFailureKind,
    found_order_query_result,
    query_failure_result,
)
from app.domain.decimal_values import FeeAmount, Price, Quantity


_BINANCE_ORDER_STATE_MAP = {
    "NEW": "SUBMITTED",
    "PARTIALLY_FILLED": "PARTIALLY_FILLED",
    "FILLED": "FILLED",
    "CANCELED": "CANCELLED",
    "EXPIRED": "CANCELLED",
    "EXPIRED_IN_MATCH": "CANCELLED",
    "REJECTED": "REJECTED",
}


def classify_binance_query_http_failure(
    *,
    status_code: int | None = None,
    timed_out: bool = False,
) -> VenueQueryFailureKind:
    """Classify only evidence that is unambiguous at this formatting boundary.

    Binance's legacy adapter raises broad ``LiveTradingError`` instances, so an
    unparsed 4xx body is deliberately ``INVALID_RESPONSE`` instead of
    ``NOT_FOUND``. A future adapter can map a documented venue error code to
    ``NOT_FOUND`` only after preserving that code as an auditable fact.
    """

    if timed_out:
        return VenueQueryFailureKind.TIMEOUT
    if status_code == 429:
        return VenueQueryFailureKind.RATE_LIMITED
    if status_code is not None and status_code >= 500:
        return VenueQueryFailureKind.SERVER_ERROR
    if status_code in {401, 403}:
        return VenueQueryFailureKind.AUTH_OR_PERMISSION
    return VenueQueryFailureKind.INVALID_RESPONSE


def format_binance_order_query(
    request: OrderQueryRequest,
    payload: Mapping[str, Any] | object,
    *,
    response_account_scope: str,
) -> NormalizedOrderQuery:
    """Validate a mocked or completed Binance read response, fail closed.

    A missing identifier, missing/unknown status, non-mapping payload, or
    symbol/account-scope mismatch produces ``INVALID_RESPONSE`` rather than
    pretending the order was absent.  API secrets are never accepted here.
    """

    if not isinstance(payload, Mapping):
        return query_failure_result(request, VenueQueryFailureKind.INVALID_RESPONSE)
    exchange_order_id = str(payload.get("orderId") or "")
    client_order_id = str(payload.get("clientOrderId") or "")
    raw_state = str(payload.get("status") or "").strip().upper()
    normalized_state = _BINANCE_ORDER_STATE_MAP.get(raw_state)
    if not exchange_order_id or not raw_state or normalized_state is None:
        return query_failure_result(request, VenueQueryFailureKind.INVALID_RESPONSE)
    payload_symbol = str(payload.get("symbol") or "").strip().upper()
    if payload_symbol != str(request.instrument).strip().upper():
        return query_failure_result(request, VenueQueryFailureKind.INVALID_RESPONSE)
    try:
        return found_order_query_result(
            request,
            response_venue="binance",
            response_market_type=request.market_type,
            response_account_scope=response_account_scope,
            response_instrument=request.instrument,
            exchange_order_id=exchange_order_id,
            client_order_id=client_order_id,
            normalized_state=normalized_state,
            raw_state=raw_state,
        )
    except ValueError:
        return query_failure_result(request, VenueQueryFailureKind.INVALID_RESPONSE)


def format_binance_fill_identity(
    expected_order_scope: VenueOrderScope,
    payload: Mapping[str, Any] | object,
) -> VenueFillIdentity:
    """Extract a stable Binance trade ID into the pure immutable fill contract.

    ``id`` is the venue trade/fill identifier.  This intentionally rejects a
    missing ID rather than manufacturing identity from timestamp, price, or
    quantity. Numeric venue payload fields must remain strings (or approved
    Decimal inputs); a binary float from an adapter is rejected by PR-01.
    """

    if not isinstance(payload, Mapping):
        raise VenueContractError("invalid Binance fill response")
    if str(payload.get("symbol") or "").strip().upper() != expected_order_scope.instrument.upper():
        raise VenueContractError("fill scope mismatch")
    if str(payload.get("orderId") or "") != expected_order_scope.exchange_order_id:
        raise VenueContractError("fill scope mismatch")
    venue_fill_id = str(payload.get("id") or "")
    try:
        fee = FillFee(
            str(payload.get("commissionAsset") or ""),
            FeeAmount(payload.get("commission")),
        )
        return VenueFillIdentity.from_venue_fact(
            expected_order_scope,
            venue="binance",
            market_type=expected_order_scope.market_type,
            account_scope=expected_order_scope.account_scope,
            instrument=expected_order_scope.instrument,
            exchange_order_id=expected_order_scope.exchange_order_id,
            venue_fill_id=venue_fill_id,
            quantity=Quantity(payload.get("qty")),
            price=Price(payload.get("price")),
            fees=(fee,),
        )
    except VenueContractError:
        raise
    except (TypeError, ValueError) as exc:
        raise VenueContractError("invalid Binance fill response") from exc
