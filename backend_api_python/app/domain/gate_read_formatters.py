"""Offline Gate read-response formatters.

The formatter boundary accepts only an already-retrieved, sanitized mapping.
It performs no HTTP I/O and never accepts credentials.  Ambiguous or
incomplete payloads fail closed before they can become account/market facts.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence, Tuple

from app.domain.gate_vertical_read_contracts import (
    GateBalanceFact,
    GateFillFact,
    GateInstrumentRuleSnapshot,
    GateMarginMode,
    GatePositionFact,
    GatePositionSide,
    GateOrderFact,
    GateOrderSide,
    GateOrderStatus,
    GateVerticalContractError,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType


GATE_READ_FORMATTER_VERSION = "gate-read-format-v1"


class GateReadPayloadError(GateVerticalContractError):
    """A payload cannot be safely normalized to a typed read fact."""


class GateReadErrorKind(str, Enum):
    AUTH_OR_PERMISSION = "AUTH_OR_PERMISSION"
    RATE_LIMIT = "RATE_LIMIT"
    TEMPORARY = "TEMPORARY"
    INVALID_RESPONSE = "INVALID_RESPONSE"


def classify_gate_response_error(status_code: int, payload: Mapping[str, Any] | None = None) -> GateReadErrorKind:
    """Classify an HTTP response without exposing raw payload or secrets."""
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise GateReadPayloadError("status_code must be an integer")
    if status_code in (401, 403):
        return GateReadErrorKind.AUTH_OR_PERMISSION
    if status_code == 429:
        return GateReadErrorKind.RATE_LIMIT
    if status_code >= 500 or status_code in (408, 425):
        return GateReadErrorKind.TEMPORARY
    if status_code < 200 or status_code >= 300:
        return GateReadErrorKind.INVALID_RESPONSE
    if payload is not None and not isinstance(payload, Mapping):
        raise GateReadPayloadError("payload must be a mapping")
    return GateReadErrorKind.INVALID_RESPONSE


def _rows(payload: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    rows = payload.get("data") if isinstance(payload, Mapping) and "data" in payload else payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise GateReadPayloadError(f"{field_name} payload must be a list")
    result = tuple(row for row in rows if isinstance(row, Mapping))
    if len(result) != len(rows):
        raise GateReadPayloadError(f"{field_name} payload contains a non-object row")
    return result


def _required(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    raise GateReadPayloadError(f"missing required read field: {names[0]}")


def normalize_gate_balances(
    payload: Any,
    *,
    market_type: AssetMarketType,
    account_scope: str,
    valuation_ccy: str,
    observed_at: datetime,
    source_event_prefix: str,
    evidence_hash_prefix: str,
) -> Tuple[GateBalanceFact, ...]:
    rows = _rows(payload, "balances")
    result = []
    for index, row in enumerate(rows):
        result.append(GateBalanceFact(
            "gate", market_type, account_scope,
            str(_required(row, "asset", "currency")).upper(),
            _required(row, "total", "balance"),
            _required(row, "available", "available_balance"),
            _required(row, "locked", "locked_balance"),
            valuation_ccy.upper(), observed_at,
            f"{source_event_prefix}:{index}", f"{evidence_hash_prefix}:{index}",
        ))
    return tuple(result)


def normalize_gate_positions(
    payload: Any,
    *,
    market_type: AssetMarketType,
    account_scope: str,
    observed_at: datetime,
    source_event_prefix: str,
) -> Tuple[GatePositionFact, ...]:
    rows = _rows(payload, "positions")
    if market_type.value != "perpetual":
        raise GateReadPayloadError("positions require the perpetual market profile")
    result = []
    for index, row in enumerate(rows):
        side = str(_required(row, "side", "position_side")).lower()
        try:
            typed_side = GatePositionSide(side)
            margin_mode = GateMarginMode(str(_required(row, "margin_mode", "marginMode")).lower())
        except ValueError as exc:
            raise GateReadPayloadError("position side or margin mode is unsupported") from exc
        result.append(GatePositionFact(
            "gate", market_type, account_scope,
            str(_required(row, "instrument_id", "contract", "symbol")), typed_side,
            _required(row, "quantity", "size"), _required(row, "average_entry_price", "entry_price"),
            _required(row, "mark_price", "mark"), _required(row, "leverage"), margin_mode,
            observed_at, f"{source_event_prefix}:{index}",
        ))
    return tuple(result)


def normalize_gate_instruments(payload: Any, *, market_type: AssetMarketType, observed_at: datetime, rule_version: str) -> Tuple[GateInstrumentRuleSnapshot, ...]:
    rows = _rows(payload, "instruments")
    result = []
    for row in rows:
        result.append(GateInstrumentRuleSnapshot(
            "gate", market_type, str(_required(row, "instrument_id", "name", "contract", "symbol")),
            _required(row, "tick_size", "order_price_round"),
            _required(row, "quantity_step", "order_size_increment"),
            _required(row, "minimum_quantity", "order_size_min"),
            _required(row, "minimum_notional", "min_notional"), rule_version, observed_at,
        ))
    return tuple(result)


def normalize_gate_orders(
    payload: Any,
    *,
    market_type: AssetMarketType,
    account_scope: str,
    observed_at: datetime,
    source_event_prefix: str,
) -> Tuple[GateOrderFact, ...]:
    rows = _rows(payload, "orders")
    result = []
    for index, row in enumerate(rows):
        try:
            side = GateOrderSide(str(_required(row, "side")).lower())
            raw_status = str(_required(row, "status", "state")).lower()
            finish_reason = row.get("finish_as", row.get("finish_reason"))
            if finish_reason not in (None, ""):
                finish_reason = str(finish_reason).lower()
            if raw_status == "open":
                status = GateOrderStatus.OPEN
            elif raw_status == "partially_filled":
                status = GateOrderStatus.PARTIALLY_FILLED
            elif raw_status in {"finished", "closed"}:
                if finish_reason in {"filled", "succeeded"}:
                    status = GateOrderStatus.FILLED
                elif finish_reason in {"cancelled", "liquidated", "liquidate_cancelled", "ioc", "poc", "fok", "stp", "small", "depth_not_enough", "trader_not_enough", "reduce_only", "position_closed", "reduce_out", "auto_deleveraged"}:
                    status = GateOrderStatus.CANCELLED
                else:
                    raise ValueError("finished Gate order lacks an authoritative finish reason")
            elif raw_status == "cancelled":
                status = GateOrderStatus.CANCELLED
            elif raw_status == "rejected":
                status = GateOrderStatus.REJECTED
            else:
                raise ValueError("unsupported Gate order status")
        except ValueError as exc:
            raise GateReadPayloadError("order side or status is unsupported") from exc
        result.append(GateOrderFact(
            "gate", market_type, account_scope,
            str(_required(row, "instrument_id", "contract", "symbol")),
            str(_required(row, "exchange_order_id", "id")),
            (str(row["client_order_id"]) if row.get("client_order_id") not in (None, "") else None),
            side, status, _required(row, "quantity", "size"),
            _required(row, "filled_quantity", "filled_size", "filled") ,
            (row.get("average_fill_price", row.get("avg_deal_price")) or None),
            observed_at, f"{source_event_prefix}:{index}", raw_status, finish_reason,
        ))
    return tuple(result)


def normalize_gate_fills(
    payload: Any,
    *,
    market_type: AssetMarketType,
    account_scope: str,
    observed_at: datetime,
    source_event_prefix: str,
) -> Tuple[GateFillFact, ...]:
    rows = _rows(payload, "fills")
    result = []
    for index, row in enumerate(rows):
        try:
            side = GateOrderSide(str(_required(row, "side")).lower())
        except ValueError as exc:
            raise GateReadPayloadError("fill side is unsupported") from exc
        result.append(GateFillFact(
            "gate", market_type, account_scope,
            str(_required(row, "instrument_id", "contract", "symbol")),
            str(_required(row, "exchange_order_id", "order_id")),
            str(_required(row, "venue_fill_id", "trade_id", "id")),
            side, _required(row, "quantity", "size"), _required(row, "price", "fill_price"),
            (str(row["fee_asset"]).upper() if row.get("fee_asset") not in (None, "") else None),
            row.get("fee_amount", row.get("fee")), observed_at, f"{source_event_prefix}:{index}",
        ))
    return tuple(result)


__all__ = ["GATE_READ_FORMATTER_VERSION", "GateReadErrorKind", "GateReadPayloadError", "classify_gate_response_error", "normalize_gate_balances", "normalize_gate_fills", "normalize_gate_instruments", "normalize_gate_orders", "normalize_gate_positions"]
