"""Fail-closed coverage for retired native protection entries."""

from app.services.live_trading.native_protection import (
    NativeProtectionDisabledError,
    NativeProtectionRequest,
    place_native_protection_orders,
    protection_prices_from_payload,
)
from app.services.quick_trade.orders import attach_quick_trade_protection


def _request(**overrides):
    values = {
        "symbol": "BTC/USDT",
        "pos_side": "long",
        "quantity": 0.01,
        "entry_price": 100.0,
        "stop_loss_price": 90.0,
        "take_profit_price": 120.0,
        "margin_mode": "cross",
        "client_order_id": "qdprot1",
    }
    values.update(overrides)
    return NativeProtectionRequest(**values)


def test_resolves_strategy_protection_percentages():
    stop, take, trailing, activation = protection_prices_from_payload(
        {
            "protection": {
                "stop_loss_pct": 0.1,
                "take_profit_pct": 0.2,
                "trailing_stop_pct": 0.03,
                "trailing_activation_pct": 0.04,
            }
        },
        entry_price=100,
        pos_side="long",
    )
    assert stop == 90
    assert take == 120
    assert trailing == 0.03
    assert activation == 0.04


def test_native_protection_entry_is_typed_disabled_before_client_use():
    class ExplodingClient:
        def __getattr__(self, name):
            raise AssertionError(f"legacy client attribute accessed: {name}")

    try:
        place_native_protection_orders(ExplodingClient(), _request())
    except NativeProtectionDisabledError as exc:
        assert "permanently disabled" in str(exc)
    else:  # pragma: no cover - assertion keeps the contract explicit
        raise AssertionError("native protection must fail closed")


def test_quick_trade_protection_entry_is_typed_disabled_before_client_use():
    class ExplodingClient:
        def __getattr__(self, name):
            raise AssertionError(f"legacy client attribute accessed: {name}")

    try:
        attach_quick_trade_protection(
            ExplodingClient(),
            symbol="BTC/USDT",
            side="buy",
            filled_qty=0.01,
            avg_price=100.0,
            tp_price=120.0,
            sl_price=90.0,
            market_type="swap",
            exchange_config={},
            leverage=1.0,
            margin_mode="cross",
            client_order_id="qdprot1",
        )
    except NativeProtectionDisabledError as exc:
        assert "permanently disabled" in str(exc)
    else:  # pragma: no cover - assertion keeps the contract explicit
        raise AssertionError("quick-trade protection must fail closed")


def test_quick_trade_protection_is_disabled_even_for_non_swap_or_empty_prices():
    for market_type, filled_qty, tp_price, sl_price in (
        ("spot", 0.01, 120.0, 90.0),
        ("swap", 0.0, 120.0, 90.0),
        ("swap", 0.01, 0.0, 0.0),
    ):
        try:
            attach_quick_trade_protection(
                object(),
                symbol="BTC/USDT",
                side="buy",
                filled_qty=filled_qty,
                avg_price=100.0,
                tp_price=tp_price,
                sl_price=sl_price,
                market_type=market_type,
                exchange_config={},
                leverage=1.0,
                margin_mode="cross",
                client_order_id="qdprot1",
            )
        except NativeProtectionDisabledError:
            continue
        raise AssertionError("all legacy protection calls must fail closed")
