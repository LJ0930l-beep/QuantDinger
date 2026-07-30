"""Typed, persisted-source contract for runtime hard-risk facts.

This module locks the source-selection boundary before a runtime provider is
introduced.  It does not access a database, create a connection, evaluate a
trade, or control a transaction.  Repository code must turn persisted rows
into these records and fail closed before it invokes hard-risk evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.domain.canonical_entry_contracts import EntrySource
from app.domain.canonical_entry_v2_contracts import DurableEntryGraphV2, EconomicOrderSubject
from app.domain.decimal_values import Price
from app.domain.hard_risk_contracts import KillSwitchState, RiskExposureSnapshot, RiskLimitPolicy
from app.domain.order_contracts import OrderAction, RiskEffect


AUTHORITATIVE_RISK_FACTS_CONTRACT_VERSION = "authoritative-risk-facts-v1"
NON_STRATEGY_SCOPE = "__NON_STRATEGY__"


class RiskFactsError(ValueError):
    """Base fail-closed error for authoritative source selection."""


class RiskFactsUnavailable(RiskFactsError):
    """A required persisted source has no eligible observation."""


class RiskFactsStale(RiskFactsError):
    """A persisted source exists but exceeds its explicit age budget."""


class RiskFactsScopeConflict(RiskFactsError):
    """A source row does not bind the exact durable-entry scope."""


class RiskFactsVersionConflict(RiskFactsError):
    """A source identity/version cannot be replayed consistently."""


class RiskFactsAmbiguous(RiskFactsError):
    """More than one equally eligible source is present."""


class RiskCapacityConflict(RiskFactsError):
    """A capacity lock or active-reservation observation conflicts."""


class RiskFactsRepositoryError(RuntimeError):
    """Typed repository boundary; raw database errors must not escape."""


class RiskFactSourceKind(str, Enum):
    POLICY = "POLICY"
    ACCOUNT = "ACCOUNT"
    MARKET = "MARKET"
    KILL_SWITCH_GLOBAL = "KILL_SWITCH_GLOBAL"
    KILL_SWITCH_ACCOUNT = "KILL_SWITCH_ACCOUNT"
    KILL_SWITCH_STRATEGY = "KILL_SWITCH_STRATEGY"
    RECONCILIATION = "RECONCILIATION"
    ACTIVE_RESERVATIONS = "ACTIVE_RESERVATIONS"


class MarketPriceType(str, Enum):
    LAST = "LAST"
    MARK = "MARK"
    INDEX = "INDEX"


def _text(value: object, name: str, *, uppercase: bool = False, lowercase: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise RiskFactsError(f"{name} must be canonical ASCII text")
    if uppercase:
        value = value.upper()
    if lowercase:
        value = value.lower()
    return value


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise RiskFactsError(f"{name} must be UTC")
    return value.astimezone(timezone.utc)


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RiskFactsError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class AuthoritativeRiskFactScope:
    """Exact scope used for every authoritative source lookup and capacity lock."""

    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    strategy_scope: str

    @classmethod
    def from_graph(cls, graph: DurableEntryGraphV2) -> "AuthoritativeRiskFactScope":
        if not isinstance(graph, DurableEntryGraphV2) or not isinstance(graph.subject, EconomicOrderSubject):
            raise RiskFactsScopeConflict("authoritative risk facts require a non-CANCEL durable graph")
        request = graph.specification
        strategy_scope = request.actor.actor_id if request.actor.entry_source is EntrySource.STRATEGY else NON_STRATEGY_SCOPE
        return cls(
            request.tenant_id,
            request.credential_id,
            request.account_scope,
            request.instrument_id,
            request.market_type,
            strategy_scope,
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _positive_int(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "credential_id", _positive_int(self.credential_id, "credential_id"))
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", uppercase=True))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lowercase=True))
        object.__setattr__(self, "strategy_scope", _text(self.strategy_scope, "strategy_scope"))


@dataclass(frozen=True, slots=True)
class RiskFactProvenance:
    """Immutable identity for a single persisted authoritative observation."""

    source_kind: RiskFactSourceKind
    source_identity: str
    source_version: str
    source_fingerprint: str
    observed_at: datetime
    max_age_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, RiskFactSourceKind):
            raise RiskFactsError("source_kind must be typed")
        object.__setattr__(self, "source_identity", _text(self.source_identity, "source_identity"))
        object.__setattr__(self, "source_version", _text(self.source_version, "source_version"))
        fingerprint = _text(self.source_fingerprint, "source_fingerprint")
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise RiskFactsError("source_fingerprint must be lowercase SHA-256")
        object.__setattr__(self, "source_fingerprint", fingerprint)
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if isinstance(self.max_age_seconds, bool) or not isinstance(self.max_age_seconds, int) or self.max_age_seconds < 0:
            raise RiskFactsError("max_age_seconds must be a non-negative integer")

    def validate_selection_anchor(self, occurred_at: datetime) -> None:
        anchor = _utc(occurred_at, "occurred_at")
        if self.observed_at > anchor:
            raise RiskFactsVersionConflict("authoritative observation is after durable-entry anchor")
        if anchor - self.observed_at > timedelta(seconds=self.max_age_seconds):
            raise RiskFactsStale("authoritative observation exceeds its age budget")


@dataclass(frozen=True, slots=True)
class AuthoritativePolicyRecord:
    scope: AuthoritativeRiskFactScope
    provenance: RiskFactProvenance
    policy: RiskLimitPolicy
    reservation_ttl_seconds: int

    def __post_init__(self) -> None:
        if self.provenance.source_kind is not RiskFactSourceKind.POLICY or not isinstance(self.policy, RiskLimitPolicy):
            raise RiskFactsError("policy record must contain a typed policy source")
        if isinstance(self.reservation_ttl_seconds, bool) or not isinstance(self.reservation_ttl_seconds, int) or self.reservation_ttl_seconds <= 0:
            raise RiskFactsError("reservation_ttl_seconds must be positive")


@dataclass(frozen=True, slots=True)
class AuthoritativeAccountFactsRecord:
    scope: AuthoritativeRiskFactScope
    provenance: RiskFactProvenance
    exposure: RiskExposureSnapshot

    def __post_init__(self) -> None:
        if self.provenance.source_kind is not RiskFactSourceKind.ACCOUNT or not isinstance(self.exposure, RiskExposureSnapshot):
            raise RiskFactsError("account facts record must contain a typed account source")
        if (self.exposure.account_scope, self.exposure.instrument_id) != (self.scope.account_scope, self.scope.instrument_id):
            raise RiskFactsScopeConflict("account facts do not match authoritative scope")


@dataclass(frozen=True, slots=True)
class AuthoritativeMarketObservation:
    scope: AuthoritativeRiskFactScope
    provenance: RiskFactProvenance
    valuation_currency: str
    price_type: MarketPriceType
    price: Price

    def __post_init__(self) -> None:
        if self.provenance.source_kind is not RiskFactSourceKind.MARKET or not isinstance(self.price_type, MarketPriceType) or not isinstance(self.price, Price):
            raise RiskFactsError("market observation must use typed source and price")
        object.__setattr__(self, "valuation_currency", _text(self.valuation_currency, "valuation_currency", uppercase=True))


@dataclass(frozen=True, slots=True)
class AuthoritativeKillSwitchRecord:
    scope: AuthoritativeRiskFactScope
    provenance: RiskFactProvenance
    state: KillSwitchState

    def __post_init__(self) -> None:
        if self.provenance.source_kind not in {
            RiskFactSourceKind.KILL_SWITCH_GLOBAL,
            RiskFactSourceKind.KILL_SWITCH_ACCOUNT,
            RiskFactSourceKind.KILL_SWITCH_STRATEGY,
        } or not isinstance(self.state, KillSwitchState):
            raise RiskFactsError("kill switch record must use typed source and state")


def required_market_price_type(graph: DurableEntryGraphV2) -> MarketPriceType:
    """Lock valuation selection; no fallback to limit or last price is permitted."""

    if not isinstance(graph, DurableEntryGraphV2):
        raise RiskFactsError("graph must be DurableEntryGraphV2")
    intent = graph.specification.economic_intent
    if graph.specification.action not in {OrderAction.OPEN, OrderAction.INCREASE}:
        raise RiskFactsError("only OPEN/INCREASE requires a valuation observation")
    if intent.execution_kind is None:
        raise RiskFactsError("execution_kind is required for valuation")
    if intent.execution_kind.value in {"MARKET", "LIMIT"}:
        return MarketPriceType.MARK
    if intent.trigger_price_type is None:
        raise RiskFactsUnavailable("STOP valuation requires explicit trigger price type")
    return MarketPriceType(intent.trigger_price_type.value)


@dataclass(frozen=True, slots=True)
class AuthoritativeRiskFactsSelection:
    """Complete selected source set before V2 risk facts can be evaluated."""

    scope: AuthoritativeRiskFactScope
    selection_anchor: datetime
    policy: AuthoritativePolicyRecord
    account: AuthoritativeAccountFactsRecord
    global_kill_switch: AuthoritativeKillSwitchRecord
    account_kill_switch: AuthoritativeKillSwitchRecord
    strategy_kill_switch: AuthoritativeKillSwitchRecord
    reconciliation_checkpoint_id: str
    reconciliation_checkpoint_version: int
    market: AuthoritativeMarketObservation | None
    active_reservation_set_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "selection_anchor", _utc(self.selection_anchor, "selection_anchor"))
        records = (self.policy, self.account, self.global_kill_switch, self.account_kill_switch, self.strategy_kill_switch)
        if any(record.scope != self.scope for record in records):
            raise RiskFactsScopeConflict("authoritative source scope mismatch")
        for record in records:
            record.provenance.validate_selection_anchor(self.selection_anchor)
        if self.market is not None:
            if self.market.scope != self.scope:
                raise RiskFactsScopeConflict("market observation scope mismatch")
            self.market.provenance.validate_selection_anchor(self.selection_anchor)
        object.__setattr__(self, "reconciliation_checkpoint_id", _text(self.reconciliation_checkpoint_id, "reconciliation_checkpoint_id"))
        if isinstance(self.reconciliation_checkpoint_version, bool) or not isinstance(self.reconciliation_checkpoint_version, int) or self.reconciliation_checkpoint_version < 0:
            raise RiskFactsError("reconciliation_checkpoint_version must be non-negative")
        fingerprint = _text(self.active_reservation_set_fingerprint, "active_reservation_set_fingerprint")
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise RiskFactsError("active_reservation_set_fingerprint must be lowercase SHA-256")
        object.__setattr__(self, "active_reservation_set_fingerprint", fingerprint)

    def require_market_for(self, graph: DurableEntryGraphV2) -> AuthoritativeMarketObservation:
        if graph.specification.risk_effect is not RiskEffect.INCREASE_RISK:
            raise RiskFactsError("reducing actions must not request a valuation observation")
        if self.market is None:
            raise RiskFactsUnavailable("OPEN/INCREASE requires a persisted market observation")
        if self.market.price_type is not required_market_price_type(graph):
            raise RiskFactsScopeConflict("market price type does not match execution contract")
        if self.market.valuation_currency != self.policy.policy.valuation_currency:
            raise RiskFactsScopeConflict("market valuation currency does not match policy")
        return self.market
