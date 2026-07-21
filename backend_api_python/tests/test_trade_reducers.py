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
):
    return domain.FillEvent(
        event_id=event_id,
        sequence=sequence,
        side=side,
        price=domain.Price(price),
        quantity=domain.Quantity(quantity),
        quote_quantity=(
            domain.QuoteAmount(quote_quantity) if quote_quantity is not None else None
        ),
        fees=tuple(fees),
        instrument_rule_version="fixture-rules-v1",
    )


def fee(asset, amount):
    return domain.Fee(asset, domain.FeeAmount(amount))


class EconomicOrderReducerTests(unittest.TestCase):
    def test_one_hundred_partial_fills_accumulate_exactly(self):
        fills = [
            fill(f"fill-{index}", index, "BUY", "10", "0.01")
            for index in range(100)
        ]
        result = domain.reduce_economic_order(
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
            domain.reduce_economic_order(
                domain.Quantity("2"),
                [event, event],
                quantity_tolerance=domain.Quantity("0"),
            )

    def test_sequence_conflict_is_rejected(self):
        with self.assertRaises(domain.FillSequenceConflictError):
            domain.reduce_economic_order(
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
        first = domain.reduce_economic_order(
            domain.Quantity("1"), ordered, quantity_tolerance=domain.Quantity("0")
        )
        replay = domain.reduce_economic_order(
            domain.Quantity("1"), reversed(ordered), quantity_tolerance=domain.Quantity("0")
        )
        self.assertEqual(first, replay)
        self.assertEqual(first.stable_hash(), replay.stable_hash())
        self.assertEqual(first.applied_event_ids, ("one", "two"))

    def test_overfill_is_reported_without_truncation(self):
        result = domain.reduce_economic_order(
            domain.Quantity("1"),
            [fill("overfill", 1, "BUY", "100", "1.2")],
            quantity_tolerance=domain.Quantity("0"),
        )
        self.assertEqual(result.cumulative_filled_quantity.to_string(), "1.2")
        self.assertEqual(result.remaining_quantity.to_string(), "0")
        self.assertEqual(result.overfill_quantity.to_string(), "0.2")
        self.assertTrue(result.reached_target_within_tolerance)

    def test_tolerance_is_explicit_and_cannot_exceed_target(self):
        result = domain.reduce_economic_order(
            domain.Quantity("1"),
            [fill("near", 1, "BUY", "100", "0.99")],
            quantity_tolerance=domain.Quantity("0.01"),
        )
        self.assertTrue(result.reached_target_within_tolerance)
        exact = domain.reduce_economic_order(
            domain.Quantity("1"),
            [fill("exact", 2, "BUY", "100", "1")],
            quantity_tolerance=domain.Quantity("0"),
        )
        tolerant = domain.reduce_economic_order(
            domain.Quantity("1"),
            [fill("exact", 2, "BUY", "100", "1")],
            quantity_tolerance=domain.Quantity("0.1"),
        )
        self.assertNotEqual(exact.stable_hash(), tolerant.stable_hash())
        with self.assertRaises(domain.ReducerContractError):
            domain.reduce_economic_order(
                domain.Quantity("1"),
                [],
                quantity_tolerance=domain.Quantity("1.1"),
            )

    def test_quote_facts_and_weighted_price_have_separate_semantics(self):
        result = domain.reduce_economic_order(
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
        result = domain.reduce_economic_order(
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


class PositionReducerTests(unittest.TestCase):
    def test_empty_position_opens_long_and_short(self):
        long_state = domain.reduce_position([fill("long", 1, "BUY", "100", "2")])
        short_state = domain.reduce_position([fill("short", 1, "SELL", "100", "2")])
        self.assertEqual(long_state.signed_quantity.to_string(), "2")
        self.assertEqual(short_state.signed_quantity.to_string(), "-2")
        self.assertEqual(long_state.average_entry_price.to_string(), "100")
        self.assertEqual(short_state.average_entry_price.to_string(), "100")

    def test_long_and_short_additions_use_weighted_average_entry(self):
        long_state = domain.reduce_position(
            [fill("one", 1, "BUY", "100", "1"), fill("two", 2, "BUY", "110", "1")]
        )
        short_state = domain.reduce_position(
            [fill("one", 1, "SELL", "100", "2"), fill("two", 2, "SELL", "90", "1")]
        )
        self.assertEqual(long_state.signed_quantity.to_string(), "2")
        self.assertEqual(long_state.average_entry_price.to_string(), "105")
        self.assertEqual(short_state.signed_quantity.to_string(), "-3")
        self.assertEqual(
            short_state.average_entry_price.to_string(), "96.666666666666666667"
        )

    def test_long_and_short_partial_closes_realize_gross_pnl(self):
        long_state = domain.reduce_position(
            [fill("open", 1, "BUY", "100", "2"), fill("close", 2, "SELL", "110", "0.5")]
        )
        short_state = domain.reduce_position(
            [fill("open", 1, "SELL", "100", "2"), fill("close", 2, "BUY", "90", "0.5")]
        )
        for state, signed in ((long_state, "1.5"), (short_state, "-1.5")):
            self.assertEqual(state.signed_quantity.to_string(), signed)
            self.assertEqual(state.average_entry_price.to_string(), "100")
            self.assertEqual(state.realized_pnl.to_string(), "5")
            self.assertEqual(state.closed_quantity.to_string(), "0.5")

    def test_complete_close_is_flat_and_clears_average_entry(self):
        state = domain.reduce_position(
            [fill("open", 1, "BUY", "100", "2"), fill("close", 2, "SELL", "110", "2")]
        )
        self.assertEqual(state.signed_quantity.to_string(), "0")
        self.assertIsNone(state.average_entry_price)
        self.assertEqual(state.realized_pnl.to_string(), "20")
        self.assertEqual(state.closed_quantity.to_string(), "2")

    def test_position_flip_closes_old_side_and_opens_remainder_at_fill_price(self):
        long_to_short = domain.reduce_position(
            [fill("open", 1, "BUY", "100", "2"), fill("flip", 2, "SELL", "110", "3")]
        )
        short_to_long = domain.reduce_position(
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
        state = domain.reduce_position(events)
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
        first = domain.reduce_position(events)
        replay = domain.reduce_position(reversed(events))
        self.assertEqual(first, replay)
        self.assertEqual(first.stable_hash(), replay.stable_hash())


if __name__ == "__main__":
    unittest.main()
