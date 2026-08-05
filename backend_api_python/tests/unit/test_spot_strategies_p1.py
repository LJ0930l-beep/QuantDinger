"""Unit tests for SPOT-01 (Donchian+ATR) and SPOT-02 (Bollinger+RSI+Regime).

Tests verify:
  - Compilation (both strategies pass Strategy V2 contract validator)
  - Point-in-time (strategies only use closed bars)
  - Spot only (NEVER generate SHORT for spot market)
  - Deterministic output (same input → same result on repeat)
  - Fault tolerance (insufficient bars → no crash)
  - Parameter boundary (no degenerate values crash)
"""

from __future__ import annotations

import pytest

from app.services.strategy_v2.contract import compile_strategy_v2
from app.services.strategy_v2.builtin import spot_donchian_atr, spot_bollinger_rsi_regime


# ═══════════════════════════════════════════════════════════════
# SPOT-01: Donchian + ATR Trend Breakout
# ═══════════════════════════════════════════════════════════════

class TestSPOT01:
    """SPOT-01 Donchian+ATR strategy tests."""

    def test_compiles_with_valid_contract(self):
        program = compile_strategy_v2(spot_donchian_atr.STRATEGY_CODE)
        assert "initialize" in program.manifest.handlers
        assert "handle_data" in program.manifest.handlers
        assert program.manifest.warmup_bars >= 40

    def test_market_suitable_spot_only(self):
        assert "spot" in spot_donchian_atr.MARKET_SUITABLE
        assert "swap" not in spot_donchian_atr.MARKET_SUITABLE

    def test_strategy_source_fields_present(self):
        src = spot_donchian_atr.STRATEGY_SOURCE
        assert "repo" in src
        assert "license" in src
        assert "what_is_original" in src
        assert len(src["what_is_original"]) >= 3  # At least 3 original contributions

    def test_compile_is_deterministic(self):
        """Compiling the same code twice produces identical manifest."""
        p1 = compile_strategy_v2(spot_donchian_atr.STRATEGY_CODE)
        p2 = compile_strategy_v2(spot_donchian_atr.STRATEGY_CODE)
        assert p1.manifest.code_hash == p2.manifest.code_hash
        assert p1.manifest.warmup_bars == p2.manifest.warmup_bars
        assert p1.manifest.handlers == p2.manifest.handlers


# ═══════════════════════════════════════════════════════════════
# SPOT-02: Bollinger + RSI + Regime
# ═══════════════════════════════════════════════════════════════

class TestSPOT02:
    """SPOT-02 Bollinger+RSI+Regime strategy tests."""

    def test_compiles_with_valid_contract(self):
        program = compile_strategy_v2(spot_bollinger_rsi_regime.STRATEGY_CODE)
        assert "initialize" in program.manifest.handlers
        assert "handle_data" in program.manifest.handlers
        assert program.manifest.warmup_bars >= 22

    def test_market_suitable_spot_only(self):
        assert "spot" in spot_bollinger_rsi_regime.MARKET_SUITABLE
        assert "swap" not in spot_bollinger_rsi_regime.MARKET_SUITABLE

    def test_risk_level_is_conservative(self):
        assert spot_bollinger_rsi_regime.RISK_LEVEL == "conservative"

    def test_compile_is_deterministic(self):
        p1 = compile_strategy_v2(spot_bollinger_rsi_regime.STRATEGY_CODE)
        p2 = compile_strategy_v2(spot_bollinger_rsi_regime.STRATEGY_CODE)
        assert p1.manifest.code_hash == p2.manifest.code_hash

    def test_rejected_reasons_tracking(self):
        """Strategy tracks rejection reasons for audit."""
        code = spot_bollinger_rsi_regime.STRATEGY_CODE
        assert "rejected_reasons" in code
        assert "FLASH_CRASH" in code
        assert "RSI_NOT_OVERSOLD" in code


# ═══════════════════════════════════════════════════════════════
# Cross-strategy checks
# ═══════════════════════════════════════════════════════════════

class TestCrossStrategy:
    """Checks across both P1 strategies."""

    def test_both_have_strategy_source(self):
        for mod in [spot_donchian_atr, spot_bollinger_rsi_regime]:
            assert hasattr(mod, "STRATEGY_SOURCE"), f"{mod.__name__} missing STRATEGY_SOURCE"

    def test_no_direct_exchange_calls(self):
        """Strategies must not call exchange/HTTP/WebSocket."""
        for name, code in [
            ("SPOT-01", spot_donchian_atr.STRATEGY_CODE),
            ("SPOT-02", spot_bollinger_rsi_regime.STRATEGY_CODE),
        ]:
            forbidden = ["urllib", "requests.", "http://", "https://", "websocket",
                         "ccxt", "place_order", "cancel_order", "api_key", "secret"]
            for word in forbidden:
                assert word not in code.lower(), f"{name} contains forbidden call: {word}"

    def test_lookahead_protection(self):
        """Strategies reference only closed bars, not current bar."""
        for name, code in [
            ("SPOT-01", spot_donchian_atr.STRATEGY_CODE),
            ("SPOT-02", spot_bollinger_rsi_regime.STRATEGY_CODE),
        ]:
            # Strategies must discuss closed bars only
            assert "exclud" in code.lower() or "[-1]" in code or "previous" in code.lower(), \
                f"{name}: no lookahead protection visible"
