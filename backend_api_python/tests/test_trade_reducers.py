from __future__ import annotations

import unittest

from tests.pr01_domain_loader import load_pr01_domain


domain = load_pr01_domain()


def fill(
    event_id,
    sequence,
    side,
    price,
    quantity,
    *,
    quote_quantity=None,
    fees=(),
    economic_order_id="economic-order-1",
    instrument_id="BTC-USDT",
    account_scope="account-1",
    instrument_rule_version="fixture-rules-v1",
):
    return domain.FillEvent(
        event_id=event_id,
        sequence=sequence,
        economic_order_id=economic_order_id,
        instrument_id=instrument_id,
        account_scope=account_scope,
        side=side,
        price=domain.Price(price),
        quantity=domain.Quantity(quantity),
        quote_quantity=(
            domain.QuoteAmount(quote_quantity) if quote_quantity is not None else None
        ),
        fees=tuple(fees),
        instrument_rule_version=instrument_rule_version,
    )


def fee(asset, amount):
    return domain.Fee(asset, domain.FeeAmount(amount))


def economic_scope(
    economic_order_id="economic-order-1",
    instrument_id="BTC-USDT",
    account_scope="account-1",
    expected_side="BUY",
):
    return domain.EconomicOrderScope(
        economic_order_id=economic_order_id,
        instrument_id=instrument_id,
        account_scope=account_scope,
        expected_side=expected_side,
    )


def position_scope(instrument_id="BTC-USDT", account_scope="account-1"):
    return domain.PositionScope(
        instrument_id=instrument_id,
        account_scope=account_scope,
    )


def reduce_economic_order(target_quantity, fills, *, quantity_tolerance, scope=None):
    return domain.reduce_economic_order(
        target_quantity,
        fills,
        scope=scope or economic_scope(),
        quantity_tolerance=quantity_tolerance,
    )


def reduce_position(fills, *, scope=None):
    return domain.reduce_position(fills, scope=scope or position_scope())


