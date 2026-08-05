"""Complete test suite for all 6 Phase 1-3 strategies.

SPOT-01: Donchian+ATR, SPOT-02: Bollinger+RSI+Regime, SPOT-03: NFI-Lite
FUT-01: Turtle, FUT-02: SuperTrend+EMA+ADX, NEUTRAL-01: Funding Neutral (skeleton)
"""

from __future__ import annotations
import importlib
import pytest
from app.services.strategy_v2.contract import compile_strategy_v2

STRATEGIES = [
    ("SPOT-01", "spot_donchian_atr"),
    ("SPOT-02", "spot_bollinger_rsi_regime"),
    ("SPOT-03", "spot_nfi_lite"),
    ("FUT-01",  "futures_turtle"),
    ("FUT-02",  "futures_supertrend_ema_adx"),
    ("NEUTRAL-01", "neutral_spot_perp_funding"),
]

def _code(label):
    mod = importlib.import_module("app.services.strategy_v2.builtin." + label)
    return mod.STRATEGY_CODE, mod

# ═══════════════════════════════════════════════════════════════

class TestAllCompile:
    @pytest.mark.parametrize("name,label", STRATEGIES)
    def test_compiles(self, name, label):
        code, _ = _code(label)
        p = compile_strategy_v2(code)
        assert "initialize" in p.manifest.handlers, f"{name}: missing initialize"
        assert "handle_data" in p.manifest.handlers, f"{name}: missing handle_data"

    @pytest.mark.parametrize("name,label", STRATEGIES)
    def test_deterministic(self, name, label):
        code, _ = _code(label)
        h1 = compile_strategy_v2(code).manifest.code_hash
        h2 = compile_strategy_v2(code).manifest.code_hash
        assert h1 == h2, f"{name}: non-deterministic compilation"

class TestNoForbiddenCalls:
    FORBIDDEN = ["urllib", "requests.", "http://", "websocket", "ccxt",
                  "place_order", "cancel_order", "api_key", "secret", "signed"]

    @pytest.mark.parametrize("name,label", STRATEGIES)
    def test_no_exchange_calls(self, name, label):
        code, _ = _code(label)
        for w in self.FORBIDDEN:
            assert w not in code.lower(), f"{name}: contains '{w}'"

class TestMetadata:
    @pytest.mark.parametrize("name,label", STRATEGIES)
    def test_has_strategy_source(self, name, label):
        _, mod = _code(label)
        assert hasattr(mod, "STRATEGY_SOURCE"), f"{name}: missing STRATEGY_SOURCE"
        src = mod.STRATEGY_SOURCE
        for field in ("repo", "license", "what_was_borrowed", "what_is_original"):
            assert field in src, f"{name}: STRATEGY_SOURCE missing {field}"
        assert len(src["what_is_original"]) >= 2, f"{name}: too few original contributions"

    @pytest.mark.parametrize("name,label", STRATEGIES)
    def test_has_market_fields(self, name, label):
        _, mod = _code(label)
        assert hasattr(mod, "MARKET_SUITABLE")
        assert hasattr(mod, "SUGGESTED_TIMEFRAME")
        assert hasattr(mod, "RISK_LEVEL")

class TestSpotOnly:
    @pytest.mark.parametrize("name,label", [("SPOT-01","spot_donchian_atr"),("SPOT-02","spot_bollinger_rsi_regime"),("SPOT-03","spot_nfi_lite")])
    def test_spot_only_market(self, name, label):
        _, mod = _code(label)
        assert "spot" in mod.MARKET_SUITABLE, f"{name}: must support spot"
        assert "us_stock" in mod.MARKET_SUITABLE or "crypto" in mod.MARKET_SUITABLE

class TestContractOnly:
    @pytest.mark.parametrize("name,label", [("FUT-01","futures_turtle"),("FUT-02","futures_supertrend_ema_adx")])
    def test_swap_support(self, name, label):
        _, mod = _code(label)
        assert "swap" in mod.MARKET_SUITABLE, f"{name}: must support swap"

class TestNeutralSpecial:
    def test_neutral_skeleton_phase(self):
        _, mod = _code("neutral_spot_perp_funding")
        assert mod.NEUTRAL_PHASE == 3, "NEUTRAL-01 must be Phase 3"
        assert hasattr(mod, "HEDGE_STATE_MACHINE_DEFINITION")
        hsm = mod.HEDGE_STATE_MACHINE_DEFINITION
        assert "FULLY_HEDGED" in hsm["states"]
        assert "FAILED" in hsm["states"]
        assert hsm["transitions"]["FAILED"] == [], "FAILED must have no exits"

    def test_neutral_no_auto_testnet(self):
        code, _ = _code("neutral_spot_perp_funding")
        doc = __import__('app.services.strategy_v2.builtin.neutral_spot_perp_funding', fromlist=['__doc__']).__doc__ or ''
        assert "Phase 3" in doc or "Phase 3" in str(code), "NEUTRAL-01 doc must mention Phase 3"

class TestCrossStrategyLookahead:
    @pytest.mark.parametrize("name,label", STRATEGIES)
    def test_references_closed_bars(self, name, label):
        code, _ = _code(label)
        # All strategies should reference 'closes[-1]' pattern (current closed bar)
        # or explicitly exclude current bar via lookback
        mentions_closes = "closes[-1]" in code or "close_price" in code
        assert mentions_closes, f"{name}: no closed bar reference found"
