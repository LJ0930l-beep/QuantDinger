from datetime import datetime, timezone

from app.services.strategy_daily_pnl import (
    choose_opening_equity,
    resolve_business_day_window,
    stabilize_idle_strategy_opening,
)


def test_business_day_window_uses_client_iana_timezone():
    start, end, name = resolve_business_day_window(
        now=datetime(2026, 7, 21, 18, 30, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
    )

    assert name == "Asia/Shanghai"
    assert start == datetime(2026, 7, 21, 16, 0)
    assert end == datetime(2026, 7, 22, 16, 0)


def test_opening_equity_prefers_nearest_midnight_snapshot():
    day_start = datetime(2026, 7, 21, 16, 0)
    opening, estimated, source = choose_opening_equity(
        day_start=day_start,
        before={"equity": 1010, "captured_at": datetime(2026, 7, 21, 15, 58)},
        after={"equity": 1011, "captured_at": datetime(2026, 7, 21, 16, 4)},
        reconstructed=999,
    )

    assert opening == 1010
    assert estimated is False
    assert source == "snapshot_before"


def test_opening_equity_marks_ledger_reconstruction_as_estimated():
    opening, estimated, source = choose_opening_equity(
        day_start=datetime(2026, 7, 21, 16, 0),
        before=None,
        after=None,
        reconstructed=987.5,
    )

    assert opening == 987.5
    assert estimated is True
    assert source == "ledger_reconstruction"


def test_idle_strategy_does_not_report_stale_capital_snapshot_as_daily_loss():
    opening, estimated, source = stabilize_idle_strategy_opening(
        opening=10000,
        current_equity=100,
        initial_capital=100,
        realized_net=0,
        unrealized=0,
        open_positions=0,
    )

    assert opening == 100
    assert estimated is True
    assert source == "idle_strategy_baseline"


def test_active_strategy_keeps_opening_snapshot():
    opening, estimated, source = stabilize_idle_strategy_opening(
        opening=10000,
        current_equity=100,
        initial_capital=100,
        realized_net=-5,
        unrealized=0,
        open_positions=0,
    )

    assert opening == 10000
    assert estimated is False
    assert source == ""
