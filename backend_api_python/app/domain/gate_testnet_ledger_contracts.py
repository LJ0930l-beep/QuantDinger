"""Translate typed Gate TestNet receipts into immutable ledger input facts.

This is deliberately a pure boundary.  It does not open a connection, call a
venue, or persist anything.  The caller must provide the economic-order and
asset facts that are not present in a venue receipt; missing valuation facts
fail closed instead of being inferred from a symbol or account setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from app.domain.decimal_values import FeeAmount, Price, Quantity, QuoteAmount
from app.domain.gate_testnet_execution_contracts import GateTestnetExecutionReceipt
from app.domain.gate_vertical_read_contracts import GateFillFact, GateOrderSide
from app.domain.immutable_fill_ledger import (
    FillLedgerInput,
    FillSide,
    InstrumentAssetScope,
    QuoteQuantityFact,
    QuoteQuantityOrigin,
    ValuationEvidence,
    ValuationEvidenceSource,
    FeeComponent,
)
from app.domain.venue_order_contracts import FillFee, VenueFillIdentity, VenueOrderScope


GATE_TESTNET_QUOTE_POLICY_VERSION = "gate-testnet-quote-v1"
GATE_TESTNET_FEE_POLICY_VERSION = "gate-testnet-fee-identity-v1"


class GateTestnetLedgerContractError(ValueError):
    """Receipt facts cannot be safely attached to an immutable ledger fill."""


@dataclass(frozen=True, slots=True)
class GateTestnetLedgerScope:
    """Explicit facts needed to value and persist a TestNet fill."""

    economic_order_id: str
    assets: InstrumentAssetScope
    valuation_ccy: str
    quote_valuation_price: Price | None = None
    fee_valuation_prices: Mapping[str, Price] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.economic_order_id, str) or not self.economic_order_id.strip():
            raise GateTestnetLedgerContractError("economic_order_id is required")
        if not isinstance(self.assets, InstrumentAssetScope):
            raise GateTestnetLedgerContractError("instrument asset scope is required")
        if not isinstance(self.valuation_ccy, str) or not self.valuation_ccy.strip() or not self.valuation_ccy.isascii():
            raise GateTestnetLedgerContractError("valuation_ccy must be canonical ASCII text")
        object.__setattr__(self, "valuation_ccy", self.valuation_ccy.strip().upper())
        if self.quote_valuation_price is not None and not isinstance(self.quote_valuation_price, Price):
            raise GateTestnetLedgerContractError("quote valuation price must use Price")
        prices = self.fee_valuation_prices or {}
        if any(not isinstance(asset, str) or asset != asset.strip() or not asset for asset in prices):
            raise GateTestnetLedgerContractError("fee valuation assets must be canonical text")
        if any(not isinstance(price, Price) for price in prices.values()):
            raise GateTestnetLedgerContractError("fee valuation prices must use Price")
        object.__setattr__(self, "fee_valuation_prices", {asset.upper(): price for asset, price in prices.items()})


def _evidence_hash(fill_key: str, purpose: str) -> str:
    import hashlib

    return hashlib.sha256(f"gate-testnet:{purpose}:{fill_key}".encode("ascii")).hexdigest()


def _price_for_asset(scope: GateTestnetLedgerScope, asset: str, *, quote: bool) -> tuple[Price, ValuationEvidenceSource, str]:
    asset = asset.upper()
    if asset == scope.valuation_ccy:
        return Price(Decimal("1")), ValuationEvidenceSource.IDENTITY, "IDENTITY"
    price = scope.quote_valuation_price if quote else (scope.fee_valuation_prices or {}).get(asset)
    if price is None:
        raise GateTestnetLedgerContractError(f"missing explicit valuation evidence for {asset}")
    # A caller-supplied cross-currency price is not venue evidence.  Keep the
    # provenance explicit so the ledger never presents an unverified rate as a
    # Gate fact; production callers should replace it with an approved oracle
    # or manual-approval evidence record.
    return price, ValuationEvidenceSource.MANUAL_APPROVED, "EXPLICIT"


def build_gate_fill_ledger_input(
    gate_fill: GateFillFact,
    *,
    economic_order_id: str,
    scope: GateTestnetLedgerScope,
) -> FillLedgerInput:
    """Build one immutable ledger bundle input from a normalized Gate fill.

    The helper is shared by fixture and network settlement paths.  It accepts
    only already-normalized ``GateFillFact`` values and requires explicit
    asset/valuation scope; it never parses symbols or invents exchange rates.
    """

    if not isinstance(gate_fill, GateFillFact):
        raise GateTestnetLedgerContractError("typed Gate fill fact is required")
    if not isinstance(scope, GateTestnetLedgerScope):
        raise GateTestnetLedgerContractError("typed Gate ledger scope is required")
    if not isinstance(economic_order_id, str) or not economic_order_id.strip():
        raise GateTestnetLedgerContractError("economic_order_id is required")
    if gate_fill.instrument_id.upper() != scope.assets.instrument_id:
        raise GateTestnetLedgerContractError("fill and ledger instrument scope mismatch")

    fees: tuple[FillFee, ...] = ()
    if gate_fill.fee_asset is not None:
        if gate_fill.fee_amount is None:
            raise GateTestnetLedgerContractError("fee asset requires fee amount")
        fees = (FillFee(gate_fill.fee_asset, FeeAmount(gate_fill.fee_amount)),)
    venue_scope = VenueOrderScope(
        gate_fill.venue_id,
        gate_fill.market_type.value,
        gate_fill.account_scope,
        gate_fill.instrument_id,
        gate_fill.exchange_order_id,
    )
    venue_fill = VenueFillIdentity.from_venue_fact(
        venue_scope,
        venue="gate",
        market_type=gate_fill.market_type.value,
        account_scope=gate_fill.account_scope,
        instrument=gate_fill.instrument_id,
        exchange_order_id=gate_fill.exchange_order_id,
        venue_fill_id=gate_fill.venue_fill_id,
        quantity=Quantity(gate_fill.quantity),
        price=Price(gate_fill.price),
        fees=fees,
    )
    fill_key = venue_fill.canonical_key
    quote_amount = QuoteAmount(gate_fill.quantity * gate_fill.price)
    quote_price, quote_source, _ = _price_for_asset(scope, scope.assets.quote_asset, quote=True)
    quote_evidence = ValuationEvidence(
        fill_key=fill_key,
        asset=scope.assets.quote_asset,
        valuation_ccy=scope.valuation_ccy,
        price=quote_price,
        source=quote_source,
        policy_version=GATE_TESTNET_QUOTE_POLICY_VERSION,
        evidence_hash=_evidence_hash(fill_key, "quote"),
    )
    quote = QuoteQuantityFact(
        amount=quote_amount,
        origin=QuoteQuantityOrigin.DERIVED,
        evidence_hash=_evidence_hash(fill_key, "quote-quantity"),
        valuation_evidence=quote_evidence,
        calculation_policy_version=GATE_TESTNET_QUOTE_POLICY_VERSION,
    )
    components: list[FeeComponent] = []
    for fee_seq, fee in enumerate(fees, start=1):
        fee_price, fee_source, _ = _price_for_asset(scope, fee.asset, quote=False)
        components.append(FeeComponent(
            fee_seq=fee_seq,
            fee=fee,
            valuation_evidence=ValuationEvidence(
                fill_key=fill_key,
                asset=fee.asset,
                valuation_ccy=scope.valuation_ccy,
                price=fee_price,
                source=fee_source,
                policy_version=GATE_TESTNET_FEE_POLICY_VERSION,
                evidence_hash=_evidence_hash(fill_key, f"fee-{fee_seq}"),
            ),
        ))
    return FillLedgerInput(
        venue_fill=venue_fill,
        economic_order_id=economic_order_id,
        side=FillSide.BUY if gate_fill.side is GateOrderSide.BUY else FillSide.SELL,
        assets=scope.assets,
        valuation_ccy=scope.valuation_ccy,
        quote_quantity=quote,
        fee_components=tuple(components),
    )


def build_gate_testnet_ledger_inputs(
    receipt: GateTestnetExecutionReceipt,
    *,
    scope: GateTestnetLedgerScope,
) -> tuple[FillLedgerInput, ...]:
    """Build one immutable ``FillLedgerInput`` per stable venue fill.

    A partial or fully filled receipt is accepted; a receipt without fills
    returns an empty tuple.  No timestamp/price/quantity identity is ever
    synthesized, and every cross-scope or missing valuation fact is rejected.
    """

    if not isinstance(receipt, GateTestnetExecutionReceipt):
        raise GateTestnetLedgerContractError("typed Gate TestNet receipt is required")
    request = receipt.request
    if request.account_scope != receipt.order.account_scope or request.instrument_id != scope.assets.instrument_id:
        raise GateTestnetLedgerContractError("receipt and ledger instrument/account scope mismatch")
    if receipt.order.market_type is not request.market_type:
        raise GateTestnetLedgerContractError("receipt market scope mismatch")
    outputs: list[FillLedgerInput] = []
    for gate_fill in receipt.fills:
        if (
            gate_fill.account_scope != request.account_scope
            or gate_fill.instrument_id != request.instrument_id
            or gate_fill.exchange_order_id != receipt.order.exchange_order_id
            or gate_fill.market_type is not request.market_type
            or gate_fill.side is not request.side
        ):
            raise GateTestnetLedgerContractError("fill scope does not match the requested order")
        outputs.append(build_gate_fill_ledger_input(gate_fill, economic_order_id=scope.economic_order_id, scope=scope))
    return tuple(outputs)


__all__ = [
    "GATE_TESTNET_FEE_POLICY_VERSION",
    "GATE_TESTNET_QUOTE_POLICY_VERSION",
    "GateTestnetLedgerContractError",
    "GateTestnetLedgerScope",
    "build_gate_fill_ledger_input",
    "build_gate_testnet_ledger_inputs",
]
