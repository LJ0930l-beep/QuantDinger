"""Atomic PostgreSQL persistence for the PR-06 immutable fill-ledger bundle.

The caller supplies an already-open DB-API connection.  This module has no
Flask, worker, executor, strategy, exchange-client, or live-trading imports.
It writes a normalized fill, its fee/evidence facts, and balanced TRADE/FEE
ledger transactions in one database transaction.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from app.domain.immutable_fill_ledger import (
    FillLedgerBundle,
    FillLedgerInput,
    ImmutableLedgerContractError,
    LedgerTransaction,
    LedgerTransactionType,
    QuoteQuantityOrigin,
    reduce_fill_to_ledger_bundle,
)


FILL_KEY_VERSION = "venue-fill-key-v1"
_LEDGER_UUID_NAMESPACE = uuid.UUID("27c9263e-201c-4e35-9d0f-0ec6b0212b7c")


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class ImmutableLedgerRepositoryError(RuntimeError):
    """A database failure represented at the immutable-ledger boundary."""


class FillLedgerReplayConflict(ImmutableLedgerRepositoryError):
    """The stable fill key already names different immutable facts."""


class FillLedgerPersistenceConflict(ImmutableLedgerRepositoryError):
    """A database constraint or serialization conflict prevents safe persistence."""


class FillLedgerCommitDisposition(str, Enum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class FillLedgerPersistenceScope:
    tenant_id: int
    credential_id: int
    intent_id: str | None
    economic_order_id: str
    source: str
    exchange_event_at: datetime
    received_at: datetime
    normalizer_version: str
    instrument_rule_version: str
    durable_entry_command_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.tenant_id, bool) or not isinstance(self.tenant_id, int) or self.tenant_id < 1:
            raise ImmutableLedgerContractError("tenant_id must be a positive integer")
        if isinstance(self.credential_id, bool) or not isinstance(self.credential_id, int) or self.credential_id < 1:
            raise ImmutableLedgerContractError("credential_id must be a positive integer")
        if (self.intent_id is None) == (self.durable_entry_command_id is None):
            raise ImmutableLedgerContractError(
                "exactly one of intent_id or durable_entry_command_id is required"
            )
        if self.intent_id is not None:
            object.__setattr__(self, "intent_id", _canonical_uuid(self.intent_id, "intent_id"))
        if self.durable_entry_command_id is not None:
            object.__setattr__(
                self,
                "durable_entry_command_id",
                _canonical_uuid(self.durable_entry_command_id, "durable_entry_command_id"),
            )
        object.__setattr__(self, "economic_order_id", _canonical_uuid(self.economic_order_id, "economic_order_id"))
        source = _canonical_string(self.source, "source", case="upper")
        if source not in {"WS", "REST", "BACKFILL", "MANUAL"}:
            raise ImmutableLedgerContractError("source must be a normalized fill source")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "exchange_event_at", _strict_utc(self.exchange_event_at, "exchange_event_at"))
        object.__setattr__(self, "received_at", _strict_utc(self.received_at, "received_at"))
        object.__setattr__(self, "normalizer_version", _canonical_string(self.normalizer_version, "normalizer_version"))
        object.__setattr__(self, "instrument_rule_version", _canonical_string(self.instrument_rule_version, "instrument_rule_version"))


@dataclass(frozen=True, slots=True)
class FillLedgerCommitResult:
    fill_event_id: str
    trade_transaction_id: str
    fee_transaction_id: str | None
    replay_fingerprint: str
    disposition: FillLedgerCommitDisposition


def _canonical_string(value: object, field: str, *, case: str | None = None) -> str:
    if not isinstance(value, str):
        raise ImmutableLedgerContractError(f"{field} must be a string")
    canonical = value.strip()
    if not canonical:
        raise ImmutableLedgerContractError(f"{field} is required")
    if case == "upper":
        canonical = canonical.upper()
    elif case == "lower":
        canonical = canonical.lower()
    return canonical


def _canonical_uuid(value: object, field: str) -> str:
    raw = _canonical_string(value, field)
    try:
        return str(uuid.UUID(raw))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ImmutableLedgerContractError(f"{field} must be a UUID") from exc


def _strict_utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ImmutableLedgerContractError(f"{field} must be UTC-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ImmutableLedgerContractError(f"{field} must already be UTC")
    return value.astimezone(timezone.utc)


def _stable_uuid(material: str) -> str:
    return str(uuid.uuid5(_LEDGER_UUID_NAMESPACE, material))


def _scoped_fill_event_id(scope: FillLedgerPersistenceScope, fill_key: str) -> str:
    """Keep the database primary key isolated by immutable credential scope.

    ``VenueFillIdentity.canonical_key`` intentionally models venue evidence
    scope and contains no credential identifier.  Database primary keys and
    global source-fingerprint indexes must additionally isolate tenant and
    credential facts; otherwise identical venue identifiers from two accounts
    collide before the composite database uniqueness rules can arbitrate.
    """

    return _stable_uuid(f"fill-event:{scope.tenant_id}:{scope.credential_id}:{fill_key}")


def _storage_source_fingerprint(
    scope: FillLedgerPersistenceScope,
    transaction: LedgerTransaction,
) -> str:
    material = (
        f"{scope.tenant_id}:{scope.credential_id}:{transaction.account_scope}:"
        f"{transaction.source_fingerprint}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _payload_hash(bundle: FillLedgerBundle) -> str:
    return hashlib.sha256(bundle.replay_fingerprint.encode("ascii")).hexdigest()


def _row_value(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


class ImmutableFillLedgerRepository:
    """Persist a complete fill bundle atomically or return a typed replay/conflict."""

    def persist_fill_bundle(
        self,
        connection: Connection,
        *,
        scope: FillLedgerPersistenceScope,
        fill: FillLedgerInput,
    ) -> FillLedgerCommitResult:
        try:
            result = self.persist_fill_bundle_caller_owned(connection, scope=scope, fill=fill)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    def persist_fill_bundle_caller_owned(
        self,
        connection: Connection,
        *,
        scope: FillLedgerPersistenceScope,
        fill: FillLedgerInput,
    ) -> FillLedgerCommitResult:
        """Append/replay a fill bundle without committing or rolling back.

        This is the composition seam for Admission/Worker transactions.  The
        legacy ``persist_fill_bundle`` wrapper below preserves its historical
        commit-on-success and rollback-on-error behavior.
        """

        bundle = reduce_fill_to_ledger_bundle(fill)
        self._validate_scope(scope, fill)
        cursor = connection.cursor()
        try:
            fill_event_id = _scoped_fill_event_id(scope, bundle.fill_key)
            inserted = self._insert_fill(cursor, fill_event_id, scope, fill, bundle)
            if not inserted:
                return self._load_matching_replay(cursor, fill_event_id, scope, fill, bundle)
            evidence_ids = self._insert_valuation_evidence(cursor, fill_event_id, scope, fill)
            self._insert_fee_components(cursor, fill_event_id, scope, fill, evidence_ids)
            trade_transaction_id = self._insert_transaction(cursor, fill_event_id, scope, fill, bundle.trade)
            fee_transaction_id = None
            if bundle.fee is not None:
                fee_transaction_id = self._insert_transaction(cursor, fill_event_id, scope, fill, bundle.fee)
            return FillLedgerCommitResult(
                fill_event_id=fill_event_id,
                trade_transaction_id=trade_transaction_id,
                fee_transaction_id=fee_transaction_id,
                replay_fingerprint=bundle.replay_fingerprint,
                disposition=FillLedgerCommitDisposition.APPLIED,
            )
        except (ImmutableLedgerContractError, FillLedgerReplayConflict, FillLedgerPersistenceConflict):
            raise
        except Exception as exc:
            raise self._map_database_error(exc) from exc
        finally:
            cursor.close()

    def _validate_scope(self, scope: FillLedgerPersistenceScope, fill: FillLedgerInput) -> None:
        venue_scope = fill.venue_fill.order_scope
        if scope.economic_order_id != fill.economic_order_id:
            raise FillLedgerReplayConflict("persistence economic order does not match immutable fill facts")
        if venue_scope.account_scope != fill.account_scope:
            raise FillLedgerReplayConflict("persistence account scope does not match immutable fill facts")

    def _insert_fill(
        self,
        cursor: Cursor,
        fill_event_id: str,
        scope: FillLedgerPersistenceScope,
        fill: FillLedgerInput,
        bundle: FillLedgerBundle,
    ) -> bool:
        venue_scope = fill.venue_fill.order_scope
        fee_summary_state, fee_amount, fee_asset, fee_quote_amount = self._scalar_fee_columns(fill)
        quote = fill.quote_quantity
        if scope.intent_id is None:
            table = "qd_durable_entry_fill_events"
            parent_column = "command_id"
            parent_value = scope.durable_entry_command_id
        else:
            table = "qd_exchange_fill_events"
            parent_column = "intent_id"
            parent_value = scope.intent_id
        cursor.execute(
            f"""
            INSERT INTO {table} (
                id, key_version, dedupe_key, exchange, tenant_id, credential_id,
                account_scope, market_type, economic_order_id, {parent_column},
                exchange_order_id, exchange_fill_id, instrument_id, side, price,
                quantity, quote_quantity, quote_quantity_origin,
                quote_quantity_policy_version, quote_quantity_evidence_hash,
                fee_summary_state, fee_amount, fee_asset, fee_quote_amount,
                exchange_event_at, received_at, source, raw_payload, raw_payload_hash,
                normalizer_version, instrument_rule_version
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s
            ) ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                fill_event_id, FILL_KEY_VERSION, bundle.fill_key, venue_scope.venue,
                scope.tenant_id, scope.credential_id, fill.account_scope, venue_scope.market_type,
                scope.economic_order_id, parent_value, venue_scope.exchange_order_id,
                fill.venue_fill.venue_fill_id, venue_scope.instrument, fill.side.value,
                fill.venue_fill.price.to_string(), fill.venue_fill.quantity.to_string(),
                quote.amount.to_string(), quote.origin.value, quote.calculation_policy_version,
                quote.evidence_hash, fee_summary_state, fee_amount, fee_asset, fee_quote_amount,
                scope.exchange_event_at, scope.received_at, scope.source,
                json.dumps(fill.canonical_payload(), sort_keys=True, separators=(",", ":")),
                _payload_hash(bundle), scope.normalizer_version, scope.instrument_rule_version,
            ),
        )
        return cursor.fetchone() is not None

    def _scalar_fee_columns(self, fill: FillLedgerInput) -> tuple[str, str, str, str | None]:
        if not fill.fee_components:
            return ("NONE", "0", "", None)
        if len(fill.fee_components) == 1:
            component = fill.fee_components[0]
            return (
                "SINGLE_COMPONENT",
                component.fee.amount.to_string(),
                component.fee.asset,
                component.valuation_amount.to_string(),
            )
        return ("MULTI_COMPONENT", "0", "", None)

    def _insert_valuation_evidence(
        self,
        cursor: Cursor,
        fill_event_id: str,
        scope: FillLedgerPersistenceScope,
        fill: FillLedgerInput,
    ) -> dict[str, str]:
        table = (
            "qd_durable_entry_ledger_valuation_evidence"
            if scope.intent_id is None
            else "qd_ledger_valuation_evidence"
        )
        evidence_ids: dict[str, str] = {}
        evidence_by_hash = [fill.quote_quantity.valuation_evidence] + [
            component.valuation_evidence for component in fill.fee_components
        ]
        for evidence in evidence_by_hash:
            evidence_id = _stable_uuid(f"valuation-evidence:{fill_event_id}:{evidence.evidence_hash}")
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    id, fill_event_id, asset, valuation_ccy, price, evidence_source,
                    policy_version, observed_at, payload_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (
                    evidence_id, fill_event_id, evidence.asset, evidence.valuation_ccy,
                    evidence.price.to_string(), evidence.source.value, evidence.policy_version,
                    scope.exchange_event_at, evidence.evidence_hash,
                ),
            )
            evidence_ids[evidence.evidence_hash] = evidence_id
        return evidence_ids

    def _insert_fee_components(
        self,
        cursor: Cursor,
        fill_event_id: str,
        scope: FillLedgerPersistenceScope,
        fill: FillLedgerInput,
        evidence_ids: dict[str, str],
    ) -> None:
        table = (
            "qd_durable_entry_fill_fee_components"
            if scope.intent_id is None
            else "qd_exchange_fill_fee_components"
        )
        for component in fill.fee_components:
            evidence = component.valuation_evidence
            evidence_id = evidence_ids.get(evidence.evidence_hash)
            if evidence_id is None:
                raise FillLedgerReplayConflict("fee valuation evidence is incomplete")
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    fill_event_id, fee_seq, asset, amount, fee_quote_amount,
                    valuation_ccy, valuation_evidence_id, raw_component_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    fill_event_id, component.fee_seq, component.fee.asset,
                    component.fee.amount.to_string(), component.valuation_amount.to_string(),
                    evidence.valuation_ccy, evidence_id, evidence.evidence_hash,
                ),
            )

    def _insert_transaction(
        self,
        cursor: Cursor,
        fill_event_id: str,
        scope: FillLedgerPersistenceScope,
        fill: FillLedgerInput,
        transaction: LedgerTransaction,
    ) -> str:
        storage_source_fingerprint = _storage_source_fingerprint(scope, transaction)
        transaction_id = _stable_uuid(f"ledger-transaction:{storage_source_fingerprint}")
        cursor.execute(
            """
            INSERT INTO qd_ledger_transactions (
                id, tenant_id, credential_id, account_scope, transaction_type,
                source_event_type, source_event_id, source_fingerprint,
                effective_at, valuation_ccy, policy_version, correlation_id, description_code
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                transaction_id, scope.tenant_id, scope.credential_id, transaction.account_scope,
                transaction.transaction_type.value, transaction.source_event_type, fill_event_id,
                storage_source_fingerprint, scope.exchange_event_at, transaction.valuation_ccy,
                "fill-ledger-v1", fill.fill_key, "IMMUTABLE_FILL_BUNDLE",
            ),
        )
        for line_no, entry in enumerate(transaction.entries, 1):
            if scope.intent_id is None:
                cursor.execute(
                    """
                    INSERT INTO qd_ledger_entries (
                        id, transaction_id, line_no, book, account_code, asset,
                        signed_amount, value_in_valuation_ccy, instrument_id,
                        economic_order_id, durable_entry_command_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        _stable_uuid(f"ledger-entry:{storage_source_fingerprint}:{line_no}"),
                        transaction_id, line_no, entry.book.value, entry.account_code, entry.asset,
                        str(entry.signed_amount),
                        None if entry.value_in_valuation_ccy is None else str(entry.value_in_valuation_ccy),
                        entry.instrument_id, None, scope.durable_entry_command_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO qd_ledger_entries (
                        id, transaction_id, line_no, book, account_code, asset,
                        signed_amount, value_in_valuation_ccy, instrument_id, economic_order_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        _stable_uuid(f"ledger-entry:{storage_source_fingerprint}:{line_no}"),
                        transaction_id, line_no, entry.book.value, entry.account_code, entry.asset,
                        str(entry.signed_amount),
                        None if entry.value_in_valuation_ccy is None else str(entry.value_in_valuation_ccy),
                        entry.instrument_id, scope.economic_order_id,
                    ),
                )
        return transaction_id

    def _load_matching_replay(
        self,
        cursor: Cursor,
        fill_event_id: str,
        scope: FillLedgerPersistenceScope,
        fill: FillLedgerInput,
        bundle: FillLedgerBundle,
    ) -> FillLedgerCommitResult:
        table = (
            "qd_durable_entry_fill_events"
            if scope.intent_id is None
            else "qd_exchange_fill_events"
        )
        parent_column = "command_id" if scope.intent_id is None else "intent_id"
        parent_value = scope.durable_entry_command_id if scope.intent_id is None else scope.intent_id
        cursor.execute(
            f"""
            SELECT id, tenant_id, credential_id, account_scope, market_type,
                   economic_order_id, {parent_column}, exchange_order_id, exchange_fill_id,
                   instrument_id, side, raw_payload_hash
              FROM {table}
             WHERE id = %s
             FOR UPDATE
            """,
            (fill_event_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise FillLedgerPersistenceConflict("duplicate fill is not visible for replay comparison")
        venue_scope = fill.venue_fill.order_scope
        observed = (
            str(_row_value(row, 0, "id")), _row_value(row, 1, "tenant_id"),
            _row_value(row, 2, "credential_id"), _row_value(row, 3, "account_scope"),
            _row_value(row, 4, "market_type"), str(_row_value(row, 5, "economic_order_id")),
            str(_row_value(row, 6, parent_column)), _row_value(row, 7, "exchange_order_id"),
            _row_value(row, 8, "exchange_fill_id"), _row_value(row, 9, "instrument_id"),
            _row_value(row, 10, "side"), _row_value(row, 11, "raw_payload_hash"),
        )
        expected = (
            fill_event_id, scope.tenant_id, scope.credential_id, fill.account_scope,
            venue_scope.market_type, scope.economic_order_id, parent_value,
            venue_scope.exchange_order_id, fill.venue_fill.venue_fill_id,
            venue_scope.instrument, fill.side.value, _payload_hash(bundle),
        )
        if observed != expected:
            raise FillLedgerReplayConflict("stable fill key names different immutable facts")
        trade_source_fingerprint = _storage_source_fingerprint(scope, bundle.trade)
        trade_id = _stable_uuid(f"ledger-transaction:{trade_source_fingerprint}")
        fee_source_fingerprint = (
            None if bundle.fee is None else _storage_source_fingerprint(scope, bundle.fee)
        )
        fee_id = (
            None if fee_source_fingerprint is None
            else _stable_uuid(f"ledger-transaction:{fee_source_fingerprint}")
        )
        cursor.execute(
            """
            SELECT id, transaction_type, source_fingerprint
              FROM qd_ledger_transactions
             WHERE id = %s OR (%s IS NOT NULL AND id = %s)
             FOR UPDATE
            """,
            (trade_id, fee_id, fee_id),
        )
        rows = cursor.fetchall() if hasattr(cursor, "fetchall") else []
        expected_transactions = {(trade_id, "TRADE", trade_source_fingerprint)}
        if bundle.fee is not None and fee_id is not None and fee_source_fingerprint is not None:
            expected_transactions.add((fee_id, "FEE", fee_source_fingerprint))
        observed_transactions = {
            (str(_row_value(item, 0, "id")), _row_value(item, 1, "transaction_type"), _row_value(item, 2, "source_fingerprint"))
            for item in rows
        }
        if observed_transactions != expected_transactions:
            raise FillLedgerReplayConflict("existing fill has no complete immutable ledger bundle")
        return FillLedgerCommitResult(
            fill_event_id=fill_event_id,
            trade_transaction_id=trade_id,
            fee_transaction_id=fee_id,
            replay_fingerprint=bundle.replay_fingerprint,
            disposition=FillLedgerCommitDisposition.REPLAYED,
        )

    def _map_database_error(self, exc: Exception) -> ImmutableLedgerRepositoryError:
        pgcode = getattr(exc, "pgcode", None)
        if pgcode in {"23505", "23503", "23514", "40001", "40P01"}:
            return FillLedgerPersistenceConflict("immutable fill-ledger database conflict")
        return ImmutableLedgerRepositoryError("immutable fill-ledger persistence failed")
