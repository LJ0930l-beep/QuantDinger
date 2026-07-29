"""Shared immutable contract for Canonical Entry V2 PostgreSQL persistence.

This module locks the durable-entry boundary before the repository and schema
lanes diverge.  It performs no database I/O and owns no transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from app.domain.canonical_entry_v2_contracts import CancelTargetSubject, EconomicOrderSubject
from app.domain.order_contracts import OrderAction, RiskEffect


DURABLE_ENTRY_CONTRACT_VERSION = "canonical-entry-v2"
DURABLE_ENTRY_SPECIFICATION_TABLE = "qd_durable_entry_specifications"

# The unique database identity deliberately excludes mutable audit context and
# database-generated facts.  Replay still compares every authoritative fact.
DURABLE_ENTRY_IDEMPOTENCY_COLUMNS = (
    "tenant_id",
    "credential_id",
    "account_scope",
    "idempotency_key",
    "contract_version",
)

DURABLE_ENTRY_SQL_COLUMNS = (
    "contract_version",
    "command_id",
    "tenant_id",
    "credential_id",
    "account_scope",
    "instrument_id",
    "market_type",
    "action",
    "risk_effect",
    "side",
    "quantity",
    "quantity_semantics",
    "execution_kind",
    "limit_price",
    "trigger_price",
    "trigger_direction",
    "trigger_price_type",
    "reduce_only",
    "position_side",
    "cancel_target_kind",
    "cancel_target_id",
    "target_position_id",
    "close_quantity",
    "close_all",
    "economic_order_id",
    "economic_fingerprint",
    "request_fingerprint",
    "actor_type",
    "actor_id",
    "source",
    "mode",
    "idempotency_key",
    "correlation_id",
    "occurred_at",
    "created_at",
)

# JSON can be an audit mirror in a future migration, but cannot replace any
# entry in this tuple and is never replay authority.
DURABLE_ENTRY_AUTHORITATIVE_COLUMNS = tuple(
    column for column in DURABLE_ENTRY_SQL_COLUMNS if column != "created_at"
)
DURABLE_ENTRY_AUDIT_COLUMNS = (
    "actor_type",
    "actor_id",
    "source",
    "mode",
    "correlation_id",
    "occurred_at",
)
DURABLE_ENTRY_INIT_MIRROR_POLICY = "full-sql-mirror"


class DurableEntryPersistDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


class DurableEntryRepositoryError(RuntimeError):
    """Typed database-boundary failure for durable Canonical Entry facts."""


class DurableEntryConflict(DurableEntryRepositoryError):
    """A business idempotency identity names differing authoritative facts."""


class DurableEntryIntegrityError(DurableEntryRepositoryError):
    """A graph or persisted row violates the durable-entry contract."""


DurableEntrySubject: TypeAlias = EconomicOrderSubject | CancelTargetSubject


@dataclass(frozen=True, slots=True)
class DurableEntryActionRule:
    risk_effect: RiskEffect
    subject_type: type[EconomicOrderSubject] | type[CancelTargetSubject]
    economic_order_required: bool


DURABLE_ENTRY_ACTION_MATRIX = {
    OrderAction.OPEN: DurableEntryActionRule(RiskEffect.INCREASE_RISK, EconomicOrderSubject, True),
    OrderAction.INCREASE: DurableEntryActionRule(RiskEffect.INCREASE_RISK, EconomicOrderSubject, True),
    OrderAction.REDUCE: DurableEntryActionRule(RiskEffect.REDUCE_RISK, EconomicOrderSubject, True),
    OrderAction.CLOSE: DurableEntryActionRule(RiskEffect.REDUCE_RISK, EconomicOrderSubject, True),
    OrderAction.EMERGENCY_CLOSE: DurableEntryActionRule(RiskEffect.REDUCE_RISK, EconomicOrderSubject, True),
    OrderAction.PROTECTION: DurableEntryActionRule(RiskEffect.REDUCE_RISK, EconomicOrderSubject, True),
    OrderAction.CANCEL: DurableEntryActionRule(RiskEffect.NEUTRAL, CancelTargetSubject, False),
}


def durable_entry_action_rule(action: OrderAction) -> DurableEntryActionRule:
    if not isinstance(action, OrderAction):
        raise DurableEntryIntegrityError("action must use OrderAction")
    return DURABLE_ENTRY_ACTION_MATRIX[action]


@dataclass(frozen=True, slots=True)
class DurableEntryPersistResult:
    command_id: str
    action: OrderAction
    subject: DurableEntrySubject
    economic_order_id: str | None
    economic_fingerprint: str
    request_fingerprint: str
    disposition: DurableEntryPersistDisposition
