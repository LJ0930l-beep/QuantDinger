"""Caller-owned persisted-source provider for Durable Risk V2.

The provider is deliberately a database reader only: it opens no connection,
never commits or rolls back, and has no runtime/exchange dependency.  The
caller keeps the one transaction open through durable-risk and outbox writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Protocol

from app.domain.authoritative_risk_facts_contracts import (
    AUTHORITATIVE_RISK_FACTS_CONTRACT_VERSION,
    AuthoritativeAccountFactsRecord,
    AuthoritativeInstrumentRiskRule,
    AuthoritativeKillSwitchRecord,
    AuthoritativeMarketObservation,
    AuthoritativePolicyRecord,
    AuthoritativeRiskFactScope,
    MarketPriceType,
    RiskFactProvenance,
    RiskFactSourceKind,
    RiskFactsError,
    RiskFactsAmbiguous,
    RiskCapacityConflict,
    RiskFactsRepositoryError,
    RiskFactsScopeConflict,
    RiskFactsUnavailable,
    required_market_price_type,
)
from app.domain.canonical_entry_contracts import OrderSide
from app.domain.canonical_entry_v2_contracts import DurableEntryGraphV2, EconomicOrderSubject
from app.domain.decimal_values import Price, QuoteAmount, fit_calculated_decimal
from app.domain.entry_admission_v2_contracts import DurableRiskAdmissionInputs, EntryAdmissionError
from app.domain.hard_risk_contracts import (
    HardRiskRequest,
    KillSwitchMode,
    KillSwitchSnapshot,
    KillSwitchState,
    MarketDataHealth,
    RiskExposureSnapshot,
    RiskLimitPolicy,
    RiskReservationDemand,
)
from app.domain.order_contracts import (
    OrderAction,
    ReconciliationCheckpointStatus,
    derive_reconciliation_health,
)


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def _value(row: Any, index: int, name: str) -> Any:
    return row[name] if isinstance(row, dict) else row[index]


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise RiskFactsScopeConflict(f"persisted {field} must be UTC")
    return value.astimezone(timezone.utc)


def _sha256(material: object) -> str:
    try:
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    except (TypeError, ValueError) as exc:
        raise RiskFactsScopeConflict("risk fact material cannot be canonicalized") from exc
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


class AuthoritativeRiskFactsProvider:
    """Select exact persisted facts at the durable-entry occurrence anchor."""

    def prepare(self, connection: Connection, graph: DurableEntryGraphV2) -> DurableRiskAdmissionInputs:
        if not isinstance(graph, DurableEntryGraphV2) or not isinstance(graph.subject, EconomicOrderSubject):
            raise EntryAdmissionError("authoritative risk facts require a non-CANCEL durable graph")
        try:
            cursor = connection.cursor()
        except Exception as exc:
            raise RiskFactsRepositoryError("authoritative risk facts cursor could not be opened") from exc
        try:
            scope = AuthoritativeRiskFactScope.from_graph(graph)
            anchor = graph.specification.occurred_at
            policy = self._policy(cursor, scope, anchor)
            instrument = self._instrument_rule(cursor, scope, anchor, policy.policy.valuation_currency)
            account = self._account(cursor, scope, anchor, policy.policy.valuation_currency)
            reconciliation, reconciliation_provenance = self._reconciliation(cursor, scope, anchor)
            switches = self._switches(cursor, scope, anchor)
            increasing = graph.specification.action in {OrderAction.OPEN, OrderAction.INCREASE}
            market = self._market(cursor, scope, anchor, policy.policy.valuation_currency, graph) if increasing else None
            if increasing:
                self._capacity_lock(cursor, scope, policy.policy.valuation_currency)
            active, active_provenance = self._active_reservations(cursor, scope, anchor, policy.policy.valuation_currency)
            return self._inputs(
                graph, policy, instrument, account, reconciliation, reconciliation_provenance,
                switches, market, active, active_provenance, anchor,
            )
        except (EntryAdmissionError, RiskFactsError, RiskFactsRepositoryError):
            raise
        except Exception as exc:
            raise RiskFactsRepositoryError("authoritative risk facts database operation failed") from exc
        finally:
            try:
                cursor.close()
            except Exception as exc:
                raise RiskFactsRepositoryError("authoritative risk facts cursor could not be closed") from exc

    @staticmethod
    def _latest(cursor: Cursor, query: str, params: tuple[Any, ...], label: str, observed_index: int) -> Any:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        if not rows:
            raise RiskFactsUnavailable(f"authoritative {label} fact is absent")
        if len(rows) > 1 and _utc(_value(rows[0], observed_index, "observed_at"), "observed_at") == _utc(_value(rows[1], observed_index, "observed_at"), "observed_at"):
            raise RiskFactsAmbiguous(f"authoritative {label} facts tie at the selection anchor")
        return rows[0]

    @staticmethod
    def _scope_params(scope: AuthoritativeRiskFactScope) -> tuple[Any, ...]:
        return (scope.tenant_id, scope.credential_id, scope.account_scope, scope.instrument_id, scope.market_type)

    @staticmethod
    def _provenance(kind: RiskFactSourceKind, row: Any, identity_i: int, version_i: int, fingerprint_i: int, observed_i: int, max_age_i: int, anchor: datetime) -> RiskFactProvenance:
        provenance = RiskFactProvenance(kind, _value(row, identity_i, "source_identity"), str(_value(row, version_i, "source_version")), _value(row, fingerprint_i, "source_fingerprint"), _utc(_value(row, observed_i, "observed_at"), "observed_at"), _value(row, max_age_i, "max_age_seconds"))
        provenance.validate_selection_anchor(anchor)
        return provenance

    def _policy(self, cursor: Cursor, scope: AuthoritativeRiskFactScope, anchor: datetime) -> AuthoritativePolicyRecord:
        row = self._latest(cursor, """
            SELECT policy_identity, policy_version, policy_fingerprint, observed_at, max_age_seconds,
                   reservation_ttl_seconds, valuation_currency, max_gross_notional, max_net_notional,
                   max_instrument_notional, max_leverage, minimum_available_margin, max_daily_loss,
                   max_drawdown_ratio
              FROM qd_authoritative_risk_policies
             WHERE tenant_id=%s AND credential_id=%s AND account_scope=%s AND instrument_id=%s AND market_type=%s
               AND strategy_scope=%s AND observed_at <= %s
             ORDER BY observed_at DESC, id DESC LIMIT 2
        """, (*self._scope_params(scope), scope.strategy_scope, anchor), "policy", 3)
        provenance = self._provenance(RiskFactSourceKind.POLICY, row, 0, 1, 2, 3, 4, anchor)
        policy = RiskLimitPolicy(str(_value(row, 1, "policy_version")), _value(row, 6, "valuation_currency"), QuoteAmount(_value(row, 7, "max_gross_notional")), QuoteAmount(_value(row, 8, "max_net_notional")), QuoteAmount(_value(row, 9, "max_instrument_notional")), _value(row, 10, "max_leverage"), QuoteAmount(_value(row, 11, "minimum_available_margin")), QuoteAmount(_value(row, 12, "max_daily_loss")), _value(row, 13, "max_drawdown_ratio"))
        return AuthoritativePolicyRecord(scope, provenance, policy, _value(row, 5, "reservation_ttl_seconds"))

    def _instrument_rule(self, cursor: Cursor, scope: AuthoritativeRiskFactScope, anchor: datetime, valuation_currency: str) -> AuthoritativeInstrumentRiskRule:
        row = self._latest(cursor, """
            SELECT source_identity, source_version, source_fingerprint, observed_at, max_age_seconds,
                   valuation_currency, quantity_to_quote_multiplier, initial_margin_ratio
              FROM qd_authoritative_instrument_risk_rules
             WHERE tenant_id=%s AND credential_id=%s AND account_scope=%s AND instrument_id=%s AND market_type=%s
               AND valuation_currency=%s AND observed_at <= %s
             ORDER BY observed_at DESC, id DESC LIMIT 2
        """, (*self._scope_params(scope), valuation_currency, anchor), "instrument rule", 3)
        rule = AuthoritativeInstrumentRiskRule(scope, self._provenance(RiskFactSourceKind.INSTRUMENT_RULES, row, 0, 1, 2, 3, 4, anchor), _value(row, 5, "valuation_currency"), _value(row, 6, "quantity_to_quote_multiplier"), _value(row, 7, "initial_margin_ratio"))
        if rule.valuation_currency != valuation_currency:
            raise RiskFactsScopeConflict("persisted instrument rule valuation currency does not match policy")
        return rule

    def _account(self, cursor: Cursor, scope: AuthoritativeRiskFactScope, anchor: datetime, valuation_currency: str) -> AuthoritativeAccountFactsRecord:
        row = self._latest(cursor, """
            SELECT source_identity, source_version, source_fingerprint, observed_at, max_age_seconds,
                   valuation_currency, gross_notional, net_notional, instrument_notional, available_margin,
                   equity, peak_equity, daily_realized_pnl, account_facts_verified
              FROM qd_authoritative_account_risk_facts
             WHERE tenant_id=%s AND credential_id=%s AND account_scope=%s AND instrument_id=%s AND market_type=%s
               AND valuation_currency=%s AND observed_at <= %s
             ORDER BY observed_at DESC, id DESC LIMIT 2
        """, (*self._scope_params(scope), valuation_currency, anchor), "account", 3)
        exposure = RiskExposureSnapshot(scope.account_scope, scope.instrument_id, _value(row, 5, "valuation_currency"), *(_value(row, index, name) for index, name in ((6, "gross_notional"), (7, "net_notional"), (8, "instrument_notional"), (9, "available_margin"), (10, "equity"), (11, "peak_equity"), (12, "daily_realized_pnl"))), reconciliation_health=derive_reconciliation_health(None), market_data_health=MarketDataHealth.UNKNOWN, account_facts_verified=_value(row, 13, "account_facts_verified"))
        if exposure.valuation_currency != valuation_currency:
            raise RiskFactsScopeConflict("persisted account facts valuation currency does not match policy")
        return AuthoritativeAccountFactsRecord(scope, self._provenance(RiskFactSourceKind.ACCOUNT, row, 0, 1, 2, 3, 4, anchor), exposure)

    def _reconciliation(self, cursor: Cursor, scope: AuthoritativeRiskFactScope, anchor: datetime):
        row = self._latest(cursor, """
            SELECT id, status, version, evidence_hash, updated_at, risk_max_age_seconds, sla_deadline
              FROM qd_reconciliation_checkpoints
             WHERE tenant_id=%s AND credential_id=%s AND account_scope=%s AND instrument_id=%s AND market_type=%s
               AND updated_at <= %s AND risk_max_age_seconds IS NOT NULL
             ORDER BY updated_at DESC, id DESC LIMIT 2
        """, (*self._scope_params(scope), anchor), "reconciliation checkpoint", 4)
        provenance = self._provenance(RiskFactSourceKind.RECONCILIATION, row, 0, 2, 3, 4, 5, anchor)
        try:
            status = ReconciliationCheckpointStatus(_value(row, 1, "status"))
        except (TypeError, ValueError) as exc:
            raise RiskFactsScopeConflict("persisted reconciliation checkpoint status is invalid") from exc
        deadline = _value(row, 6, "sla_deadline")
        health = derive_reconciliation_health(status, sla_expired=deadline is not None and _utc(deadline, "sla_deadline") <= anchor)
        return health, provenance

    def _switches(self, cursor: Cursor, scope: AuthoritativeRiskFactScope, anchor: datetime):
        records = []
        for kind in ("GLOBAL", "ACCOUNT", "STRATEGY"):
            row = self._latest(cursor, """
                SELECT source_identity, source_version, source_fingerprint, observed_at, max_age_seconds,
                       switch_version, enabled, mode
                  FROM qd_authoritative_kill_switch_observations
                 WHERE tenant_id=%s AND credential_id=%s AND account_scope=%s AND strategy_scope=%s
                   AND scope_kind=%s AND observed_at <= %s
                 ORDER BY observed_at DESC, id DESC LIMIT 2
            """, (scope.tenant_id, scope.credential_id, scope.account_scope, scope.strategy_scope, kind, anchor), f"{kind.lower()} kill switch", 3)
            source_kind = RiskFactSourceKind(f"KILL_SWITCH_{kind}")
            provenance = self._provenance(source_kind, row, 0, 1, 2, 3, 4, anchor)
            mode = None if _value(row, 7, "mode") is None else KillSwitchMode(_value(row, 7, "mode"))
            records.append(AuthoritativeKillSwitchRecord(scope, provenance, KillSwitchState(_value(row, 5, "switch_version"), _value(row, 6, "enabled"), mode)))
        return tuple(records)

    def _market(self, cursor: Cursor, scope: AuthoritativeRiskFactScope, anchor: datetime, valuation_currency: str, graph: DurableEntryGraphV2) -> AuthoritativeMarketObservation:
        expected = required_market_price_type(graph)
        row = self._latest(cursor, """
            SELECT source_identity, source_version, source_fingerprint, observed_at, max_age_seconds,
                   valuation_currency, price_type, price, market_data_health
              FROM qd_authoritative_market_observations
             WHERE tenant_id=%s AND credential_id=%s AND account_scope=%s AND instrument_id=%s AND market_type=%s
               AND valuation_currency=%s AND price_type=%s AND observed_at <= %s AND market_data_health IS NOT NULL
             ORDER BY observed_at DESC, id DESC LIMIT 2
        """, (*self._scope_params(scope), valuation_currency, expected.value, anchor), "market", 3)
        observation = AuthoritativeMarketObservation(scope, self._provenance(RiskFactSourceKind.MARKET, row, 0, 1, 2, 3, 4, anchor), _value(row, 5, "valuation_currency"), MarketPriceType(_value(row, 6, "price_type")), Price(_value(row, 7, "price")), MarketDataHealth(_value(row, 8, "market_data_health")))
        if observation.valuation_currency != valuation_currency:
            raise RiskFactsScopeConflict("persisted market valuation currency does not match policy")
        return observation

    @staticmethod
    def _capacity_lock(cursor: Cursor, scope: AuthoritativeRiskFactScope, valuation_currency: str) -> None:
        key = f"rf01-capacity-v1:{scope.tenant_id}:{scope.credential_id}:{scope.account_scope}:{valuation_currency}"
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))

    def _active_reservations(self, cursor: Cursor, scope: AuthoritativeRiskFactScope, anchor: datetime, valuation_currency: str):
        cursor.execute("""
            SELECT id, account_scope, instrument_id, valuation_currency, reserved_gross_notional,
                   reserved_net_notional, reserved_instrument_notional, reserved_margin, reservation_hash
              FROM qd_durable_risk_reservations
             WHERE tenant_id=%s AND credential_id=%s AND account_scope=%s
               AND market_type=%s AND valuation_currency=%s AND state='ACTIVE'
             ORDER BY id
             FOR UPDATE
        """, (scope.tenant_id, scope.credential_id, scope.account_scope, scope.market_type, valuation_currency))
        rows = cursor.fetchall()
        demands = tuple(RiskReservationDemand(str(_value(row, 0, "id")), _value(row, 1, "account_scope"), _value(row, 2, "instrument_id"), _value(row, 3, "valuation_currency"), _value(row, 4, "reserved_gross_notional"), _value(row, 5, "reserved_net_notional"), _value(row, 6, "reserved_instrument_notional"), _value(row, 7, "reserved_margin")) for row in rows)
        fingerprint = _sha256([{"id": item.reservation_id, "hash": _value(row, 8, "reservation_hash")} for item, row in zip(demands, rows)])
        return demands, RiskFactProvenance(RiskFactSourceKind.ACTIVE_RESERVATIONS, "qd_durable_risk_reservations", "durable-risk-enforcement-v2", fingerprint, anchor, 0)

    def _inputs(self, graph, policy, instrument, account, reconciliation, reconciliation_provenance, switches, market, active, active_provenance, anchor):
        spec = graph.specification
        base_exposure = account.exposure
        same_instrument = tuple(item for item in active if item.instrument_id == base_exposure.instrument_id)
        cross_instrument = tuple(item for item in active if item.instrument_id != base_exposure.instrument_id)
        # The V1 reducer accepts one instrument only.  Account-wide capacity is
        # preserved by folding other-instrument reservations into the persisted
        # account exposure before the same-instrument reducer is invoked.
        cross_margin = sum((item.margin for item in cross_instrument), Decimal("0"))
        if base_exposure.available_margin < cross_margin:
            raise RiskCapacityConflict("persisted active reservations exceed verified available margin")
        exposure = RiskExposureSnapshot(
            base_exposure.account_scope, base_exposure.instrument_id, base_exposure.valuation_currency,
            fit_calculated_decimal(base_exposure.gross_notional + sum((item.gross_notional for item in cross_instrument), Decimal("0"))),
            fit_calculated_decimal(base_exposure.net_notional + sum((item.net_notional for item in cross_instrument), Decimal("0"))),
            base_exposure.instrument_notional,
            fit_calculated_decimal(base_exposure.available_margin - cross_margin),
            base_exposure.equity, base_exposure.peak_equity, base_exposure.daily_realized_pnl,
            reconciliation, MarketDataHealth.UNKNOWN if market is None else market.health, base_exposure.account_facts_verified,
        )
        increasing = spec.action in {OrderAction.OPEN, OrderAction.INCREASE}
        if increasing:
            observation = market
            if observation is None or spec.economic_intent.quantity is None:
                raise RiskFactsUnavailable("increasing risk admission lacks typed valuation or quantity")
            gross = fit_calculated_decimal(spec.economic_intent.quantity.to_decimal() * observation.price.to_decimal() * instrument.quantity_to_quote_multiplier)
            signed = gross if spec.economic_intent.side is OrderSide.BUY else -gross
            margin = fit_calculated_decimal(gross * instrument.initial_margin_ratio)
            request = HardRiskRequest(spec.action, spec.actor.actor_type, spec.risk_effect, gross, signed, gross, margin)
            demand = RiskReservationDemand(graph.subject.economic_order_id, spec.account_scope, spec.instrument_id, policy.policy.valuation_currency, gross, signed, gross, margin)
            expires_at = anchor + timedelta(seconds=policy.reservation_ttl_seconds)
        else:
            request = HardRiskRequest(spec.action, spec.actor.actor_type, spec.risk_effect, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
            demand = None
            expires_at = None
        provenance = (policy.provenance, account.provenance, instrument.provenance, reconciliation_provenance, *(record.provenance for record in switches), active_provenance, *(() if market is None else (market.provenance,)))
        return DurableRiskAdmissionInputs(policy.policy, exposure, KillSwitchSnapshot(*(record.state for record in switches)), request, anchor, same_instrument, demand, expires_at, provenance)
