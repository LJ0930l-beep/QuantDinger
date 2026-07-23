"""Pure, fail-closed venue identity and read-model contracts.

This module is deliberately independent from Flask, database models, exchange
clients, and recovery policy.  It defines facts that a future submission
attempt, reconciliation process, and immutable fill ledger will persist.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from .decimal_values import FeeAmount, Price, Quantity


class VenueContractError(ValueError):
    """Raised when a venue fact cannot be represented safely."""


class UnsupportedVenueCapability(VenueContractError):
    """Raised when an exact exchange + market capability is not approved."""


BINANCE_USDM_CLIENT_ID_PATTERN = r"^[\.A-Z\:/a-z0-9_-]{1,36}$"
CLIENT_ID_ALGORITHM_VERSION = "v1"
PREFIX_NORMALIZATION_VERSION = "ascii-nonsensitive-v1"


def _canonical_string(value: object, field: str, *, case: str | None = None) -> str:
    if not isinstance(value, str):
        raise VenueContractError(f"{field} must be a string")
    canonical = value.strip()
    if not canonical:
        raise VenueContractError(f"{field} is required")
    if case == "lower":
        canonical = canonical.lower()
    elif case == "upper":
        canonical = canonical.upper()
    return canonical


def _canonical_uuid(value: object, field: str) -> str:
    raw = _canonical_string(value, field)
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise VenueContractError(f"{field} must be a UUID") from exc


def _canonical_nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VenueContractError(f"{field} must be an int")
    if value < 0:
        raise VenueContractError(f"{field} cannot be negative")
    return value


class ClientOrderIdCapability(Protocol):
    """The PR-00 exact venue profile fields needed by this pure generator."""

    exchange_id: str
    market_type: str
    can_generate_safe_client_order_id: bool
    client_id_max_length: int | None
    client_id_pattern: str | None


@dataclass(frozen=True, slots=True)
class SubmissionAttemptIdentity:
    """Stable identity inputs for one future persisted submission attempt.

    ``broker_prefix`` is an explicit immutable submission-attempt snapshot. A
    later SubmissionAttempt record must persist it (or its complete Client ID),
    and recovery must never reread the then-current runtime configuration. It
    must be a non-sensitive attribution prefix: never a credential ID, account
    ID, API key, secret, or any other account-specific value.
    """

    economic_order_id: str
    child_seq: int
    attempt_no: int
    exchange_id: str
    market_type: str
    broker_prefix: str
    algorithm_version: str = CLIENT_ID_ALGORITHM_VERSION
    prefix_normalization_version: str = PREFIX_NORMALIZATION_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "economic_order_id", _canonical_uuid(self.economic_order_id, "economic_order_id"))
        object.__setattr__(self, "child_seq", _canonical_nonnegative_int(self.child_seq, "child_seq"))
        object.__setattr__(self, "attempt_no", _canonical_nonnegative_int(self.attempt_no, "attempt_no"))
        object.__setattr__(self, "exchange_id", _canonical_string(self.exchange_id, "exchange_id", case="lower"))
        object.__setattr__(self, "market_type", _canonical_string(self.market_type, "market_type", case="lower"))
        object.__setattr__(self, "broker_prefix", _canonical_broker_prefix(self.broker_prefix))
        if self.algorithm_version != CLIENT_ID_ALGORITHM_VERSION:
            raise VenueContractError("unsupported client order ID algorithm version")
        if self.prefix_normalization_version != PREFIX_NORMALIZATION_VERSION:
            raise VenueContractError("unsupported broker_prefix normalization version")


def _canonical_broker_prefix(value: str) -> str:
    """Normalize exactly once before hashing; reject rather than repair input."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise VenueContractError("invalid broker_prefix")
    # ASCII is intentional: it rejects Unicode confusables and invisible spaces.
    if not value.isascii() or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", value):
        raise VenueContractError("invalid broker_prefix")
    # This is defense in depth. The caller still owns the non-sensitive input
    # contract because no local heuristic can prove a string is not an account ID.
    if any(token in value.lower() for token in ("secret", "apikey", "api_key", "credential", "account")):
        raise VenueContractError("invalid broker_prefix")
    return value


