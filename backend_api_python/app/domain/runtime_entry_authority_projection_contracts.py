"""Pure projection contracts for Runtime Entry authority facts.

This module converts one already-validated Gate read snapshot into the typed
INSERT facts for ``qd_runtime_entry_scope_bindings``,
``qd_runtime_entry_instrument_authorities``, ``qd_runtime_entry_position_subjects``
and their upstream ``qd_instrument_rule_snapshots`` / ``qd_position_projections``
rows.  It deliberately performs no I/O and never fabricates facts: every output
row is derived 1:1 from fields already present in the snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .gate_read_snapshot_contracts import (
    GATE_READ_SNAPSHOT_CONTRACT_VERSION,
    GateReadSnapshot,
    GateReadSnapshotError,
)
from .gate_vertical_read_contracts import GatePositionFact, GatePositionSide


PROJECTION_CONTRACT_VERSION = "runtime-entry-authority-v1"
SOURCE_IDENTITY = "gate-private-read-v1"
POSITION_POLICY_VERSION = "runtime-entry-projection-v1"
_NAMESPACE = "runtime-entry-authority-v1"


class RuntimeEntryAuthorityProjectionError(ValueError):
    """A snapshot could not be projected into authority facts."""


def _exchange(value: str) -> str:
    return str(value).strip().lower()


def _instrument(value: str) -> str:
    return str(value).strip().upper()


def _market(value: Any) -> str:
    return str(value).strip().lower()


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise RuntimeEntryAuthorityProjectionError("observed_at must be zero-offset UTC")
    return value.astimezone(timezone.utc)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _fingerprint(snapshot: GateReadSnapshot, label: str) -> str:
    fingerprint = str(getattr(snapshot, "snapshot_fingerprint", "") or "")
    if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
        raise RuntimeEntryAuthorityProjectionError(f"{label} snapshot_fingerprint must be lowercase SHA-256")
    return fingerprint


def scope_binding_id(tenant_id: int, credential_id: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{_NAMESPACE}|scope|{int(tenant_id)}|{int(credential_id)}")).lower()


def instrument_rule_snapshot_id(exchange: str, market_type: str, instrument_id: str, rule_version: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{_NAMESPACE}|rule|{_exchange(exchange)}|{_market(market_type)}|{_instrument(instrument_id)}|{rule_version}")).lower()


def instrument_authority_id(tenant_id: int, credential_id: int, account_scope: str, instrument_id: str, market_type: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{_NAMESPACE}|instrument|{int(tenant_id)}|{int(credential_id)}|{account_scope}|{_instrument(instrument_id)}|{_market(market_type)}")).lower()


def position_projection_id(tenant_id: int, credential_id: int, account_scope: str, instrument_id: str, side: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{_NAMESPACE}|position|{int(tenant_id)}|{int(credential_id)}|{account_scope}|{_instrument(instrument_id)}|{side}")).lower()


def position_subject_id(instrument_authority_id: str, side: str, projection_id: str, checkpoint_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{_NAMESPACE}|subject|{instrument_authority_id}|{side}|{projection_id}|{checkpoint_id}")).lower()


def build_scope_binding_facts(
    snapshot: GateReadSnapshot,
    *,
    tenant_id: int,
    credential_id: int,
) -> dict[str, Any]:
    """One scope binding row from the snapshot auth facts (no fabrication)."""

    auth = snapshot.auth
    account_scope = str(auth.account_scope or "").strip()
    if not account_scope:
        raise RuntimeEntryAuthorityProjectionError("snapshot auth account_scope is empty")
    exchange = _exchange(getattr(auth, "venue_id", "gate") or "gate")
    if exchange != "gate":
        raise RuntimeEntryAuthorityProjectionError("snapshot auth venue must be gate")
    observed = _utc(snapshot.observed_at)
    return {
        "id": scope_binding_id(tenant_id, credential_id),
        "contract_version": PROJECTION_CONTRACT_VERSION,
        "tenant_id": int(tenant_id),
        "credential_id": int(credential_id),
        "account_scope": account_scope,
        "exchange_id": exchange,
        "source_identity": SOURCE_IDENTITY,
        "source_version": GATE_READ_SNAPSHOT_CONTRACT_VERSION,
        "source_fingerprint": _fingerprint(snapshot, "scope"),
        "observed_at": observed,
    }


def build_instrument_rule_snapshot_facts(snapshot: GateReadSnapshot) -> list[dict[str, Any]]:
    """One rule-snapshot row per snapshot instrument (1:1 mapping).

    The schema row is identified by ``(exchange, market_type, instrument_id,
    rule_version)`` and carries no observed_at column; the immutable snapshot
    time is preserved in ``raw_rules_json`` for auditability.
    """

    if not isinstance(snapshot, GateReadSnapshot):
        raise RuntimeEntryAuthorityProjectionError("snapshot must be typed GateReadSnapshot")
    observed = _utc(snapshot.observed_at)
    rows: list[dict[str, Any]] = []
    for item in snapshot.instruments:
        market = _market(item.market_type.value)
        instrument = _instrument(item.instrument_id)
        rule_version = str(item.rule_version or "").strip() or "gate-private-read-instrument-v1"
        rows.append({
            "id": instrument_rule_snapshot_id("gate", market, instrument, rule_version),
            "exchange": "gate",
            "market_type": market,
            "instrument_id": instrument,
            "rule_version": rule_version,
            "tick_size": item.tick_size,
            "quantity_step": item.quantity_step,
            "minimum_quantity": item.minimum_quantity,
            "minimum_notional": item.minimum_notional,
            "price_scale": max(0, abs(item.tick_size.as_tuple().exponent)),
            "quantity_scale": max(0, abs(item.quantity_step.as_tuple().exponent)),
            "rounding_policy_version": "gate-private-read-v1",
            "raw_rules_json": {
                "contract_size": _decimal_text(item.contract_size) if item.contract_size is not None else None,
                "leverage_min": _decimal_text(item.leverage_min) if item.leverage_min is not None else None,
                "leverage_max": _decimal_text(item.leverage_max) if item.leverage_max is not None else None,
                "source_version": GATE_READ_SNAPSHOT_CONTRACT_VERSION,
                "observed_at": observed.isoformat(),
            },
        })
    return rows


def build_instrument_authority_facts(
    snapshot: GateReadSnapshot,
    rule_facts: list[dict[str, Any]],
    *,
    tenant_id: int,
    credential_id: int,
    account_scope: str,
) -> list[dict[str, Any]]:
    """One instrument-authority row per projected rule that exists in snapshot."""

    auth = snapshot.auth
    scope = str(account_scope or auth.account_scope or "").strip()
    if not scope:
        raise RuntimeEntryAuthorityProjectionError("account scope is empty")
    exchange = _exchange(getattr(auth, "venue_id", "gate") or "gate")
    observed = _utc(snapshot.observed_at)
    fingerprint = _fingerprint(snapshot, "instrument")
    by_key: dict[str, dict[str, Any]] = {}
    for row in rule_facts:
        by_key[(row["market_type"], row["instrument_id"])] = row
    rows: list[dict[str, Any]] = []
    for item in snapshot.instruments:
        market = _market(item.market_type.value)
        instrument = _instrument(item.instrument_id)
        rule = by_key.get((market, instrument))
        if rule is None:
            raise RuntimeEntryAuthorityProjectionError(f"instrument rule snapshot missing for {instrument}")
        rows.append({
            "id": instrument_authority_id(tenant_id, credential_id, scope, instrument, market),
            "contract_version": PROJECTION_CONTRACT_VERSION,
            "scope_binding_id": scope_binding_id(tenant_id, credential_id),
            "tenant_id": int(tenant_id),
            "credential_id": int(credential_id),
            "account_scope": scope,
            "exchange_id": exchange,
            "instrument_id": instrument,
            "market_type": market,
            "instrument_rule_snapshot_id": rule["id"],
            "source_identity": SOURCE_IDENTITY,
            "source_version": GATE_READ_SNAPSHOT_CONTRACT_VERSION,
            "source_fingerprint": fingerprint,
            "observed_at": observed,
        })
    return rows


def position_side(side: GatePositionSide) -> str:
    """Map a Gate position side to the LONG/SHORT projection vocabulary."""

    if side is GatePositionSide.LONG:
        return "LONG"
    if side is GatePositionSide.SHORT:
        return "SHORT"
    raise RuntimeEntryAuthorityProjectionError(f"unsupported Gate position side {side!r}")


def build_position_projection_facts(
    snapshot: GateReadSnapshot,
    *,
    tenant_id: int,
    credential_id: int,
    account_scope: str,
) -> list[dict[str, Any]]:
    """One position projection row per open (quantity>0) Gate position."""

    observed = _utc(snapshot.observed_at)
    scope = str(account_scope or snapshot.auth.account_scope or "").strip()
    if not scope:
        raise RuntimeEntryAuthorityProjectionError("account scope is empty")
    rows: list[dict[str, Any]] = []
    for item in snapshot.positions:
        if item.quantity <= 0:
            continue
        side = position_side(item.side)
        rows.append({
            "id": position_projection_id(int(tenant_id), int(credential_id), scope, item.instrument_id, side),
            "tenant_id": int(tenant_id),
            "credential_id": int(credential_id),
            "account_scope": scope,
            "strategy_id": None,
            "instrument_id": _instrument(item.instrument_id),
            "side": side,
            "quantity": item.quantity,
            "average_cost": item.average_entry_price,
            "realized_pnl": item.realized_pnl,
            "last_event_seq": 0,
            "projection_version": 1,
            "policy_version": POSITION_POLICY_VERSION,
            "rebuilt_at": observed,
        })
    return rows


__all__ = [
    "PROJECTION_CONTRACT_VERSION",
    "SOURCE_IDENTITY",
    "POSITION_POLICY_VERSION",
    "RuntimeEntryAuthorityProjectionError",
    "scope_binding_id",
    "instrument_rule_snapshot_id",
    "instrument_authority_id",
    "position_projection_id",
    "position_subject_id",
    "build_scope_binding_facts",
    "build_instrument_rule_snapshot_facts",
    "build_instrument_authority_facts",
    "build_position_projection_facts",
    "position_side",
]
