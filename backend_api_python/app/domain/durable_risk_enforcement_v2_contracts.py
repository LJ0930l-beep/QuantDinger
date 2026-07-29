"""Shared contract lock for durable-entry hard-risk enforcement V2.

The V2 boundary is deliberately independent from ``hard-risk-enforcement-v1``:
it reads only a typed ``DurableEntryGraphV2`` / durable-entry specification and
will never create, query, or imitate legacy command, intent, or economic-order
rows.  This module owns no database I/O or transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
from uuid import UUID, uuid5

from app.domain.canonical_entry_v2_contracts import DurableEntryGraphV2, EconomicOrderSubject
from app.domain.durable_entry_persistence_contracts import DURABLE_ENTRY_CONTRACT_VERSION
from app.domain.order_contracts import OrderAction, RiskEffect


DURABLE_RISK_ENFORCEMENT_V2_CONTRACT_VERSION = "durable-risk-enforcement-v2"
DURABLE_RISK_UUID_NAMESPACE = UUID("9d2ce9cf-7860-5e7d-a7c8-5e3aec2cc0e5")

DURABLE_RISK_POLICY_SNAPSHOT_TABLE = "qd_durable_risk_policy_snapshots"
DURABLE_RISK_INPUT_SNAPSHOT_TABLE = "qd_durable_risk_input_snapshots"
DURABLE_RISK_DECISION_TABLE = "qd_durable_risk_decisions"
DURABLE_RISK_RESERVATION_TABLE = "qd_durable_risk_reservations"
DURABLE_RISK_TABLES = (
    DURABLE_RISK_POLICY_SNAPSHOT_TABLE,
    DURABLE_RISK_INPUT_SNAPSHOT_TABLE,
    DURABLE_RISK_DECISION_TABLE,
    DURABLE_RISK_RESERVATION_TABLE,
)
DURABLE_RISK_INIT_MIRROR_POLICY = "full-sql-mirror"

# Typed columns, rather than JSON mirrors, are replay authority.  Lane B must
# mirror these expand-only definitions exactly in its migration and init.sql.
DURABLE_RISK_SCOPE_SQL_COLUMNS = (
    "contract_version", "command_id", "economic_order_id",
    "durable_entry_contract_version", "economic_fingerprint",
    "request_fingerprint", "tenant_id", "credential_id", "account_scope",
    "instrument_id", "market_type", "action", "risk_effect", "actor_type",
    "actor_id", "source", "mode", "correlation_id", "entry_occurred_at",
    "scope_fingerprint", "audit_fingerprint",
)
DURABLE_RISK_POLICY_SNAPSHOT_SQL_COLUMNS = (
    "id", *DURABLE_RISK_SCOPE_SQL_COLUMNS, "policy_hash", "policy_version",
    "valuation_currency", "max_gross_notional", "max_net_notional",
    "max_instrument_notional", "max_leverage", "minimum_available_margin",
    "max_daily_loss", "max_drawdown_ratio", "policy_payload_json", "created_at",
)
DURABLE_RISK_INPUT_SNAPSHOT_SQL_COLUMNS = (
    "id", *DURABLE_RISK_SCOPE_SQL_COLUMNS, "input_hash", "input_version",
    "valuation_currency", "gross_notional", "net_notional", "instrument_notional",
    "available_margin", "equity", "peak_equity", "daily_realized_pnl",
    "reconciliation_health", "market_data_health", "account_facts_verified",
    "global_kill_switch_version", "global_kill_switch_enabled", "global_kill_switch_mode",
    "account_kill_switch_version", "account_kill_switch_enabled", "account_kill_switch_mode",
    "strategy_kill_switch_version", "strategy_kill_switch_enabled", "strategy_kill_switch_mode",
    "exposure_payload_json", "kill_switch_payload_json", "observed_at", "created_at",
)
DURABLE_RISK_DECISION_SQL_COLUMNS = (
    "id", *DURABLE_RISK_SCOPE_SQL_COLUMNS, "policy_snapshot_id",
    "input_snapshot_id", "policy_hash", "input_hash", "decision_fingerprint",
    "allowed", "decision_status", "rejection_codes_json",
    "projected_gross_notional", "projected_net_notional",
    "projected_instrument_notional", "projected_available_margin",
    "projected_leverage", "projected_daily_loss", "projected_drawdown_ratio",
    "projected_risk_payload_json", "created_at",
)
DURABLE_RISK_RESERVATION_SQL_COLUMNS = (
    "id", *DURABLE_RISK_SCOPE_SQL_COLUMNS, "decision_id", "reservation_hash",
    "valuation_currency", "reserved_gross_notional", "reserved_net_notional",
    "reserved_instrument_notional", "reserved_margin", "state", "expires_at",
    "created_at",
)

DURABLE_RISK_DECISION_STATUSES = frozenset({"ALLOW", "DENY", "RECONCILIATION_REQUIRED"})
DURABLE_RISK_RESERVATION_ACTIONS = frozenset({OrderAction.OPEN, OrderAction.INCREASE})
DURABLE_RISK_RESERVATION_RISK_EFFECT = RiskEffect.INCREASE_RISK
DURABLE_RISK_APPEND_ONLY_TABLES = frozenset(DURABLE_RISK_TABLES)


class DurableRiskEnforcementV2Error(ValueError):
    """Raised when a V2 durable-risk fact is incomplete or unsafe."""


class DurableRiskUnsupportedAction(DurableRiskEnforcementV2Error):
    """Raised before SQL when an action has no durable hard-risk path."""


class DurableRiskConflict(DurableRiskEnforcementV2Error):
    """Future repository replay identity names non-identical V2 facts."""


class DurableRiskRepositoryError(RuntimeError):
    """Future typed database-boundary failure; raw driver errors never escape."""


class DurableRiskPersistDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


def _canonical_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise DurableRiskEnforcementV2Error(f"{field_name} must be a lowercase SHA-256 hex string")
    return value


def _canonical_json(material: object) -> str:
    try:
        return json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise DurableRiskEnforcementV2Error("durable risk material cannot be canonically encoded") from exc


def _fingerprint(material: object) -> str:
    return hashlib.sha256(_canonical_json(material).encode("ascii")).hexdigest()


def _uuid5(kind: str, material: object) -> str:
    if not isinstance(kind, str) or not kind or not kind.isascii():
        raise DurableRiskEnforcementV2Error("stable identifier kind must be canonical ASCII text")
    return str(uuid5(DURABLE_RISK_UUID_NAMESPACE, f"{DURABLE_RISK_ENFORCEMENT_V2_CONTRACT_VERSION}:{kind}:{_canonical_json(material)}"))


@dataclass(frozen=True, slots=True)
class DurableRiskScopeV2:
    """Lossless V2 risk scope derived only from a non-CANCEL durable graph."""

    graph: DurableEntryGraphV2
    contract_version: str = DURABLE_RISK_ENFORCEMENT_V2_CONTRACT_VERSION
    scope_fingerprint: str = field(init=False)
    audit_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.graph, DurableEntryGraphV2):
            raise DurableRiskEnforcementV2Error("durable risk scope requires DurableEntryGraphV2")
        if self.contract_version != DURABLE_RISK_ENFORCEMENT_V2_CONTRACT_VERSION:
            raise DurableRiskEnforcementV2Error("unsupported durable risk contract version")
        if self.graph.specification.action is OrderAction.CANCEL:
            raise DurableRiskUnsupportedAction("CANCEL cannot construct a durable hard-risk scope")
        if not isinstance(self.graph.subject, EconomicOrderSubject):
            raise DurableRiskEnforcementV2Error("non-CANCEL durable risk requires EconomicOrderSubject")
        specification = self.graph.specification
        if specification.risk_effect not in (RiskEffect.INCREASE_RISK, RiskEffect.REDUCE_RISK):
            raise DurableRiskEnforcementV2Error("durable hard-risk scope requires a non-neutral risk effect")
        scope = self.scope_material()
        audit = self.audit_material()
        object.__setattr__(self, "scope_fingerprint", _fingerprint(scope))
        object.__setattr__(self, "audit_fingerprint", _fingerprint(audit))

    @property
    def command_id(self) -> str:
        return self.graph.command_id

    @property
    def economic_order_id(self) -> str:
        return self.graph.subject.economic_order_id

    @property
    def durable_entry_contract_version(self) -> str:
        return DURABLE_ENTRY_CONTRACT_VERSION

    def scope_material(self) -> dict[str, object]:
        specification = self.graph.specification
        return {
            "contract_version": self.contract_version,
            "command_id": self.command_id,
            "economic_order_id": self.economic_order_id,
            "durable_entry_contract_version": self.durable_entry_contract_version,
            "economic_fingerprint": specification.economic_fingerprint,
            "tenant_id": specification.tenant_id,
            "credential_id": specification.credential_id,
            "account_scope": specification.account_scope,
            "instrument_id": specification.instrument_id,
            "market_type": specification.market_type,
            "action": specification.action.value,
            "risk_effect": specification.risk_effect.value,
        }

    def audit_material(self) -> dict[str, object]:
        specification = self.graph.specification
        return {
            "contract_version": self.contract_version,
            "request_fingerprint": specification.request_fingerprint,
            "actor_type": specification.actor.actor_type.value,
            "actor_id": specification.actor.actor_id,
            "source": specification.actor.entry_source.value,
            "mode": specification.mode.value,
            "correlation_id": specification.correlation_id,
            "entry_occurred_at": specification.occurred_at.isoformat(),
        }


def build_durable_risk_scope_v2(graph: DurableEntryGraphV2) -> DurableRiskScopeV2:
    """Fail closed before evaluator or SQL for CANCEL and untyped graphs."""

    return DurableRiskScopeV2(graph)


def stable_policy_snapshot_id(scope: DurableRiskScopeV2, policy_hash: str) -> str:
    return _uuid5("policy-snapshot", {"scope": scope.scope_fingerprint, "policy_hash": _canonical_hash(policy_hash, "policy_hash")})


def stable_input_snapshot_id(scope: DurableRiskScopeV2, input_hash: str) -> str:
    return _uuid5("input-snapshot", {"scope": scope.scope_fingerprint, "input_hash": _canonical_hash(input_hash, "input_hash")})


def stable_decision_id(scope: DurableRiskScopeV2, policy_hash: str, input_hash: str) -> str:
    return _uuid5("decision", {
        "command_id": scope.command_id,
        "economic_fingerprint": scope.graph.specification.economic_fingerprint,
        "policy_hash": _canonical_hash(policy_hash, "policy_hash"),
        "input_hash": _canonical_hash(input_hash, "input_hash"),
    })


def stable_reservation_id(decision_id: UUID | str, reservation_demand_hash: str) -> str:
    try:
        normalized_decision_id = str(UUID(str(decision_id))).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise DurableRiskEnforcementV2Error("decision_id must be UUID") from exc
    return _uuid5("reservation", {
        "decision_id": normalized_decision_id,
        "reservation_demand_hash": _canonical_hash(reservation_demand_hash, "reservation_demand_hash"),
    })
