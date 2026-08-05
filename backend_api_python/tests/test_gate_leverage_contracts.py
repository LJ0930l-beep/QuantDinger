from decimal import Decimal

import pytest

from app.domain.gate_leverage_contracts import (
    GATE_CRYPTO_SWAP_LEVERAGE_MAX,
    GATE_CRYPTO_SWAP_LEVERAGE_MIN,
    GateLeverageContractError,
    validate_gate_crypto_swap_leverage,
)


def test_gate_perpetual_contract_accepts_only_integer_50_to_100():
    assert validate_gate_crypto_swap_leverage("50") == GATE_CRYPTO_SWAP_LEVERAGE_MIN
    assert validate_gate_crypto_swap_leverage(Decimal("100")) == GATE_CRYPTO_SWAP_LEVERAGE_MAX
    with pytest.raises(GateLeverageContractError):
        validate_gate_crypto_swap_leverage(49)
    with pytest.raises(GateLeverageContractError):
        validate_gate_crypto_swap_leverage(101)
    with pytest.raises(GateLeverageContractError):
        validate_gate_crypto_swap_leverage("50.5")


def test_gate_perpetual_contract_rejects_unknown_values_without_clamping():
    for value in (None, True, "", "not-a-number", float("nan"), float("inf")):
        with pytest.raises(GateLeverageContractError):
            validate_gate_crypto_swap_leverage(value)
