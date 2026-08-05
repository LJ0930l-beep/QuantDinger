"""Builtin strategy library — 6 Phase 1-3 strategies from audited GitHub sources.

P0-B (Contracts): app/domain/strategy_regime_contracts, hedge_candidate_contracts
P1 (Spot):      SPOT-01 Donchian+ATR, SPOT-02 Bollinger+RSI+Regime, SPOT-03 NFI-Lite
P2 (Futures):   FUT-01 Turtle Bidirectional, FUT-02 SuperTrend+EMA+ADX
P3 (Neutral):   NEUTRAL-01 Spot-Perpetual Funding (skeleton, Paper only)

All strategies are symbol/timeframe-agnostic.
Source audit: docs/strategy-sources/SOURCE_AUDIT.md
"""

from __future__ import annotations

from . import spot_donchian_atr
from . import spot_bollinger_rsi_regime
from . import spot_nfi_lite
from . import futures_turtle
from . import futures_supertrend_ema_adx
from . import neutral_spot_perp_funding

ALL_STRATEGIES = [
    spot_donchian_atr,
    spot_bollinger_rsi_regime,
    spot_nfi_lite,
    futures_turtle,
    futures_supertrend_ema_adx,
    neutral_spot_perp_funding,
]

BUILTIN_DSL_SOURCES = {
    "spot_donchian_atr": {
        "code": spot_donchian_atr.STRATEGY_CODE,
        "display_name": "SPOT-01: Donchian + ATR Trend Breakout | spot | 1h",
        "market_suitable": spot_donchian_atr.MARKET_SUITABLE,
        "suggested_timeframe": spot_donchian_atr.SUGGESTED_TIMEFRAME,
        "risk_level": spot_donchian_atr.RISK_LEVEL,
    },
    "spot_bollinger_rsi_regime": {
        "code": spot_bollinger_rsi_regime.STRATEGY_CODE,
        "display_name": "SPOT-02: Bollinger + RSI + Regime | spot | 15m",
        "market_suitable": spot_bollinger_rsi_regime.MARKET_SUITABLE,
        "suggested_timeframe": spot_bollinger_rsi_regime.SUGGESTED_TIMEFRAME,
        "risk_level": spot_bollinger_rsi_regime.RISK_LEVEL,
    },
    "spot_nfi_lite": {
        "code": spot_nfi_lite.STRATEGY_CODE,
        "display_name": "SPOT-03: NFI-Lite Multi-Timeframe | spot | 5m/15m",
        "market_suitable": spot_nfi_lite.MARKET_SUITABLE,
        "suggested_timeframe": spot_nfi_lite.SUGGESTED_TIMEFRAME,
        "risk_level": spot_nfi_lite.RISK_LEVEL,
    },
    "futures_turtle": {
        "code": futures_turtle.STRATEGY_CODE,
        "display_name": "FUT-01: Turtle Bidirectional Trend | swap | 1h",
        "market_suitable": futures_turtle.MARKET_SUITABLE,
        "suggested_timeframe": futures_turtle.SUGGESTED_TIMEFRAME,
        "risk_level": futures_turtle.RISK_LEVEL,
    },
    "futures_supertrend_ema_adx": {
        "code": futures_supertrend_ema_adx.STRATEGY_CODE,
        "display_name": "FUT-02: SuperTrend + EMA + ADX | swap | 15m/1h",
        "market_suitable": futures_supertrend_ema_adx.MARKET_SUITABLE,
        "suggested_timeframe": futures_supertrend_ema_adx.SUGGESTED_TIMEFRAME,
        "risk_level": futures_supertrend_ema_adx.RISK_LEVEL,
    },
    "neutral_spot_perp_funding": {
        "code": neutral_spot_perp_funding.STRATEGY_CODE,
        "display_name": "NEUTRAL-01: Spot-Perp Funding Neutral | spot+swap | Phase3",
        "market_suitable": neutral_spot_perp_funding.MARKET_SUITABLE,
        "suggested_timeframe": neutral_spot_perp_funding.SUGGESTED_TIMEFRAME,
        "risk_level": neutral_spot_perp_funding.RISK_LEVEL,
    },
}

STRATEGY_META = BUILTIN_DSL_SOURCES
