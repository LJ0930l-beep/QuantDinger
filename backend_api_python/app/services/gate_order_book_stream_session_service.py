"""Transport-free public Gate order-book session assembly.

This is the narrow boundary between a caller-owned public stream transport and
the immutable order-book session contracts.  It accepts only already-received
JSON-compatible frames plus caller-owned timestamps.  It does not open a
socket, perform HTTP, load credentials, access an account, write a database,
or submit/cancel an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Mapping

from app.domain.gate_market_read_contracts import GateOrderBookSnapshot
from app.domain.gate_order_book_stream_contracts import (
    GateOrderBookStreamError,
    GateOrderBookStreamFrame,
    GateOrderBookStreamSubscription,
)
from app.domain.gate_order_book_stream_payload_contracts import (
    GateOrderBookStreamPayloadError,
    normalize_gate_order_book_update_frame,
)
from app.domain.gate_order_book_stream_session_contracts import (
    GateOrderBookStreamSession,
    GateOrderBookStreamSessionError,
    GateOrderBookStreamSessionResult,
    apply_gate_order_book_stream_session_frame,
    assess_gate_order_book_stream_session_freshness,
    gate_order_book_stream_session_from_snapshot,
    reseed_gate_order_book_stream_session,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType


GATE_ORDER_BOOK_STREAM_SESSION_SERVICE_VERSION = "gate-order-book-stream-session-service-v1"


class GateOrderBookStreamSessionServiceError(GateOrderBookStreamSessionError):
    """The public frame boundary cannot form safe typed session evidence."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(character.isspace() for character in value):
        raise GateOrderBookStreamSessionServiceError(f"{field_name} must be canonical ASCII text")
    return value


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class GateOrderBookStreamSessionReceipt:
    """One typed parsed public frame and its immutable session disposition."""

    frame: GateOrderBookStreamFrame
    result: GateOrderBookStreamSessionResult
    receipt_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.frame, GateOrderBookStreamFrame) or not isinstance(self.result, GateOrderBookStreamSessionResult):
            raise GateOrderBookStreamSessionServiceError("receipt requires typed frame and session result")
        subscription = self.result.session.materialized_state.subscription
        if self.frame.subscription.subscription_fingerprint != subscription.subscription_fingerprint:
            raise GateOrderBookStreamSessionServiceError("receipt frame does not match result session subscription")
        materialization = self.result.materialization_result
        if materialization is not None and materialization.stream_result.delta != self.frame.delta:
            raise GateOrderBookStreamSessionServiceError("receipt frame does not match its materialization result")
        object.__setattr__(self, "receipt_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_STREAM_SESSION_SERVICE_VERSION,
            "frame": self.frame.frame_fingerprint,
            "result_disposition": self.result.disposition.value,
            "session": self.result.session.session_fingerprint,
            "reason": self.result.reason,
        }))


