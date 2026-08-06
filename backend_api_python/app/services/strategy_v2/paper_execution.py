"""Strategy V2 -> PAPER admission and durable order bridge.

This module is the runtime seam for the non-live Strategy V2 mode.  A signal
is converted to the same typed Runtime Entry facts used by the authenticated
entry API, admitted through Hard Risk and the durable outbox, and only then
materialised as a durable PAPER order.  The caller owns the transaction; this
module never creates a connection and never commits or rolls back.

There is intentionally no venue client in this module.  TESTNET and LIVE use
separate reviewed executor boundaries and remain fail-closed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID, NAMESPACE_URL, uuid5

from app.domain.canonical_entry_contracts import (
    EntryMode,
    EntrySource,
    ExecutionKind,
    OrderSide,
    PositionSide,
)
from app.domain.canonical_entry_v2_contracts import QuantitySemantics
from app.domain.order_contracts import OrderAction
from app.domain.paper_execution_contracts import (
    PaperExecutionEventType,
    PaperExecutionOrder,
    PaperExecutionOrderEvent,
    PaperExecutionStatus,
)
from app.domain.runtime_entry_admission_contracts import (
    RuntimeEntryAdmissionDisposition,
    RuntimeEntryAdmissionResult,
)
from app.domain.runtime_entry_ingress_contracts import (
    RuntimeEntryIngressV1,
    RuntimeIngressPrincipal,
)
from app.services.paper_execution_repository import (
    PaperExecutionDisposition,
    PaperExecutionRepository,
    PaperExecutionResult,
)
from app.services.runtime_entry_admission_service import RuntimeEntryAdmissionService


class StrategyV2PaperExecutionError(RuntimeError):
    """A strategy signal cannot be admitted as a durable PAPER order."""


class AdmissionService(Protocol):
    def admit_with_graph(
        self,
        connection: object,
        ingress: RuntimeEntryIngressV1,
        principal: RuntimeIngressPrincipal,
        *,
        correlation_id: str,
        occurred_at: datetime,
        mode: EntryMode,
    ) -> tuple[RuntimeEntryAdmissionResult, object | None]: ...


@dataclass(frozen=True, slots=True)
class StrategyV2PaperExecutionReceipt:
    admission: RuntimeEntryAdmissionResult
    paper_order: PaperExecutionOrder | None
    paper_result: PaperExecutionResult | None

    @property
    def disposition(self) -> RuntimeEntryAdmissionDisposition:
        return self.admission.disposition


def _signal_facts(signal_type: str) -> tuple[OrderAction, OrderSide, PositionSide, bool]:
    value = str(signal_type or "").strip().lower()
    if value in {"open_long", "add_long"}:
        action = OrderAction.OPEN if value == "open_long" else OrderAction.INCREASE
        return action, OrderSide.BUY, PositionSide.LONG, False
    if value in {"open_short", "add_short"}:
        action = OrderAction.OPEN if value == "open_short" else OrderAction.INCREASE
        return action, OrderSide.SELL, PositionSide.SHORT, False
    if value in {"close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing"}:
        return OrderAction.CLOSE if value.startswith("close_") else OrderAction.REDUCE, OrderSide.SELL, PositionSide.LONG, True
    if value in {"close_short", "reduce_short", "close_short_stop", "close_short_profit", "close_short_trailing"}:
        return OrderAction.CLOSE if value.startswith("close_") else OrderAction.REDUCE, OrderSide.BUY, PositionSide.SHORT, True
    raise StrategyV2PaperExecutionError("strategyV2.paperUnsupportedSignal")


def _decimal_text(value: object, field_name: str, *, positive: bool = False) -> str:
    if isinstance(value, (bool, float)) or value is None:
        raise StrategyV2PaperExecutionError(f"{field_name} must be a Decimal-compatible value")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise StrategyV2PaperExecutionError(f"{field_name} must be a Decimal-compatible value") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise StrategyV2PaperExecutionError(f"{field_name} has invalid numeric bounds")
    return format(parsed.normalize(), "f")


def _utc_from_signal_timestamp(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StrategyV2PaperExecutionError("strategyV2.paperSignalTimestampInvalid")
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _uuid(value: object, field_name: str) -> str:
    try:
        return str(UUID(str(value))).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise StrategyV2PaperExecutionError(f"{field_name} must be a UUID") from exc


class StrategyV2PaperExecutionService:
    """Persist one Strategy V2 signal through the caller-owned PAPER chain."""

    def __init__(
        self,
        *,
        admission_service: AdmissionService | None = None,
        repository: PaperExecutionRepository | None = None,
    ) -> None:
        if admission_service is None:
            from app.services.entry_admission_gateway import CanonicalEntryAdmissionGateway
            from app.services.entry_admission_v2_adapters import (
                AdmissionOutboxAdapter,
                DurableRiskAdmissionAdapter,
            )
            from app.services.authoritative_risk_facts_provider import AuthoritativeRiskFactsProvider
            from app.services.durable_entry_repository import DurableEntryRepository
            from app.services.durable_risk_enforcement_v2_repository import DurableRiskEnforcementRepositoryV2
            from app.services.outbox_projection_repository import OutboxProjectionRepository
            from app.services.runtime_entry_authority_repository import RuntimeEntryAuthorityRepository

            gateway = CanonicalEntryAdmissionGateway(
                durable_entries=DurableEntryRepository(),
                durable_risk=DurableRiskAdmissionAdapter(
                    provider=AuthoritativeRiskFactsProvider(),
                    repository=DurableRiskEnforcementRepositoryV2(),
                ),
                outbox=AdmissionOutboxAdapter(repository=OutboxProjectionRepository()),
            )
            admission_service = RuntimeEntryAdmissionService(
                authorities=RuntimeEntryAuthorityRepository(),
                admissions=gateway,
            )
        self._admission_service = admission_service
        self._repository = repository or PaperExecutionRepository()

    def persist(
        self,
        connection: object,
        request: Any,
        *,
        credential_id: int,
        expected_account_scope: str | None = None,
        target_position_id: str | None = None,
    ) -> StrategyV2PaperExecutionReceipt:
        """Admit and persist a PAPER order without transaction control."""

        mode = str(getattr(request, "execution_mode", "")).strip().lower()
        if mode != "paper":
            raise StrategyV2PaperExecutionError("strategyV2.paperModeRequired")
        user_id = int(getattr(request, "user_id", 0) or 0)
        strategy_id = int(getattr(request, "strategy_id", 0) or 0)
        strategy_run_id = int(getattr(request, "strategy_run_id", 0) or 0)
        if user_id <= 0 or strategy_id <= 0 or strategy_run_id <= 0:
            raise StrategyV2PaperExecutionError("strategyV2.paperIdentityMissing")
        if isinstance(credential_id, bool) or not isinstance(credential_id, int) or credential_id <= 0:
            raise StrategyV2PaperExecutionError("strategyV2.paperCredentialRequired")

        signal_type = str(getattr(request, "action", ""))
        action, side, position_side, reducing = _signal_facts(signal_type)
        symbol = str(getattr(request, "symbol", "")).strip().upper()
        if not symbol or not symbol.isascii() or symbol != symbol.strip():
            raise StrategyV2PaperExecutionError("strategyV2.paperInstrumentInvalid")
        market_type = str(getattr(request, "market_type", "spot") or "spot").strip().lower()
        if market_type in {"swap", "future", "futures", "perp"}:
            market_type = "swap"
        elif market_type != "spot":
            raise StrategyV2PaperExecutionError("strategyV2.paperMarketTypeUnsupported")
        execution_name = str(getattr(request, "order_type", "market") or "market").strip().upper()
        if execution_name not in {"MARKET", "LIMIT"}:
            raise StrategyV2PaperExecutionError("strategyV2.paperExecutionUnsupported")
        quantity = _decimal_text(getattr(request, "quantity", None), "quantity", positive=True)
        limit_price = None
        if execution_name == "LIMIT":
            limit_price = _decimal_text(getattr(request, "limit_price", None), "limit_price", positive=True)

        target = target_position_id or getattr(request, "target_position_id", None)
        if reducing and not target:
            raise StrategyV2PaperExecutionError("strategyV2.paperTargetPositionRequired")
        if not reducing:
            target = None
        if reducing and target is not None:
            target = self._resolve_position_subject(
                connection,
                target,
                strategy_id=strategy_id,
                user_id=user_id,
                credential_id=credential_id,
                account_scope=expected_account_scope,
                instrument_id=symbol,
                market_type=market_type,
                position_side=position_side,
            )

        signal_timestamp = getattr(request, "signal_timestamp", None)
        occurred_at = _utc_from_signal_timestamp(signal_timestamp)
        signal_id = f"{signal_type}:{symbol}:{int(signal_timestamp)}"
        if not signal_id.isascii() or len(signal_id) > 160:
            raise StrategyV2PaperExecutionError("strategyV2.paperSignalIdentityInvalid")
        correlation_id = f"strategy-v2-{strategy_id}-{strategy_run_id}-{int(signal_timestamp)}"
        principal = RuntimeIngressPrincipal(
            tenant_id=user_id,
            actor_id=f"strategy:{strategy_id}",
            source=EntrySource.STRATEGY,
        )
        from app.domain.strategy_v2_candidate_contracts import StrategyV2CandidateTradePlan

        candidate = StrategyV2CandidateTradePlan(
            strategy_id=strategy_id,
            strategy_run_id=strategy_run_id,
            signal_id=signal_id,
            instrument_id=symbol,
            market_type=market_type,
            action=action,
            side=side,
            quantity=None if reducing else quantity,
            execution_kind=ExecutionKind[execution_name],
            limit_price=limit_price,
            reduce_only=reducing,
            position_side=position_side,
            target_position_id=None if target is None else str(target),
            close_quantity=quantity if reducing else None,
            close_all=False,
        )
        ingress = RuntimeEntryIngressV1(
            credential_id=credential_id,
            instrument_id=symbol,
            market_type=market_type,
            action=action,
            side=side,
            quantity=None if reducing else quantity,
            quantity_semantics=None if reducing else QuantitySemantics.ABSOLUTE,
            execution_kind=ExecutionKind[execution_name],
            limit_price=limit_price,
            reduce_only=reducing,
            position_side=position_side,
            target_position_id=None if target is None else str(target),
            close_quantity=quantity if reducing else None,
            close_all=False,
            idempotency_key=candidate.idempotency_key(),
        )
        admission, graph = self._admission_service.admit_with_graph(
            connection,
            ingress,
            principal,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            mode=EntryMode.PAPER,
        )
        if expected_account_scope and graph is not None:
            actual_scope = getattr(getattr(graph, "specification", None), "account_scope", None)
            if actual_scope != expected_account_scope:
                raise StrategyV2PaperExecutionError("strategyV2.paperAccountScopeConflict")
        if admission.disposition is RuntimeEntryAdmissionDisposition.RISK_REJECTED:
            return StrategyV2PaperExecutionReceipt(admission, None, None)
        if admission.disposition not in {
            RuntimeEntryAdmissionDisposition.CREATED,
            RuntimeEntryAdmissionDisposition.REPLAYED,
        } or admission.admission is None or graph is None:
            raise StrategyV2PaperExecutionError("strategyV2.paperAdmissionUnavailable")
        order_id = _uuid(admission.admission.economic_order_id, "economic_order_id")
        paper_market_type = "perpetual" if market_type == "swap" else "spot"
        order = PaperExecutionOrder(
            order_id=order_id,
            user_id=user_id,
            idempotency_key=ingress.idempotency_key,
            request_fingerprint=admission.admission.request_fingerprint,
            market=str(getattr(request, "exchange_id", "gate") or "gate").lower(),
            symbol=symbol,
            market_type=paper_market_type,
            side=side.value,
            order_type=execution_name,
            quantity=Decimal(quantity),
            limit_price=None if limit_price is None else Decimal(limit_price),
            status=PaperExecutionStatus.SUBMITTED,
            created_at=occurred_at,
        )
        paper_result = self._repository.persist_order(connection, order)
        event = PaperExecutionOrderEvent(
            event_id=str(uuid5(NAMESPACE_URL, f"paper-submitted:{order.order_id}")).lower(),
            order_id=order.order_id,
            event_seq=1,
            event_type=PaperExecutionEventType.SUBMITTED,
            occurred_at=occurred_at,
        )
        self._repository.append_order_event(connection, event, user_id=user_id)
        return StrategyV2PaperExecutionReceipt(admission, order, paper_result)

    @staticmethod
    def _resolve_position_subject(
        connection: object,
        supplied_id: object,
        *,
        strategy_id: int,
        user_id: int,
        credential_id: int,
        account_scope: str | None,
        instrument_id: str,
        market_type: str,
        position_side: PositionSide,
    ) -> str:
        """Resolve a legacy strategy position reference to one UUID subject.

        Strategy V2's canonical contract only accepts the UUID from
        ``qd_runtime_entry_position_subjects``.  Older strategy rows expose a
        serial ``qd_strategy_positions.id``; that identifier is therefore
        treated only as an input hint.  A subject is accepted only when the
        persisted projection, strategy, scope, side and healthy checkpoint
        produce exactly one row on the caller's connection.
        """

        try:
            return _uuid(supplied_id, "target_position_id")
        except StrategyV2PaperExecutionError:
            pass
        if not isinstance(account_scope, str) or not account_scope.strip():
            raise StrategyV2PaperExecutionError("strategyV2.paperAccountScopeRequiredForLegacyPosition")
        if not isinstance(position_side, PositionSide) or position_side is PositionSide.NET:
            raise StrategyV2PaperExecutionError("strategyV2.paperPositionSideRequired")
        try:
            cursor = connection.cursor()
        except StrategyV2PaperExecutionError:
            raise
        except Exception as exc:
            raise StrategyV2PaperExecutionError("strategyV2.paperPositionSubjectUnavailable") from exc
        try:
            try:
                legacy_id = int(str(supplied_id).strip())
            except (TypeError, ValueError) as exc:
                raise StrategyV2PaperExecutionError("strategyV2.paperTargetPositionInvalid") from exc
            if legacy_id <= 0:
                raise StrategyV2PaperExecutionError("strategyV2.paperTargetPositionInvalid")
            cursor.execute(
                """
                SELECT id, user_id, strategy_id, credential_id,
                       symbol_canonical, market_type, side
                  FROM qd_strategy_positions
                 WHERE id = %s
                   AND user_id = %s
                   AND strategy_id = %s
                   AND credential_id = %s
                   AND split_part(COALESCE(NULLIF(symbol_canonical, ''), symbol), ':', 1)
                       = split_part(%s, ':', 1)
                   AND market_type = %s
                   AND LOWER(side) = LOWER(%s)
                 FOR KEY SHARE
                """,
                (
                    legacy_id,
                    user_id,
                    strategy_id,
                    credential_id,
                    instrument_id,
                    "perpetual" if market_type == "swap" else market_type,
                    position_side.value,
                ),
            )
            legacy_row = cursor.fetchone()
            if legacy_row is None:
                raise StrategyV2PaperExecutionError("strategyV2.paperTargetPositionUnavailable")
            cursor.execute(
                """
                SELECT s.id
                  FROM qd_runtime_entry_position_subjects s
                  JOIN qd_position_projections p
                    ON p.id = s.position_projection_id
                  JOIN qd_reconciliation_checkpoints c
                    ON c.id = s.reconciliation_checkpoint_id
                 WHERE p.strategy_id = %s
                   AND s.tenant_id = %s
                   AND s.credential_id = %s
                   AND s.account_scope = %s
                   AND s.instrument_id = %s
                   AND s.market_type = %s
                   AND s.position_side = %s
                   AND c.status = 'HEALTHY'
                   AND p.quantity > 0
                 ORDER BY s.id
                 FOR KEY SHARE OF s, c, p
                """,
                (
                    strategy_id,
                    user_id,
                    credential_id,
                    account_scope.strip(),
                    instrument_id,
                    market_type,
                    position_side.value,
                ),
            )
            rows = cursor.fetchall() or []
        except StrategyV2PaperExecutionError:
            raise
        except Exception as exc:
            raise StrategyV2PaperExecutionError("strategyV2.paperPositionSubjectUnavailable") from exc
        finally:
            try:
                cursor.close()
            except Exception as exc:
                raise StrategyV2PaperExecutionError("strategyV2.paperPositionSubjectUnavailable") from exc
        if len(rows) != 1:
            raise StrategyV2PaperExecutionError("strategyV2.paperPositionSubjectAmbiguous")
        row = rows[0]
        value = row.get("id") if isinstance(row, dict) else row[0]
        return _uuid(value, "resolved_position_subject_id")


__all__ = [
    "StrategyV2PaperExecutionError",
    "StrategyV2PaperExecutionReceipt",
    "StrategyV2PaperExecutionService",
]
