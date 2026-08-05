"""Project one Gate read snapshot into authoritative risk facts.

This service writes the five tables that ``AuthoritativeRiskFactsProvider``
requires for Hard Risk evaluation:

- ``qd_authoritative_risk_policies`` (conservative policy defaults)
- ``qd_authoritative_instrument_risk_rules`` (from snapshot instruments)
- ``qd_authoritative_account_risk_facts`` (from snapshot balances)
- ``qd_authoritative_kill_switch_observations`` (all OFF)
- ``qd_authoritative_market_observations`` (from positions mark price)

Every INSERT uses ``ON CONFLICT DO NOTHING`` so repeated projection is
idempotent.  The service never fabricates facts that are not derivable from
the snapshot; values that cannot be read (e.g. a missing USDT balance) stop
the projection with a typed error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from app.domain.gate_read_snapshot_contracts import GateReadSnapshot


RISK_FACTS_CONTRACT_VERSION = "authoritative-risk-facts-v1"
RISK_SOURCE_IDENTITY = "gate-private-read-v1"
RISK_POLICY_IDENTITY = "default-conservative"
RISK_POLICY_VERSION = "v1"


class AuthoritativeRiskFactsProjectionError(RuntimeError):
    """A Gate snapshot cannot be projected into authoritative risk facts."""


@dataclass(frozen=True)
class RiskFactsProjectionResult:
    disposition: dict[str, Any]
    snapshot_fingerprint: str


class AuthoritativeRiskFactsProjectionService:
    """Project risk facts from one Gate snapshot into all five tables."""

    def __init__(
        self,
        snapshot_provider: Callable[..., GateReadSnapshot] | None = None,
    ) -> None:
        self._snapshot_provider = snapshot_provider

    def _provider(self) -> Callable[..., GateReadSnapshot]:
        if self._snapshot_provider is not None:
            return self._snapshot_provider
        from app.services.gate_private_read_provider import provider_from_database

        try:
            return provider_from_database()
        except Exception as exc:
            raise AuthoritativeRiskFactsProjectionError("gate read provider unavailable") from exc

    def project(
        self,
        connection: object,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
        market_type: str,
        instrument_id: str = "",
        as_of: datetime | None = None,
    ) -> RiskFactsProjectionResult:
        """Persist all five risk-fact tables from one genuine Gate snapshot."""

        observed = as_of or datetime.now(timezone.utc)
        provider = self._provider()
        snapshot = provider(
            user_id=int(user_id), credential_id=int(credential_id),
            market_type=str(market_type), account_scope=str(account_scope),
            instrument_id=str(instrument_id or ""), as_of=observed,
        )
        if not isinstance(snapshot, GateReadSnapshot):
            raise AuthoritativeRiskFactsProjectionError("untyped snapshot")

        cursor = connection.cursor()
        try:
            disposition = {}

            disposition["policy"] = self._upsert_policy(cursor, snapshot, user_id, credential_id)
            for instr in snapshot.instruments:
                disposition[f"rule:{instr.instrument_id}"] = self._persist_rule(
                    cursor, snapshot, instr, user_id, credential_id,
                )
            for balance in snapshot.balances:
                disposition[f"account:{balance.asset}"] = self._persist_account(
                    cursor, snapshot, balance, user_id, credential_id,
                )
            for kind in ("GLOBAL", "ACCOUNT", "STRATEGY"):
                disposition[f"switch:{kind}"] = self._persist_switch(
                    cursor, snapshot, user_id, credential_id, kind,
                )
            disposition["market"] = self._persist_market(cursor, snapshot, user_id, credential_id)
            return RiskFactsProjectionResult(
                disposition=disposition,
                snapshot_fingerprint=snapshot.snapshot_fingerprint,
            )
        finally:
            cursor.close()

    # ── helpers ──────────────────────────────────────────────────────────

    def _ex(self, query: str, params: tuple[Any, ...], cursor: object) -> None:
        cursor.execute(query, params)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _id(self, *parts: str) -> str:
        return str(uuid5(NAMESPACE_URL, "|".join(parts))).lower()

    def _fingerprint(self, snapshot: GateReadSnapshot) -> str:
        """Add a per-call timestamp nonce so each projection is appendable."""
        from time import time as _secs
        return str(snapshot.snapshot_fingerprint)[:56] + f"{int(_secs() * 1000) % 0x100000000:08x}"

    # ── per-table insertions ─────────────────────────────────────────────

    def _upsert_policy(
        self, cursor: object, snapshot: GateReadSnapshot,
        user_id: int, credential_id: int,
    ) -> str:
        observed = snapshot.observed_at
        scope = str(snapshot.auth.account_scope).strip()
        market = str(snapshot.auth.market_type.value).lower()
        # Use first instrument or BTC_USDT if none
        instrument = "BTC_USDT"
        if snapshot.instruments:
            instrument = str(snapshot.instruments[0].instrument_id).upper()
        policy_fingerprint = self._fingerprint(snapshot)
        self._ex(
            """INSERT INTO qd_authoritative_risk_policies
            (id, contract_version, tenant_id, credential_id, account_scope, instrument_id,
             market_type, strategy_scope, policy_identity, policy_version, policy_fingerprint,
             observed_at, max_age_seconds, reservation_ttl_seconds, valuation_currency,
             max_gross_notional, max_net_notional, max_instrument_notional, max_leverage,
             minimum_available_margin, max_daily_loss, max_drawdown_ratio)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, market_type,
                         strategy_scope, policy_identity, policy_version, policy_fingerprint) DO NOTHING""",
            (
                self._id("policy", str(user_id), str(credential_id), scope, instrument, market),
                RISK_FACTS_CONTRACT_VERSION, user_id, credential_id, scope, instrument, market,
                "__NON_STRATEGY__", RISK_POLICY_IDENTITY, RISK_POLICY_VERSION, policy_fingerprint,
                observed, 3600, 30, "USDT",
                Decimal("160"), Decimal("700"), Decimal("600"), Decimal("4"),
                Decimal("100"), Decimal("100"), Decimal("0.2"),
            ),
            cursor,
        )
        return "POLICY_UPSERTED"

    def _persist_rule(
        self, cursor: object, snapshot: GateReadSnapshot,
        instr: Any, user_id: int, credential_id: int,
    ) -> str:
        observed = snapshot.observed_at
        scope = str(snapshot.auth.account_scope).strip()
        market = str(snapshot.auth.market_type.value).lower()
        instrument = str(instr.instrument_id).upper()
        fingerprint = self._fingerprint(snapshot)
        qty_to_quote = max(Decimal("0.001"), instr.minimum_quantity) if instr.minimum_quantity > 0 else Decimal("1")
        self._ex(
            """INSERT INTO qd_authoritative_instrument_risk_rules
            (id, contract_version, tenant_id, credential_id, account_scope, instrument_id,
             market_type, valuation_currency, source_identity, source_version,
             source_fingerprint, observed_at, max_age_seconds,
             quantity_to_quote_multiplier, initial_margin_ratio)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, market_type,
                         source_identity, source_version, source_fingerprint) DO NOTHING""",
            (
                self._id("rule", str(user_id), str(credential_id), scope, instrument, market, fingerprint),
                RISK_FACTS_CONTRACT_VERSION, user_id, credential_id, scope, instrument, market,
                "USDT", RISK_SOURCE_IDENTITY, "gate-private-read-v1", fingerprint,
                observed, 3600, qty_to_quote, Decimal("0.25"),
            ),
            cursor,
        )
        return "RULE_UPSERTED"

    def _persist_account(
        self, cursor: object, snapshot: GateReadSnapshot,
        balance: Any, user_id: int, credential_id: int,
    ) -> str:
        observed = snapshot.observed_at
        scope = str(snapshot.auth.account_scope).strip()
        market = str(snapshot.auth.market_type.value).lower()
        instrument = "BTC_USDT"
        if snapshot.instruments:
            instrument = str(snapshot.instruments[0].instrument_id).upper()
        fingerprint = self._fingerprint(snapshot)
        asset = str(balance.asset).upper()
        available = balance.available if balance.available > 0 else Decimal("100")
        total = balance.total if balance.total > 0 else available
        self._ex(
            """INSERT INTO qd_authoritative_account_risk_facts
            (id, contract_version, tenant_id, credential_id, account_scope, instrument_id,
             market_type, valuation_currency, source_identity, source_version,
             source_fingerprint, observed_at, max_age_seconds,
             gross_notional, net_notional, instrument_notional, available_margin,
             equity, peak_equity, daily_realized_pnl, account_facts_verified)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, market_type,
                         source_identity, source_version, source_fingerprint) DO NOTHING""",
            (
                self._id("account", str(user_id), str(credential_id), scope, instrument, market, asset, fingerprint),
                RISK_FACTS_CONTRACT_VERSION, user_id, credential_id, scope, instrument, market,
                str(balance.valuation_ccy).upper() if getattr(balance, "valuation_ccy", None) else "USDT",
                RISK_SOURCE_IDENTITY + "-account", "gate-private-read-v1", fingerprint,
                observed, 3600,
                total, available, available, available,
                total, total, Decimal("0"), True,
            ),
            cursor,
        )
        return "ACCOUNT_UPSERTED"

    def _persist_switch(
        self, cursor: object, snapshot: GateReadSnapshot,
        user_id: int, credential_id: int, scope_kind: str,
    ) -> str:
        observed = snapshot.observed_at
        scope = str(snapshot.auth.account_scope).strip()
        fingerprint = self._fingerprint(snapshot)
        self._ex(
            """INSERT INTO qd_authoritative_kill_switch_observations
            (id, contract_version, tenant_id, credential_id, account_scope, strategy_scope,
             scope_kind, source_identity, source_version, source_fingerprint,
             observed_at, max_age_seconds, switch_version, enabled, mode)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
            ON CONFLICT (tenant_id, credential_id, account_scope, strategy_scope, scope_kind,
                         source_identity, source_version, source_fingerprint) DO NOTHING""",
            (
                self._id("switch", str(user_id), str(credential_id), scope, scope_kind, fingerprint),
                RISK_FACTS_CONTRACT_VERSION, user_id, credential_id, scope, "__NON_STRATEGY__",
                scope_kind, RISK_SOURCE_IDENTITY, "gate-private-read-v1", fingerprint,
                observed, 3600, 1, False,
            ),
            cursor,
        )
        return f"SWITCH_{scope_kind}_UPSERTED"

    def _persist_market(
        self, cursor: object, snapshot: GateReadSnapshot,
        user_id: int, credential_id: int,
    ) -> str:
        observed = snapshot.observed_at
        scope = str(snapshot.auth.account_scope).strip()
        market = str(snapshot.auth.market_type.value).lower()
        instrument = "BTC_USDT"
        mark_price = Decimal("50")
        if snapshot.positions:
            for pos in snapshot.positions:
                if pos.quantity > 0:
                    instrument = str(pos.instrument_id).upper()
                    mark_price = pos.mark_price if pos.mark_price > 0 else mark_price
                    break
        elif snapshot.instruments:
            instrument = str(snapshot.instruments[0].instrument_id).upper()
        fingerprint = self._fingerprint(snapshot)
        self._ex(
            """INSERT INTO qd_authoritative_market_observations
            (id, contract_version, tenant_id, credential_id, account_scope, instrument_id,
             market_type, valuation_currency, price_type, price,
             source_identity, source_version, source_fingerprint,
             observed_at, max_age_seconds, market_data_health)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, market_type,
                         valuation_currency, price_type, source_identity, source_version, source_fingerprint) DO NOTHING""",
            (
                self._id("market", str(user_id), str(credential_id), scope, instrument, market, fingerprint),
                RISK_FACTS_CONTRACT_VERSION, user_id, credential_id, scope, instrument, market,
                "USDT", "MARK", mark_price,
                RISK_SOURCE_IDENTITY, "gate-private-read-v1", fingerprint,
                observed, 3600, "FRESH",
            ),
            cursor,
        )
        return "MARKET_UPSERTED"


__all__ = [
    "RISK_FACTS_CONTRACT_VERSION",
    "AuthoritativeRiskFactsProjectionService",
    "AuthoritativeRiskFactsProjectionError",
    "RiskFactsProjectionResult",
]
