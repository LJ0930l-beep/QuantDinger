"""Pure, fail-closed hard-risk contracts and deterministic reducers.

This module is intentionally not wired into a gateway, worker, database, or
exchange adapter.  It defines the arithmetic and policy boundary that a
future durable command gateway must evaluate atomically with a risk
reservation.  No actor can supply an override for a hard rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Iterable

from app.domain.decimal_values import (
    DecimalInput,
    DecimalValueError,
    QuoteAmount,
    canonical_decimal_string,
    fit_calculated_decimal,
    validate_numeric_38_18,
)
from app.domain.order_contracts import (
    Actor,
    OrderAction,
    ReconciliationHealth,
    RiskEffect,
    classify_risk_effect,
    is_action_allowed,
)


HARD_RISK_CONTRACT_VERSION = "hard-risk-contract-v1"


class HardRiskContractError(ValueError):
    """Raised when a hard-risk input is non-canonical or unsafe."""


class ReservationReducerError(HardRiskContractError):
    """Raised when reservation facts cannot be safely reduced together."""


class KillSwitchMode(str, Enum):
    OPEN_BLOCKED = "OPEN_BLOCKED"
    ALL_NEW_COMMANDS_BLOCKED = "ALL_NEW_COMMANDS_BLOCKED"
    EMERGENCY_REDUCE_ONLY = "EMERGENCY_REDUCE_ONLY"


class MarketDataHealth(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class RiskRejectionCode(str, Enum):
    RECONCILIATION_UNHEALTHY = "RECONCILIATION_UNHEALTHY"
    KILL_SWITCH = "KILL_SWITCH"
    MARKET_DATA_NOT_FRESH = "MARKET_DATA_NOT_FRESH"
    ACCOUNT_FACTS_UNVERIFIED = "ACCOUNT_FACTS_UNVERIFIED"
    RISK_DELTA_NOT_REDUCE_ONLY = "RISK_DELTA_NOT_REDUCE_ONLY"
    GROSS_NOTIONAL_LIMIT = "GROSS_NOTIONAL_LIMIT"
    NET_NOTIONAL_LIMIT = "NET_NOTIONAL_LIMIT"
    INSTRUMENT_NOTIONAL_LIMIT = "INSTRUMENT_NOTIONAL_LIMIT"
    LEVERAGE_LIMIT = "LEVERAGE_LIMIT"
    AVAILABLE_MARGIN_LIMIT = "AVAILABLE_MARGIN_LIMIT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"


def _canonical_text(value: object, field_name: str, *, uppercase: bool = False) -> str:
    if not isinstance(value, str):
        raise HardRiskContractError(f"{field_name} must be text")
    result = value.strip()
    if not result or result != value or len(result) > 160 or not result.isascii():
        raise HardRiskContractError(f"{field_name} must be canonical ASCII text")
    return result.upper() if uppercase else result


def _decimal(value: DecimalInput, field_name: str) -> Decimal:
    try:
        return validate_numeric_38_18(value)
    except (DecimalValueError, TypeError) as exc:
        raise HardRiskContractError(f"{field_name} is not a valid NUMERIC(38,18) value") from exc


def _non_negative(value: DecimalInput, field_name: str) -> Decimal:
    parsed = _decimal(value, field_name)
    if parsed < 0:
        raise HardRiskContractError(f"{field_name} cannot be negative")
    return parsed


def _positive(value: DecimalInput, field_name: str) -> Decimal:
    parsed = _non_negative(value, field_name)
    if parsed == 0:
        raise HardRiskContractError(f"{field_name} must be greater than zero")
    return parsed


def _fit(value: Decimal) -> Decimal:
    try:
        return fit_calculated_decimal(value)
    except DecimalValueError as exc:
        raise HardRiskContractError("hard-risk calculation cannot fit NUMERIC(38,18)") from exc


def _effect(action: OrderAction | str, explicit_effect: RiskEffect | str | None) -> RiskEffect:
    try:
        return classify_risk_effect(action, protection_effect=explicit_effect)
    except (TypeError, ValueError) as exc:
        raise HardRiskContractError("action must have an unambiguous risk effect") from exc


@dataclass(frozen=True, slots=True)
class RiskLimitPolicy:
    """Versioned limits in one explicitly named valuation currency."""

    policy_version: str
    valuation_currency: str
    max_gross_notional: QuoteAmount
    max_net_notional: QuoteAmount
    max_instrument_notional: QuoteAmount
    max_leverage: DecimalInput
    minimum_available_margin: QuoteAmount
    max_daily_loss: QuoteAmount
    max_drawdown_ratio: DecimalInput

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _canonical_text(self.policy_version, "policy_version"))
        object.__setattr__(self, "valuation_currency", _canonical_text(self.valuation_currency, "valuation_currency", uppercase=True))
        for name in (
            "max_gross_notional", "max_net_notional", "max_instrument_notional",
            "minimum_available_margin", "max_daily_loss",
        ):
            value = getattr(self, name)
            if not isinstance(value, QuoteAmount):
                raise HardRiskContractError(f"{name} must use QuoteAmount")
        object.__setattr__(self, "max_leverage", _positive(self.max_leverage, "max_leverage"))
        drawdown = _non_negative(self.max_drawdown_ratio, "max_drawdown_ratio")
        if drawdown > Decimal("1"):
            raise HardRiskContractError("max_drawdown_ratio cannot exceed one")
        object.__setattr__(self, "max_drawdown_ratio", drawdown)


@dataclass(frozen=True, slots=True)
class RiskExposureSnapshot:
    """Immutable account truth used by a single risk evaluation."""

    account_scope: str
    instrument_id: str
    valuation_currency: str
    gross_notional: DecimalInput
    net_notional: DecimalInput
    instrument_notional: DecimalInput
    available_margin: DecimalInput
    equity: DecimalInput
    peak_equity: DecimalInput
    daily_realized_pnl: DecimalInput
    reconciliation_health: ReconciliationHealth
    market_data_health: MarketDataHealth
    account_facts_verified: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_scope", _canonical_text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _canonical_text(self.instrument_id, "instrument_id", uppercase=True))
        object.__setattr__(self, "valuation_currency", _canonical_text(self.valuation_currency, "valuation_currency", uppercase=True))
        for name in ("gross_notional", "instrument_notional", "available_margin", "equity", "peak_equity"):
            object.__setattr__(self, name, _non_negative(getattr(self, name), name))
        object.__setattr__(self, "net_notional", _decimal(self.net_notional, "net_notional"))
        object.__setattr__(self, "daily_realized_pnl", _decimal(self.daily_realized_pnl, "daily_realized_pnl"))
        if self.equity <= 0:
            raise HardRiskContractError("equity must be greater than zero")
        if self.peak_equity < self.equity:
            raise HardRiskContractError("peak_equity cannot be below equity")
        if not isinstance(self.reconciliation_health, ReconciliationHealth):
            raise HardRiskContractError("reconciliation_health must use ReconciliationHealth")
        if not isinstance(self.market_data_health, MarketDataHealth):
            raise HardRiskContractError("market_data_health must use MarketDataHealth")
        if not isinstance(self.account_facts_verified, bool):
            raise HardRiskContractError("account_facts_verified must be bool")


@dataclass(frozen=True, slots=True)
class RiskReservationDemand:
    """The immutable capacity held by one active reservation."""

    reservation_id: str
    account_scope: str
    instrument_id: str
    valuation_currency: str
    gross_notional: DecimalInput
    net_notional: DecimalInput
    instrument_notional: DecimalInput
    margin: DecimalInput

    def __post_init__(self) -> None:
        object.__setattr__(self, "reservation_id", _canonical_text(self.reservation_id, "reservation_id"))
        object.__setattr__(self, "account_scope", _canonical_text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _canonical_text(self.instrument_id, "instrument_id", uppercase=True))
        object.__setattr__(self, "valuation_currency", _canonical_text(self.valuation_currency, "valuation_currency", uppercase=True))
        for name in ("gross_notional", "instrument_notional", "margin"):
            object.__setattr__(self, name, _non_negative(getattr(self, name), name))
        object.__setattr__(self, "net_notional", _decimal(self.net_notional, "net_notional"))


@dataclass(frozen=True, slots=True)
class ReducedReservationDemand:
    account_scope: str
    instrument_id: str
    valuation_currency: str
    gross_notional: Decimal
    net_notional: Decimal
    instrument_notional: Decimal
    margin: Decimal
    reservation_ids: tuple[str, ...]


def reduce_active_reservations(
    reservations: Iterable[RiskReservationDemand],
    *,
    account_scope: str,
    instrument_id: str,
    valuation_currency: str,
) -> ReducedReservationDemand:
    """Reduce active reservations deterministically, rejecting mixed scopes."""

    scope = _canonical_text(account_scope, "account_scope")
    instrument = _canonical_text(instrument_id, "instrument_id", uppercase=True)
    currency = _canonical_text(valuation_currency, "valuation_currency", uppercase=True)
    seen: set[str] = set()
    gross = net = instrument_total = margin = Decimal("0")
    identifiers: list[str] = []
    for item in reservations:
        if not isinstance(item, RiskReservationDemand):
            raise ReservationReducerError("reservation must use RiskReservationDemand")
        if (item.account_scope, item.instrument_id, item.valuation_currency) != (scope, instrument, currency):
            raise ReservationReducerError("reservation scope does not match risk evaluation scope")
        if item.reservation_id in seen:
            raise ReservationReducerError("duplicate active reservation identity")
        seen.add(item.reservation_id)
        identifiers.append(item.reservation_id)
        gross += item.gross_notional
        net += item.net_notional
        instrument_total += item.instrument_notional
        margin += item.margin
    return ReducedReservationDemand(
        scope, instrument, currency, _fit(gross), _fit(net), _fit(instrument_total), _fit(margin), tuple(sorted(identifiers))
    )


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    """An explicitly observed switch state; disabled is not an unknown value."""

    version: int
    enabled: bool
    mode: KillSwitchMode | None = None

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise HardRiskContractError("kill switch version must be a non-negative integer")
        if not isinstance(self.enabled, bool):
            raise HardRiskContractError("kill switch enabled must be bool")
        if self.enabled and not isinstance(self.mode, KillSwitchMode):
            raise HardRiskContractError("an enabled kill switch requires KillSwitchMode")
        if not self.enabled and self.mode is not None:
            raise HardRiskContractError("a disabled kill switch cannot carry a mode")


@dataclass(frozen=True, slots=True)
class KillSwitchSnapshot:
    """All three switch scopes must be explicitly observed to evaluate risk."""

    global_state: KillSwitchState
    account_state: KillSwitchState
    strategy_state: KillSwitchState

    def __post_init__(self) -> None:
        if not all(isinstance(value, KillSwitchState) for value in (self.global_state, self.account_state, self.strategy_state)):
            raise HardRiskContractError("all kill switch scopes must use KillSwitchState")


@dataclass(frozen=True, slots=True)
class HardRiskRequest:
    """A proposed command's incremental reservation demand, never an override."""

    action: OrderAction
    actor: Actor
    risk_effect: RiskEffect | None
    gross_notional: DecimalInput
    net_notional: DecimalInput
    instrument_notional: DecimalInput
    margin: DecimalInput

    def __post_init__(self) -> None:
        if not isinstance(self.action, OrderAction) or not isinstance(self.actor, Actor):
            raise HardRiskContractError("action and actor must use PR-00 enums")
        effect = _effect(self.action, self.risk_effect)
        object.__setattr__(self, "risk_effect", effect)
        for name in ("gross_notional", "instrument_notional", "margin"):
            object.__setattr__(self, name, _non_negative(getattr(self, name), name))
        object.__setattr__(self, "net_notional", _decimal(self.net_notional, "net_notional"))
        if effect is not RiskEffect.INCREASE_RISK and any(
            value != 0 for value in (self.gross_notional, self.net_notional, self.instrument_notional, self.margin)
        ):
            raise HardRiskContractError("non-increasing actions must carry a zero risk reservation demand")


