"""Offline evidence tests for Decimal-only Gate visible-depth estimates."""

import importlib.util
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def load():
    names = (
        "app",
        "app.domain",
        "app.domain.decimal_values",
        "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts",
        "app.domain.gate_order_book_stream_contracts",
        "app.domain.gate_order_book_materialization_contracts",
        "app.domain.gate_order_book_stream_session_contracts",
        "app.domain.gate_vertical_read_contracts",
        "app.domain.gate_order_book_execution_contracts",
    )
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        paths = {
            names[2]: ROOT / "app/domain/decimal_values.py",
            names[3]: ROOT / "app/domain/multi_asset_capability_contracts.py",
            names[4]: ROOT / "app/domain/gate_market_read_contracts.py",
            names[5]: ROOT / "app/domain/gate_order_book_stream_contracts.py",
            names[6]: ROOT / "app/domain/gate_order_book_materialization_contracts.py",
            names[7]: ROOT / "app/domain/gate_order_book_stream_session_contracts.py",
            names[8]: ROOT / "app/domain/gate_vertical_read_contracts.py",
            names[9]: ROOT / "app/domain/gate_order_book_execution_contracts.py",
        }
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return tuple(sys.modules[name] for name in names[2:])
    finally:
        for name in reversed(names):
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


DECIMAL, CAP, MARKET, STREAM, MATERIAL, SESSION, VERTICAL, EXEC = load()


def rule(*, market_type=None, instrument_id="BTC_USDT", contract_size=None, step="1", minimum="1"):
    return VERTICAL.GateInstrumentRuleSnapshot(
        venue_id="gate",
        market_type=CAP.AssetMarketType.SPOT if market_type is None else market_type,
        instrument_id=instrument_id,
        tick_size=Decimal("0.1"),
        quantity_step=Decimal(step),
        minimum_quantity=Decimal(minimum),
        minimum_notional=Decimal("0"),
        rule_version="gate-book-rule-v1",
        observed_at=UTC,
        contract_size=None if contract_size is None else Decimal(contract_size),
    )


def snapshot(*, market_type=None, instrument_id="BTC_USDT", bids=None, asks=None, sequence=100, observed=UTC):
    return MARKET.GateOrderBookSnapshot(
        market_type=CAP.AssetMarketType.SPOT if market_type is None else market_type,
        instrument_id=instrument_id,
        bids=(
            MARKET.GateOrderBookLevel(Decimal("99"), Decimal("1")),
            MARKET.GateOrderBookLevel(Decimal("98"), Decimal("2")),
        ) if bids is None else bids,
        asks=(
            MARKET.GateOrderBookLevel(Decimal("100"), Decimal("1")),
            MARKET.GateOrderBookLevel(Decimal("101"), Decimal("2")),
        ) if asks is None else asks,
        occurred_at=observed,
        observed_at=observed,
        sequence=sequence,
        source_event_id=f"rest:{sequence}",
        snapshot_id="estimate-snapshot-1",
        rule_version="gate-book-rule-v1",
        evidence_hash="a" * 64,
        depth_limit=20,
    )


def session(*, market_type=None, instrument_id="BTC_USDT", book=None, as_of=UTC):
    market_type = CAP.AssetMarketType.SPOT if market_type is None else market_type
    source = snapshot(market_type=market_type, instrument_id=instrument_id) if book is None else book
    subscription = STREAM.GateOrderBookStreamSubscription(
        market_type=market_type,
        instrument_id=instrument_id,
        snapshot_id=source.snapshot_id,
        rule_version=source.rule_version,
        depth_limit=20,
        update_interval="20ms",
    )
    return SESSION.gate_order_book_stream_session_from_snapshot(
        source,
        subscription,
        as_of=as_of,
        max_staleness=timedelta(seconds=30),
    )


def policy(*, seconds=30, version="visible-depth-v1"):
    return EXEC.GateOrderBookExecutionPolicy(version, timedelta(seconds=seconds))