@dataclass(frozen=True, slots=True)
class GateOrderBookStreamSessionService:
    """Build and advance one strict public depth evidence policy.

    The policy fixes every immutable subscription fact except ``snapshot_id``;
    that value comes from the caller-provided REST snapshot and may change only
    through an explicit fresh reseed.  Raw payloads are normalized before the
    underlying sequencer can inspect them.
    """

    market_type: AssetMarketType
    instrument_id: str
    rule_version: str
    depth_limit: int
    update_interval: str
    source_event_prefix: str
    max_staleness: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, AssetMarketType) or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
            raise GateOrderBookStreamSessionServiceError("market_type must be typed spot or perpetual")
        for field_name in ("instrument_id", "rule_version", "source_event_prefix"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        # Construct one inert subscription to use the exact centralized Gate
        # depth/cadence validation, rather than carrying a parallel table.
        try:
            GateOrderBookStreamSubscription(
                market_type=self.market_type,
                instrument_id=self.instrument_id,
                snapshot_id="validation-snapshot",
                rule_version=self.rule_version,
                depth_limit=self.depth_limit,
                update_interval=self.update_interval,
            )
        except GateOrderBookStreamError as exc:
            raise GateOrderBookStreamSessionServiceError("public stream policy is not supported") from exc
        if not isinstance(self.max_staleness, timedelta) or self.max_staleness <= timedelta(0):
            raise GateOrderBookStreamSessionServiceError("max_staleness must be a positive timedelta")

    def subscription_for_snapshot(self, snapshot: GateOrderBookSnapshot) -> GateOrderBookStreamSubscription:
        """Bind this fixed public depth policy to one typed REST snapshot."""

        if not isinstance(snapshot, GateOrderBookSnapshot):
            raise GateOrderBookStreamSessionServiceError("snapshot must be typed")
        if snapshot.depth_limit is None:
            raise GateOrderBookStreamSessionServiceError("snapshot must retain its REST depth_limit")
        if (
            snapshot.market_type is not self.market_type
            or snapshot.instrument_id != self.instrument_id
            or snapshot.rule_version != self.rule_version
            or snapshot.depth_limit != self.depth_limit
        ):
            raise GateOrderBookStreamSessionServiceError("snapshot does not match immutable public stream policy")
        return GateOrderBookStreamSubscription(
            market_type=self.market_type,
            instrument_id=self.instrument_id,
            snapshot_id=snapshot.snapshot_id,
            rule_version=self.rule_version,
            depth_limit=self.depth_limit,
            update_interval=self.update_interval,
        )

    def _require_session_policy(self, session: GateOrderBookStreamSession) -> GateOrderBookStreamSubscription:
        """Refuse sessions that were created for a different public policy."""

        if not isinstance(session, GateOrderBookStreamSession):
            raise GateOrderBookStreamSessionServiceError("session must be typed")
        subscription = session.materialized_state.subscription
        if (
            subscription.market_type is not self.market_type
            or subscription.instrument_id != self.instrument_id
            or subscription.rule_version != self.rule_version
            or subscription.depth_limit != self.depth_limit
            or subscription.update_interval != self.update_interval
        ):
            raise GateOrderBookStreamSessionServiceError("session does not match immutable public stream policy")
        return subscription

    def start(self, snapshot: GateOrderBookSnapshot, *, as_of: datetime) -> GateOrderBookStreamSession:
        """Start a session from an injected public REST snapshot."""

        try:
            subscription = self.subscription_for_snapshot(snapshot)
            return gate_order_book_stream_session_from_snapshot(
                snapshot,
                subscription,
                as_of=as_of,
                max_staleness=self.max_staleness,
            )
        except GateOrderBookStreamSessionServiceError:
            raise
        except GateOrderBookStreamSessionError as exc:
            raise GateOrderBookStreamSessionServiceError("public REST snapshot cannot start a session") from exc
        except Exception as exc:
            raise GateOrderBookStreamSessionServiceError("public REST snapshot cannot start a session") from exc

    def receive_update(
        self,
        session: GateOrderBookStreamSession,
        payload: Mapping[str, Any],
        *,
        observed_at: datetime,
        as_of: datetime,
    ) -> GateOrderBookStreamSessionReceipt:
        """Normalize one caller-received public frame and apply it safely."""

        if not isinstance(payload, Mapping):
            raise GateOrderBookStreamSessionServiceError("payload must be a JSON object")
        subscription = self._require_session_policy(session)
        try:
            frame = normalize_gate_order_book_update_frame(
                payload,
                subscription=subscription,
                observed_at=observed_at,
                source_event_prefix=self.source_event_prefix,
            )
            result = apply_gate_order_book_stream_session_frame(
                session,
                frame,
                as_of=as_of,
                max_staleness=self.max_staleness,
            )
            return GateOrderBookStreamSessionReceipt(frame, result)
        except GateOrderBookStreamSessionServiceError:
            raise
        except (GateOrderBookStreamPayloadError, GateOrderBookStreamSessionError) as exc:
            raise GateOrderBookStreamSessionServiceError("public order book update cannot become safe session evidence") from exc
        except Exception as exc:
            raise GateOrderBookStreamSessionServiceError("public order book update could not be processed") from exc

    def assess(self, session: GateOrderBookStreamSession, *, as_of: datetime) -> GateOrderBookStreamSessionResult:
        """Expose health evaluation without creating a synthetic stream frame."""

        self._require_session_policy(session)
        try:
            return assess_gate_order_book_stream_session_freshness(
                session,
                as_of=as_of,
                max_staleness=self.max_staleness,
            )
        except GateOrderBookStreamSessionError as exc:
            raise GateOrderBookStreamSessionServiceError("public order book session cannot be assessed") from exc

    def reseed(
        self,
        session: GateOrderBookStreamSession,
        snapshot: GateOrderBookSnapshot,
        *,
        as_of: datetime,
    ) -> GateOrderBookStreamSessionResult:
        """Restore health only from a new injected REST snapshot."""

        self._require_session_policy(session)
        try:
            subscription = self.subscription_for_snapshot(snapshot)
            return reseed_gate_order_book_stream_session(
                session,
                snapshot,
                subscription,
                as_of=as_of,
                max_staleness=self.max_staleness,
            )
        except GateOrderBookStreamSessionError as exc:
            raise GateOrderBookStreamSessionServiceError("public order book session cannot be reseeded") from exc


__all__ = [
    "GATE_ORDER_BOOK_STREAM_SESSION_SERVICE_VERSION",
    "GateOrderBookStreamSessionReceipt",
    "GateOrderBookStreamSessionService",
    "GateOrderBookStreamSessionServiceError",
]
