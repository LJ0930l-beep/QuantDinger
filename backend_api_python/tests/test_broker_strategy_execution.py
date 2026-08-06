"""SC-15 retired legacy broker worker execution; tests assert fail-closed."""
from types import SimpleNamespace

import pytest

from app.services import pending_order_worker as worker_module
from app.services.live_trading.base import LiveTradingError


def _worker():
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker.sent = []
    worker.failed = []
    worker._mark_sent = lambda **kwargs: worker.sent.append(kwargs)
    worker._mark_failed = lambda **kwargs: worker.failed.append(kwargs)
    return worker


def test_broker_order_type_is_fail_closed_for_maker_then_market():
    with pytest.raises(LiveTradingError, match="maker_then_market"):
        worker_module._broker_order_type({"order_type": "maker_then_market"}, 100)


def test_ibkr_strategy_execution_fails_closed_after_sc15(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="SC-15: legacy worker execution permanently retired"):
        worker._execute_ibkr_order(
            order_id=10,
            order_row={},
            payload={"signal_type": "open_short", "symbol": "AAPL", "amount": 2, "order_type": "limit", "limit_price": 201.25},
            client=object(),
            strategy_id=3,
            exchange_config={},
            _notify_live_best_effort=lambda **kwargs: None,
            _console_print=lambda message: None,
        )
    assert worker.sent == []
    assert worker.failed == []


def test_alpaca_strategy_execution_fails_closed_after_sc15(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="SC-15: legacy worker execution permanently retired"):
        worker._execute_alpaca_order(
            order_id=12,
            order_row={},
            payload={"signal_type": "open_short", "symbol": "AAPL", "amount": 2, "order_type": "limit", "limit_price": 200},
            client=object(),
            strategy_id=3,
            exchange_config={},
            market_category="USStock",
            _notify_live_best_effort=lambda **kwargs: None,
            _console_print=lambda message: None,
        )
    assert worker.sent == []


def test_alpaca_crypto_short_never_reaches_client_after_sc15(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="SC-15: legacy worker execution permanently retired"):
        worker._execute_alpaca_order(
            order_id=13,
            order_row={},
            payload={"signal_type": "open_short", "symbol": "BTC/USD", "amount": 0.1},
            client=object(),
            strategy_id=3,
            exchange_config={},
            market_category="Crypto",
            _notify_live_best_effort=lambda **kwargs: None,
            _console_print=lambda message: None,
        )
    assert worker.sent == []
