from __future__ import annotations

import ast
import hashlib
import unittest
import uuid
from decimal import Decimal
from pathlib import Path

from tests.pr06_contract_loader import load_pr06_contracts


modules = load_pr06_contracts()
decimal_values = modules.decimal_values
venue = modules.venue
ledger = modules.ledger


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def valuation(fill_key, asset, valuation_ccy, price, *, source=None, label="evidence"):
    source = source or ledger.ValuationEvidenceSource.IDENTITY
    return ledger.ValuationEvidence(
        fill_key=fill_key,
        asset=asset,
        valuation_ccy=valuation_ccy,
        price=decimal_values.Price(price),
        source=source,
        policy_version="valuation-v1",
        evidence_hash=digest(label),
    )


def fill_identity(*, account_scope="account-a", fees=()):
    return venue.VenueFillIdentity(
        venue.VenueOrderScope("binance", "swap", account_scope, "BTC-USDT", "order-1"),
        "venue-fill-1",
        decimal_values.Quantity("1"),
        decimal_values.Price("100"),
        fees,
    )


def fill_input(*, side=ledger.FillSide.BUY, account_scope="account-a", include_fees=True):
    fees = ()
    if include_fees:
        fees = (
            venue.FillFee("USDT", decimal_values.FeeAmount("0.1")),
            venue.FillFee("BNB", decimal_values.FeeAmount("0.001")),
        )
    venue_fill = fill_identity(account_scope=account_scope, fees=fees)
    fill_key = venue_fill.canonical_key
    quote_evidence = valuation(fill_key, "USDT", "USDT", "1", label="quote")
    components = ()
    if include_fees:
        components = (
            ledger.FeeComponent(1, fees[0], valuation(fill_key, "USDT", "USDT", "1", label="fee-usdt")),
            ledger.FeeComponent(
                2,
                fees[1],
                valuation(
                    fill_key,
                    "BNB",
                    "USDT",
                    "300",
                    source=ledger.ValuationEvidenceSource.VENUE,
                    label="fee-bnb",
                ),
            ),
        )
    return ledger.FillLedgerInput(
        venue_fill=venue_fill,
        economic_order_id="00000000-0000-0000-0000-000000000001",
        side=side,
        assets=ledger.InstrumentAssetScope("BTC-USDT", "BTC", "USDT"),
        valuation_ccy="USDT",
        quote_quantity=ledger.QuoteQuantityFact(
            decimal_values.QuoteAmount("100"),
            ledger.QuoteQuantityOrigin.VENUE,
            digest("quote-observation"),
            quote_evidence,
        ),
        fee_components=components,
    )


