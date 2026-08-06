"""Tests for the 6 corrected Phase 1-3 strategies with proper runtime API."""

from __future__ import annotations
import importlib
import pytest
from app.services.strategy_v2.contract import compile_strategy_v2

STRATEGIES = [
    ("SPOT-01", "spot_donchian_atr", "crypto_spot", "conservative"),
    ("SPOT-02", "spot_bollinger_rsi_regime", "crypto_spot", "conservative"),
    ("SPOT-03", "spot_nfi_lite", "crypto_spot", "conservative"),
    ("FUT-01",  "futures_turtle", "crypto_swap", "aggressive"),
    ("FUT-02",  "futures_supertrend_ema_adx", "crypto_swap", "aggressive"),
    ("NEUTRAL-01", "neutral_spot_perp_funding", "crypto_spot", "neutral"),
]


def _load(label):
    mod = importlib.import_module("app.services.strategy_v2.builtin." + label)
    return mod.STRATEGY_CODE, mod


# ═══════════════════════════════════════════════════════════════
# Compilation
# ═══════════════════════════════════════════════════════════════

class TestCompile:
    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_compiles(self, name, label, market, risk):
        code, _ = _load(label)
        p = compile_strategy_v2(code)
        assert "initialize" in p.manifest.handlers
        assert "handle_data" in p.manifest.handlers

    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_deterministic(self, name, label, market, risk):
        code, _ = _load(label)
        h1 = compile_strategy_v2(code).manifest.code_hash
        h2 = compile_strategy_v2(code).manifest.code_hash
        assert h1 == h2


# ═══════════════════════════════════════════════════════════════
# Correct runtime API usage
# ═══════════════════════════════════════════════════════════════

class TestRuntimeAPI:
    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_uses_correct_runtime_api(self, name, label, market, risk):
        code, _ = _load(label)
        # Must use context.subscribe, not context.bars
        assert "context.subscribe" in code, f"{name}: missing context.subscribe"
        assert "context.bars" not in code, f"{name}: uses context.bars (wrong API)"
        # Must use get_history
        assert "get_history" in code, f"{name}: missing get_history"
        # Must use context.order to submit (NEUTRAL-01 skeleton exempt)
        if name != "NEUTRAL-01":
            assert "context.order" in code, f"{name}: missing context.order"

    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_initialize_has_no_context_params(self, name, label, market, risk):
        """initialize() cannot use context.params - validator forbids it."""
        code, _ = _load(label)
        # Find initialize function body
        idx = code.find("def initialize(context)")
        idx_end = code.find("def initialize", idx + 1)
        if idx_end == -1:
            idx_end = code.find("def handle_data", idx)
        init_body = code[idx:idx_end]
        assert "context.params" not in init_body, f"{name}: initialize() uses context.params (forbidden)"

    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_handle_data_uses_context_params(self, name, label, market, risk):
        """handle_data() should read user config from context.params."""
        code, _ = _load(label)
        assert "context.params" in code, f"{name}: handle_data() doesn't use context.params"

    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_no_unsafe_truthiness(self, name, label, market, risk):
        """`if not bars` is ambiguous - use explicit length check."""
        code, _ = _load(label)
        # The DAG pattern is: bars is None or len(bars) < N
        assert "if bars is None or len(bars) < " in code, f"{name}: bars check not explicit"

    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_no_direct_exchange_calls(self, name, label, market, risk):
        """Strategy must not call exchange/HTTP/CCXT."""
        code, _ = _load(label)
        forbidden = ["urllib", "requests.", "http://", "websocket", "ccxt",
                      "place_order", "cancel_order", "api_key", "secret"]
        for w in forbidden:
            assert w not in code.lower(), f"{name}: contains '{w}'"


# ═══════════════════════════════════════════════════════════════
# Configurable symbol/timeframe/leverage
# ═══════════════════════════════════════════════════════════════

class TestConfigurability:
    """All strategies should let user choose symbol, timeframe, leverage."""

    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_configurable_via_params(self, name, label, market, risk):
        code, _ = _load(label)
        # Must read all three from context.params
        assert "frequency" in code, f"{name}: not configurable for frequency"
        assert "symbol" in code, f"{name}: not configurable for symbol"

    def test_contracts_allow_leverage(self):
        for name, label, _, _ in [s for s in STRATEGIES if "swap" in s[2]]:
            code, _ = _load(label)
            assert "allow_leverage" in code, f"{name}: contracts strategy must allow leverage"

    def test_spot_does_not_force_leverage(self):
        for name, label, _, _ in [s for s in STRATEGIES if "spot" in s[2] and "swap" not in s[2]]:
            code, _ = _load(label)
            # Spot strategies should not have allow_leverage in initialize
            assert "allow_leverage" not in code, f"{name}: spot strategy should not declare leverage"


# ═══════════════════════════════════════════════════════════════
# Market suitability
# ═══════════════════════════════════════════════════════════════

class TestMarketSuitability:
    @pytest.mark.parametrize("name,label,market,risk", STRATEGIES)
    def test_market_suitable_declared(self, name, label, market, risk):
        _, mod = _load(label)
        assert hasattr(mod, "MARKET_SUITABLE")
        assert isinstance(mod.MARKET_SUITABLE, str)
        assert len(mod.MARKET_SUITABLE) > 0

    def test_spot_strategies_list_spot(self):
        for name, label, _, _ in [s for s in STRATEGIES if "spot" in s[2] and "swap" not in s[2]]:
            _, mod = _load(label)
            assert "spot" in mod.MARKET_SUITABLE, f"{name}: SPOT should list spot"

    def test_swap_strategies_list_swap(self):
        for name, label, _, _ in [s for s in STRATEGIES if "swap" in s[2] and "spot" not in s[2]]:
            _, mod = _load(label)
            assert "swap" in mod.MARKET_SUITABLE, f"{name}: swap should list swap"