class EconomicOrderReducerTests(unittest.TestCase):
    def test_one_hundred_partial_fills_accumulate_exactly(self):
        fills = [
            fill(f"fill-{index}", index, "BUY", "10", "0.01")
            for index in range(100)
        ]
        result = reduce_economic_order(
            domain.Quantity("1"), fills, quantity_tolerance=domain.Quantity("0")
        )
        self.assertEqual(result.cumulative_filled_quantity.to_string(), "1")
        self.assertEqual(result.quantity_tolerance.to_string(), "0")
        self.assertEqual(result.cumulative_quote_quantity.to_string(), "10")
        self.assertEqual(result.weighted_average_fill_price.to_string(), "10")
        self.assertEqual(result.remaining_quantity.to_string(), "0")
        self.assertEqual(result.overfill_quantity.to_string(), "0")
        self.assertTrue(result.reached_target_within_tolerance)
        self.assertEqual(len(result.applied_event_ids), 100)
        self.assertEqual(len(result.derived_quote_event_ids), 100)

    def test_duplicate_event_id_is_rejected(self):
        event = fill("same", 1, "BUY", "100", "1")
        with self.assertRaises(domain.DuplicateFillEventError):
            reduce_economic_order(
                domain.Quantity("2"),
                [event, event],
                quantity_tolerance=domain.Quantity("0"),
            )

    def test_sequence_conflict_is_rejected(self):
        with self.assertRaises(domain.FillSequenceConflictError):
            reduce_economic_order(
                domain.Quantity("2"),
                [
                    fill("one", 7, "BUY", "100", "1"),
                    fill("two", 7, "BUY", "100", "1"),
                ],
                quantity_tolerance=domain.Quantity("0"),
            )

    def test_out_of_order_events_use_sequence_and_replay_identically(self):
        ordered = [
            fill("one", 1, "BUY", "100", "0.4"),
            fill("two", 2, "BUY", "110", "0.6"),
        ]
        first = reduce_economic_order(
            domain.Quantity("1"), ordered, quantity_tolerance=domain.Quantity("0")
        )
        replay = reduce_economic_order(
            domain.Quantity("1"), reversed(ordered), quantity_tolerance=domain.Quantity("0")
        )
        self.assertEqual(first, replay)
        self.assertEqual(first.stable_hash(), replay.stable_hash())
        self.assertEqual(first.applied_event_ids, ("one", "two"))

    def test_overfill_is_reported_without_truncation(self):
        result = reduce_economic_order(
            domain.Quantity("1"),
            [fill("overfill", 1, "BUY", "100", "1.2")],
            quantity_tolerance=domain.Quantity("0"),
        )
        self.assertEqual(result.cumulative_filled_quantity.to_string(), "1.2")
        self.assertEqual(result.remaining_quantity.to_string(), "0")
        self.assertEqual(result.overfill_quantity.to_string(), "0.2")
        self.assertTrue(result.reached_target_within_tolerance)

    def test_tolerance_is_explicit_and_cannot_exceed_target(self):
        result = reduce_economic_order(
            domain.Quantity("1"),
            [fill("near", 1, "BUY", "100", "0.99")],
            quantity_tolerance=domain.Quantity("0.01"),
        )
        self.assertTrue(result.reached_target_within_tolerance)
        exact = reduce_economic_order(
            domain.Quantity("1"),
            [fill("exact", 2, "BUY", "100", "1")],
            quantity_tolerance=domain.Quantity("0"),
        )
        tolerant = reduce_economic_order(
            domain.Quantity("1"),
            [fill("exact", 2, "BUY", "100", "1")],
            quantity_tolerance=domain.Quantity("0.1"),
        )
        self.assertNotEqual(exact.stable_hash(), tolerant.stable_hash())
        with self.assertRaises(domain.ReducerContractError):
            reduce_economic_order(
                domain.Quantity("1"),
                [],
                quantity_tolerance=domain.Quantity("1.1"),
            )

    def test_quote_facts_and_weighted_price_have_separate_semantics(self):
        result = reduce_economic_order(
            domain.Quantity("2"),
            [
                fill("one", 1, "BUY", "100", "1", quote_quantity="100.1"),
                fill("two", 2, "BUY", "110", "1", quote_quantity="109.9"),
            ],
            quantity_tolerance=domain.Quantity("0"),
        )
        self.assertEqual(result.cumulative_quote_quantity.to_string(), "210")
        self.assertEqual(result.weighted_average_fill_price.to_string(), "105")
        self.assertEqual(result.derived_quote_event_ids, ())

    def test_fee_assets_are_never_combined_or_guessed(self):
        result = reduce_economic_order(
            domain.Quantity("1"),
            [
                fill(
                    "fees",
                    1,
                    "BUY",
                    "100",
                    "1",
                    fees=(fee("USDT", "0.2"), fee("BTC", "0.0001"), fee("BNB", "0.01")),
                )
            ],
            quantity_tolerance=domain.Quantity("0"),
        )
        self.assertEqual(
            [(item.asset, item.amount.to_string()) for item in result.cumulative_fee],
            [("BNB", "0.01"), ("BTC", "0.0001"), ("USDT", "0.2")],
        )

    def test_mixed_buy_and_sell_fills_are_rejected(self):
        with self.assertRaises(domain.FillSideMismatchError):
            reduce_economic_order(
                domain.Quantity("2"),
                [
                    fill("buy", 1, "BUY", "100", "1"),
                    fill("sell", 2, "SELL", "100", "1"),
                ],
                quantity_tolerance=domain.Quantity("0"),
            )

    def test_different_economic_order_id_is_rejected(self):
        with self.assertRaises(domain.FillEconomicOrderMismatchError):
            reduce_economic_order(
                domain.Quantity("1"),
                [
                    fill(
                        "other-order",
                        1,
                        "BUY",
                        "100",
                        "1",
                        economic_order_id="economic-order-2",
                    )
                ],
                quantity_tolerance=domain.Quantity("0"),
            )

    def test_different_instrument_id_is_rejected(self):
        with self.assertRaises(domain.FillInstrumentMismatchError):
            reduce_economic_order(
                domain.Quantity("1"),
                [
                    fill(
                        "other-instrument",
                        1,
                        "BUY",
                        "100",
                        "1",
                        instrument_id="ETH-USDT",
                    )
                ],
                quantity_tolerance=domain.Quantity("0"),
            )

    def test_different_account_scope_is_rejected(self):
        with self.assertRaises(domain.FillAccountScopeMismatchError):
            reduce_economic_order(
                domain.Quantity("1"),
                [
                    fill(
                        "other-account",
                        1,
                        "BUY",
                        "100",
                        "1",
                        account_scope="account-2",
                    )
                ],
                quantity_tolerance=domain.Quantity("0"),
            )

    def test_scope_and_expected_side_change_canonical_hash(self):
        buy = reduce_economic_order(
            domain.Quantity("1"),
            [fill("same-event", 1, "BUY", "100", "1")],
            quantity_tolerance=domain.Quantity("0"),
        )
        other_order = reduce_economic_order(
            domain.Quantity("1"),
            [
                fill(
                    "same-event",
                    1,
                    "BUY",
                    "100",
                    "1",
                    economic_order_id="economic-order-2",
                )
            ],
            scope=economic_scope(economic_order_id="economic-order-2"),
            quantity_tolerance=domain.Quantity("0"),
        )
        sell = reduce_economic_order(
            domain.Quantity("1"),
            [fill("same-event", 1, "SELL", "100", "1")],
            scope=economic_scope(expected_side="SELL"),
            quantity_tolerance=domain.Quantity("0"),
        )
        self.assertEqual(
            buy.cumulative_filled_quantity,
            other_order.cumulative_filled_quantity,
        )
        self.assertNotEqual(buy.stable_hash(), other_order.stable_hash())
        self.assertNotEqual(buy.stable_hash(), sell.stable_hash())


