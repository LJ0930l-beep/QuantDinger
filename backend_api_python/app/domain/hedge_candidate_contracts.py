"""Hedge candidate contracts for neutral strategies (P0-B HEDGE-01).

A hedge strategy targets delta neutrality by holding offsetting positions
(e.g. spot long + perpetual short).  These contracts model the paired-leg
state machine, failure compensation, and atomic admission requirements.

NEUTRAL-01 must NOT auto-route to TestNet.  These contracts are pure domain;
they have no HTTP, exchange, worker, or admission authority.

Reference: Hummingbot (Apache-2.0) and nateemma (GPL-3.0) — ideas studied,
independently reimplemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.strategy_library_contracts import StrategyLibraryError, _text, _decimal, _utc


HEDGE_CANDIDATE_CONTRACT_VERSION = "hedge-candidate-v1"


class HedgeLegRole(str, Enum):
    """Role of each leg in a hedge pair."""
    SPOT_LONG = "spot_long"
    PERP_SHORT = "perp_short"
    SPOT_SHORT = "spot_short"
    PERP_LONG = "perp_long"


class HedgeState(str, Enum):
    """State machine for a hedged position.

    Transitions are enforced by HedgeStateMachine, not by individual strategies.
    """
    IDLE = "idle"                       # No legs open
    LEG1_REQUESTED = "leg1_requested"   # First leg submitted to Admission
    LEG1_OPEN = "leg1_open"             # First leg filled
    LEG2_REQUESTED = "leg2_requested"   # Second leg submitted to Admission
    FULLY_HEDGED = "fully_hedged"       # Both legs open, delta near zero
    LEG1_CLOSING = "leg1_closing"       # First leg closing (partial unwind)
    LEG2_CLOSING = "leg2_closing"       # Second leg closing
    EMERGENCY_UNWIND = "emergency_unwind"  # Force-close all legs
    FAILED = "failed"                   # Irrecoverable failure


_HEDGE_TRANSITIONS = {
    HedgeState.IDLE: {HedgeState.LEG1_REQUESTED},
    HedgeState.LEG1_REQUESTED: {HedgeState.LEG1_OPEN, HedgeState.FAILED},
    HedgeState.LEG1_OPEN: {HedgeState.LEG2_REQUESTED, HedgeState.LEG1_CLOSING, HedgeState.EMERGENCY_UNWIND},
    HedgeState.LEG2_REQUESTED: {HedgeState.FULLY_HEDGED, HedgeState.FAILED, HedgeState.EMERGENCY_UNWIND},
    HedgeState.FULLY_HEDGED: {HedgeState.LEG1_CLOSING, HedgeState.LEG2_CLOSING, HedgeState.EMERGENCY_UNWIND},
    HedgeState.LEG1_CLOSING: {HedgeState.IDLE, HedgeState.LEG1_OPEN, HedgeState.EMERGENCY_UNWIND},
    HedgeState.LEG2_CLOSING: {HedgeState.LEG1_OPEN, HedgeState.EMERGENCY_UNWIND},
    HedgeState.EMERGENCY_UNWIND: {HedgeState.IDLE, HedgeState.FAILED},
    HedgeState.FAILED: set(),  # Terminal state — requires manual intervention
}


@dataclass(frozen=True, slots=True)
class HedgeLegSpec:
    """Specification for one leg of a hedge pair."""
    instrument_id: str
    role: HedgeLegRole
    quantity: Decimal           # Positive = long, negative = short
    market_type: str            # "spot" or "swap"
    min_quantity: Decimal       # Exchange minimum order size
    price_step: Decimal         # Exchange price precision


@dataclass(frozen=True, slots=True)
class HedgeCandidate:
    """A validated hedge opportunity ready for Admission.

    Both legs MUST succeed or both MUST be rolled back / compensated.
    The hedge_group_id ties them together for atomic admission.
    """
    hedge_group_id: str
    strategy_id: str
    strategy_version: str
    parameter_fingerprint: str

    # Legs
    leg1: HedgeLegSpec
    leg2: HedgeLegSpec

    # Economics (all costs factored)
    expected_net_yield_bps: Decimal   # Expected yield after all costs (fee, funding, slippage)
    max_basis_bps: Decimal            # Maximum acceptable basis spread
    delta_tolerance: Decimal          # Maximum net delta deviation (as fraction)
    max_leg_delay_sec: int            # Maximum seconds between leg fills
    margin_buffer_pct: Decimal        # Safety margin above exchange requirement

    # Admission gate helpers
    credential_id: int
    idempotency_key: str

    def __post_init__(self) -> None:
        _text(self.hedge_group_id, "hedge_group_id")
        _text(self.strategy_id, "strategy_id")
        _text(self.strategy_version, "strategy_version")
        _text(self.parameter_fingerprint, "parameter_fingerprint")
        _text(self.idempotency_key, "idempotency_key")
        _decimal(self.expected_net_yield_bps, "expected_net_yield_bps")
        _decimal(self.max_basis_bps, "max_basis_bps")
        _decimal(self.delta_tolerance, "delta_tolerance", ratio=True)
        if not isinstance(self.max_leg_delay_sec, int) or self.max_leg_delay_sec < 1:
            raise StrategyLibraryError("max_leg_delay_sec must be positive int")
        _decimal(self.margin_buffer_pct, "margin_buffer_pct", positive=True)
        if not isinstance(self.credential_id, int) or self.credential_id < 1:
            raise StrategyLibraryError("credential_id must be positive int")
        if not isinstance(self.leg1, HedgeLegSpec) or not isinstance(self.leg2, HedgeLegSpec):
            raise StrategyLibraryError("both legs must be typed")
        if self.leg1.instrument_id == self.leg2.instrument_id:
            raise StrategyLibraryError("hedge legs must use different instruments")
        # Fundamental: one leg LONG, one SHORT
        if self.leg1.quantity * self.leg2.quantity >= Decimal("0"):
            raise StrategyLibraryError("hedge legs must have opposite directions")


@dataclass(frozen=True, slots=True)
class HedgeStateMachine:
    """Immutable snapshot of the hedge state + transition rules.

    Strategies produce HedgeCandidates; the Hedge Admission layer
    owns the state machine.  This dataclass enforces valid transitions.
    """
    hedge_group_id: str
    state: HedgeState = HedgeState.IDLE

    def transition(self, target: HedgeState) -> HedgeStateMachine:
        if target not in _HEDGE_TRANSITIONS[self.state]:
            raise StrategyLibraryError(
                f"invalid hedge transition: {self.state.value} -> {target.value}"
            )
        return HedgeStateMachine(self.hedge_group_id, target)

    def is_terminal(self) -> bool:
        return self.state is HedgeState.FAILED


@dataclass(frozen=True, slots=True)
class HedgeFailureCompensation:
    """Compensation plan when one leg fails.

    Must be generated BEFORE the surviving leg is touched.
    """
    hedge_group_id: str
    failed_leg: HedgeLegRole
    surviving_leg_instrument: str
    surviving_leg_quantity: Decimal
    compensation_action: str   # "close_surviving_market" | "hold_and_retry" | "hedge_externally"
    max_compensation_delay_sec: int
    estimated_cost_bps: Decimal


__all__ = [
    "HEDGE_CANDIDATE_CONTRACT_VERSION",
    "HedgeCandidate",
    "HedgeFailureCompensation",
    "HedgeLegRole",
    "HedgeLegSpec",
    "HedgeState",
    "HedgeStateMachine",
]
