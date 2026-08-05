"""Fail-closed derivatives account configuration across supported venues."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Tuple

from app.services.live_trading.base import LiveTradingError


def normalize_margin_mode(value: str) -> str:
    raw = str(value or "cross").strip().lower()
    if raw in {"cross", "crossed"}:
        return "cross"
    if raw in {"isolated", "iso"}:
        return "isolated"
    raise LiveTradingError(f"Unsupported margin mode: {value}")


def _decimal_contract_bound(value: Any, *, field: str) -> Decimal:
    """Parse an exchange-provided leverage bound without a float round-trip."""

    try:
        bound = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LiveTradingError(f"Invalid exchange leverage bound: {field}") from exc
    if not bound.is_finite() or bound <= 0:
        raise LiveTradingError(f"Invalid exchange leverage bound: {field}")
    return bound


def _gate_contract_leverage_bounds(client: Any, *, contract: str) -> Tuple[Decimal, Decimal]:
    """Return the exact Gate contract leverage interval or fail closed.

    Gate exposes these values in the contract metadata.  They are contract and
    risk-tier facts, not a global ``50x``/``100x`` application setting.  An
    absent or malformed interval must never be replaced with a guessed default.
    """

    metadata = client.get_contract(contract=contract)
    if not isinstance(metadata, dict):
        raise LiveTradingError("Gate leverage bounds unavailable")
    minimum = _decimal_contract_bound(
        metadata.get("leverage_min"), field="leverage_min"
    )
    maximum = _decimal_contract_bound(
        metadata.get("leverage_max"), field="leverage_max"
    )
    if minimum > maximum:
        raise LiveTradingError("Gate leverage bounds are inconsistent")
    return minimum, maximum


def configure_derivatives_account(
    client: Any,
    *,
    exchange_id: str,
    symbol: str,
    leverage: float,
    margin_mode: str,
) -> Dict[str, Any]:
    """Apply requested leverage and margin mode or reject the order."""
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.gate import GateUsdtFuturesClient
    from app.services.live_trading.htx import HtxClient
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.symbols import to_gate_currency_pair, to_okx_swap_inst_id

    mode = normalize_margin_mode(margin_mode)
    try:
        target_leverage = int(float(leverage or 1))
    except (TypeError, ValueError) as exc:
        raise LiveTradingError(f"Invalid leverage: {leverage}") from exc
    if target_leverage < 1:
        raise LiveTradingError(f"Invalid leverage: {leverage}")

    details: Dict[str, Any] = {
        "exchange": str(exchange_id or "").strip().lower(),
        "symbol": str(symbol or ""),
        "leverage": target_leverage,
        "margin_mode": mode,
    }

    if isinstance(client, BinanceFuturesClient):
        try:
            client.set_margin_type(symbol=symbol, margin_mode=mode)
        except Exception as exc:
            text = str(exc).lower()
            if "-4046" not in text and "no need to change margin type" not in text:
                raise LiveTradingError(f"Binance margin mode setup failed: {exc}") from exc
        client.set_leverage(symbol=symbol, leverage=target_leverage)
        return details

    if isinstance(client, OkxClient):
        account_config = client.get_account_config() or {}
        account_level = str(account_config.get("acctLv") or "").strip()
        if account_level:
            details["account_mode"] = account_level
        if account_level == "1":
            raise LiveTradingError("OKX_SWAP_ACCOUNT_MODE_REQUIRED")
        ok = client.set_leverage(
            inst_id=to_okx_swap_inst_id(symbol),
            lever=target_leverage,
            mgn_mode=mode,
        )
    elif isinstance(client, BitgetMixClient):
        ok = client.set_leverage(
            symbol=symbol,
            leverage=target_leverage,
            margin_mode="crossed" if mode == "cross" else "isolated",
        )
    elif isinstance(client, BybitClient):
        ok = client.set_margin_mode(mode) and client.set_leverage(
            symbol=symbol,
            leverage=target_leverage,
        )
    elif isinstance(client, GateUsdtFuturesClient):
        contract = to_gate_currency_pair(symbol)
        exchange_min, exchange_max = _gate_contract_leverage_bounds(
            client, contract=contract
        )
        requested = Decimal(target_leverage)
        if requested < exchange_min or requested > exchange_max:
            raise LiveTradingError(
                "Gate leverage outside contract bounds: "
                f"requested={target_leverage}, "
                f"allowed={format(exchange_min, 'f')}-{format(exchange_max, 'f')}"
            )
        details.update(
            {
                "contract": contract,
                "exchange_leverage_min": format(exchange_min, "f"),
                "exchange_leverage_max": format(exchange_max, "f"),
                "leverage_verified": True,
            }
        )
        ok = client.set_leverage(
            contract=contract,
            leverage=target_leverage,
            margin_mode=mode,
        )
    elif isinstance(client, HtxClient):
        client.margin_mode = mode
        ok = client.set_leverage(symbol=symbol, leverage=target_leverage, margin_mode=mode)
    else:
        raise LiveTradingError(
            f"Derivatives account configuration is not implemented for {type(client).__name__}"
        )

    if not ok:
        raise LiveTradingError(
            f"{details['exchange'] or type(client).__name__} rejected leverage/margin configuration"
        )
    return details
