"""Pure, fail-closed Gate market-evidence coherence contracts.

Candles, a static market bundle, and a materialized order-book session are
different evidence streams.  This module proves that their immutable scope,
quality, and timing are coherent at one caller-supplied UTC instant before a
research, PAPER, or SHADOW caller may consume them together.  It deliberately
does not fetch data, create a connection, or alter a backtest / execution
result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .backtest_dataset_contracts import BacktestDatasetSnapshot
from .gate_backtest_dataset_contracts import GateBacktestDatasetError, build_gate_backtest_dataset
from .gate_order_book_stream_session_contracts import (
    GateOrderBookEvidenceHealth,
    GateOrderBookStreamSession,
    GateOrderBookStreamSessionDisposition,
    GateOrderBookStreamSessionError,
    assess_gate_order_book_stream_session_freshness,
)
from .multi_asset_capability_contracts import AssetMarketType

if TYPE_CHECKING:
    from app.services.gate_market_research_service import GateMarketEvidenceBundle


GATE_MARKET_EVIDENCE_COHERENCE_CONTRACT_VERSION = "gate-market-evidence-coherence-v1"


class GateMarketEvidenceCoherenceError(ValueError):
    """Gate market facts cannot prove one coherent point-in-time view."""


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise GateMarketEvidenceCoherenceError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def _text(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or not value.isascii()
        or any(character.isspace() for character in value)
    ):
        raise GateMarketEvidenceCoherenceError(f"{field_name} must be canonical ASCII text")
    return value


def _fingerprint_text(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise GateMarketEvidenceCoherenceError(f"{field_name} must be lowercase SHA-256 text")
    return text


def _window(value: object, field_name: str) -> timedelta:
    if not isinstance(value, timedelta) or value <= timedelta(0):
        raise GateMarketEvidenceCoherenceError(f"{field_name} must be a positive timedelta")
    return value


def _microseconds(value: timedelta) -> int:
    return value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _bundle_is_typed(value: object) -> bool:
    """Validate the service-owned immutable bundle without importing services.

    The domain package intentionally does not import its service layer.  The
    bundle itself is constructed and validated by ``GateMarketResearchService``;
    this boundary checks its exact public shape again before consuming it.
    """

    required = (
        "market_type",
        "instrument_id",
        "interval",
        "candles",
        "order_book",
        "observed_at",
        "snapshot_id",
        "rule_version",
        "bundle_fingerprint",
    )
    return (
        value.__class__.__name__ == "GateMarketEvidenceBundle"
        and value.__class__.__module__.endswith("gate_market_research_service")
        and all(hasattr(value, name) for name in required)
    )


def _require_bundle(value: object) -> "GateMarketEvidenceBundle":
    if not _bundle_is_typed(value):
        raise GateMarketEvidenceCoherenceError("bundle must be a typed GateMarketEvidenceBundle")
    market_type = getattr(value, "market_type")
    if not isinstance(market_type, AssetMarketType) or market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
        raise GateMarketEvidenceCoherenceError("bundle market_type must be typed spot or perpetual")
    _text(getattr(value, "instrument_id"), "bundle.instrument_id")
    _text(getattr(value, "interval"), "bundle.interval")
    _text(getattr(value, "snapshot_id"), "bundle.snapshot_id")
    _text(getattr(value, "rule_version"), "bundle.rule_version")
    _fingerprint_text(getattr(value, "bundle_fingerprint"), "bundle.bundle_fingerprint")
    if not isinstance(getattr(value, "candles"), tuple) or not getattr(value, "candles"):
        raise GateMarketEvidenceCoherenceError("bundle must retain non-empty typed candle evidence")
    return value  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class GateMarketEvidenceCoherencePolicy:
    """Versioned freshness budget for one multi-feed research decision."""

    policy_version: str
    max_candle_observation_age: timedelta
    max_candle_close_age: timedelta
    max_order_book_age: timedelta
    max_cross_feed_skew: timedelta
    policy_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        version = _text(self.policy_version, "policy_version")
        windows = {
            name: _window(getattr(self, name), name)
            for name in (
                "max_candle_observation_age",
                "max_candle_close_age",
                "max_order_book_age",
                "max_cross_feed_skew",
            )
        }
        object.__setattr__(self, "policy_version", version)
        for name, value in windows.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "policy_fingerprint", _fingerprint({
            "version": GATE_MARKET_EVIDENCE_COHERENCE_CONTRACT_VERSION,
            "policy_version": version,
            **{f"{name}_microseconds": _microseconds(value) for name, value in windows.items()},
        }))


@dataclass(frozen=True, slots=True)
class GateMarketEvidenceCoherenceReceipt:
    """An immutable READY receipt for one coherent scoped evidence view."""

    bundle: "GateMarketEvidenceBundle"
    session: GateOrderBookStreamSession
    dataset: BacktestDatasetSnapshot
    policy: GateMarketEvidenceCoherencePolicy
    as_of: datetime
    latest_candle_close_at: datetime
    latest_candle_observed_at: datetime
    order_book_observed_at: datetime
    receipt_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        bundle = _require_bundle(self.bundle)
        if not isinstance(self.session, GateOrderBookStreamSession):
            raise GateMarketEvidenceCoherenceError("session must be typed")
        if not isinstance(self.dataset, BacktestDatasetSnapshot):
            raise GateMarketEvidenceCoherenceError("dataset must be typed")
        if not isinstance(self.policy, GateMarketEvidenceCoherencePolicy):
            raise GateMarketEvidenceCoherenceError("policy must be typed")
        as_of = _utc(self.as_of, "as_of")
        expected = _validate_coherence(bundle, self.session, self.policy, as_of)
        if self.dataset.dataset_fingerprint != expected["dataset"].dataset_fingerprint:
            raise GateMarketEvidenceCoherenceError("dataset does not match coherent bundle evidence")
        for field_name in (
            "latest_candle_close_at",
            "latest_candle_observed_at",
            "order_book_observed_at",
        ):
            actual = _utc(getattr(self, field_name), field_name)
            if actual != expected[field_name]:
                raise GateMarketEvidenceCoherenceError(f"{field_name} does not match coherent evidence")
            object.__setattr__(self, field_name, actual)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "receipt_fingerprint", _fingerprint({
            "version": GATE_MARKET_EVIDENCE_COHERENCE_CONTRACT_VERSION,
            "bundle": bundle.bundle_fingerprint,
            "session": self.session.session_fingerprint,
            "dataset": self.dataset.dataset_fingerprint,
            "policy": self.policy.policy_fingerprint,
            "as_of": as_of.isoformat(),
            "latest_candle_close_at": self.latest_candle_close_at.isoformat(),
            "latest_candle_observed_at": self.latest_candle_observed_at.isoformat(),
            "order_book_observed_at": self.order_book_observed_at.isoformat(),
        }))


def _age(as_of: datetime, evidence_at: datetime, maximum: timedelta, field_name: str) -> None:
    if evidence_at > as_of:
        raise GateMarketEvidenceCoherenceError(f"{field_name} cannot follow as_of")
    if as_of - evidence_at > maximum:
        raise GateMarketEvidenceCoherenceError(f"{field_name} exceeds immutable freshness policy")


def _validate_coherence(
    bundle: "GateMarketEvidenceBundle",
    session: GateOrderBookStreamSession,
    policy: GateMarketEvidenceCoherencePolicy,
    as_of: datetime,
) -> dict[str, Any]:
    """Validate complete market scope, quality and timing without I/O."""

    subscription = session.materialized_state.subscription
    if session.health is not GateOrderBookEvidenceHealth.HEALTHY:
        raise GateMarketEvidenceCoherenceError("order book session is not healthy")
    try:
        freshness = assess_gate_order_book_stream_session_freshness(
            session,
            as_of=as_of,
            max_staleness=policy.max_order_book_age,
        )
        order_book = session.healthy_snapshot()
    except GateOrderBookStreamSessionError as exc:
        raise GateMarketEvidenceCoherenceError("order book session cannot provide current evidence") from exc
    if freshness.disposition is not GateOrderBookStreamSessionDisposition.REPLAYED:
        raise GateMarketEvidenceCoherenceError("order book evidence exceeds immutable freshness policy")
    if (
        bundle.market_type is not subscription.market_type
        or bundle.instrument_id != subscription.instrument_id
        or bundle.snapshot_id != subscription.snapshot_id
        or bundle.rule_version != subscription.rule_version
        or order_book.market_type is not bundle.market_type
        or order_book.instrument_id != bundle.instrument_id
        or order_book.snapshot_id != bundle.snapshot_id
        or order_book.rule_version != bundle.rule_version
    ):
        raise GateMarketEvidenceCoherenceError("bundle and order book session scope mismatch")
    if bundle.order_book.market_type is not bundle.market_type or bundle.order_book.instrument_id != bundle.instrument_id:
        raise GateMarketEvidenceCoherenceError("bundle order book scope mismatch")
    if bundle.order_book.snapshot_id != bundle.snapshot_id or bundle.order_book.rule_version != bundle.rule_version:
        raise GateMarketEvidenceCoherenceError("bundle order book identity mismatch")
    try:
        dataset = build_gate_backtest_dataset(
            bundle.candles,
            dataset_snapshot_id=bundle.snapshot_id,
            as_of=as_of,
        )
    except (GateBacktestDatasetError, ValueError) as exc:
        raise GateMarketEvidenceCoherenceError("candle evidence is not complete point-in-time data") from exc
    if (
        dataset.venue != "gate"
        or dataset.market_type != bundle.market_type.value
        or dataset.instrument_id != bundle.instrument_id
        or dataset.rule_version != bundle.rule_version
        or dataset.timeframe != bundle.interval
    ):
        raise GateMarketEvidenceCoherenceError("derived dataset scope mismatch")
    latest_candle = bundle.candles[-1]
    latest_close = _utc(latest_candle.close_time, "latest_candle_close_at")
    latest_observed = max(_utc(item.observed_at, "candle.observed_at") for item in bundle.candles)
    order_observed = _utc(session.latest_observed_at, "order_book_observed_at")
    bundle_observed = _utc(bundle.observed_at, "bundle.observed_at")
    _age(as_of, bundle_observed, policy.max_candle_observation_age, "bundle.observed_at")
    _age(as_of, latest_observed, policy.max_candle_observation_age, "latest_candle_observed_at")
    _age(as_of, latest_close, policy.max_candle_close_age, "latest_candle_close_at")
    _age(as_of, order_observed, policy.max_order_book_age, "order_book_observed_at")
    if abs(latest_observed - order_observed) > policy.max_cross_feed_skew:
        raise GateMarketEvidenceCoherenceError("candle and order book evidence exceed cross-feed skew policy")
    return {
        "dataset": dataset,
        "latest_candle_close_at": latest_close,
        "latest_candle_observed_at": latest_observed,
        "order_book_observed_at": order_observed,
    }


def assess_gate_market_evidence_coherence(
    bundle: "GateMarketEvidenceBundle",
    session: GateOrderBookStreamSession,
    *,
    as_of: datetime,
    policy: GateMarketEvidenceCoherencePolicy,
) -> GateMarketEvidenceCoherenceReceipt:
    """Return a replayable READY receipt or fail closed without defaults."""

    bundle = _require_bundle(bundle)
    if not isinstance(session, GateOrderBookStreamSession):
        raise GateMarketEvidenceCoherenceError("session must be typed")
    if not isinstance(policy, GateMarketEvidenceCoherencePolicy):
        raise GateMarketEvidenceCoherenceError("policy must be typed")
    checked = _utc(as_of, "as_of")
    values = _validate_coherence(bundle, session, policy, checked)
    return GateMarketEvidenceCoherenceReceipt(
        bundle=bundle,
        session=session,
        dataset=values["dataset"],
        policy=policy,
        as_of=checked,
        latest_candle_close_at=values["latest_candle_close_at"],
        latest_candle_observed_at=values["latest_candle_observed_at"],
        order_book_observed_at=values["order_book_observed_at"],
    )


__all__ = [
    "GATE_MARKET_EVIDENCE_COHERENCE_CONTRACT_VERSION",
    "GateMarketEvidenceCoherenceError",
    "GateMarketEvidenceCoherencePolicy",
    "GateMarketEvidenceCoherenceReceipt",
    "assess_gate_market_evidence_coherence",
]
