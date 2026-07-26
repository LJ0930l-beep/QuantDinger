"""Pure, deterministic contracts for an immutable exchange-fill ledger.

This module deliberately has no Flask, database, exchange-client, worker, or
runtime-order imports.  It converts one already-normalized venue fill into an
auditable TRADE bundle and, when present, one lossless FEE bundle.  Persistence
and live execution are intentionally outside this PR-06 domain boundary.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum

from .decimal_values import (
    CALCULATION_PRECISION,
    DecimalInputTypeError,
    DecimalValueError,
    FeeAmount,
    Price,
    Quantity,
    QuoteAmount,
    canonical_decimal_string,
    fit_calculated_decimal,
    validate_numeric_38_18,
)
from .venue_order_contracts import FillFee, VenueFillIdentity


LEDGER_BUNDLE_CONTRACT_VERSION = "fill-ledger-bundle-v1"
LEDGER_SOURCE_FINGERPRINT_VERSION = "fill-ledger-source-v1"


class ImmutableLedgerContractError(ValueError):
    """Raised when an immutable fill fact cannot be represented safely."""


class IncompleteValuationEvidenceError(ImmutableLedgerContractError):
    """Raised rather than guessing a fee or quote valuation."""


class LedgerBalanceError(ImmutableLedgerContractError):
    """Raised when a pure ledger transaction is not balanced in both books."""


class FillLedgerConflictError(ImmutableLedgerContractError):
    """Raised when two purported facts share a key but have different content."""


class FillSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class LedgerBook(str, Enum):
    QUANTITY = "QUANTITY"
    MONETARY = "MONETARY"


class LedgerTransactionType(str, Enum):
    TRADE = "TRADE"
    FEE = "FEE"
    REVERSAL = "REVERSAL"
    CORRECTION = "CORRECTION"


class ValuationEvidenceSource(str, Enum):
    VENUE = "VENUE"
    ORACLE = "ORACLE"
    MANUAL_APPROVED = "MANUAL_APPROVED"
    IDENTITY = "IDENTITY"


class QuoteQuantityOrigin(str, Enum):
    VENUE = "VENUE"
    DERIVED = "DERIVED"


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


def _canonical_hash(value: object, field: str) -> str:
    canonical = _canonical_string(value, field, case="lower")
    if len(canonical) != 64 or any(char not in "0123456789abcdef" for char in canonical):
        raise ImmutableLedgerContractError(f"{field} must be a lowercase sha256 hex digest")
    return canonical


def _signed_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, float):
        raise DecimalInputTypeError(f"{field} rejects binary float input")
    try:
        return validate_numeric_38_18(value)  # type: ignore[arg-type]
    except (DecimalInputTypeError, DecimalValueError) as exc:
        raise ImmutableLedgerContractError(f"{field} must satisfy NUMERIC(38,18)") from exc


def _multiply_decimal(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        return fit_calculated_decimal(left * right)


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class InstrumentAssetScope:
    """Explicit base/quote asset facts; no symbol parser is used or guessed."""

    instrument_id: str
    base_asset: str
    quote_asset: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _canonical_string(self.instrument_id, "instrument_id", case="upper"))
        object.__setattr__(self, "base_asset", _canonical_string(self.base_asset, "base_asset", case="upper"))
        object.__setattr__(self, "quote_asset", _canonical_string(self.quote_asset, "quote_asset", case="upper"))
        if self.base_asset == self.quote_asset:
            raise ImmutableLedgerContractError("base_asset and quote_asset must differ")


@dataclass(frozen=True, slots=True)
class ValuationEvidence:
    """One complete, immutable asset-to-valuation-currency evidence fact."""

    fill_key: str
    asset: str
    valuation_ccy: str
    price: Price
    source: ValuationEvidenceSource
    policy_version: str
    evidence_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "fill_key", _canonical_hash(self.fill_key, "fill_key"))
        object.__setattr__(self, "asset", _canonical_string(self.asset, "asset", case="upper"))
        object.__setattr__(self, "valuation_ccy", _canonical_string(self.valuation_ccy, "valuation_ccy", case="upper"))
        if not isinstance(self.price, Price):
            raise ImmutableLedgerContractError("valuation price requires the PR-01 Price contract")
        if not isinstance(self.source, ValuationEvidenceSource):
            raise ImmutableLedgerContractError("valuation source is required")
        object.__setattr__(self, "policy_version", _canonical_string(self.policy_version, "policy_version"))
        object.__setattr__(self, "evidence_hash", _canonical_hash(self.evidence_hash, "evidence_hash"))
        if self.source is ValuationEvidenceSource.IDENTITY:
            if self.asset != self.valuation_ccy or self.price.value != Decimal("1"):
                raise IncompleteValuationEvidenceError(
                    "IDENTITY evidence requires identical asset and valuation_ccy at price 1"
                )
        elif self.asset == self.valuation_ccy:
            raise IncompleteValuationEvidenceError(
                "same-asset valuation requires explicit IDENTITY evidence"
            )

    def value_for(self, amount: FeeAmount | QuoteAmount) -> QuoteAmount:
        if not isinstance(amount, (FeeAmount, QuoteAmount)):
            raise ImmutableLedgerContractError("valuation amount requires a PR-01 amount contract")
        return QuoteAmount(_multiply_decimal(amount.value, self.price.value))


@dataclass(frozen=True, slots=True)
class QuoteQuantityFact:
    """Quote quantity with its explicit authority and valuation evidence."""

    amount: QuoteAmount
    origin: QuoteQuantityOrigin
    evidence_hash: str
    valuation_evidence: ValuationEvidence
    calculation_policy_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.amount, QuoteAmount):
            raise ImmutableLedgerContractError("quote quantity requires QuoteAmount")
        if not isinstance(self.origin, QuoteQuantityOrigin):
            raise ImmutableLedgerContractError("quote quantity origin is required")
        object.__setattr__(self, "evidence_hash", _canonical_hash(self.evidence_hash, "quote quantity evidence_hash"))
        if not isinstance(self.valuation_evidence, ValuationEvidence):
            raise IncompleteValuationEvidenceError("quote quantity requires valuation evidence")
        if self.origin is QuoteQuantityOrigin.VENUE:
            if self.calculation_policy_version is not None:
                raise ImmutableLedgerContractError("VENUE quote quantity cannot carry a derivation policy")
        else:
            object.__setattr__(
                self,
                "calculation_policy_version",
                _canonical_string(self.calculation_policy_version, "quote quantity calculation_policy_version"),
            )


@dataclass(frozen=True, slots=True)
class FeeComponent:
    """One fee component and its complete valuation evidence; no aggregation."""

    fee_seq: int
    fee: FillFee
    valuation_evidence: ValuationEvidence

    def __post_init__(self) -> None:
        if isinstance(self.fee_seq, bool) or not isinstance(self.fee_seq, int) or self.fee_seq < 1:
            raise ImmutableLedgerContractError("fee_seq must be a positive integer")
        if not isinstance(self.fee, FillFee):
            raise ImmutableLedgerContractError("fee component requires an immutable FillFee")
        if not isinstance(self.valuation_evidence, ValuationEvidence):
            raise IncompleteValuationEvidenceError("every fee component requires valuation evidence")
        if self.valuation_evidence.asset != self.fee.asset:
            raise IncompleteValuationEvidenceError("fee valuation asset must equal fee asset")

    @property
    def valuation_amount(self) -> QuoteAmount:
        return self.valuation_evidence.value_for(self.fee.amount)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    book: LedgerBook
    account_code: str
    asset: str
    signed_amount: Decimal
    value_in_valuation_ccy: Decimal | None
    instrument_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.book, LedgerBook):
            raise ImmutableLedgerContractError("ledger book is required")
        object.__setattr__(self, "account_code", _canonical_string(self.account_code, "account_code", case="upper"))
        object.__setattr__(self, "asset", _canonical_string(self.asset, "asset", case="upper"))
        object.__setattr__(self, "signed_amount", _signed_decimal(self.signed_amount, "signed_amount"))
        object.__setattr__(self, "instrument_id", _canonical_string(self.instrument_id, "instrument_id", case="upper"))
        if self.book is LedgerBook.QUANTITY:
            if self.value_in_valuation_ccy is not None:
                raise ImmutableLedgerContractError("QUANTITY entries cannot carry monetary valuation")
        else:
            if self.value_in_valuation_ccy is None:
                raise ImmutableLedgerContractError("MONETARY entries require valuation evidence")
            object.__setattr__(
                self,
                "value_in_valuation_ccy",
                _signed_decimal(self.value_in_valuation_ccy, "value_in_valuation_ccy"),
            )

    def canonical_payload(self) -> dict[str, str | None]:
        return {
            "account_code": self.account_code,
            "asset": self.asset,
            "book": self.book.value,
            "instrument_id": self.instrument_id,
            "signed_amount": canonical_decimal_string(self.signed_amount),
            "value_in_valuation_ccy": (
                None if self.value_in_valuation_ccy is None
                else canonical_decimal_string(self.value_in_valuation_ccy)
            ),
        }


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    transaction_type: LedgerTransactionType
    source_event_type: str
    source_fingerprint: str
    valuation_ccy: str
    account_scope: str
    entries: tuple[LedgerEntry, ...]
    reverses_transaction_id: str | None = None
    corrects_transaction_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.transaction_type, LedgerTransactionType):
            raise ImmutableLedgerContractError("ledger transaction_type is required")
        object.__setattr__(self, "source_event_type", _canonical_string(self.source_event_type, "source_event_type", case="upper"))
        object.__setattr__(self, "source_fingerprint", _canonical_hash(self.source_fingerprint, "source_fingerprint"))
        object.__setattr__(self, "valuation_ccy", _canonical_string(self.valuation_ccy, "valuation_ccy", case="upper"))
        object.__setattr__(self, "account_scope", _canonical_string(self.account_scope, "account_scope"))
        if not self.entries or any(not isinstance(entry, LedgerEntry) for entry in self.entries):
            raise ImmutableLedgerContractError("ledger transaction requires complete entries")
        reversal = self.reverses_transaction_id
        correction = self.corrects_transaction_id
        if self.transaction_type is LedgerTransactionType.REVERSAL:
            if reversal is None or correction is not None:
                raise ImmutableLedgerContractError("REVERSAL requires only reverses_transaction_id")
            object.__setattr__(self, "reverses_transaction_id", _canonical_uuid(reversal, "reverses_transaction_id"))
        elif self.transaction_type is LedgerTransactionType.CORRECTION:
            if correction is None or reversal is not None:
                raise ImmutableLedgerContractError("CORRECTION requires only corrects_transaction_id")
            object.__setattr__(self, "corrects_transaction_id", _canonical_uuid(correction, "corrects_transaction_id"))
        elif reversal is not None or correction is not None:
            raise ImmutableLedgerContractError("TRADE and FEE transactions cannot reference reversals or corrections")
        validate_ledger_balance(self.entries)

    @property
    def replay_fingerprint(self) -> str:
        return _fingerprint(
            {
                "account_scope": self.account_scope,
                "contract_version": LEDGER_BUNDLE_CONTRACT_VERSION,
                "entries": [entry.canonical_payload() for entry in self.entries],
                "source_event_type": self.source_event_type,
                "source_fingerprint": self.source_fingerprint,
                "transaction_type": self.transaction_type.value,
                "valuation_ccy": self.valuation_ccy,
            }
        )


@dataclass(frozen=True, slots=True)
class FillLedgerInput:
    """All immutable facts required to reduce one venue fill without guesses."""

    venue_fill: VenueFillIdentity
    economic_order_id: str
    side: FillSide
    assets: InstrumentAssetScope
    valuation_ccy: str
    quote_quantity: QuoteQuantityFact
    fee_components: tuple[FeeComponent, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.venue_fill, VenueFillIdentity):
            raise ImmutableLedgerContractError("venue_fill requires a stable VenueFillIdentity")
        object.__setattr__(self, "economic_order_id", _canonical_uuid(self.economic_order_id, "economic_order_id"))
        if not isinstance(self.side, FillSide):
            raise ImmutableLedgerContractError("fill side is required")
        if not isinstance(self.assets, InstrumentAssetScope):
            raise ImmutableLedgerContractError("instrument asset scope is required")
        if self.assets.instrument_id != self.venue_fill.order_scope.instrument:
            raise ImmutableLedgerContractError("instrument asset scope must match the venue fill")
        object.__setattr__(self, "valuation_ccy", _canonical_string(self.valuation_ccy, "valuation_ccy", case="upper"))
        if not isinstance(self.quote_quantity, QuoteQuantityFact):
            raise ImmutableLedgerContractError("quote_quantity is required")
        quote_evidence = self.quote_quantity.valuation_evidence
        if quote_evidence.fill_key != self.venue_fill.canonical_key:
            raise IncompleteValuationEvidenceError("quote valuation evidence must belong to this fill")
        if quote_evidence.asset != self.assets.quote_asset or quote_evidence.valuation_ccy != self.valuation_ccy:
            raise IncompleteValuationEvidenceError("quote valuation evidence scope is incomplete")
        if any(not isinstance(component, FeeComponent) for component in self.fee_components):
            raise ImmutableLedgerContractError("fee components require complete immutable facts")
        ordered_components = tuple(sorted(self.fee_components, key=lambda component: component.fee_seq))
        if tuple(component.fee_seq for component in ordered_components) != tuple(range(1, len(ordered_components) + 1)):
            raise ImmutableLedgerContractError("fee components require contiguous deterministic fee_seq values")
        if tuple(component.fee for component in ordered_components) != self.venue_fill.fees:
            raise IncompleteValuationEvidenceError("every venue fee requires exactly one complete fee component")
        for component in ordered_components:
            evidence = component.valuation_evidence
            if evidence.fill_key != self.venue_fill.canonical_key or evidence.valuation_ccy != self.valuation_ccy:
                raise IncompleteValuationEvidenceError("fee valuation evidence scope is incomplete")
        object.__setattr__(self, "fee_components", ordered_components)

    @property
    def fill_key(self) -> str:
        return self.venue_fill.canonical_key

    @property
    def account_scope(self) -> str:
        return self.venue_fill.order_scope.account_scope

    def canonical_payload(self) -> dict[str, object]:
        return {
            "assets": {
                "base_asset": self.assets.base_asset,
                "instrument_id": self.assets.instrument_id,
                "quote_asset": self.assets.quote_asset,
            },
            "economic_order_id": self.economic_order_id,
            "fee_components": [
                {
                    "amount": component.fee.amount.to_string(),
                    "asset": component.fee.asset,
                    "evidence_hash": component.valuation_evidence.evidence_hash,
                    "fee_seq": component.fee_seq,
                    "valuation_amount": component.valuation_amount.to_string(),
                }
                for component in self.fee_components
            ],
            "fill_key": self.fill_key,
            "quote_quantity": {
                "amount": self.quote_quantity.amount.to_string(),
                "evidence_hash": self.quote_quantity.evidence_hash,
                "origin": self.quote_quantity.origin.value,
                "policy_version": self.quote_quantity.calculation_policy_version,
                "valuation_amount": self.quote_quantity.valuation_evidence.value_for(self.quote_quantity.amount).to_string(),
            },
            "side": self.side.value,
            "valuation_ccy": self.valuation_ccy,
        }


@dataclass(frozen=True, slots=True)
class FillLedgerBundle:
    fill_key: str
    trade: LedgerTransaction
    fee: LedgerTransaction | None
    replay_fingerprint: str


def validate_ledger_balance(entries: tuple[LedgerEntry, ...]) -> None:
    """Fail closed unless each book/asset and the monetary valuation book balance."""

    totals: dict[tuple[LedgerBook, str], Decimal] = {}
    monetary_total = Decimal(0)
    for entry in entries:
        key = (entry.book, entry.asset)
        totals[key] = totals.get(key, Decimal(0)) + entry.signed_amount
        if entry.book is LedgerBook.MONETARY:
            assert entry.value_in_valuation_ccy is not None
            monetary_total += entry.value_in_valuation_ccy
    unbalanced = {
        f"{book.value}:{asset}": canonical_decimal_string(total)
        for (book, asset), total in totals.items()
        if total != 0
    }
    if unbalanced or monetary_total != 0:
        raise LedgerBalanceError("ledger entries must balance by book/asset and valuation currency")


def reduce_fill_to_ledger_bundle(fill: FillLedgerInput) -> FillLedgerBundle:
    """Produce deterministic TRADE and FEE transactions from one complete fill fact."""

    if not isinstance(fill, FillLedgerInput):
        raise ImmutableLedgerContractError("fill reducer requires FillLedgerInput")
    base_quantity = fill.venue_fill.quantity.value
    quote_quantity = fill.quote_quantity.amount.value
    quote_valuation = fill.quote_quantity.valuation_evidence.value_for(fill.quote_quantity.amount).value
    direction = Decimal(1) if fill.side is FillSide.BUY else Decimal(-1)
    trade_entries = (
        LedgerEntry(LedgerBook.QUANTITY, "POSITION", fill.assets.base_asset, direction * base_quantity, None, fill.assets.instrument_id),
        LedgerEntry(LedgerBook.QUANTITY, "EXCHANGE_CLEARING", fill.assets.base_asset, -direction * base_quantity, None, fill.assets.instrument_id),
        LedgerEntry(LedgerBook.MONETARY, "CASH", fill.assets.quote_asset, -direction * quote_quantity, -direction * quote_valuation, fill.assets.instrument_id),
        LedgerEntry(LedgerBook.MONETARY, "EXCHANGE_CLEARING", fill.assets.quote_asset, direction * quote_quantity, direction * quote_valuation, fill.assets.instrument_id),
    )
    trade_source_fingerprint = _fingerprint(
        {
            "bundle_contract_version": LEDGER_BUNDLE_CONTRACT_VERSION,
            "fill_key": fill.fill_key,
            "source_fingerprint_version": LEDGER_SOURCE_FINGERPRINT_VERSION,
            "transaction_type": LedgerTransactionType.TRADE.value,
        }
    )
    trade = LedgerTransaction(
        LedgerTransactionType.TRADE,
        "EXCHANGE_FILL_TRADE",
        trade_source_fingerprint,
        fill.valuation_ccy,
        fill.account_scope,
        trade_entries,
    )

    fee: LedgerTransaction | None = None
    if fill.fee_components:
        fee_entries: list[LedgerEntry] = []
        for component in fill.fee_components:
            amount = component.fee.amount.value
            valuation = component.valuation_amount.value
            fee_entries.extend(
                (
                    LedgerEntry(LedgerBook.QUANTITY, "FEE_EXPENSE", component.fee.asset, amount, None, fill.assets.instrument_id),
                    LedgerEntry(LedgerBook.QUANTITY, "CASH", component.fee.asset, -amount, None, fill.assets.instrument_id),
                    LedgerEntry(LedgerBook.MONETARY, "FEE_EXPENSE", fill.valuation_ccy, valuation, valuation, fill.assets.instrument_id),
                    LedgerEntry(LedgerBook.MONETARY, "CASH", fill.valuation_ccy, -valuation, -valuation, fill.assets.instrument_id),
                )
            )
        fee_source_fingerprint = _fingerprint(
            {
                "bundle_contract_version": LEDGER_BUNDLE_CONTRACT_VERSION,
                "components": [
                    {
                        "amount": component.fee.amount.to_string(),
                        "asset": component.fee.asset,
                        "evidence_hash": component.valuation_evidence.evidence_hash,
                        "fee_seq": component.fee_seq,
                    }
                    for component in fill.fee_components
                ],
                "fill_key": fill.fill_key,
                "source_fingerprint_version": LEDGER_SOURCE_FINGERPRINT_VERSION,
                "transaction_type": LedgerTransactionType.FEE.value,
            }
        )
        fee = LedgerTransaction(
            LedgerTransactionType.FEE,
            "EXCHANGE_FILL_FEE_BUNDLE",
            fee_source_fingerprint,
            fill.valuation_ccy,
            fill.account_scope,
            tuple(fee_entries),
        )

    replay_fingerprint = _fingerprint(
        {
            "bundle_contract_version": LEDGER_BUNDLE_CONTRACT_VERSION,
            "fill": fill.canonical_payload(),
            "fee_replay_fingerprint": None if fee is None else fee.replay_fingerprint,
            "trade_replay_fingerprint": trade.replay_fingerprint,
        }
    )
    return FillLedgerBundle(fill.fill_key, trade, fee, replay_fingerprint)