class PositionReducerTests(unittest.TestCase):
    def test_empty_position_opens_long_and_short(self):
        long_state = reduce_position([fill("long", 1, "BUY", "100", "2")])
        short_state = reduce_position([fill("short", 1, "SELL", "100", "2")])
        self.assertEqual(long_state.signed_quantity.to_string(), "2")
        self.assertEqual(short_state.signed_quantity.to_string(), "-2")
        self.assertEqual(long_state.average_entry_price.to_string(), "100")
        self.assertEqual(short_state.average_entry_price.to_string(), "100")

    def test_long_and_short_additions_use_weighted_average_entry(self):
        long_state = reduce_position(
            [fill("one", 1, "BUY", "100", "1"), fill("two", 2, "BUY", "110", "1")]
        )
        short_state = reduce_position(
            [fill("one", 1, "SELL", "100", "2"), fill("two", 2, "SELL", "90", "1")]
        )
        self.assertEqual(long_state.signed_quantity.to_string(), "2")
        self.assertEqual(long_state.average_entry_price.to_string(), "105")
        self.assertEqual(short_state.signed_quantity.to_string(), "-3")
        self.assertEqual(
            short_state.average_entry_price.to_string(), "96.666666666666666667"
        )

    def test_long_and_short_partial_closes_realize_gross_pnl(self):
        long_state = reduce_position(
            [fill("open", 1, "BUY", "100", "2"), fill("close", 2, "SELL", "110", "0.5")]
        )
        short_state = reduce_position(
            [fill("open", 1, "SELL", "100", "2"), fill("close", 2, "BUY", "90", "0.5")]
        )
        for state, signed in ((long_state, "1.5"), (short_state, "-1.5")):
            self.assertEqual(state.signed_quantity.to_string(), signed)
            self.assertEqual(state.average_entry_price.to_string(), "100")
            self.assertEqual(state.realized_pnl.to_string(), "5")
            self.assertEqual(state.closed_quantity.to_string(), "0.5")

    def test_complete_close_is_flat_and_clears_average_entry(self):
        state = reduce_position(
            [fill("open", 1, "BUY", "100", "2"), fill("close", 2, "SELL", "110", "2")]
        )
        self.assertEqual(state.signed_quantity.to_string(), "0")
        self.assertIsNone(state.average_entry_price)
        self.assertEqual(state.realized_pnl.to_string(), "20")
        self.assertEqual(state.closed_quantity.to_string(), "2")

    def test_position_flip_closes_old_side_and_opens_remainder_at_fill_price(self):
        long_to_short = reduce_position(
            [fill("open", 1, "BUY", "100", "2"), fill("flip", 2, "SELL", "110", "3")]
        )
        short_to_long = reduce_position(
            [fill("open", 1, "SELL", "100", "2"), fill("flip", 2, "BUY", "90", "3")]
        )
        self.assertEqual(long_to_short.signed_quantity.to_string(), "-1")
        self.assertEqual(long_to_short.average_entry_price.to_string(), "110")
        self.assertEqual(long_to_short.realized_pnl.to_string(), "20")
        self.assertEqual(short_to_long.signed_quantity.to_string(), "1")
        self.assertEqual(short_to_long.average_entry_price.to_string(), "90")
        self.assertEqual(short_to_long.realized_pnl.to_string(), "20")

    def test_gross_pnl_is_separate_from_quote_base_and_third_asset_fees(self):
        events = [
            fill("open", 1, "BUY", "100", "1", fees=(fee("BTC", "0.001"),)),
            fill(
                "close",
                2,
                "SELL",
                "110",
                "1",
                fees=(fee("USDT", "1"), fee("BNB", "0.01")),
            ),
        ]
        state = reduce_position(events)
        totals = domain.aggregate_fees_by_asset(fee for event in events for fee in event.fees)
        self.assertEqual(state.realized_pnl.to_string(), "10")
        self.assertEqual(
            [(item.asset, item.amount.to_string()) for item in totals],
            [("BNB", "0.01"), ("BTC", "0.001"), ("USDT", "1")],
        )

    def test_realized_and_unrealized_pnl_functions_cover_long_and_short(self):
        self.assertEqual(
            domain.calculate_realized_pnl(
                "LONG", domain.Price("100"), domain.Price("110"), domain.Quantity("2")
            ).to_string(),
            "20",
        )
        self.assertEqual(
            domain.calculate_realized_pnl(
                "SHORT", domain.Price("100"), domain.Price("90"), domain.Quantity("2")
            ).to_string(),
            "20",
        )
        self.assertEqual(
            domain.calculate_unrealized_pnl(
                domain.SignedQuantity("2"), domain.Price("100"), domain.Price("105")
            ).to_string(),
            "10",
        )
        self.assertEqual(
            domain.calculate_unrealized_pnl(
                domain.SignedQuantity("-2"), domain.Price("100"), domain.Price("105")
            ).to_string(),
            "-10",
        )

    def test_position_replay_is_deterministic_for_same_event_set(self):
        events = [
            fill("one", 1, "BUY", "100", "1"),
            fill("two", 2, "BUY", "110", "1"),
            fill("three", 3, "SELL", "120", "0.5"),
        ]
        first = reduce_position(events)
        replay = reduce_position(reversed(events))
        self.assertEqual(first, replay)
        self.assertEqual(first.stable_hash(), replay.stable_hash())

    def test_position_allows_multiple_orders_and_rule_versions(self):
        state = reduce_position(
            [
                fill(
                    "first-order",
                    1,
                    "BUY",
                    "100",
                    "1",
                    economic_order_id="economic-order-1",
                    instrument_rule_version="rules-v1",
                ),
                fill(
                    "second-order",
                    2,
                    "BUY",
                    "110",
                    "1",
                    economic_order_id="economic-order-2",
                    instrument_rule_version="rules-v2",
                ),
            ]
        )
        self.assertEqual(state.signed_quantity.to_string(), "2")
        self.assertEqual(state.average_entry_price.to_string(), "105")

    def test_position_rejects_cross_account_fills(self):
        with self.assertRaises(domain.FillAccountScopeMismatchError):
            reduce_position(
                [
                    fill("first", 1, "BUY", "100", "1"),
                    fill(
                        "second",
                        2,
                        "BUY",
                        "100",
                        "1",
                        account_scope="account-2",
                    ),
                ]
            )

    def test_position_rejects_cross_instrument_fills(self):
        with self.assertRaises(domain.FillInstrumentMismatchError):
            reduce_position(
                [
                    fill("first", 1, "BUY", "100", "1"),
                    fill(
                        "second",
                        2,
                        "BUY",
                        "100",
                        "1",
                        instrument_id="ETH-USDT",
                    ),
                ]
            )


if __name__ == "__main__":
    unittest.main()
