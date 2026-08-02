"""Immutable aggregate for a Gate read-only evidence snapshot.

This is an assembly boundary only: it accepts already validated Gate facts,
checks their account/market scope, and exposes a deterministic public summary.
It never creates a client, reads credentials, calls Gate, or authorizes writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .gate_market_read_contracts import (
    GateCandleFact,
    GateFundingFact,
    GateMarketContractError,
    GateOrderBookSnapshot,
    GatePriceFact,
    GateTickerFact,
    GateTradeFact,
    gate_market_fingerprint,
)
from .gate_vertical_read_contracts import (
    GateAuthFacts,
    GateBalanceFact,
    GateFillFact,
    GateInstrumentRuleSnapshot,
    GateOrderFact,
    GatePositionFact,
    gate_read_fingerprint,
)


GATE_READ_SNAPSHOT_CONTRACT_VERSION = "gate-read-snapshot-v1"
_MARKET_FACT_TYPES = (GateCandleFact, GateFundingFact, GateOrderBookSnapshot, GatePriceFact, GateTickerFact, GateTradeFact)


class GateReadSnapshotError(ValueError):
    """Malformed or cross-scope Gate read evidence."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateReadSnapshotError("observed_at must be zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class GateReadSnapshot:
    auth: GateAuthFacts
    balances: tuple[GateBalanceFact, ...]
    instruments: tuple[GateInstrumentRuleSnapshot, ...]
    positions: tuple[GatePositionFact, ...]
    market_facts: tuple[Any, ...]
    observed_at: datetime
    snapshot_fingerprint: str
    orders: tuple[GateOrderFact, ...] = ()
    fills: tuple[GateFillFact, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.auth, GateAuthFacts):
            raise GateReadSnapshotError("auth must be typed GateAuthFacts")
        for name, expected in (("balances", GateBalanceFact), ("instruments", GateInstrumentRuleSnapshot), ("positions", GatePositionFact)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(item, expected) for item in values):
                raise GateReadSnapshotError(f"{name} must be a tuple of typed facts")
        if not isinstance(self.orders, tuple) or any(not isinstance(item, GateOrderFact) for item in self.orders):
            raise GateReadSnapshotError("orders must be a tuple of typed facts")
        if not isinstance(self.fills, tuple) or any(not isinstance(item, GateFillFact) for item in self.fills):
            raise GateReadSnapshotError("fills must be a tuple of typed facts")
        if not isinstance(self.market_facts, tuple) or any(not isinstance(item, _MARKET_FACT_TYPES) for item in self.market_facts):
            raise GateReadSnapshotError("market_facts must be typed Gate market facts")
        observed = _utc(self.observed_at)
        object.__setattr__(self, "observed_at", observed)
        market_type = self.auth.market_type
        account_scope = self.auth.account_scope
        for fact in (*self.balances, *self.instruments, *self.positions, *self.orders, *self.fills, *self.market_facts):
            if fact.market_type is not market_type:
                raise GateReadSnapshotError("all facts must share the auth market_type")
            if hasattr(fact, "account_scope") and fact.account_scope != account_scope:
                raise GateReadSnapshotError("account scope mismatch")
            if fact.observed_at > observed:
                raise GateReadSnapshotError("fact observed_at cannot exceed snapshot observed_at")
        if not isinstance(self.snapshot_fingerprint, str) or len(self.snapshot_fingerprint) != 64 or any(c not in "0123456789abcdef" for c in self.snapshot_fingerprint):
            raise GateReadSnapshotError("snapshot_fingerprint must be lowercase SHA-256")
        expected = build_gate_read_snapshot_fingerprint(self.auth, self.balances, self.instruments, self.positions, self.market_facts, observed, self.orders, self.fills)
        if self.snapshot_fingerprint != expected:
            raise GateReadSnapshotError("snapshot_fingerprint does not match immutable facts")

    def to_public_dict(self) -> dict[str, Any]:
        """Return a safe summary without credential references or raw payloads."""

        def decimal_text(value):
            return format(value.normalize(), "f")

        return {
            "contract_version": GATE_READ_SNAPSHOT_CONTRACT_VERSION,
            "venue_id": "gate",
            "market_type": self.auth.market_type.value,
            "account_scope": self.auth.account_scope,
            "observed_at": self.observed_at.isoformat(),
            "balance_count": len(self.balances),
            "instrument_count": len(self.instruments),
            "position_count": len(self.positions),
            "order_count": len(self.orders),
            "fill_count": len(self.fills),
            "market_fact_count": len(self.market_facts),
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "balances": [
                {"asset": item.asset, "total": decimal_text(item.total), "available": decimal_text(item.available), "locked": decimal_text(item.locked), "valuation_ccy": item.valuation_ccy}
                for item in self.balances
            ],
            "positions": [
                {"instrument_id": item.instrument_id, "side": item.side.value, "quantity": decimal_text(item.quantity), "average_entry_price": decimal_text(item.average_entry_price), "mark_price": decimal_text(item.mark_price), "leverage": decimal_text(item.leverage), "margin_mode": item.margin_mode.value}
                for item in self.positions
            ],
            "orders": [
                {"instrument_id": item.instrument_id, "exchange_order_id": item.exchange_order_id, "client_order_id": item.client_order_id, "side": item.side.value, "status": item.status.value, "quantity": decimal_text(item.quantity), "filled_quantity": decimal_text(item.filled_quantity), "average_fill_price": (decimal_text(item.average_fill_price) if item.average_fill_price is not None else None)}
                for item in self.orders
            ],
            "fills": [
                {"instrument_id": item.instrument_id, "exchange_order_id": item.exchange_order_id, "venue_fill_id": item.venue_fill_id, "side": item.side.value, "quantity": decimal_text(item.quantity), "price": decimal_text(item.price), "fee_asset": item.fee_asset, "fee_amount": (decimal_text(item.fee_amount) if item.fee_amount is not None else None)}
                for item in self.fills
            ],
        }