class ImmutableFillLedgerTests(unittest.TestCase):
    def test_trade_and_multi_fee_bundles_are_deterministic_and_balanced(self):
        first = ledger.reduce_fill_to_ledger_bundle(fill_input())
        second = ledger.reduce_fill_to_ledger_bundle(fill_input())

        self.assertEqual(first, second)
        self.assertEqual(first.fill_key, fill_input().venue_fill.canonical_key)
        self.assertEqual(first.trade.transaction_type, ledger.LedgerTransactionType.TRADE)
        self.assertEqual(first.fee.transaction_type, ledger.LedgerTransactionType.FEE)
        self.assertEqual(len(first.trade.entries), 4)
        self.assertEqual(len(first.fee.entries), 8)
        self.assertEqual(first.replay_fingerprint, second.replay_fingerprint)
        ledger.validate_ledger_balance(first.trade.entries)
        ledger.validate_ledger_balance(first.fee.entries)

    def test_buy_and_sell_have_opposite_quantity_and_cash_directions(self):
        buy = ledger.reduce_fill_to_ledger_bundle(fill_input(side=ledger.FillSide.BUY, include_fees=False))
        sell = ledger.reduce_fill_to_ledger_bundle(fill_input(side=ledger.FillSide.SELL, include_fees=False))
        self.assertEqual(buy.trade.entries[0].signed_amount, Decimal("1"))
        self.assertEqual(sell.trade.entries[0].signed_amount, Decimal("-1"))
        self.assertEqual(buy.trade.entries[2].signed_amount, Decimal("-100"))
        self.assertEqual(sell.trade.entries[2].signed_amount, Decimal("100"))

    def test_fee_assets_remain_separate_and_are_not_silently_aggregated(self):
        bundle = ledger.reduce_fill_to_ledger_bundle(fill_input())
        assert bundle.fee is not None
        quantity_assets = [
            entry.asset for entry in bundle.fee.entries if entry.book is ledger.LedgerBook.QUANTITY
        ]
        self.assertEqual(quantity_assets, ["USDT", "USDT", "BNB", "BNB"])
        self.assertEqual(
            [component.fee.asset for component in fill_input().fee_components],
            ["USDT", "BNB"],
        )

    def test_missing_or_partial_fee_evidence_fails_closed(self):
        venue_fill = fill_identity(
            fees=(venue.FillFee("BNB", decimal_values.FeeAmount("0.001")),)
        )
        fill_key = venue_fill.canonical_key
        quote = ledger.QuoteQuantityFact(
            decimal_values.QuoteAmount("100"),
            ledger.QuoteQuantityOrigin.VENUE,
            digest("quote-observation"),
            valuation(fill_key, "USDT", "USDT", "1", label="quote"),
        )
        with self.assertRaises(ledger.IncompleteValuationEvidenceError):
            ledger.FillLedgerInput(
                venue_fill=venue_fill,
                economic_order_id="00000000-0000-0000-0000-000000000001",
                side=ledger.FillSide.BUY,
                assets=ledger.InstrumentAssetScope("BTC-USDT", "BTC", "USDT"),
                valuation_ccy="USDT",
                quote_quantity=quote,
                fee_components=(),
            )

    def test_identity_evidence_requires_same_asset_at_one(self):
        fill_key = fill_identity().canonical_key
        with self.assertRaises(ledger.IncompleteValuationEvidenceError):
            valuation(fill_key, "BNB", "USDT", "1")
        with self.assertRaises(ledger.IncompleteValuationEvidenceError):
            valuation(fill_key, "USDT", "USDT", "2")

    def test_quote_quantity_authority_is_explicit(self):
        fill_key = fill_identity().canonical_key
        evidence = valuation(fill_key, "USDT", "USDT", "1")
        with self.assertRaises(ledger.ImmutableLedgerContractError):
            ledger.QuoteQuantityFact(
                decimal_values.QuoteAmount("100"),
                ledger.QuoteQuantityOrigin.VENUE,
                digest("venue"),
                evidence,
                calculation_policy_version="derived-v1",
            )
        with self.assertRaises(ledger.ImmutableLedgerContractError):
            ledger.QuoteQuantityFact(
                decimal_values.QuoteAmount("100"),
                ledger.QuoteQuantityOrigin.DERIVED,
                digest("derived"),
                evidence,
            )

    def test_float_and_unbalanced_entries_are_rejected(self):
        with self.assertRaises(decimal_values.DecimalInputTypeError):
            ledger.LedgerEntry(
                ledger.LedgerBook.QUANTITY, "POSITION", "BTC", 1.0, None, "BTC-USDT"
            )
        unbalanced = (
            ledger.LedgerEntry(ledger.LedgerBook.QUANTITY, "POSITION", "BTC", "1", None, "BTC-USDT"),
            ledger.LedgerEntry(ledger.LedgerBook.QUANTITY, "CASH", "BTC", "-0.9", None, "BTC-USDT"),
        )
        with self.assertRaises(ledger.LedgerBalanceError):
            ledger.validate_ledger_balance(unbalanced)

    def test_reversal_and_correction_are_explicitly_separate(self):
        entries = (
            ledger.LedgerEntry(ledger.LedgerBook.QUANTITY, "POSITION", "BTC", "1", None, "BTC-USDT"),
            ledger.LedgerEntry(ledger.LedgerBook.QUANTITY, "CLEARING", "BTC", "-1", None, "BTC-USDT"),
        )
        original = str(uuid.uuid4())
        reversal = ledger.LedgerTransaction(
            ledger.LedgerTransactionType.REVERSAL, "REVERSAL", digest("reverse"), "USDT", "account-a", entries,
            reverses_transaction_id=original,
        )
        correction = ledger.LedgerTransaction(
            ledger.LedgerTransactionType.CORRECTION, "CORRECTION", digest("correct"), "USDT", "account-a", entries,
            corrects_transaction_id=original,
        )
        self.assertEqual(reversal.reverses_transaction_id, original)
        self.assertEqual(correction.corrects_transaction_id, original)
        with self.assertRaises(ledger.ImmutableLedgerContractError):
            ledger.LedgerTransaction(
                ledger.LedgerTransactionType.CORRECTION, "CORRECTION", digest("invalid"), "USDT", "account-a", entries,
                reverses_transaction_id=original,
            )

    def test_scope_changes_change_fill_and_replay_identity(self):
        first = ledger.reduce_fill_to_ledger_bundle(fill_input(account_scope="account-a"))
        second = ledger.reduce_fill_to_ledger_bundle(fill_input(account_scope="account-b"))
        self.assertNotEqual(first.fill_key, second.fill_key)
        self.assertNotEqual(first.replay_fingerprint, second.replay_fingerprint)

    def test_module_remains_pure(self):
        tree = ast.parse(Path(ledger.__file__).read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse({"flask", "psycopg2", "sqlalchemy"} & imports)


if __name__ == "__main__":
    unittest.main()
