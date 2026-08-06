"""Fail-closed, in-memory Gate order-book evidence sessions.

This module joins an already typed REST snapshot with verified public stream
frames.  It deliberately has no transport, credential, account, database,
runtime, scheduler, or order-submission capability.  A caller supplies the
REST reseed snapshot after a gap or stale period; until then a prior local
book cannot be presented as healthy market evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json

from .gate_market_read_contracts import GateOrderBookSnapshot
from .gate_order_book_materialization_contracts import (
    GateOrderBookMaterializationDisposition,
    GateOrderBookMaterializationError,
    GateOrderBookMaterializationResult,
    GateOrderBookMaterializedState,
    apply_gate_order_book_materialized_frame,
    gate_order_book_materialized_state_from_snapshot,
)
from .gate_order_book_stream_contracts import (
    GateOrderBookReseedPlan,
    GateOrderBookStreamFrame,
    GateOrderBookStreamSubscription,
    plan_gate_order_book_reseed,
)


GATE_ORDER_BOOK_STREAM_SESSION_CONTRACT_VERSION = "gate-order-book-stream-session-v1"


class GateOrderBookStreamSessionError(GateOrderBookMaterializationError):
    """A stream session cannot safely prove usable market-depth evidence."""


class GateOrderBookEvidenceHealth(str, Enum):
    """Whether the local materialized depth may be consumed as evidence."""

    HEALTHY = "HEALTHY"
    RESEED_REQUIRED = "RESEED_REQUIRED"
    STALE = "STALE"


class GateOrderBookStreamSessionDisposition(str, Enum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"
    RESEED_REQUIRED = "RESEED_REQUIRED"
    CONFLICT = "CONFLICT"
    STALE = "STALE"
    RESEEDED = "RESEEDED"


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateOrderBookStreamSessionError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def _freshness_window(value: object) -> timedelta:
    if not isinstance(value, timedelta) or value <= timedelta(0):
        raise GateOrderBookStreamSessionError("max_staleness must be a positive timedelta")
    return value


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(character.isspace() for character in value):
        raise GateOrderBookStreamSessionError(f"{field_name} must be canonical ASCII text")
    return value


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _subscription_matches_state(
    subscription: GateOrderBookStreamSubscription,
    state: GateOrderBookMaterializedState,
    *,
    include_snapshot_id: bool,
) -> bool:
    expected = state.subscription
    return (
        subscription.market_type is expected.market_type
        and subscription.instrument_id == expected.instrument_id
        and subscription.rule_version == expected.rule_version
        and subscription.depth_limit == expected.depth_limit
        and subscription.update_interval == expected.update_interval
        and (not include_snapshot_id or subscription.snapshot_id == expected.snapshot_id)
    )


def _reseed_plan_matches_state(
    plan: GateOrderBookReseedPlan,
    state: GateOrderBookMaterializedState,
) -> bool:
    subscription = state.subscription
    return (
        plan.market_type is subscription.market_type
        and plan.instrument_id == subscription.instrument_id
        and plan.snapshot_id == subscription.snapshot_id
        and plan.rule_version == subscription.rule_version
        and plan.depth_limit == subscription.depth_limit
        and plan.update_interval == subscription.update_interval
        and plan.last_verified_update_id == state.stream_state.next_update_id - 1
        and plan.expected_next_update_id == state.stream_state.next_update_id
    )


@dataclass(frozen=True, slots=True)
class GateOrderBookStreamSession:
    """Immutable local order-book session with explicit health.

    ``materialized_state`` is retained for recovery diagnostics even when the
    session is stale or needs a REST reseed.  Consumers must call
    :meth:`healthy_snapshot` rather than reading that retained state directly;
    this avoids exposing a known-invalid depth image as current evidence.
    """

    materialized_state: GateOrderBookMaterializedState
    health: GateOrderBookEvidenceHealth
    last_checked_at: datetime
    reseed_plan: GateOrderBookReseedPlan | None = None
    session_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.materialized_state, GateOrderBookMaterializedState):
            raise GateOrderBookStreamSessionError("materialized_state must be typed")
        if not isinstance(self.health, GateOrderBookEvidenceHealth):
            raise GateOrderBookStreamSessionError("health must be typed")
        checked = _utc(self.last_checked_at, "last_checked_at")
        if checked < self.materialized_state.snapshot.observed_at:
            raise GateOrderBookStreamSessionError("last_checked_at cannot precede materialized evidence")
        if self.health is GateOrderBookEvidenceHealth.RESEED_REQUIRED:
            if not isinstance(self.reseed_plan, GateOrderBookReseedPlan) or not _reseed_plan_matches_state(self.reseed_plan, self.materialized_state):
                raise GateOrderBookStreamSessionError("reseed-required session must retain a matching typed reseed plan")
        elif self.reseed_plan is not None:
            raise GateOrderBookStreamSessionError("healthy or stale session cannot retain a reseed plan")
        object.__setattr__(self, "last_checked_at", checked)
        object.__setattr__(self, "session_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_STREAM_SESSION_CONTRACT_VERSION,
            "materialization": self.materialized_state.materialization_fingerprint,
            "health": self.health.value,
            "last_checked_at": checked.isoformat(),
            "reseed_plan": None if self.reseed_plan is None else self.reseed_plan.plan_fingerprint,
        }))

    @property
    def latest_observed_at(self) -> datetime:
        return self.materialized_state.snapshot.observed_at

    def healthy_snapshot(self) -> GateOrderBookSnapshot:
        """Return depth only when the session can prove it remains healthy."""

        if self.health is not GateOrderBookEvidenceHealth.HEALTHY:
            raise GateOrderBookStreamSessionError("unhealthy order book session cannot provide current depth evidence")
        return self.materialized_state.snapshot


@dataclass(frozen=True, slots=True)
class GateOrderBookStreamSessionResult:
    disposition: GateOrderBookStreamSessionDisposition
    session: GateOrderBookStreamSession
    reason: str
    materialization_result: GateOrderBookMaterializationResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, GateOrderBookStreamSessionDisposition) or not isinstance(self.session, GateOrderBookStreamSession):
            raise GateOrderBookStreamSessionError("session result requires typed disposition and session")
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        if self.materialization_result is not None and not isinstance(self.materialization_result, GateOrderBookMaterializationResult):
            raise GateOrderBookStreamSessionError("materialization_result must be typed when supplied")
        if self.disposition is GateOrderBookStreamSessionDisposition.APPLIED:
            if self.session.health is not GateOrderBookEvidenceHealth.HEALTHY or self.materialization_result is None:
                raise GateOrderBookStreamSessionError("applied result must retain healthy typed materialization")
        if self.disposition is GateOrderBookStreamSessionDisposition.REPLAYED:
            # A frame replay includes its materialization receipt.  A pure
            # freshness check does not apply a frame and therefore has no
            # synthetic materialization result to attach.
            if self.session.health is not GateOrderBookEvidenceHealth.HEALTHY:
                raise GateOrderBookStreamSessionError("replayed result must retain healthy evidence")
        if self.disposition is GateOrderBookStreamSessionDisposition.RESEED_REQUIRED:
            # A newly detected gap includes the materialization result.  A
            # later caller that is merely told the session remains blocked
            # retains the immutable reseed plan, rather than forging a second
            # materialization result for a frame it did not apply.
            if self.session.health is not GateOrderBookEvidenceHealth.RESEED_REQUIRED or self.session.reseed_plan is None:
                raise GateOrderBookStreamSessionError("reseed-required result must retain typed recovery evidence")
        if self.disposition is GateOrderBookStreamSessionDisposition.CONFLICT:
            if self.session.health is not GateOrderBookEvidenceHealth.RESEED_REQUIRED or self.session.reseed_plan is None or self.materialization_result is None:
                raise GateOrderBookStreamSessionError("conflict result must retain typed recovery evidence")
        if self.disposition is GateOrderBookStreamSessionDisposition.STALE and self.session.health is not GateOrderBookEvidenceHealth.STALE:
            raise GateOrderBookStreamSessionError("stale result must retain stale health")
        if self.disposition is GateOrderBookStreamSessionDisposition.RESEEDED and self.session.health is not GateOrderBookEvidenceHealth.HEALTHY:
            raise GateOrderBookStreamSessionError("reseeded result must restore healthy evidence")


def _session_for_as_of(
    session: GateOrderBookStreamSession,
    *,
    as_of: datetime,
    max_staleness: timedelta,
) -> GateOrderBookStreamSessionResult | None:
    """Return a non-healthy result if prior evidence may no longer be used."""

    if session.health is GateOrderBookEvidenceHealth.RESEED_REQUIRED:
        return GateOrderBookStreamSessionResult(
            GateOrderBookStreamSessionDisposition.RESEED_REQUIRED,
            session,
            "prior_reseed_required",
        )
    if session.health is GateOrderBookEvidenceHealth.STALE:
        return GateOrderBookStreamSessionResult(
            GateOrderBookStreamSessionDisposition.STALE,
            session,
            "prior_evidence_is_stale",
        )
    if as_of - session.latest_observed_at > max_staleness:
        stale = GateOrderBookStreamSession(
            materialized_state=session.materialized_state,
            health=GateOrderBookEvidenceHealth.STALE,
            last_checked_at=as_of,
        )
        return GateOrderBookStreamSessionResult(
            GateOrderBookStreamSessionDisposition.STALE,
            stale,
            "order_book_evidence_exceeds_staleness_window",
        )
    return None


def gate_order_book_stream_session_from_snapshot(
    snapshot: GateOrderBookSnapshot,
    subscription: GateOrderBookStreamSubscription,
    *,
    as_of: datetime,
    max_staleness: timedelta,
) -> GateOrderBookStreamSession:
    """Start one session from a typed REST snapshot without I/O or mutation."""

    if not isinstance(snapshot, GateOrderBookSnapshot) or not isinstance(subscription, GateOrderBookStreamSubscription):
        raise GateOrderBookStreamSessionError("typed snapshot and subscription are required")
    checked = _utc(as_of, "as_of")
    window = _freshness_window(max_staleness)
    if checked < snapshot.observed_at:
        raise GateOrderBookStreamSessionError("as_of cannot precede REST snapshot evidence")
    state = gate_order_book_materialized_state_from_snapshot(snapshot, subscription)
    health = (
        GateOrderBookEvidenceHealth.STALE
        if checked - snapshot.observed_at > window
        else GateOrderBookEvidenceHealth.HEALTHY
    )
    return GateOrderBookStreamSession(state, health, checked)


def assess_gate_order_book_stream_session_freshness(
    session: GateOrderBookStreamSession,
    *,
    as_of: datetime,
    max_staleness: timedelta,
) -> GateOrderBookStreamSessionResult:
    """Make staleness explicit before a consumer trusts local depth."""

    if not isinstance(session, GateOrderBookStreamSession):
        raise GateOrderBookStreamSessionError("session must be typed")
    checked = _utc(as_of, "as_of")
    window = _freshness_window(max_staleness)
    if checked < session.last_checked_at:
        raise GateOrderBookStreamSessionError("as_of cannot move a session clock backwards")
    result = _session_for_as_of(session, as_of=checked, max_staleness=window)
    if result is not None:
        return result
    return GateOrderBookStreamSessionResult(
        GateOrderBookStreamSessionDisposition.REPLAYED,
        session,
        "healthy_evidence_remains_within_staleness_window",
    )


def apply_gate_order_book_stream_session_frame(
    session: GateOrderBookStreamSession,
    frame: GateOrderBookStreamFrame,
    *,
    as_of: datetime,
    max_staleness: timedelta,
) -> GateOrderBookStreamSessionResult:
    """Apply one parsed public frame or fail closed with an explicit reseed."""

    if not isinstance(session, GateOrderBookStreamSession) or not isinstance(frame, GateOrderBookStreamFrame):
        raise GateOrderBookStreamSessionError("typed session and frame are required")
    checked = _utc(as_of, "as_of")
    window = _freshness_window(max_staleness)
    if checked < session.last_checked_at or checked < frame.delta.observed_at:
        raise GateOrderBookStreamSessionError("as_of cannot precede prior or incoming evidence")
    if not _subscription_matches_state(frame.subscription, session.materialized_state, include_snapshot_id=True):
        raise GateOrderBookStreamSessionError("frame subscription does not match session scope")
    prior = _session_for_as_of(session, as_of=checked, max_staleness=window)
    if prior is not None:
        return prior
    materialized = apply_gate_order_book_materialized_frame(session.materialized_state, frame)
    if materialized.disposition is GateOrderBookMaterializationDisposition.APPLIED:
        updated = GateOrderBookStreamSession(
            materialized_state=materialized.state,
            health=GateOrderBookEvidenceHealth.HEALTHY,
            last_checked_at=checked,
        )
        return GateOrderBookStreamSessionResult(
            GateOrderBookStreamSessionDisposition.APPLIED,
            updated,
            materialized.reason,
            materialized,
        )
    if materialized.disposition is GateOrderBookMaterializationDisposition.REPLAYED:
        return GateOrderBookStreamSessionResult(
            GateOrderBookStreamSessionDisposition.REPLAYED,
            session,
            materialized.reason,
            materialized,
        )
    plan = plan_gate_order_book_reseed(session.materialized_state.stream_state, frame.delta)
    recovery = GateOrderBookStreamSession(
        materialized_state=session.materialized_state,
        health=GateOrderBookEvidenceHealth.RESEED_REQUIRED,
        last_checked_at=checked,
        reseed_plan=plan,
    )
    disposition = (
        GateOrderBookStreamSessionDisposition.CONFLICT
        if materialized.disposition is GateOrderBookMaterializationDisposition.CONFLICT
        else GateOrderBookStreamSessionDisposition.RESEED_REQUIRED
    )
    return GateOrderBookStreamSessionResult(
        disposition,
        recovery,
        materialized.reason,
        materialized,
    )


def reseed_gate_order_book_stream_session(
    session: GateOrderBookStreamSession,
    snapshot: GateOrderBookSnapshot,
    subscription: GateOrderBookStreamSubscription,
    *,
    as_of: datetime,
    max_staleness: timedelta,
) -> GateOrderBookStreamSessionResult:
    """Restore a non-healthy session only from a fresher same-market REST fact."""

    if not isinstance(session, GateOrderBookStreamSession) or not isinstance(snapshot, GateOrderBookSnapshot) or not isinstance(subscription, GateOrderBookStreamSubscription):
        raise GateOrderBookStreamSessionError("typed session, snapshot, and subscription are required")
    checked = _utc(as_of, "as_of")
    window = _freshness_window(max_staleness)
    if checked < session.last_checked_at or checked < snapshot.observed_at:
        raise GateOrderBookStreamSessionError("as_of cannot precede session or reseed evidence")
    if session.health is GateOrderBookEvidenceHealth.HEALTHY:
        raise GateOrderBookStreamSessionError("healthy session does not permit an unsolicited reseed")
    if not _subscription_matches_state(subscription, session.materialized_state, include_snapshot_id=False):
        raise GateOrderBookStreamSessionError("reseed subscription changes immutable market contract facts")
    previous = session.materialized_state.snapshot
    if snapshot.observed_at <= previous.observed_at:
        raise GateOrderBookStreamSessionError("reseed snapshot must be fresher than retained evidence")
    if snapshot.sequence < session.materialized_state.stream_state.next_update_id - 1:
        raise GateOrderBookStreamSessionError("reseed snapshot cannot roll sequence evidence backwards")
    reanchored = gate_order_book_stream_session_from_snapshot(
        snapshot,
        subscription,
        as_of=checked,
        max_staleness=window,
    )
    if reanchored.health is not GateOrderBookEvidenceHealth.HEALTHY:
        raise GateOrderBookStreamSessionError("reseed snapshot is already stale")
    return GateOrderBookStreamSessionResult(
        GateOrderBookStreamSessionDisposition.RESEEDED,
        reanchored,
        "fresh_rest_snapshot_reanchors_session",
    )


__all__ = [
    "GATE_ORDER_BOOK_STREAM_SESSION_CONTRACT_VERSION",
    "GateOrderBookEvidenceHealth",
    "GateOrderBookStreamSession",
    "GateOrderBookStreamSessionDisposition",
    "GateOrderBookStreamSessionError",
    "GateOrderBookStreamSessionResult",
    "apply_gate_order_book_stream_session_frame",
    "assess_gate_order_book_stream_session_freshness",
    "gate_order_book_stream_session_from_snapshot",
    "reseed_gate_order_book_stream_session",
]
