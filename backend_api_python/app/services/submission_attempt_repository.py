"""Caller-owned persistence for canonical submission attempts.

An attempt is an immutable fact that must exist before a venue request is
sent.  This repository deliberately owns no transaction boundary: callers
compose it with admission, risk and outbox facts and decide when to commit.
The legacy ``persist`` wrapper is retained for isolated callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Mapping
from uuid import UUID

from app.domain.order_state_machine import SubmissionAttemptScope


class SubmissionAttemptPersistenceError(RuntimeError):
    """Base error for typed attempt persistence failures."""


class SubmissionAttemptConflict(SubmissionAttemptPersistenceError):
    """A durable identity exists with different immutable facts."""


class SubmissionAttemptDisposition(str, Enum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise SubmissionAttemptPersistenceError(f"{name} must be canonical ASCII text")
    return value


def _canonical_uuid(value: object, name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SubmissionAttemptPersistenceError(f"{name} must be a UUID") from exc


def _canonical_json(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise SubmissionAttemptPersistenceError("request_json_redacted must be an object")

    def reject_float(item: Any) -> Any:
        if isinstance(item, float):
            raise SubmissionAttemptPersistenceError("request_json_redacted cannot contain binary float")
        if isinstance(item, Mapping):
            return {str(key): reject_float(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [reject_float(nested) for nested in item]
        return item

    try:
        return json.dumps(reject_float(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise SubmissionAttemptPersistenceError("request_json_redacted is not JSON-safe") from exc


@dataclass(frozen=True, slots=True)
class SubmissionAttemptCreateFacts:
    id: str
    scope: SubmissionAttemptScope
    child_seq: int
    attempt_no: int
    role: str
    canonical_client_order_id: str
    venue_client_order_id: str
    request_fingerprint: str
    request_json_redacted: Mapping[str, Any] = field(default_factory=dict)
    venue_capability_snapshot_id: str = ""
    recovery_policy_snapshot_id: str = ""
    client_id_algorithm_version: str = ""
    broker_prefix_normalization_version: str = ""
    broker_prefix: str = ""
    canonical_contract_version: str = "attempt-contract-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.scope, SubmissionAttemptScope):
            raise SubmissionAttemptPersistenceError("scope must be SubmissionAttemptScope")
        object.__setattr__(self, "id", _canonical_uuid(self.id, "id"))
        for value, name in ((self.id, "id"), (self.canonical_client_order_id, "canonical_client_order_id"),
                            (self.venue_client_order_id, "venue_client_order_id"),
                            (self.request_fingerprint, "request_fingerprint"),
                            (self.venue_capability_snapshot_id, "venue_capability_snapshot_id"),
                            (self.recovery_policy_snapshot_id, "recovery_policy_snapshot_id"),
                            (self.client_id_algorithm_version, "client_id_algorithm_version"),
                            (self.broker_prefix_normalization_version, "broker_prefix_normalization_version"),
                            (self.broker_prefix, "broker_prefix"),
                            (self.canonical_contract_version, "canonical_contract_version")):
            _required_text(value, name)
        object.__setattr__(self, "venue_capability_snapshot_id", _canonical_uuid(self.venue_capability_snapshot_id, "venue_capability_snapshot_id"))
        object.__setattr__(self, "recovery_policy_snapshot_id", _canonical_uuid(self.recovery_policy_snapshot_id, "recovery_policy_snapshot_id"))
        if self.role not in {"PRIMARY", "FALLBACK", "PROTECTION", "EMERGENCY"}:
            raise SubmissionAttemptPersistenceError("role is not supported")
        if self.canonical_contract_version != "attempt-contract-v1":
            raise SubmissionAttemptPersistenceError("unsupported attempt contract version")
        for value, name in ((self.child_seq, "child_seq"), (self.attempt_no, "attempt_no")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise SubmissionAttemptPersistenceError(f"{name} must be a positive integer")
        object.__setattr__(self, "request_json_redacted", json.loads(_canonical_json(self.request_json_redacted)))


@dataclass(frozen=True, slots=True)
class SubmissionAttemptPersistenceResult:
    attempt_id: str
    disposition: SubmissionAttemptDisposition


def _row_value(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


class SubmissionAttemptRepository:
    """Insert-first attempt arbitration with no implicit transaction I/O."""

    def persist_caller_owned(self, connection: Any, facts: SubmissionAttemptCreateFacts) -> SubmissionAttemptPersistenceResult:
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO qd_submission_attempts (
                    id, economic_order_id, exchange, tenant_id, credential_id, account_scope,
                    instrument_id, market_type, child_seq, attempt_no, role,
                    canonical_client_order_id, venue_client_order_id, request_fingerprint,
                    request_json_redacted, state, venue_capability_snapshot_id,
                    recovery_policy_snapshot_id, client_id_algorithm_version,
                    broker_prefix_normalization_version, broker_prefix, canonical_contract_version
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'READY',%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (facts.id, facts.scope.economic_order_id, facts.scope.exchange,
                 facts.scope.tenant_id, facts.scope.credential_id, facts.scope.account_scope,
                 facts.scope.instrument_id, facts.scope.market_type, facts.child_seq, facts.attempt_no,
                 facts.role, facts.canonical_client_order_id, facts.venue_client_order_id,
                 facts.request_fingerprint, _canonical_json(facts.request_json_redacted),
                 facts.venue_capability_snapshot_id, facts.recovery_policy_snapshot_id,
                 facts.client_id_algorithm_version, facts.broker_prefix_normalization_version,
                 facts.broker_prefix, facts.canonical_contract_version),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return SubmissionAttemptPersistenceResult(str(_row_value(inserted, 0, "id")), SubmissionAttemptDisposition.APPLIED)
            cursor.execute(
                """
                SELECT id, economic_order_id, exchange, tenant_id, credential_id, account_scope,
                       instrument_id, market_type, child_seq, attempt_no, role,
                       canonical_client_order_id, venue_client_order_id, request_fingerprint,
                       request_json_redacted, venue_capability_snapshot_id,
                       recovery_policy_snapshot_id, client_id_algorithm_version,
                       broker_prefix_normalization_version, broker_prefix, canonical_contract_version
                  FROM qd_submission_attempts
                 WHERE id = %s
                    OR (economic_order_id = %s AND child_seq = %s AND attempt_no = %s)
                    OR (exchange = %s AND credential_id = %s AND market_type = %s AND venue_client_order_id = %s)
                 ORDER BY id
                 FOR UPDATE
                """,
                (facts.id, facts.scope.economic_order_id, facts.child_seq, facts.attempt_no,
                 facts.scope.exchange, facts.scope.credential_id, facts.scope.market_type,
                 facts.venue_client_order_id),
            )
            rows = cursor.fetchall() if hasattr(cursor, "fetchall") else []
            if not rows:
                raise SubmissionAttemptConflict("attempt uniqueness conflict is not visible")
            if len(rows) != 1:
                raise SubmissionAttemptConflict("attempt identity conflicts with multiple durable rows")
            row = rows[0]
            expected = (
                facts.id, facts.scope.economic_order_id, facts.scope.exchange,
                facts.scope.tenant_id, facts.scope.credential_id, facts.scope.account_scope,
                facts.scope.instrument_id, facts.scope.market_type, facts.child_seq, facts.attempt_no,
                facts.role, facts.canonical_client_order_id, facts.venue_client_order_id,
                facts.request_fingerprint, _canonical_json(facts.request_json_redacted),
                facts.venue_capability_snapshot_id, facts.recovery_policy_snapshot_id,
                facts.client_id_algorithm_version, facts.broker_prefix_normalization_version,
                facts.broker_prefix, facts.canonical_contract_version,
            )
            actual = tuple(_row_value(row, index, key) for index, key in enumerate((
                "id", "economic_order_id", "exchange", "tenant_id", "credential_id", "account_scope",
                "instrument_id", "market_type", "child_seq", "attempt_no", "role",
                "canonical_client_order_id", "venue_client_order_id", "request_fingerprint",
                "request_json_redacted", "venue_capability_snapshot_id", "recovery_policy_snapshot_id",
                "client_id_algorithm_version", "broker_prefix_normalization_version", "broker_prefix",
                "canonical_contract_version",
            )))
            normalized = list(actual)
            for index in (0, 1, 15, 16):
                if normalized[index] is not None:
                    normalized[index] = str(normalized[index])
            if isinstance(normalized[14], str):
                normalized[14] = json.loads(normalized[14])
            normalized[14] = _canonical_json(normalized[14])
            normalized[9] = int(normalized[9]); normalized[8] = int(normalized[8])
            if tuple(normalized) != expected:
                raise SubmissionAttemptConflict("attempt identity has different immutable facts")
            return SubmissionAttemptPersistenceResult(str(normalized[0]), SubmissionAttemptDisposition.REPLAYED)
        finally:
            cursor.close()

    def persist(self, connection: Any, facts: SubmissionAttemptCreateFacts) -> SubmissionAttemptPersistenceResult:
        try:
            result = self.persist_caller_owned(connection, facts)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


__all__ = [
    "SubmissionAttemptConflict",
    "SubmissionAttemptCreateFacts",
    "SubmissionAttemptDisposition",
    "SubmissionAttemptPersistenceError",
    "SubmissionAttemptPersistenceResult",
    "SubmissionAttemptRepository",
]