def build_gate_read_snapshot_fingerprint(
    auth: GateAuthFacts,
    balances: tuple[GateBalanceFact, ...],
    instruments: tuple[GateInstrumentRuleSnapshot, ...],
    positions: tuple[GatePositionFact, ...],
    market_facts: tuple[Any, ...],
    observed_at: datetime,
    orders: tuple[GateOrderFact, ...] = (),
    fills: tuple[GateFillFact, ...] = (),
) -> str:
    """Compute the snapshot identity from typed facts in stable order."""

    if not isinstance(auth, GateAuthFacts):
        raise GateReadSnapshotError("auth must be typed")
    observed = _utc(observed_at)
    for group in (balances, instruments, positions, market_facts, orders, fills):
        if not isinstance(group, tuple):
            raise GateReadSnapshotError("snapshot groups must be tuples")
    material = {
        "version": GATE_READ_SNAPSHOT_CONTRACT_VERSION,
        "auth": gate_read_fingerprint(auth),
        "balances": sorted(gate_read_fingerprint(item) for item in balances),
        "instruments": sorted(gate_read_fingerprint(item) for item in instruments),
        "positions": sorted(gate_read_fingerprint(item) for item in positions),
        "orders": sorted(gate_read_fingerprint(item) for item in orders),
        "fills": sorted(gate_read_fingerprint(item) for item in fills),
        "market_facts": sorted(gate_market_fingerprint(item) for item in market_facts),
        "observed_at": observed.isoformat(),
    }
    return gate_read_fingerprint(material)


def build_gate_read_snapshot(
    auth: GateAuthFacts,
    balances: tuple[GateBalanceFact, ...] = (),
    instruments: tuple[GateInstrumentRuleSnapshot, ...] = (),
    positions: tuple[GatePositionFact, ...] = (),
    market_facts: tuple[Any, ...] = (),
    orders: tuple[GateOrderFact, ...] = (),
    fills: tuple[GateFillFact, ...] = (),
    *,
    observed_at: datetime,
) -> GateReadSnapshot:
    """Validate and assemble a complete, immutable read snapshot."""

    try:
        fingerprint = build_gate_read_snapshot_fingerprint(auth, balances, instruments, positions, market_facts, observed_at, orders, fills)
        return GateReadSnapshot(auth, balances, instruments, positions, market_facts, observed_at, fingerprint, orders, fills)
    except (GateMarketContractError, GateReadSnapshotError):
        raise
    except Exception as exc:
        raise GateReadSnapshotError("invalid Gate read snapshot facts") from exc


__all__ = [
    "GATE_READ_SNAPSHOT_CONTRACT_VERSION",
    "GateReadSnapshot",
    "GateReadSnapshotError",
    "build_gate_read_snapshot",
    "build_gate_read_snapshot_fingerprint",
]
