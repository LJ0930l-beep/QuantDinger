"""Grid resting runner — integrates GridEngine with TradingExecutor."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from app.services.grid.config import GridBotConfig
from app.services.grid.engine import GridEngine
from app.services.grid.validator import validate_grid_config
from app.services.bot_scripts.grid_runtime import prepare_bot_market_guards
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log

logger = get_logger(__name__)

# Active runners for fill poller
_ACTIVE_RUNNERS: Dict[int, "GridRestingRunner"] = {}


def register_runner(runner: "GridRestingRunner") -> None:
    _ACTIVE_RUNNERS[int(runner.strategy_id)] = runner


def unregister_runner(strategy_id: int) -> None:
    _ACTIVE_RUNNERS.pop(int(strategy_id), None)


def get_runner(strategy_id: int) -> Optional["GridRestingRunner"]:
    return _ACTIVE_RUNNERS.get(int(strategy_id))


def all_runners() -> Dict[int, "GridRestingRunner"]:
    return dict(_ACTIVE_RUNNERS)


def shutdown_grid_for_strategy(strategy_id: int) -> None:
    """Cancel open grid limits on the exchange even when no runner thread is alive."""
    sid = int(strategy_id or 0)
    if sid <= 0:
        return
    gr = get_runner(sid)
    if gr is not None:
        gr.shutdown()
        return
    try:
        from app.services.exchange_execution import load_strategy_configs, resolve_exchange_config
        from app.services.live_trading.factory import create_client

        sc = load_strategy_configs(sid) or {}
        tc = sc.get("trading_config") if isinstance(sc.get("trading_config"), dict) else {}
        bot_type = str(sc.get("bot_type") or tc.get("bot_type") or "").strip().lower()
        if bot_type != "grid":
            return
        symbol = str(tc.get("symbol") or sc.get("symbol") or "").strip()
        if not symbol:
            return
        user_id = int(sc.get("user_id") or 1)
        ex_cfg = resolve_exchange_config(sc.get("exchange_config") or {}, user_id=user_id)
        mt = str(tc.get("market_type") or "swap").strip().lower()

        def _create_client():
            return create_client(ex_cfg, market_type=mt)

        engine = GridEngine(
            sid,
            symbol,
            tc,
            ex_cfg,
            create_client_fn=_create_client,
            enqueue_market=lambda *a, **k: False,
        )
        engine.shutdown()
        append_strategy_log(sid, "info", "Grid orders cancelled on strategy stop (no active runner)")
    except Exception as e:
        logger.warning("shutdown_grid_for_strategy sid=%s: %s", sid, e)
        append_strategy_log(sid, "warning", f"Grid stop cancel failed: {e}")


class GridRestingRunner:
    """Orchestrates professional resting grid for one live strategy."""

    def __init__(
        self,
        strategy_id: int,
        symbol: str,
        trading_config: Dict[str, Any],
        exchange_config: Dict[str, Any],
        *,
        user_id: int = 1,
        initial_capital: float,
        enqueue_market_fn: Callable[[str, float, float, str], bool],
        create_client_fn: Callable[[], Any],
        risk_exit_fn: Optional[Callable[[float], list]] = None,
    ) -> None:
        self.strategy_id = int(strategy_id)
        self.user_id = int(user_id or 1)
        self.symbol = str(symbol or "")
        self.trading_config = dict(trading_config or {})
        self.trading_config["initial_capital"] = float(initial_capital or 0)
        self.exchange_config = dict(exchange_config or {})
        self._risk_exit_fn = risk_exit_fn
        self._runtime_params: Dict[str, Any] = {}
        self._engine = GridEngine(
            strategy_id,
            symbol,
            self.trading_config,
            self.exchange_config,
            create_client_fn=create_client_fn,
            enqueue_market=enqueue_market_fn,
        )
        self._started = False
        self._last_sync_ts = 0.0
        self._last_exit_sync_ts = 0.0

    @property
    def engine(self) -> GridEngine:
        return self._engine

    @property
    def should_stop(self) -> bool:
        return self._engine.stop_requested

    @property
    def stop_reason(self) -> str:
        return self._engine.stop_reason

    def startup(self, current_price: float, *, bars_df: Any = None) -> tuple[bool, str]:
        return False, "Grid direct trading entry is permanently disabled"
        pass  # SC-15: legacy body retired — unreachable after terminal guard

    def shutdown(self) -> None:
        try:
            self._engine.shutdown()
        finally:
            unregister_runner(self.strategy_id)
            self._started = False

    def tick(
        self,
        current_price: float,
        *,
        high: Optional[float] = None,
        low: Optional[float] = None,
        bars_df: Any = None,
        is_closed_bar: bool = False,
    ) -> None:
        return
        pass  # SC-15: legacy body retired — unreachable after terminal guard
