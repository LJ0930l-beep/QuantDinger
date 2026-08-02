"""Compose supplied Gate account evidence into one immutable read snapshot.

This service is deliberately transport-free.  A future TestNet adapter may
inject sanitized payloads, but this boundary never reads credentials, creates
an HTTP client, or performs an exchange operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.gate_read_formatters import (
    normalize_gate_balances,
    normalize_gate_fills,
    normalize_gate_instruments,
    normalize_gate_orders,
    normalize_gate_positions,
)
from app.domain.gate_read_snapshot_contracts import GateReadSnapshot, build_gate_read_snapshot
from app.domain.gate_vertical_read_contracts import GateAuthFacts, GateVerticalContractError


class GateAccountReadSnapshotError(ValueError):
    """Supplied account evidence cannot be composed safely."""


@dataclass(frozen=True, slots=True)
class GateAccountReadSnapshotService:
    """Build an immutable account read snapshot from sanitized payloads."""

    def read_from_payloads(
        self,
        auth: GateAuthFacts,
        *,
        balances: Any,
        positions: Any = (),
        orders: Any = (),
        fills: Any = (),
        instruments: Any = (),
        valuation_ccy: str,
        observed_at: datetime,
        rule_version: str = "gate-read-v1",
    ) -> GateReadSnapshot:
        if not isinstance(auth, GateAuthFacts):
            raise GateAccountReadSnapshotError("auth must be typed GateAuthFacts")
        try:
            balance_facts = normalize_gate_balances(
                balances, market_type=auth.market_type, account_scope=auth.account_scope,
                valuation_ccy=valuation_ccy, observed_at=observed_at,
                source_event_prefix="account-balance", evidence_hash_prefix="account-balance",
            )
            position_facts = normalize_gate_positions(
                positions, market_type=auth.market_type, account_scope=auth.account_scope,
                observed_at=observed_at, source_event_prefix="account-position",
            ) if positions not in (None, (), []) else ()
            order_facts = normalize_gate_orders(
                orders, market_type=auth.market_type, account_scope=auth.account_scope,
                observed_at=observed_at, source_event_prefix="account-order",
            ) if orders not in (None, (), []) else ()
            fill_facts = normalize_gate_fills(
                fills, market_type=auth.market_type, account_scope=auth.account_scope,
                observed_at=observed_at, source_event_prefix="account-fill",
            ) if fills not in (None, (), []) else ()
            instrument_facts = normalize_gate_instruments(
                instruments, market_type=auth.market_type, observed_at=observed_at,
                rule_version=rule_version,
            ) if instruments not in (None, (), []) else ()
            return build_gate_read_snapshot(
                auth, balance_facts, instrument_facts, position_facts,
                orders=order_facts, fills=fill_facts, observed_at=observed_at,
            )
        except (GateVerticalContractError, ValueError, TypeError) as exc:
            raise GateAccountReadSnapshotError("Gate account evidence is invalid") from exc


__all__ = ["GateAccountReadSnapshotError", "GateAccountReadSnapshotService"]
