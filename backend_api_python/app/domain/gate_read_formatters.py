"""Offline Gate read-response formatters.

The formatter boundary accepts only an already-retrieved, sanitized mapping.
It performs no HTTP I/O and never accepts credentials.  Ambiguous or
incomplete payloads fail closed before they can become account/market facts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping, Sequence, Tuple

from app.domain.gate_vertical_read_contracts import (
    GateAccountBookFact,
    GateAccountBookType,
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


def _decimal_value(value: Any, field_name: str, *, non_zero: bool = False) -> Decimal:
    """Parse a Gate numeric fact without admitting binary floats."""
    if isinstance(value, (float, bool)):
        raise GateReadPayloadError(f"{field_name} rejects float/bool input")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateReadPayloadError(f"{field_name} is not decimal") from exc
    if not parsed.is_finite() or (non_zero and parsed == 0):
        raise GateReadPayloadError(f"{field_name} must be finite" + (" and non-zero" if non_zero else ""))
    return parsed


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
        # Gate's Spot `/spot/accounts` response intentionally exposes only
        # `available` and `locked`; unlike futures account rows it does not
        # include a `total`/`balance` field.  Derive the total from those two
        # documented components without introducing a guessed value.  The
        # Decimal parser keeps this boundary closed to binary floats and
        # non-finite payloads.
        available = _decimal_value(_required(row, "available", "available_balance"), "balance available")
        locked = _decimal_value(_required(row, "locked", "locked_balance"), "balance locked")
        total = row.get("total", row.get("balance"))
        if total in (None, ""):
            total = available + locked
        result.append(GateBalanceFact(
            "gate", market_type, account_scope,
            str(_required(row, "asset", "currency")).upper(),
            total,
            available,
            locked,
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
        # Gate returns one row for every contract, including zero-size rows.
        # Those rows are not open positions and do not carry valid entry or
        # leverage facts, so they must not be coerced into a fake position.
        signed_size = _decimal_value(_required(row, "quantity", "size"), "position size")
        if signed_size == 0:
            continue
        raw_side = row.get("side", row.get("position_side"))
        if raw_side in (None, ""):
            # Gate perpetual positions encode direction in the signed size.
            # Preserve an explicit side when a venue payload supplies one.
            raw_side = "long" if signed_size > 0 else "short"
        side = str(raw_side).lower()
        raw_margin_mode = row.get("margin_mode", row.get("pos_margin_mode"))
        try:
            typed_side = GatePositionSide(side)
            margin_mode = GateMarginMode(str(_required({"margin_mode": raw_margin_mode}, "margin_mode")).lower())
        except ValueError as exc:
            raise GateReadPayloadError("position side or margin mode is unsupported") from exc
        raw_leverage = row.get("leverage")
        if raw_leverage in (None, "", "0", "0.0", 0, Decimal("0")):
            # Gate reports leverage=0 for cross margin.  `lever` (or the
            # legacy cross_leverage_limit) is the authoritative positive
            # leverage fact for that position; never invent a default.
            raw_leverage = row.get("lever", row.get("cross_leverage_limit"))
        result.append(GatePositionFact(
            "gate", market_type, account_scope,
            str(_required(row, "instrument_id", "contract", "symbol")), typed_side,
            abs(signed_size), _required(row, "average_entry_price", "entry_price"),
            _required(row, "mark_price", "mark"), _required({"leverage": raw_leverage}, "leverage"), margin_mode,
            observed_at, f"{source_event_prefix}:{index}",
            row.get("unrealized_pnl", row.get("unrealised_pnl", row.get("unrealized_pl", "0"))),
            row.get("realized_pnl", row.get("realised_pnl", "0")),
            row.get("funding_pnl", row.get("funding_fee", "0")),
        ))
    return tuple(result)


def normalize_gate_instruments(payload: Any, *, market_type: AssetMarketType, observed_at: datetime, rule_version: str) -> Tuple[GateInstrumentRuleSnapshot, ...]:
    rows = _rows(payload, "instruments")
    result = []
    for row in rows:
        # Gate's official Spot endpoint returns decimal *scales* as integer
        # precision fields, while its Futures endpoint returns explicit
        # increments.  Convert only these documented representations; missing
        # rules remain a hard failure rather than an invented default.
        tick_size = row.get("tick_size")
        if tick_size in (None, ""):
            tick_size = row.get("order_price_round")
        if tick_size in (None, "") and "precision" in row:
            precision = row["precision"]
            if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
                raise GateReadPayloadError("instrument price precision is invalid")
            tick_size = Decimal("1").scaleb(-precision)
        quantity_step = row.get("quantity_step")
        if quantity_step in (None, ""):
            quantity_step = row.get("order_size_increment")
        if quantity_step in (None, "") and "amount_precision" in row:
            amount_precision = row["amount_precision"]
            if isinstance(amount_precision, bool) or not isinstance(amount_precision, int) or amount_precision < 0:
                raise GateReadPayloadError("instrument quantity precision is invalid")
            quantity_step = Decimal("1").scaleb(-amount_precision)
        if quantity_step in (None, "") and "enable_decimal" in row:
            enable_decimal = row["enable_decimal"]
            if not isinstance(enable_decimal, bool):
                raise GateReadPayloadError("instrument decimal-size capability is invalid")
            # Gate futures contracts with decimal sizing disabled accept
            # integer contract quantities.  Decimal-enabled contracts still
            # require an explicit increment; a minimum size is not silently
            # promoted to a step.
            if not enable_decimal:
                quantity_step = Decimal("1")
        minimum_notional = row.get("minimum_notional", row.get("min_notional", row.get("min_quote_amount")))
        if minimum_notional in (None, "") and market_type is AssetMarketType.PERPETUAL:
            # Gate perpetual contracts express their minimum in contracts via
            # order_size_min; the official contract payload has no separate
            # minimum-notional field.  Zero here means "no independent venue
            # fact supplied", never a guessed quote threshold.
            minimum_notional = Decimal("0")
        result.append(GateInstrumentRuleSnapshot(
            "gate", market_type, str(_required(row, "instrument_id", "name", "contract", "symbol", "id")),
            _required({"tick_size": tick_size}, "tick_size"),
            _required({"quantity_step": quantity_step}, "quantity_step"),
            _required(row, "minimum_quantity", "order_size_min", "min_base_amount"),
            _required({"minimum_notional": minimum_notional}, "minimum_notional"), rule_version, observed_at,
            row.get("contract_size", row.get("quanto_multiplier")),
            row.get("leverage_min"), row.get("leverage_max"),
        ))
    return tuple(result)


def normalize_gate_account_book(
    payload: Any,
    *,
    market_type: AssetMarketType,
    account_scope: str,
    observed_at: datetime,
    source_event_prefix: str,
) -> Tuple[GateAccountBookFact, ...]:
    """Normalize Gate futures account-book rows without losing fee/funding type."""

    rows = _rows(payload, "account_book")
    if market_type is not AssetMarketType.PERPETUAL:
        raise GateReadPayloadError("account book requires the perpetual market profile")
    result = []
    for index, row in enumerate(rows):
        try:
            change_type = GateAccountBookType(str(_required(row, "type")).lower())
        except ValueError as exc:
            raise GateReadPayloadError("account book change type is unsupported") from exc
        raw_time = _required(row, "time", "occurred_at")
        if isinstance(raw_time, bool):
            raise GateReadPayloadError("account book time is invalid")
        try:
            epoch = Decimal(str(raw_time))
            if not epoch.is_finite():
                raise ValueError("non-finite timestamp")
            occurred_at = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        except (InvalidOperation, TypeError, ValueError, OverflowError, OSError) as exc:
            raise GateReadPayloadError("account book time is invalid") from exc
        event_id = str(_required(row, "id", "event_id"))
        result.append(GateAccountBookFact(
            "gate", market_type, account_scope, event_id, change_type,
            _required(row, "change"), _required(row, "balance"), occurred_at,
            observed_at,
            (str(row["contract"]) if row.get("contract") not in (None, "") else None),
            (str(row["trade_id"]) if row.get("trade_id") not in (None, "") else None),
            (str(row["text"]) if row.get("text") not in (None, "") else None),
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
    expanded = []
    for row in rows:
        nested = row.get("orders")
        if nested is None:
            expanded.append(row)
        elif not isinstance(nested, Sequence) or isinstance(nested, (str, bytes)):
            raise GateReadPayloadError("orders envelope contains an invalid orders list")
        else:
            if any(not isinstance(item, Mapping) for item in nested):
                raise GateReadPayloadError("orders envelope contains a non-object row")
            expanded.extend(nested)
    rows = tuple(expanded)
    result = []
    for index, row in enumerate(rows):
        try:
            raw_size = _required(row, "quantity", "size", "amount")
            signed_size = _decimal_value(raw_size, "order size", non_zero=True)
            raw_side = row.get("side")
            if raw_side in (None, ""):
                side = GateOrderSide.BUY if signed_size > 0 else GateOrderSide.SELL
            else:
                side = GateOrderSide(str(raw_side).lower())
            quantity = abs(signed_size)
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
        except (ValueError, GateReadPayloadError) as exc:
            if isinstance(exc, GateReadPayloadError):
                raise
            raise GateReadPayloadError("order side or status is unsupported") from exc
        filled_raw = row.get("filled_quantity", row.get("filled_size", row.get("filled_amount", row.get("filled", row.get("filled_total")))))
        if filled_raw in (None, ""):
            left_raw = row.get("left")
            filled = quantity - abs(_decimal_value(left_raw, "order left")) if left_raw not in (None, "") else Decimal("0")
        else:
            filled = _decimal_value(filled_raw, "filled quantity")
        result.append(GateOrderFact(
            "gate", market_type, account_scope,
            str(_required(row, "instrument_id", "contract", "currency_pair", "symbol")),
            str(_required(row, "exchange_order_id", "id")),
            (str(row["client_order_id"]) if row.get("client_order_id") not in (None, "") else None),
            side, status, quantity,
            filled,
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
    default_fee_asset: str | None = None,
) -> Tuple[GateFillFact, ...]:
    rows = _rows(payload, "fills")
    result = []
    for index, row in enumerate(rows):
        try:
            raw_size = _required(row, "quantity", "size", "amount")
            signed_size = _decimal_value(raw_size, "fill size", non_zero=True)
            raw_side = row.get("side")
            side = GateOrderSide(str(raw_side).lower()) if raw_side not in (None, "") else (GateOrderSide.BUY if signed_size > 0 else GateOrderSide.SELL)
        except GateReadPayloadError:
            raise
        except ValueError as exc:
            raise GateReadPayloadError("fill side is unsupported") from exc
        fee_asset = row.get("fee_asset", row.get("fee_currency"))
        if fee_asset in (None, "") and default_fee_asset not in (None, "") and row.get("fee_amount", row.get("fee")) not in (None, "", "0", 0):
            fee_asset = default_fee_asset
        result.append(GateFillFact(
            "gate", market_type, account_scope,
            str(_required(row, "instrument_id", "contract", "currency_pair", "symbol")),
            str(_required(row, "exchange_order_id", "order_id")),
            str(_required(row, "venue_fill_id", "trade_id", "id")),
            side, abs(signed_size), _required(row, "price", "fill_price"),
            (str(fee_asset).upper() if fee_asset not in (None, "") else None),
            row.get("fee_amount", row.get("fee")), observed_at, f"{source_event_prefix}:{index}",
        ))
    return tuple(result)


__all__ = ["GATE_READ_FORMATTER_VERSION", "GateReadErrorKind", "GateReadPayloadError", "classify_gate_response_error", "normalize_gate_account_book", "normalize_gate_balances", "normalize_gate_fills", "normalize_gate_instruments", "normalize_gate_orders", "normalize_gate_positions"]
