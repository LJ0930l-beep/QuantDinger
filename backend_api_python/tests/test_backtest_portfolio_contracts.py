from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.backtest_portfolio_contracts import (
    BacktestPortfolioDisposition,
    BacktestPortfolioError,
    BacktestPortfolioFill,
    BacktestPortfolioState,
    apply_backtest_portfolio_fill,
    calculate_backtest_unrealized_pnl,
)
from app.domain.deterministic_backtest_contracts import BacktestSide


UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fill(fill_id="fill-1", side=BacktestSide.BUY, quantity="2", price="100", fee="1", fee_asset="USDT", funding="0"):
    return BacktestPortfolioFill(fill_id, "BTC_USDT", side, Decimal(quantity), Decimal(price), Decimal(fee), fee_asset, Decimal(funding), "cost-v1", UTC)


def empty():
    return BacktestPortfolioState("BTC_USDT", "USDT")


def test_open_add_partial_close_and_flip_keep_gross_costs_separate():
    state = empty()
    first = apply_backtest_portfolio_fill(state, fill()).state
    second = apply_backtest_portfolio_fill(first, fill("fill-2", quantity="1", price="110", fee="0.5", funding="-0.2")).state
    assert second.signed_quantity == Decimal("3")
    assert second.average_entry_price == Decimal("103.3333333333333333333333333")
    closed = apply_backtest_portfolio_fill(second, fill("fill-3", BacktestSide.SELL, "1", "120", "0.25", funding="0.1")).state
    assert closed.signed_quantity == Decimal("2")
    assert closed.realized_gross_pnl == Decimal("16.6666666666666666666666667")
    flipped = apply_backtest_portfolio_fill(closed, fill("fill-4", BacktestSide.SELL, "3", "90", "0.25")).state
    assert flipped.signed_quantity == Decimal("-1")
    assert flipped.average_entry_price == Decimal("90")
    assert flipped.total_fee == Decimal("2")
    assert flipped.funding == Decimal("-0.1")


def test_fill_replay_and_conflict_are_typed():
    state = apply_backtest_portfolio_fill(empty(), fill()).state
    assert apply_backtest_portfolio_fill(state, fill()).disposition is BacktestPortfolioDisposition.REPLAYED
    assert apply_backtest_portfolio_fill(state, fill(price="101")).disposition is BacktestPortfolioDisposition.CONFLICT


def test_unrealized_pnl_and_scope_fail_closed():
    state = apply_backtest_portfolio_fill(empty(), fill()).state
    assert calculate_backtest_unrealized_pnl(state, Decimal("105")) == Decimal("10")
    with pytest.raises(BacktestPortfolioError):
        apply_backtest_portfolio_fill(state, BacktestPortfolioFill("other", "ETH_USDT", BacktestSide.BUY, Decimal("1"), Decimal("100"), Decimal("0"), "USDT", Decimal("0"), "cost-v1", UTC))
    with pytest.raises(BacktestPortfolioError):
        calculate_backtest_unrealized_pnl(state, 105.0)


def test_fee_assets_remain_separate_and_scalar_total_fails_closed_for_multiple_assets():
    state = apply_backtest_portfolio_fill(empty(), fill()).state
    second = apply_backtest_portfolio_fill(
        state,
        fill("fill-2", fee="0.5", fee_asset="BTC"),
    ).state
    assert second.fees_by_asset == (("BTC", Decimal("0.5")), ("USDT", Decimal("1")))
    with pytest.raises(BacktestPortfolioError):
        _ = second.total_fee


def test_reducer_rejects_out_of_order_fill_facts():
    state = apply_backtest_portfolio_fill(empty(), fill("fill-2")).state
    with pytest.raises(BacktestPortfolioError):
        apply_backtest_portfolio_fill(state, fill("fill-1"))


def test_state_rejects_case_colliding_fee_assets():
    with pytest.raises(BacktestPortfolioError):
        BacktestPortfolioState(
            "BTC_USDT",
            "USDT",
            fees_by_asset=(("USDT", Decimal("1")), ("usdt", Decimal("2"))),
        )
