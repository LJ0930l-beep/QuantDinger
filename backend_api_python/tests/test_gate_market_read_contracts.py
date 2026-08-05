import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contracts():
    names = ["app", "app.domain", "app.domain.multi_asset_capability_contracts", "app.domain.gate_market_read_contracts"]
    old = {name: sys.modules.get(name) for name in names}
    sentinel = object()
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        multi = _load("app.domain.multi_asset_capability_contracts", ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        gate = _load("app.domain.gate_market_read_contracts", ROOT / "app" / "domain" / "gate_market_read_contracts.py")
        return gate, multi
    finally:
        for name in reversed(names):
            value = old[name]
            if value is None: sys.modules.pop(name, None)
            else: sys.modules[name] = value


GATE, MULTI = _contracts()


def trade(**changes):
    facts = dict(market_type=MULTI.AssetMarketType.SPOT, instrument_id="BTC_USDT", side=GATE.GateTradeSide.BUY,
                 price=Decimal("100"), quantity=Decimal("0.2"), occurred_at=UTC, observed_at=UTC,
                 sequence=1, source_event_id="trade-1", snapshot_id="snapshot-1", rule_version="rules-1", evidence_hash="hash-1")
    facts.update(changes)
    return GATE.GateTradeFact(**facts)


class GateMarketReadContractTests(unittest.TestCase):
    def test_trade_is_decimal_strict_and_replayable(self):
        self.assertEqual(GATE.gate_market_fingerprint(trade(price=Decimal("100.00"))), GATE.gate_market_fingerprint(trade(price=Decimal("100"))))
        with self.assertRaises(GATE.GateMarketContractError): trade(price=1.0)

    def test_common_timestamps_and_sequence_fail_closed(self):
        with self.assertRaises(GATE.GateMarketContractError): trade(sequence=-1)
        with self.assertRaises(GATE.GateMarketContractError): trade(observed_at=UTC - timedelta(seconds=1))
        with self.assertRaises(GATE.GateMarketContractError): trade(occurred_at=datetime(2026, 1, 1))

    def test_candle_bounds_and_identity_are_typed(self):
        candle = GATE.GateCandleFact(market_type=MULTI.AssetMarketType.SPOT, instrument_id="BTC_USDT", interval="1m",
            open_time=UTC, close_time=UTC + timedelta(minutes=1), open_price=Decimal("10"), high_price=Decimal("12"),
            low_price=Decimal("9"), close_price=Decimal("11"), volume=Decimal("2"), occurred_at=UTC, observed_at=UTC,
            sequence=2, source_event_id="candle-1", snapshot_id="snapshot-1", rule_version="rules-1", evidence_hash="hash-2")
        self.assertEqual(GATE.gate_market_identity(candle), ("gate", "spot", "BTC_USDT", 2))
        with self.assertRaises(GATE.GateMarketContractError):
            GATE.GateCandleFact(**{**candle.__dict__, "high_price": Decimal("8")})

    def test_ticker_and_orderbook_reject_unsafe_shapes(self):
        ticker = GATE.GateTickerFact(market_type=MULTI.AssetMarketType.SPOT, instrument_id="BTC_USDT", bid_price=Decimal("9"), ask_price=Decimal("10"), last_price=Decimal("9.5"), occurred_at=UTC, observed_at=UTC, sequence=3, source_event_id="ticker-1", snapshot_id="s", rule_version="r", evidence_hash="h")
        self.assertEqual(ticker.kind, GATE.GateMarketKind.TICKER)
        level = GATE.GateOrderBookLevel(Decimal("9"), Decimal("1"))
        ask = GATE.GateOrderBookLevel(Decimal("10"), Decimal("1"))
        book = GATE.GateOrderBookSnapshot(market_type=MULTI.AssetMarketType.SPOT, instrument_id="BTC_USDT", bids=(level,), asks=(ask,), occurred_at=UTC, observed_at=UTC, sequence=4, source_event_id="book-1", snapshot_id="s", rule_version="r", evidence_hash="h")
        self.assertEqual(book.kind, GATE.GateMarketKind.ORDER_BOOK)
        with self.assertRaises(GATE.GateMarketContractError): GATE.GateOrderBookSnapshot(**{**book.__dict__, "asks": (level,)})

    def test_mark_index_and_funding_scope(self):
        mark = GATE.GatePriceFact(market_type=MULTI.AssetMarketType.PERPETUAL, instrument_id="BTC_USDT", price=Decimal("100"), occurred_at=UTC, observed_at=UTC, sequence=5, source_event_id="mark", snapshot_id="s", rule_version="r", evidence_hash="h", kind=GATE.GateMarketKind.MARK_PRICE)
        self.assertEqual(mark.kind, GATE.GateMarketKind.MARK_PRICE)
        funding = GATE.GateFundingFact(market_type=MULTI.AssetMarketType.PERPETUAL, instrument_id="BTC_USDT", funding_rate=Decimal("0.0001"), funding_interval="8h", next_funding_at=UTC + timedelta(hours=8), occurred_at=UTC, observed_at=UTC, sequence=6, source_event_id="funding", snapshot_id="s", rule_version="r", evidence_hash="h")
        self.assertEqual(funding.kind, GATE.GateMarketKind.FUNDING)
        with self.assertRaises(GATE.GateMarketContractError):
            GATE.GateFundingFact(**{**funding.__dict__, "market_type": MULTI.AssetMarketType.SPOT})

    def test_scope_and_sequence_change_identity(self):
        self.assertNotEqual(GATE.gate_market_identity(trade(sequence=1)), GATE.gate_market_identity(trade(sequence=2)))
        self.assertNotEqual(GATE.gate_market_fingerprint(trade(instrument_id="ETH_USDT")), GATE.gate_market_fingerprint(trade(instrument_id="BTC_USDT")))


if __name__ == "__main__":
    unittest.main()