@dataclass(frozen=True, slots=True)
class ProjectedRiskExposure:
    gross_notional: Decimal
    net_notional: Decimal
    instrument_notional: Decimal
    available_margin: Decimal
    leverage: Decimal
    daily_loss: Decimal
    drawdown_ratio: Decimal


@dataclass(frozen=True, slots=True)
class HardRiskDecision:
    policy_version: str
    account_scope: str
    instrument_id: str
    valuation_currency: str
    action: OrderAction
    risk_effect: RiskEffect
    allowed: bool
    rejections: tuple[RiskRejectionCode, ...]
    projected: ProjectedRiskExposure
    canonical_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _canonical_text(self.policy_version, "policy_version"))
        object.__setattr__(self, "account_scope", _canonical_text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _canonical_text(self.instrument_id, "instrument_id", uppercase=True))
        object.__setattr__(self, "valuation_currency", _canonical_text(self.valuation_currency, "valuation_currency", uppercase=True))
        if not isinstance(self.action, OrderAction) or not isinstance(self.risk_effect, RiskEffect):
            raise HardRiskContractError("decision action and risk_effect must use PR-00 enums")
        if not isinstance(self.projected, ProjectedRiskExposure):
            raise HardRiskContractError("decision projected must use ProjectedRiskExposure")
        if self.allowed != (not self.rejections):
            raise HardRiskContractError("decision allowed flag must match rejection facts")
        material = {
            "version": HARD_RISK_CONTRACT_VERSION,
            "policy_version": self.policy_version,
            "account_scope": self.account_scope,
            "instrument_id": self.instrument_id,
            "valuation_currency": self.valuation_currency,
            "action": self.action.value,
            "risk_effect": self.risk_effect.value,
            "allowed": self.allowed,
            "rejections": [item.value for item in self.rejections],
            "projected": {name: canonical_decimal_string(getattr(self.projected, name)) for name in (
                "gross_notional", "net_notional", "instrument_notional", "available_margin", "leverage", "daily_loss", "drawdown_ratio"
            )},
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        object.__setattr__(self, "canonical_fingerprint", hashlib.sha256(encoded).hexdigest())


def _kill_switch_allows(state: KillSwitchState, effect: RiskEffect) -> bool:
    if not state.enabled:
        return True
    mode = state.mode
    if mode is None:
        return False
    if mode is KillSwitchMode.OPEN_BLOCKED:
        return effect is not RiskEffect.INCREASE_RISK
    if mode is KillSwitchMode.ALL_NEW_COMMANDS_BLOCKED:
        return False
    return effect is RiskEffect.REDUCE_RISK


def _project(snapshot: RiskExposureSnapshot, reserved: ReducedReservationDemand, request: HardRiskRequest) -> ProjectedRiskExposure:
    gross = _fit(snapshot.gross_notional + reserved.gross_notional + request.gross_notional)
    net = _fit(snapshot.net_notional + reserved.net_notional + request.net_notional)
    instrument = _fit(snapshot.instrument_notional + reserved.instrument_notional + request.instrument_notional)
    margin = _fit(snapshot.available_margin - reserved.margin - request.margin)
    leverage = _fit(gross / snapshot.equity)
    daily_loss = _fit(max(Decimal("0"), -snapshot.daily_realized_pnl))
    drawdown = _fit((snapshot.peak_equity - snapshot.equity) / snapshot.peak_equity)
    return ProjectedRiskExposure(gross, net, instrument, margin, leverage, daily_loss, drawdown)


def evaluate_hard_risk(
    *,
    policy: RiskLimitPolicy,
    snapshot: RiskExposureSnapshot,
    request: HardRiskRequest,
    kill_switches: KillSwitchSnapshot,
    active_reservations: Iterable[RiskReservationDemand] = (),
) -> HardRiskDecision:
    """Evaluate a command deterministically without actor overrides or I/O.

    Reduce/close/cancel paths retain their safety value during degraded health,
    while every new risk increase fails closed on unhealthy reconciliation,
    stale market data, or unverified account facts.
    """

    if snapshot.valuation_currency != policy.valuation_currency:
        raise HardRiskContractError("snapshot and policy valuation currencies must match")
    reserved = reduce_active_reservations(
        active_reservations,
        account_scope=snapshot.account_scope,
        instrument_id=snapshot.instrument_id,
        valuation_currency=snapshot.valuation_currency,
    )
    projected = _project(snapshot, reserved, request)
    reasons: set[RiskRejectionCode] = set()
    effect = request.risk_effect
    if not is_action_allowed(request.action, snapshot.reconciliation_health, risk_effect=effect, actor=request.actor):
        reasons.add(RiskRejectionCode.RECONCILIATION_UNHEALTHY)
    if any(not _kill_switch_allows(state, effect) for state in (kill_switches.global_state, kill_switches.account_state, kill_switches.strategy_state)):
        reasons.add(RiskRejectionCode.KILL_SWITCH)
    if effect is RiskEffect.INCREASE_RISK:
        if snapshot.market_data_health is not MarketDataHealth.FRESH:
            reasons.add(RiskRejectionCode.MARKET_DATA_NOT_FRESH)
        if not snapshot.account_facts_verified:
            reasons.add(RiskRejectionCode.ACCOUNT_FACTS_UNVERIFIED)
        if projected.gross_notional > policy.max_gross_notional.value:
            reasons.add(RiskRejectionCode.GROSS_NOTIONAL_LIMIT)
        if abs(projected.net_notional) > policy.max_net_notional.value:
            reasons.add(RiskRejectionCode.NET_NOTIONAL_LIMIT)
        if projected.instrument_notional > policy.max_instrument_notional.value:
            reasons.add(RiskRejectionCode.INSTRUMENT_NOTIONAL_LIMIT)
        if projected.leverage > policy.max_leverage:
            reasons.add(RiskRejectionCode.LEVERAGE_LIMIT)
        if projected.available_margin < policy.minimum_available_margin.value:
            reasons.add(RiskRejectionCode.AVAILABLE_MARGIN_LIMIT)
        if projected.daily_loss > policy.max_daily_loss.value:
            reasons.add(RiskRejectionCode.DAILY_LOSS_LIMIT)
        if projected.drawdown_ratio > policy.max_drawdown_ratio:
            reasons.add(RiskRejectionCode.DRAWDOWN_LIMIT)
    return HardRiskDecision(
        policy.policy_version,
        snapshot.account_scope,
        snapshot.instrument_id,
        snapshot.valuation_currency,
        request.action,
        request.risk_effect,
        not reasons,
        tuple(sorted(reasons, key=lambda item: item.value)),
        projected,
    )
