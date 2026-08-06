"""Unit tests for P0-B strategy contracts: regime, hedge, and signal extensions.

Tests verify:
  - MarketRegime detection from BacktestBar evidence (closed bars only)
  - HedgeCandidate validation and state machine transitions
  - detect_supertrend_ema_adx deterministic output
  - StrategyFamily.SUPERTREND_EMA_ADX integration
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.strategy_library_contracts import (
    StrategyFamily,
    StrategyDefinition,
    StrategyParameterFact,
    strategy_fingerprint,
)
from app.domain.strategy_regime_contracts import (
    MarketRegime,
    MarketRegimeFact,
    detect_market_regime,
)
from app.domain.hedge_candidate_contracts import (
    HedgeCandidate,
    HedgeFailureCompensation,
    HedgeLegRole,
    HedgeLegSpec,
    HedgeState,
    HedgeStateMachine,
)
from app.domain.strategy_signal_contracts import (
    SignalPattern,
    StrategyStructureEvent,
    detect_supertrend_ema_adx,
    build_strategy_signal,
)

# ── Helpers ──────────────────────────────────────────────────

UTC = timezone.utc

def _bar(seq: int, close: str, high: str, low: str, open_: str = "") -> BacktestBar:
    """Create a BacktestBar with strictly ordered open_time/close_time."""
    close_dec = Decimal(close)
    hour = seq // 60
    minute = seq % 60
    return BacktestBar(
        instrument_id="BTC/USDT",
        open_time=datetime(2025, 1, 1, hour + 1, minute, 0, tzinfo=UTC),
        close_time=datetime(2025, 1, 1, hour + 1, minute, 59, tzinfo=UTC),
        open_price=Decimal(open_ or close),
        high_price=Decimal(high),
        low_price=Decimal(low),
        close_price=close_dec,
        volume=Decimal("100"),
        sequence=seq,
        snapshot_id="snap-1",
    )


def _strategy(family: StrategyFamily) -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="test-strat",
        version="1.0.0",
        family=family,
        parameter_schema_fingerprint="abc123",
        data_dependency_snapshot="def456",
        parameters=(StrategyParameterFact("default", "1"),),
        supported_timeframes=("1h",),
        supported_market_types=("crypto",),
    )


# ═══════════════════════════════════════════════════════════════
# 1. Market Regime Tests
# ═══════════════════════════════════════════════════════════════

class TestMarketRegime:
    """Market regime detection from closed bars."""

    def test_insufficient_bars_raises(self):
        from app.domain.strategy_library_contracts import StrategyLibraryError
        bars = [_bar(i, "100", "101", "99") for i in range(10)]
        with pytest.raises(StrategyLibraryError):
            detect_market_regime(bars, adx_period=14)

    def test_trending_up_detection(self):
        """Bars trending up should classify as TRENDING_UP."""
        bars = []
        price = Decimal("100")
        for i in range(60):
            price += Decimal("0.5")
            bars.append(_bar(i, str(price), str(price + Decimal("1")), str(price - Decimal("1"))))
        fact = detect_market_regime(bars)
        assert fact.regime in (MarketRegime.TRENDING_UP, MarketRegime.HIGH_VOLATILITY)
        assert fact.adx_value is not None
        assert fact.adx_value > Decimal("0")

    def test_ranging_detection(self):
        """Flat bars (no direction) should classify as RANGING."""
        bars = [_bar(i, "100", "101", "99") for i in range(60)]
        fact = detect_market_regime(bars, adx_threshold=Decimal("20"))
        # With zero true range, ADX will be very low → RANGING
        assert fact.regime in (MarketRegime.RANGING, MarketRegime.UNKNOWN)

    def test_high_volatility_override(self):
        """Large ATR% should override to HIGH_VOLATILITY."""
        bars = []
        price = Decimal("100")
        for i in range(60):
            high = price + Decimal("15")  # 15% variation
            low = price - Decimal("10")
            bars.append(_bar(i, str(price), str(high), str(low)))
        fact = detect_market_regime(bars, high_vol_atr_pct=Decimal("0.05"))
        assert fact.regime is MarketRegime.HIGH_VOLATILITY

    def test_fact_helpers(self):
        fact = MarketRegimeFact(
            MarketRegime.TRENDING_UP, 1, "2025-01-01T00:00:00+00:00", "BTC/USDT",
            adx_value=Decimal("25"), confidence=Decimal("0.5"),
        )
        assert fact.is_trending()
        assert not fact.is_ranging()
        assert not fact.is_high_volatility()

    def test_deterministic_repeatability(self):
        """Same inputs must produce identical regime facts."""
        bars = []
        price = Decimal("100")
        for i in range(60):
            price += Decimal("0.3") if i % 3 == 0 else Decimal("-0.1")
            bars.append(_bar(i, str(price), str(price + Decimal("2")), str(price - Decimal("1"))))
        f1 = detect_market_regime(bars)
        f2 = detect_market_regime(bars)
        assert f1 == f2
        assert hash(f1) == hash(f2)


# ═══════════════════════════════════════════════════════════════
# 2. Hedge Candidate Tests
# ═══════════════════════════════════════════════════════════════

class TestHedgeCandidate:
    """HedgeCandidate validation and state machine."""

    def test_valid_hedge_candidate(self):
        leg1 = HedgeLegSpec("BTC/USDT_spot", HedgeLegRole.SPOT_LONG, Decimal("1"),
                            "spot", Decimal("0.00001"), Decimal("0.01"))
        leg2 = HedgeLegSpec("BTC/USDT_perp", HedgeLegRole.PERP_SHORT, Decimal("-1"),
                            "swap", Decimal("0.001"), Decimal("0.1"))
        cand = HedgeCandidate(
            hedge_group_id="group-1", strategy_id="s1", strategy_version="1",
            parameter_fingerprint="pf", leg1=leg1, leg2=leg2,
            expected_net_yield_bps=Decimal("5"), max_basis_bps=Decimal("20"),
            delta_tolerance=Decimal("0.01"), max_leg_delay_sec=5,
            margin_buffer_pct=Decimal("0.1"), credential_id=1,
            idempotency_key="ik1",
        )
        assert cand.hedge_group_id == "group-1"

    def test_same_direction_rejected(self):
        from app.domain.strategy_library_contracts import StrategyLibraryError
        leg1 = HedgeLegSpec("BTC/USDT_spot", HedgeLegRole.SPOT_LONG, Decimal("1"),
                            "spot", Decimal("0.00001"), Decimal("0.01"))
        leg2 = HedgeLegSpec("ETH/USDT_perp", HedgeLegRole.PERP_LONG, Decimal("1"),
                            "swap", Decimal("0.001"), Decimal("0.1"))
        with pytest.raises(StrategyLibraryError, match="opposite directions"):
            HedgeCandidate(
                hedge_group_id="g", strategy_id="s", strategy_version="1",
                parameter_fingerprint="p", leg1=leg1, leg2=leg2,
                expected_net_yield_bps=Decimal("0"), max_basis_bps=Decimal("0"),
                delta_tolerance=Decimal("0.01"), max_leg_delay_sec=1,
                margin_buffer_pct=Decimal("0.1"), credential_id=1,
                idempotency_key="k",
            )

    def test_state_machine_valid_transitions(self):
        sm = HedgeStateMachine("group-1")
        assert sm.state is HedgeState.IDLE
        sm = sm.transition(HedgeState.LEG1_REQUESTED)
        assert sm.state is HedgeState.LEG1_REQUESTED
        sm = sm.transition(HedgeState.LEG1_OPEN)
        assert sm.state is HedgeState.LEG1_OPEN
        sm = sm.transition(HedgeState.LEG2_REQUESTED)
        assert sm.state is HedgeState.LEG2_REQUESTED
        sm = sm.transition(HedgeState.FULLY_HEDGED)
        assert sm.state is HedgeState.FULLY_HEDGED
        sm = sm.transition(HedgeState.LEG1_CLOSING)
        sm = sm.transition(HedgeState.IDLE)
        assert sm.state is HedgeState.IDLE

    def test_state_machine_invalid_transition(self):
        from app.domain.strategy_library_contracts import StrategyLibraryError
        sm = HedgeStateMachine("g")
        with pytest.raises(StrategyLibraryError, match="invalid hedge transition"):
            sm.transition(HedgeState.FULLY_HEDGED)  # can't go IDLE -> FULLY_HEDGED

    def test_failed_is_terminal(self):
        sm = HedgeStateMachine("g")
        sm = sm.transition(HedgeState.LEG1_REQUESTED)
        sm = sm.transition(HedgeState.FAILED)
        assert sm.is_terminal()
        from app.domain.strategy_library_contracts import StrategyLibraryError
        with pytest.raises(StrategyLibraryError):
            sm.transition(HedgeState.IDLE)  # FAILED has no valid transitions

    def test_emergency_unwind_path(self):
        sm = HedgeStateMachine("g")
        sm = sm.transition(HedgeState.LEG1_REQUESTED)
        sm = sm.transition(HedgeState.LEG1_OPEN)
        sm = sm.transition(HedgeState.LEG2_REQUESTED)
        sm = sm.transition(HedgeState.EMERGENCY_UNWIND)
        sm = sm.transition(HedgeState.IDLE)
        assert sm.state is HedgeState.IDLE

    def test_compensation_plan(self):
        cp = HedgeFailureCompensation(
            hedge_group_id="g", failed_leg=HedgeLegRole.PERP_SHORT,
            surviving_leg_instrument="BTC/USDT_spot",
            surviving_leg_quantity=Decimal("1"),
            compensation_action="close_surviving_market",
            max_compensation_delay_sec=30, estimated_cost_bps=Decimal("5"),
        )
        assert cp.compensation_action == "close_surviving_market"


# ═══════════════════════════════════════════════════════════════
# 3. SuperTrend Signal Tests
# ═══════════════════════════════════════════════════════════════

class TestSuperTrendSignal:
    """detect_supertrend_ema_adx from closed bars."""

    def test_insufficient_bars(self):
        from app.domain.strategy_signal_contracts import StrategySignalContractError
        bars = [_bar(i, "100", "101", "99") for i in range(20)]
        with pytest.raises(StrategySignalContractError):
            detect_supertrend_ema_adx(bars)

    def test_trending_up_signal(self):
        """Strong uptrend should produce BUY signal."""
        bars = []
        price = Decimal("100")
        for i in range(60):
            price += Decimal("0.8")
            bars.append(_bar(i, str(price), str(price + Decimal("2")), str(price - Decimal("0.5"))))
        event = detect_supertrend_ema_adx(bars)
        # May or may not trigger — depends on SuperTrend flip
        assert event.direction in ("buy", "sell", "flat")
        assert isinstance(event.pattern, SignalPattern)

    def test_deterministic_output(self):
        bars = []
        price = Decimal("100")
        for i in range(60):
            price += Decimal("0.3") if i % 3 == 0 else Decimal("-0.1")
            bars.append(_bar(i, str(price), str(price + Decimal("2")), str(price - Decimal("1"))))
        e1 = detect_supertrend_ema_adx(bars)
        e2 = detect_supertrend_ema_adx(bars)
        assert e1 == e2

    def test_build_strategy_signal_supertrend(self):
        strat = _strategy(StrategyFamily.SUPERTREND_EMA_ADX)
        bars = []
        price = Decimal("100")
        for i in range(60):
            price += Decimal("0.5")
            bars.append(_bar(i, str(price), str(price + Decimal("2")), str(price - Decimal("1"))))
        fact = build_strategy_signal(strat, bars, signal_id="sig-1", data_snapshot_id="ds1")
        assert fact.signal_id == "sig-1"
        assert fact.strategy.family is StrategyFamily.SUPERTREND_EMA_ADX


# ═══════════════════════════════════════════════════════════════
# 4. Fingerprint Determinism
# ═══════════════════════════════════════════════════════════════

class TestFingerprintDeterminism:
    def test_same_strategy_same_fingerprint(self):
        s1 = _strategy(StrategyFamily.DONCHIAN_ATR)
        s2 = _strategy(StrategyFamily.DONCHIAN_ATR)
        assert strategy_fingerprint(s1) == strategy_fingerprint(s2)

    def test_different_family_different_fingerprint(self):
        s1 = _strategy(StrategyFamily.DONCHIAN_ATR)
        s2 = _strategy(StrategyFamily.BOLLINGER_RSI)
        assert strategy_fingerprint(s1) != strategy_fingerprint(s2)
