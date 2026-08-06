"""Deterministic Decimal-only fee, slippage, and funding contracts.

The backtest layer must treat execution costs as immutable input facts.  This
module is deliberately pure: it neither fetches a venue schedule nor mutates
an account.  Callers must persist or include the policy fingerprint alongside
the run before a result can be considered replayable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from .deterministic_backtest_contracts import BacktestSide


BACKTEST_COST_CONTRACT_VERSION = "backtest-cost-v1"
_TEN_THOUSAND = Decimal("10000")


class BacktestCostContractError(ValueError):
    """Raised when an execution-cost policy or calculation is unsafe."""


class BacktestLiquidityRole(str, Enum):
    MAKER = "maker"
    TAKER = "taker"


def coerce_backtest_liquidity_role(value: Any) -> BacktestLiquidityRole:
    """Normalize an equivalent enum loaded by an isolated test/module loader.

    Some research tests intentionally load domain modules from file paths to
    keep Flask and database imports out of the fixture.  That can create a
    second enum class with the same public value.  We accept only the exact
    canonical values and return this module's enum, preserving the fail-closed
    contract while avoiding an identity-only false rejection.
    """
    if isinstance(value, BacktestLiquidityRole):
        return value
    if type(value).__name__ != BacktestLiquidityRole.__name__:
        raise BacktestCostContractError("liquidity_role must be typed")
    raw = getattr(value, "value", None)
    try:
        return BacktestLiquidityRole(raw)
    except (TypeError, ValueError) as exc:
        raise BacktestCostContractError("liquidity_role must be typed") from exc


def _text(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(ord(char) > 127 or char.isspace() for char in value)
    ):
        raise BacktestCostContractError(f"{field_name} must be canonical ASCII text")
    return value


def _decimal(value: Any, field_name: str, *, non_negative: bool = False, upper: Decimal | None = None) -> Decimal:
    if isinstance(value, (float, bool)):
        raise BacktestCostContractError(f"{field_name} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BacktestCostContractError(f"{field_name} must be a decimal") from exc
    if not result.is_finite() or (non_negative and result < 0) or (upper is not None and result > upper):
        raise BacktestCostContractError(f"{field_name} has invalid numeric bounds")
    return result


def _canonical_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class BacktestCostPolicySnapshot:
    """Immutable cost inputs used by one deterministic backtest run."""

    policy_version: str
    valuation_ccy: str
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    buy_slippage_bps: Decimal
    sell_slippage_bps: Decimal
    funding_rate: Decimal
    funding_interval_seconds: int
    evidence_hash: str

    def __post_init__(self) -> None:
        _text(self.policy_version, "policy_version")
        currency = _text(self.valuation_ccy, "valuation_ccy")
        if currency != currency.upper():
            raise BacktestCostContractError("valuation_ccy must be uppercase")
        object.__setattr__(self, "valuation_ccy", currency)
        for name in ("maker_fee_rate", "taker_fee_rate", "buy_slippage_bps", "sell_slippage_bps"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, non_negative=True))
        for name in ("maker_fee_rate", "taker_fee_rate"):
            if getattr(self, name) > Decimal("1"):
                raise BacktestCostContractError(f"{name} cannot exceed one")
        for name in ("buy_slippage_bps", "sell_slippage_bps"):
            if getattr(self, name) >= _TEN_THOUSAND:
                raise BacktestCostContractError(f"{name} must stay below 10000 bps")
        object.__setattr__(self, "funding_rate", _decimal(self.funding_rate, "funding_rate", upper=Decimal("1")))
        if self.funding_rate < Decimal("-1"):
            raise BacktestCostContractError("funding_rate cannot be below -1")
        if (
            isinstance(self.funding_interval_seconds, bool)
            or not isinstance(self.funding_interval_seconds, int)
            or self.funding_interval_seconds <= 0
        ):
            raise BacktestCostContractError("funding_interval_seconds must be positive integer")
        _text(self.evidence_hash, "evidence_hash")

    def canonical_facts(self) -> dict[str, Any]:
        return {
            "contract_version": BACKTEST_COST_CONTRACT_VERSION,
            "policy_version": self.policy_version,
            "valuation_ccy": self.valuation_ccy,
            "maker_fee_rate": _canonical_decimal(self.maker_fee_rate),
            "taker_fee_rate": _canonical_decimal(self.taker_fee_rate),
            "buy_slippage_bps": _canonical_decimal(self.buy_slippage_bps),
            "sell_slippage_bps": _canonical_decimal(self.sell_slippage_bps),
            "funding_rate": _canonical_decimal(self.funding_rate),
            "funding_interval_seconds": self.funding_interval_seconds,
            "evidence_hash": self.evidence_hash,
        }


@dataclass(frozen=True, slots=True)
class BacktestExecutionCostFacts:
    """One fully explained cost calculation, suitable for replay."""

    policy_version: str
    side: BacktestSide
    liquidity_role: BacktestLiquidityRole
    reference_price: Decimal
    executed_price: Decimal
    notional: Decimal
    fee: Decimal
    funding: Decimal
    order_id: str = ""

    def __post_init__(self) -> None:
        _text(self.policy_version, "policy_version")
        if not isinstance(self.side, BacktestSide) or not isinstance(self.liquidity_role, BacktestLiquidityRole):
            raise BacktestCostContractError("side and liquidity_role must be typed")
        if self.order_id and (not isinstance(self.order_id, str) or self.order_id.strip() != self.order_id or any(ord(char) > 127 or char.isspace() for char in self.order_id)):
            raise BacktestCostContractError("order_id must be canonical ASCII text")
        for name in ("reference_price", "executed_price", "notional"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, non_negative=False))
            if getattr(self, name) <= 0:
                raise BacktestCostContractError(f"{name} must be positive")
        object.__setattr__(self, "fee", _decimal(self.fee, "fee", non_negative=True))
        object.__setattr__(self, "funding", _decimal(self.funding, "funding"))


def cost_policy_fingerprint(policy: BacktestCostPolicySnapshot) -> str:
    if not isinstance(policy, BacktestCostPolicySnapshot):
        raise BacktestCostContractError("policy must be typed")
    payload = json.dumps(policy.canonical_facts(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def execution_cost_fingerprint(facts: BacktestExecutionCostFacts) -> str:
    """Fingerprint one calculated cost fact without relying on float repr."""
    if not isinstance(facts, BacktestExecutionCostFacts):
        raise BacktestCostContractError("facts must be typed")
    payload = {
        "contract_version": BACKTEST_COST_CONTRACT_VERSION,
        "policy_version": facts.policy_version,
        "side": facts.side.value,
        "liquidity_role": facts.liquidity_role.value,
        "reference_price": _canonical_decimal(facts.reference_price),
        "executed_price": _canonical_decimal(facts.executed_price),
        "notional": _canonical_decimal(facts.notional),
        "fee": _canonical_decimal(facts.fee),
        "funding": _canonical_decimal(facts.funding),
        "order_id": facts.order_id,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def calculate_execution_costs(
    policy: BacktestCostPolicySnapshot,
    *,
    side: BacktestSide,
    liquidity_role: BacktestLiquidityRole,
    reference_price: Any,
    notional: Any,
    order_id: str = "",
) -> BacktestExecutionCostFacts:
    """Apply explicit costs without floats, hidden defaults, or live state."""

    if not isinstance(policy, BacktestCostPolicySnapshot):
        raise BacktestCostContractError("policy must be typed")
    liquidity_role = coerce_backtest_liquidity_role(liquidity_role)
    if not isinstance(side, BacktestSide) or not isinstance(liquidity_role, BacktestLiquidityRole):
        raise BacktestCostContractError("side and liquidity_role must be typed")
    price = _decimal(reference_price, "reference_price", non_negative=True)
    amount = _decimal(notional, "notional", non_negative=True)
    if price <= 0 or amount <= 0:
        raise BacktestCostContractError("reference_price and notional must be positive")
    bps = policy.buy_slippage_bps if side is BacktestSide.BUY else policy.sell_slippage_bps
    slippage = bps / _TEN_THOUSAND
    executed = price * (Decimal("1") + slippage if side is BacktestSide.BUY else Decimal("1") - slippage)
    if executed <= 0:
        raise BacktestCostContractError("slippage produces non-positive execution price")
    fee_rate = policy.maker_fee_rate if liquidity_role is BacktestLiquidityRole.MAKER else policy.taker_fee_rate
    fee = amount * fee_rate
    # Positive funding means a long pays and a short receives; negative funding
    # reverses that direction.  The signed amount remains a separate fact.
    funding = -amount * policy.funding_rate if side is BacktestSide.BUY else amount * policy.funding_rate
    return BacktestExecutionCostFacts(policy.policy_version, side, liquidity_role, price, executed, amount, fee, funding, order_id)


def calculate_realized_costs(
    policy: BacktestCostPolicySnapshot,
    *,
    side: BacktestSide,
    liquidity_role: BacktestLiquidityRole,
    executed_price: Any,
    notional: Any,
    order_id: str = "",
) -> BacktestExecutionCostFacts:
    """Calculate fee/funding for an already-priced fill without re-slipping it."""
    if not isinstance(policy, BacktestCostPolicySnapshot):
        raise BacktestCostContractError("policy must be typed")
    liquidity_role = coerce_backtest_liquidity_role(liquidity_role)
    if not isinstance(side, BacktestSide) or not isinstance(liquidity_role, BacktestLiquidityRole):
        raise BacktestCostContractError("side and liquidity_role must be typed")
    price = _decimal(executed_price, "executed_price", non_negative=True)
    amount = _decimal(notional, "notional", non_negative=True)
    if price <= 0 or amount <= 0:
        raise BacktestCostContractError("executed_price and notional must be positive")
    fee_rate = policy.maker_fee_rate if liquidity_role is BacktestLiquidityRole.MAKER else policy.taker_fee_rate
    fee = amount * fee_rate
    funding = -amount * policy.funding_rate if side is BacktestSide.BUY else amount * policy.funding_rate
    return BacktestExecutionCostFacts(policy.policy_version, side, liquidity_role, price, price, amount, fee, funding, order_id)


__all__ = [
    "BACKTEST_COST_CONTRACT_VERSION",
    "BacktestCostContractError",
    "BacktestCostPolicySnapshot",
    "BacktestExecutionCostFacts",
    "BacktestLiquidityRole",
    "coerce_backtest_liquidity_role",
    "calculate_execution_costs",
    "cost_policy_fingerprint",
    "calculate_realized_costs",
    "execution_cost_fingerprint",
]
