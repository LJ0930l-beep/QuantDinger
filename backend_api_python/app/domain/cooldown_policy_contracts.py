"""Deterministic account-cooldown policy facts.

An active cooldown blocks risk-increasing actions until both the immutable
time floor and the required number of completed trade cycles are satisfied.
Risk-reducing and neutral actions remain permitted.  This module is pure and
does not mutate an account or contact an execution service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from .order_contracts import OrderAction, RiskEffect, classify_risk_effect


COOLDOWN_POLICY_CONTRACT_VERSION = "cooldown-policy-v1"


class CooldownPolicyError(ValueError):
    """Invalid or incomplete cooldown facts."""


class CooldownDisposition(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"


@dataclass(frozen=True, slots=True)
class CooldownPolicy:
    minimum_hours: int = 12
    required_trade_cycles: int = 3

    def __post_init__(self) -> None:
        if isinstance(self.minimum_hours, bool) or not isinstance(self.minimum_hours, int) or self.minimum_hours != 12:
            raise CooldownPolicyError("minimum_hours must be the approved 12-hour policy")
        if isinstance(self.required_trade_cycles, bool) or not isinstance(self.required_trade_cycles, int) or self.required_trade_cycles != 3:
            raise CooldownPolicyError("required_trade_cycles must be the approved three-cycle policy")


@dataclass(frozen=True, slots=True)
class CooldownEvaluation:
    disposition: CooldownDisposition
    risk_effect: RiskEffect
    until: datetime
    completed_trade_cycles: int
    cycles_remaining: int
    reason: str


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise CooldownPolicyError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


def evaluate_cooldown(
    *,
    action: OrderAction,
    started_at: datetime,
    now_utc: datetime,
    completed_trade_cycles: int,
    policy: CooldownPolicy = CooldownPolicy(),
) -> CooldownEvaluation:
    """Evaluate a caller-owned cooldown without silently lowering its policy."""

    if not isinstance(action, OrderAction):
        raise CooldownPolicyError("action must use OrderAction")
    if not isinstance(policy, CooldownPolicy):
        raise CooldownPolicyError("policy must use CooldownPolicy")
    started = _utc(started_at, "started_at")
    now = _utc(now_utc, "now_utc")
    if now < started:
        raise CooldownPolicyError("now_utc cannot precede started_at")
    if isinstance(completed_trade_cycles, bool) or not isinstance(completed_trade_cycles, int) or completed_trade_cycles < 0:
        raise CooldownPolicyError("completed_trade_cycles must be non-negative")
    until = started + timedelta(hours=policy.minimum_hours)
    cycles_remaining = max(0, policy.required_trade_cycles - completed_trade_cycles)
    risk_effect = classify_risk_effect(action)
    if risk_effect is not RiskEffect.INCREASE_RISK:
        return CooldownEvaluation(CooldownDisposition.RELEASED, risk_effect, until, completed_trade_cycles, cycles_remaining, "risk_reducing_or_neutral_action_permitted")
    if now < until:
        return CooldownEvaluation(CooldownDisposition.ACTIVE, risk_effect, until, completed_trade_cycles, cycles_remaining, "cooldown_time_floor_active")
    if cycles_remaining:
        return CooldownEvaluation(CooldownDisposition.ACTIVE, risk_effect, until, completed_trade_cycles, cycles_remaining, "cooldown_trade_cycles_incomplete")
    return CooldownEvaluation(CooldownDisposition.RELEASED, risk_effect, until, completed_trade_cycles, 0, "cooldown_requirements_satisfied")


__all__ = ["COOLDOWN_POLICY_CONTRACT_VERSION", "CooldownDisposition", "CooldownEvaluation", "CooldownPolicy", "CooldownPolicyError", "evaluate_cooldown"]