def request(*, market_type=None, instrument_id="BTC_USDT", side=None, quantity="2", instrument_rule=None, as_of=UTC + timedelta(seconds=1), price_protection=None, execution_policy=None):
    market_type = CAP.AssetMarketType.SPOT if market_type is None else market_type
    return EXEC.GateOrderBookExecutionRequest(
        market_type=market_type,
        instrument_id=instrument_id,
        side=EXEC.GateOrderBookExecutionSide.BUY if side is None else side,
        quantity=DECIMAL.Quantity(quantity),
        quantity_unit=(
            EXEC.GateOrderBookQuantityUnit.BASE_ASSET
            if market_type is CAP.AssetMarketType.SPOT
            else EXEC.GateOrderBookQuantityUnit.CONTRACT
        ),
        instrument_rule=rule(market_type=market_type, instrument_id=instrument_id) if instrument_rule is None else instrument_rule,
        policy=policy() if execution_policy is None else execution_policy,
        as_of=as_of,
        price_protection=None if price_protection is None else DECIMAL.Price(price_protection),
    )


class GateOrderBookExecutionContractTests(unittest.TestCase):
    def test_spot_buy_and_sell_consume_canonical_levels_with_decimal_vwap(self):
        current = session()
        buy = EXEC.estimate_gate_order_book_execution(current, request(quantity="2"))
        self.assertEqual(buy.disposition, EXEC.GateOrderBookExecutionDisposition.FULLY_FILLABLE)
        self.assertEqual(buy.filled_quantity.to_string(), "2")
        self.assertEqual(buy.remaining_quantity.to_string(), "0")
        self.assertEqual(buy.quote_amount.to_string(), "201")
        self.assertEqual(buy.weighted_average_price.to_string(), "100.5")
        self.assertEqual(buy.best_price.to_string(), "100")
        self.assertEqual(buy.worst_price.to_string(), "101")
        self.assertEqual(buy.spread.to_string(), "1")
        self.assertEqual([item.consumed_quantity.to_string() for item in buy.consumed_levels], ["1", "1"])
        self.assertEqual(buy.estimate_fingerprint, EXEC.estimate_gate_order_book_execution(current, request(quantity="2")).estimate_fingerprint)

        sell = EXEC.estimate_gate_order_book_execution(
            current,
            request(side=EXEC.GateOrderBookExecutionSide.SELL, quantity="2"),
        )
        self.assertEqual(sell.disposition, EXEC.GateOrderBookExecutionDisposition.FULLY_FILLABLE)
        self.assertEqual(sell.quote_amount.to_string(), "197")
        self.assertEqual(sell.weighted_average_price.to_string(), "98.5")
        self.assertEqual(sell.best_price.to_string(), "99")
        self.assertEqual(sell.worst_price.to_string(), "98")

    def test_insufficient_visible_depth_and_price_protection_remain_explicit(self):
        current = session()
        insufficient = EXEC.estimate_gate_order_book_execution(current, request(quantity="4"))
        self.assertEqual(insufficient.disposition, EXEC.GateOrderBookExecutionDisposition.INSUFFICIENT_LIQUIDITY)
        self.assertEqual(insufficient.filled_quantity.to_string(), "3")
        self.assertEqual(insufficient.remaining_quantity.to_string(), "1")
        self.assertEqual(insufficient.quote_amount.to_string(), "302")

        protected = EXEC.estimate_gate_order_book_execution(current, request(quantity="2", price_protection="100"))
        self.assertEqual(protected.disposition, EXEC.GateOrderBookExecutionDisposition.PRICE_PROTECTION_REJECTED)
        self.assertEqual(protected.filled_quantity.to_string(), "1")
        self.assertEqual(protected.remaining_quantity.to_string(), "1")
        self.assertEqual(protected.quote_amount.to_string(), "100")

        rejected = EXEC.estimate_gate_order_book_execution(current, request(quantity="1", price_protection="99.9"))
        self.assertEqual(rejected.disposition, EXEC.GateOrderBookExecutionDisposition.PRICE_PROTECTION_REJECTED)
        self.assertEqual(rejected.filled_quantity.to_string(), "0")
        self.assertEqual(rejected.remaining_quantity.to_string(), "1")
        self.assertEqual(rejected.quote_amount.to_string(), "0")
        self.assertIsNone(rejected.weighted_average_price)
        self.assertIsNone(rejected.best_price)

    def test_stale_or_cross_scope_session_cannot_be_turned_into_execution_evidence(self):
        current = session()
        stale = EXEC.estimate_gate_order_book_execution(
            current,
            request(as_of=UTC + timedelta(seconds=31)),
        )
        self.assertEqual(stale.disposition, EXEC.GateOrderBookExecutionDisposition.UNHEALTHY_ORDER_BOOK)
        self.assertEqual(stale.filled_quantity.to_string(), "0")
        self.assertEqual(stale.remaining_quantity.to_string(), "2")
        self.assertFalse(stale.consumed_levels)
        self.assertIsNone(stale.spread)

        with self.assertRaises(EXEC.GateOrderBookExecutionScopeConflict):
            EXEC.estimate_gate_order_book_execution(
                current,
                request(instrument_id="ETH_USDT", instrument_rule=rule(instrument_id="ETH_USDT")),
            )

    def test_perpetual_requires_contract_multiplier_and_uses_contract_quantity_units(self):
        market_type = CAP.AssetMarketType.PERPETUAL
        current = session(market_type=market_type)
        with self.assertRaises(EXEC.GateOrderBookExecutionError):
            request(market_type=market_type)
        perpetual_rule = rule(market_type=market_type, contract_size="0.001")
        estimate = EXEC.estimate_gate_order_book_execution(
            current,
            request(market_type=market_type, quantity="2", instrument_rule=perpetual_rule),
        )
        self.assertEqual(estimate.disposition, EXEC.GateOrderBookExecutionDisposition.FULLY_FILLABLE)
        self.assertEqual(estimate.quote_amount.to_string(), "0.201")
        self.assertEqual(estimate.weighted_average_price.to_string(), "100.5")
        self.assertEqual(estimate.request.quantity_unit, EXEC.GateOrderBookQuantityUnit.CONTRACT)

    def test_request_rejects_float_wrong_unit_and_immutable_rule_step_or_minimum_violations(self):
        with self.assertRaises(DECIMAL.DecimalInputTypeError):
            DECIMAL.Quantity(1.0)
        with self.assertRaises(EXEC.GateOrderBookExecutionError):
            EXEC.GateOrderBookExecutionRequest(
                market_type=CAP.AssetMarketType.SPOT,
                instrument_id="BTC_USDT",
                side=EXEC.GateOrderBookExecutionSide.BUY,
                quantity=DECIMAL.Quantity("1"),
                quantity_unit=EXEC.GateOrderBookQuantityUnit.CONTRACT,
                instrument_rule=rule(),
                policy=policy(),
                as_of=UTC + timedelta(seconds=1),
            )
        with self.assertRaises(EXEC.GateOrderBookExecutionError):
            request(quantity="0.5", instrument_rule=rule(step="0.25", minimum="1"))
        with self.assertRaises(EXEC.GateOrderBookExecutionError):
            request(quantity="1.1", instrument_rule=rule(step="0.25", minimum="1"))

    def test_request_and_estimate_fingerprints_change_only_with_economic_or_policy_evidence(self):
        current = session()
        left = request(quantity="2")
        changed_quantity = request(quantity="3")
        changed_protection = request(quantity="2", price_protection="101")
        changed_policy = request(quantity="2", execution_policy=policy(version="visible-depth-v2"))
        self.assertNotEqual(left.request_fingerprint, changed_quantity.request_fingerprint)
        self.assertNotEqual(left.request_fingerprint, changed_protection.request_fingerprint)
        self.assertNotEqual(left.request_fingerprint, changed_policy.request_fingerprint)
        self.assertNotEqual(
            EXEC.estimate_gate_order_book_execution(current, left).estimate_fingerprint,
            EXEC.estimate_gate_order_book_execution(current, changed_quantity).estimate_fingerprint,
        )

    def test_estimate_rejects_tampered_level_quote_or_vwap_evidence(self):
        estimate = EXEC.estimate_gate_order_book_execution(session(), request(quantity="2"))
        bad_level = replace(estimate.consumed_levels[0], quote_amount=DECIMAL.QuoteAmount("1"))
        with self.assertRaises(EXEC.GateOrderBookExecutionError):
            replace(estimate, consumed_levels=(bad_level, *estimate.consumed_levels[1:]))
        with self.assertRaises(EXEC.GateOrderBookExecutionError):
            replace(estimate, weighted_average_price=DECIMAL.Price("100"))

    def test_contract_source_has_no_transport_account_database_or_order_submit_capability(self):
        source = (ROOT / "app/domain/gate_order_book_execution_contracts.py").read_text(encoding="utf-8")
        for forbidden in (
            "urlopen", "requests", "websocket", "socket.", "api_key", "commit(", "rollback(",
            "submit_order", "place_order", "cancel_order", "connect(",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