def _canonical_identity_material(identity: SubmissionAttemptIdentity, prefix: str) -> str:
    return json.dumps(
        {
            "algorithm_version": identity.algorithm_version,
            "attempt_no": identity.attempt_no,
            "broker_prefix": prefix,
            "child_seq": identity.child_seq,
            "economic_order_id": identity.economic_order_id,
            "exchange_id": identity.exchange_id,
            "market_type": identity.market_type,
            "prefix_normalization_version": identity.prefix_normalization_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def generate_venue_client_order_id(
    identity: SubmissionAttemptIdentity,
    *,
    capability: ClientOrderIdCapability,
) -> str:
    """Generate a versioned deterministic ID without truncation or fallback.

    The caller must resolve ``capability`` from the PR-00 exact
    exchange + market profile. An unsupported or unknown profile cannot reuse
    another market's rule. The algorithm and prefix-normalization versions are
    hashed, and the algorithm version is rendered, so neither can silently
    collide with a historical identity.
    """

    if (
        not capability.can_generate_safe_client_order_id
        or capability.client_id_max_length is None
        or capability.client_id_pattern is None
    ):
        raise UnsupportedVenueCapability("deterministic client order ID unsupported for this venue profile")
    if (
        _canonical_string(capability.exchange_id, "capability.exchange_id", case="lower") != identity.exchange_id
        or _canonical_string(capability.market_type, "capability.market_type", case="lower") != identity.market_type
    ):
        raise VenueContractError("venue capability scope mismatch")
    digest = hashlib.sha256(_canonical_identity_material(identity, identity.broker_prefix).encode("utf-8")).hexdigest()[:20]
    value = f"{identity.broker_prefix}-{identity.algorithm_version}-{digest}"
    if len(value) > capability.client_id_max_length or not re.fullmatch(capability.client_id_pattern, value):
        raise VenueContractError("venue client order ID violates the explicit venue rule")
    return value


def validate_binance_usdm_client_order_id(value: str) -> str:
    """Validate an already formed USD-M Futures ID without truncating it."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise VenueContractError("venue client order ID violates the explicit venue rule")
    if len(value) > 36 or not re.fullmatch(BINANCE_USDM_CLIENT_ID_PATTERN, value):
        raise VenueContractError("venue client order ID violates the explicit venue rule")
    return value


class OrderQueryReference(str, Enum):
    EXCHANGE_ORDER_ID = "EXCHANGE_ORDER_ID"
    CLIENT_ORDER_ID = "CLIENT_ORDER_ID"


class OrderQueryStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    AUTH_OR_PERMISSION_FAILURE = "AUTH_OR_PERMISSION_FAILURE"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class VenueQueryFailureKind(str, Enum):
    """Adapter error categories that must not be collapsed into NOT_FOUND."""

    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    AUTH_OR_PERMISSION = "AUTH_OR_PERMISSION"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_RESPONSE = "INVALID_RESPONSE"


_QUERY_FAILURE_STATUS = {
    VenueQueryFailureKind.NOT_FOUND: OrderQueryStatus.NOT_FOUND,
    VenueQueryFailureKind.CONFLICT: OrderQueryStatus.CONFLICT,
    VenueQueryFailureKind.TIMEOUT: OrderQueryStatus.TEMPORARY_FAILURE,
    VenueQueryFailureKind.RATE_LIMITED: OrderQueryStatus.TEMPORARY_FAILURE,
    VenueQueryFailureKind.SERVER_ERROR: OrderQueryStatus.TEMPORARY_FAILURE,
    VenueQueryFailureKind.AUTH_OR_PERMISSION: OrderQueryStatus.AUTH_OR_PERMISSION_FAILURE,
    VenueQueryFailureKind.UNSUPPORTED: OrderQueryStatus.UNSUPPORTED,
    VenueQueryFailureKind.INVALID_RESPONSE: OrderQueryStatus.INVALID_RESPONSE,
}

_NORMALIZED_ORDER_STATES = frozenset(
    {
        "SUBMITTED",
        "PARTIALLY_FILLED",
        "FILLED",
        "SUBMISSION_UNKNOWN",
        "CANCEL_REQUESTED",
        "CANCELLING",
        "CANCELLED",
        "REJECTED",
        "RECONCILIATION_REQUIRED",
    }
)


@dataclass(frozen=True, slots=True)
class OrderQueryRequest:
    """Exact read-only lookup scope; this does not decide recovery actions."""

    reference: OrderQueryReference
    venue: str
    market_type: str
    account_scope: str
    instrument: str
    exchange_order_id: str = ""
    client_order_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.reference, OrderQueryReference):
            raise VenueContractError("reference must be an OrderQueryReference")
        object.__setattr__(self, "venue", _canonical_string(self.venue, "venue", case="lower"))
        object.__setattr__(self, "market_type", _canonical_string(self.market_type, "market_type", case="lower"))
        object.__setattr__(self, "account_scope", _canonical_string(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument", _canonical_string(self.instrument, "instrument", case="upper"))
        if self.exchange_order_id:
            object.__setattr__(self, "exchange_order_id", _canonical_string(self.exchange_order_id, "exchange_order_id"))
        if self.client_order_id:
            object.__setattr__(self, "client_order_id", _canonical_string(self.client_order_id, "client_order_id"))
        if self.reference is OrderQueryReference.EXCHANGE_ORDER_ID:
            if not self.exchange_order_id or self.client_order_id:
                raise VenueContractError("exchange-order lookup requires only exchange_order_id")
        elif self.reference is OrderQueryReference.CLIENT_ORDER_ID:
            if not self.client_order_id or self.exchange_order_id:
                raise VenueContractError("client-order lookup requires only client_order_id")
        else:
            raise VenueContractError("unknown query reference")


@dataclass(frozen=True, slots=True)
class NormalizedOrderQuery:
    """Typed result for a single read-only venue order lookup.

    ``raw_state`` is an auditable venue status token, never a secret or raw
    request payload. ``FOUND`` requires a complete normalized fact; all other
    statuses are explicit and cannot be mistaken for absence.
    """

    status: OrderQueryStatus
    reference: OrderQueryReference
    venue: str
    market_type: str
    account_scope: str
    instrument: str
    exchange_order_id: str = ""
    client_order_id: str = ""
    normalized_state: str = ""
    raw_state: str = ""

    def __post_init__(self) -> None:
        if not all((self.venue, self.market_type, self.account_scope, self.instrument)):
            raise VenueContractError("query scope is required")
        if self.status is OrderQueryStatus.FOUND:
            if not self.exchange_order_id or not self.normalized_state or not self.raw_state:
                raise VenueContractError("FOUND requires order identity and normalized state")
            if self.normalized_state not in _NORMALIZED_ORDER_STATES:
                raise VenueContractError("unknown normalized order state")
        elif self.normalized_state or self.raw_state:
            raise VenueContractError("non-found query result cannot claim an order state")


def query_failure_result(
    request: OrderQueryRequest,
    failure: VenueQueryFailureKind,
) -> NormalizedOrderQuery:
    """Map an adapter's typed failure category without exposing credentials."""

    return NormalizedOrderQuery(
        status=_QUERY_FAILURE_STATUS[failure],
        reference=request.reference,
        venue=request.venue,
        market_type=request.market_type,
        account_scope=request.account_scope,
        instrument=request.instrument,
        exchange_order_id=request.exchange_order_id,
        client_order_id=request.client_order_id,
    )


def found_order_query_result(
    request: OrderQueryRequest,
    *,
    response_venue: str,
    response_market_type: str,
    response_account_scope: str,
    response_instrument: str,
    exchange_order_id: str,
    client_order_id: str = "",
    normalized_state: str,
    raw_state: str,
) -> NormalizedOrderQuery:
    """Validate a formatted adapter response against the requested scope."""

    if (_canonical_string(response_venue, "response_venue", case="lower") != request.venue
        or _canonical_string(response_market_type, "response_market_type", case="lower") != request.market_type
        or _canonical_string(response_account_scope, "response_account_scope") != request.account_scope
        or _canonical_string(response_instrument, "response_instrument", case="upper") != request.instrument):
        raise VenueContractError("query response scope mismatch")
    exchange_order_id = _canonical_string(exchange_order_id, "exchange_order_id")
    if client_order_id:
        client_order_id = _canonical_string(client_order_id, "client_order_id")
    if request.reference is OrderQueryReference.EXCHANGE_ORDER_ID and exchange_order_id != request.exchange_order_id:
        raise VenueContractError("query response exchange_order_id mismatch")
    if request.reference is OrderQueryReference.CLIENT_ORDER_ID and client_order_id != request.client_order_id:
        raise VenueContractError("query response client_order_id mismatch")
    return NormalizedOrderQuery(
        status=OrderQueryStatus.FOUND,
        reference=request.reference,
        venue=request.venue,
        market_type=request.market_type,
        account_scope=request.account_scope,
        instrument=request.instrument,
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
        normalized_state=normalized_state,
        raw_state=raw_state,
    )


@dataclass(frozen=True, slots=True)
class VenueOrderScope:
    """Order-level scope used to reject cross-account/instrument fill facts."""

    venue: str
    market_type: str
    account_scope: str
    instrument: str
    exchange_order_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "venue", _canonical_string(self.venue, "venue", case="lower"))
        object.__setattr__(self, "market_type", _canonical_string(self.market_type, "market_type", case="lower"))
        object.__setattr__(self, "account_scope", _canonical_string(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument", _canonical_string(self.instrument, "instrument", case="upper"))
        object.__setattr__(self, "exchange_order_id", _canonical_string(self.exchange_order_id, "exchange_order_id"))


@dataclass(frozen=True, slots=True)
class FillFee:
    """One fee fact; assets are deliberately not converted or coalesced."""

    asset: str
    amount: FeeAmount

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", _canonical_string(self.asset, "fee asset", case="upper"))
        if not isinstance(self.amount, FeeAmount):
            raise VenueContractError("fee requires a non-empty asset and FeeAmount")


@dataclass(frozen=True, slots=True)
class VenueFillIdentity:
    """Stable, replayable fill fact keyed only by a venue-provided fill ID.

    It does not synthesize an identity from timestamp, price, or quantity. The
    canonical key includes the order scope to preserve evidence provenance even
    when a venue's fill ID uniqueness is narrower than expected.
    """

    order_scope: VenueOrderScope
    venue_fill_id: str
    quantity: Quantity
    price: Price
    fees: tuple[FillFee, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "venue_fill_id", _canonical_string(self.venue_fill_id, "stable venue_fill_id"))
        if not isinstance(self.quantity, Quantity) or not isinstance(self.price, Price):
            raise VenueContractError("fill price and quantity require PR-01 Decimal contracts")
        if any(not isinstance(fee, FillFee) for fee in self.fees):
            raise VenueContractError("fees require FillFee facts")

    @classmethod
    def from_venue_fact(
        cls,
        expected_order_scope: VenueOrderScope,
        *,
        venue: str,
        market_type: str,
        account_scope: str,
        instrument: str,
        exchange_order_id: str,
        venue_fill_id: str,
        quantity: Quantity,
        price: Price,
        fees: tuple[FillFee, ...] = (),
    ) -> "VenueFillIdentity":
        """Build a fill only after every adapter-extracted scope field agrees.

        The future adapter formatting layer must call this boundary rather than
        trusting an isolated trade payload.  A mismatch is evidence requiring
        reconciliation, not a fact that can be silently attached to an order.
        """

        observed_scope = VenueOrderScope(
            venue, market_type, account_scope, instrument, exchange_order_id
        )
        if observed_scope != expected_order_scope:
            raise VenueContractError("fill scope mismatch")
        return cls(expected_order_scope, venue_fill_id, quantity, price, fees)

    @property
    def canonical_key(self) -> str:
        scope = self.order_scope
        material = json.dumps(
            {
                "account_scope": scope.account_scope,
                "exchange_order_id": scope.exchange_order_id,
                "instrument": scope.instrument,
                "market_type": scope.market_type,
                "venue": scope.venue,
                "venue_fill_id": self.venue_fill_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
