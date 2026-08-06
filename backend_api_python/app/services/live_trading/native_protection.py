"""Exchange-native protective orders for filled derivative entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Tuple

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.symbols import (
    to_binance_futures_symbol,
    to_bitget_um_symbol,
    to_bybit_symbol,
    to_gate_currency_pair,
    to_htx_contract_code,
    to_okx_swap_inst_id,
)


class NativeProtectionDisabledError(LiveTradingError):
    """Raised when the retired native-protection entry is reached.

    Native exchange protection orders are not part of the canonical admission
    path.  Keeping a typed error at this boundary makes every legacy caller
    fail closed before it can import a venue client or invoke an exchange API.
    """


@dataclass(frozen=True)
class NativeProtectionRequest:
    symbol: str
    pos_side: str
    quantity: float
    entry_price: float
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_stop_pct: float = 0.0
    trailing_activation_pct: float = 0.0
    margin_mode: str = "cross"
    leverage: float = 1.0
    product_type: str = "USDT-FUTURES"
    margin_coin: str = "USDT"
    client_order_id: str = ""


def protection_prices_from_payload(
    payload: Mapping[str, Any],
    *,
    entry_price: float,
    pos_side: str,
) -> Tuple[float, float, float, float]:
    """Resolve absolute SL/TP and trailing ratios from a queued-order payload."""
    protection = payload.get("protection")
    spec = protection if isinstance(protection, Mapping) else {}
    entry = max(0.0, float(entry_price or 0.0))
    side = str(pos_side or "").strip().lower()

    def number(*values: Any) -> float:
        for value in values:
            try:
                result = float(value or 0.0)
            except Exception:
                continue
            if result > 0:
                return result
        return 0.0

    stop = number(payload.get("stop_loss_price"), payload.get("stopLossPrice"))
    take = number(payload.get("take_profit_price"), payload.get("takeProfitPrice"))
    stop_pct = number(spec.get("stop_loss_pct"), payload.get("stop_loss_pct"))
    take_pct = number(spec.get("take_profit_pct"), payload.get("take_profit_pct"))
    trailing_pct = number(spec.get("trailing_stop_pct"), payload.get("trailing_stop_pct"))
    activation_pct = number(
        spec.get("trailing_activation_pct"), payload.get("trailing_activation_pct")
    )
    if entry > 0 and stop <= 0 and stop_pct > 0:
        stop = entry * (1.0 - stop_pct if side == "long" else 1.0 + stop_pct)
    if entry > 0 and take <= 0 and take_pct > 0:
        take = entry * (1.0 + take_pct if side == "long" else 1.0 - take_pct)
    return stop, take, trailing_pct, activation_pct


def place_native_protection_orders(
    client: Any,
    request: NativeProtectionRequest,
) -> List[Dict[str, Any]]:
    """Reject the retired direct native-protection entry point.

    The implementation below is retained as inert compatibility code while
    callers migrate to canonical Protection admission.  This guard must stay
    first: no request validation, venue-client import, or exchange call may
    occur from this legacy path.
    """
    raise NativeProtectionDisabledError("native protection entry is permanently disabled")
    pass  # SC-15: legacy body retired


def _place_binance(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    raise NativeProtectionDisabledError("native protection entry is permanently disabled")
    pass  # SC-15: legacy body retired


def _place_okx(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    raise NativeProtectionDisabledError("native protection entry is permanently disabled")
    pass  # SC-15: legacy body retired


def _place_bitget(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    raise NativeProtectionDisabledError("native protection entry is permanently disabled")
    pass  # SC-15: legacy body retired


def _place_bybit(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    raise NativeProtectionDisabledError("native protection entry is permanently disabled")
    pass  # SC-15: legacy body retired


def _place_gate(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    raise NativeProtectionDisabledError("native protection entry is permanently disabled")
    pass  # SC-15: legacy body retired


def _place_htx(client: Any, request: NativeProtectionRequest) -> List[Dict[str, Any]]:
    raise NativeProtectionDisabledError("native protection entry is permanently disabled")
    pass  # SC-15: legacy body retired


def _activation_price(request: NativeProtectionRequest) -> float:
    pct = float(request.trailing_activation_pct or 0.0)
    entry = float(request.entry_price or 0.0)
    if pct <= 0 or entry <= 0:
        return 0.0
    return entry * (1.0 + pct if request.pos_side == "long" else 1.0 - pct)
