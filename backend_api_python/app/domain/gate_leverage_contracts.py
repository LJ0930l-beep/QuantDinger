"""Deterministic Gate USDT-perpetual leverage contract.

This is a validation boundary only.  It does not call Gate and does not
choose a fallback leverage when the caller supplies an invalid value.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


GATE_CRYPTO_SWAP_LEVERAGE_MIN = Decimal("50")
GATE_CRYPTO_SWAP_LEVERAGE_MAX = Decimal("100")


class GateLeverageContractError(ValueError):
    """The requested Gate perpetual leverage is not contract-compliant."""


def validate_gate_crypto_swap_leverage(value: Any) -> Decimal:
    """Return a canonical Decimal leverage or fail closed.

    ``str`` conversion is intentional at this external adapter boundary so
    an existing float-configured caller cannot introduce binary arithmetic;
    no rounding or clamping is performed.
    """

    if isinstance(value, bool) or value is None:
        raise GateLeverageContractError("Gate perpetual leverage is required")
    try:
        normalized = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateLeverageContractError("Gate perpetual leverage is invalid") from exc
    if not normalized.is_finite() or normalized != normalized.to_integral_value():
        raise GateLeverageContractError("Gate perpetual leverage must be an integer")
    if normalized < GATE_CRYPTO_SWAP_LEVERAGE_MIN or normalized > GATE_CRYPTO_SWAP_LEVERAGE_MAX:
        raise GateLeverageContractError("Gate perpetual leverage must be between 50x and 100x")
    return normalized


__all__ = [
    "GATE_CRYPTO_SWAP_LEVERAGE_MAX",
    "GATE_CRYPTO_SWAP_LEVERAGE_MIN",
    "GateLeverageContractError",
    "validate_gate_crypto_swap_leverage",
]
