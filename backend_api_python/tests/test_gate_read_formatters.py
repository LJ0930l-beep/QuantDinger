import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    names = ["app", "app.domain", "app.domain.multi_asset_capability_contracts", "app.domain.gate_vertical_read_contracts", "app.domain.gate_read_formatters"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name, path in ((names[2], ROOT / "app/domain/multi_asset_capability_contracts.py"), (names[3], ROOT / "app/domain/gate_vertical_read_contracts.py"), (names[4], ROOT / "app/domain/gate_read_formatters.py")):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[4]], sys.modules[names[2]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, C = load()


class GateReadFormatterTests(unittest.TestCase):
    def test_balance_formatter_is_decimal_and_scoped(self):
        rows = M.normalize_gate_balances([{"currency": "USDT", "total": "10.000", "available": "9", "locked": "1"}], market_type=C.AssetMarketType.SPOT, account_scope="paper", valuation_ccy="usdt", observed_at=NOW, source_event_prefix="balance", evidence_hash_prefix="hash")
        self.assertEqual(rows[0].asset, "USDT"); self.assertEqual(rows[0].total, Decimal("10")); self.assertEqual(rows[0].available + rows[0].locked, rows[0].total)

    def test_spot_balance_derives_total_from_official_account_shape(self):
        rows = M.normalize_gate_balances([{"currency": "USDT", "available": "10.000000000000000000", "locked": "2"}], market_type=C.AssetMarketType.SPOT, account_scope="testnet", valuation_ccy="usdt", observed_at=NOW, source_event_prefix="balance", evidence_hash_prefix="hash")
        self.assertEqual(rows[0].total, Decimal("12.000000000000000000"))
        self.assertEqual(rows[0].available, Decimal("10"))
        self.assertEqual(rows[0].locked, Decimal("2"))

    def test_spot_balance_without_available_or_locked_fails_closed(self):
        with self.assertRaises(M.GateReadPayloadError):
            M.normalize_gate_balances([{"currency": "USDT", "locked": "2"}], market_type=C.AssetMarketType.SPOT, account_scope="testnet", valuation_ccy="usdt", observed_at=NOW, source_event_prefix="balance", evidence_hash_prefix="hash")
        with self.assertRaises(M.GateReadPayloadError):
            M.normalize_gate_balances([{"currency": "USDT", "available": "10"}], market_type=C.AssetMarketType.SPOT, account_scope="testnet", valuation_ccy="usdt", observed_at=NOW, source_event_prefix="balance", evidence_hash_prefix="hash")

    def test_position_formatter_is_perpetual_only_and_typed(self):
        rows = M.normalize_gate_positions([{"contract": "BTC_USDT", "side": "long", "size": "1", "entry_price": "100", "mark_price": "101", "leverage": "3", "margin_mode": "cross", "unrealized_pnl": "1.250000000000000000", "realized_pnl": "-0.25", "funding_pnl": "0.01"}], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW, source_event_prefix="position")
        self.assertEqual(rows[0].instrument_id, "BTC_USDT"); self.assertEqual(rows[0].quantity, Decimal("1"))
        self.assertEqual(rows[0].unrealized_pnl, Decimal("1.25"))
        self.assertEqual(rows[0].realized_pnl, Decimal("-0.25"))
        self.assertEqual(rows[0].funding_pnl, Decimal("0.01"))
        with self.assertRaises(M.GateReadPayloadError): M.normalize_gate_positions([], market_type=C.AssetMarketType.SPOT, account_scope="paper", observed_at=NOW, source_event_prefix="position")

    def test_gate_futures_signed_positions_skip_zero_rows_and_use_gate_fields(self):
        rows = M.normalize_gate_positions([
            {"contract": "BTC_USDT", "size": "0", "entry_price": "0", "mark_price": "101", "leverage": "0", "mode": "dual_short", "pos_margin_mode": "cross"},
            {"contract": "ETH_USDT", "size": "-2", "entry_price": "100", "mark_price": "101", "leverage": "3", "mode": "dual_short", "pos_margin_mode": "cross"},
        ], market_type=C.AssetMarketType.PERPETUAL, account_scope="testnet", observed_at=NOW, source_event_prefix="position")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].side, M.GatePositionSide.SHORT)
        self.assertEqual(rows[0].quantity, Decimal("2"))
        self.assertEqual(rows[0].margin_mode, M.GateMarginMode.CROSS)

    def test_gate_cross_position_uses_positive_lever_when_leverage_is_zero(self):
        rows = M.normalize_gate_positions([{
            "contract": "BTC_USDT", "size": "-1", "entry_price": "100", "mark_price": "101",
            "leverage": "0", "lever": "100", "mode": "dual_short", "pos_margin_mode": "cross",
        }], market_type=C.AssetMarketType.PERPETUAL, account_scope="testnet", observed_at=NOW, source_event_prefix="position")
        self.assertEqual(rows[0].leverage, Decimal("100"))

    def test_account_book_formatter_preserves_pnl_fee_and_funding_categories(self):
        rows = M.normalize_gate_account_book([
            {"id": "book-1", "type": "pnl", "change": "1.250000000000000000", "balance": "11.25", "time": "1767225600", "contract": "BTC_USDT", "trade_id": "trade-1"},
            {"id": "book-2", "type": "fee", "change": "-0.03", "balance": "11.22", "time": "1767225601", "text": "BTC_USDT:trade-1"},
            {"id": "book-3", "type": "fund", "change": "-0.01", "balance": "11.21", "time": "1767225602"},
        ], market_type=C.AssetMarketType.PERPETUAL, account_scope="testnet", observed_at=datetime(2026, 1, 1, 0, 0, 3, tzinfo=timezone.utc), source_event_prefix="account-book")
        self.assertEqual([row.change_type.value for row in rows], ["pnl", "fee", "fund"])
        self.assertEqual(rows[0].change, Decimal("1.25")); self.assertEqual(rows[1].change, Decimal("-0.03"))
        self.assertEqual(rows[0].instrument_id, "BTC_USDT"); self.assertEqual(rows[1].comment, "BTC_USDT:trade-1")
        with self.assertRaises(M.GateReadPayloadError):
            M.normalize_gate_account_book([{"id": "book-4", "type": "unknown", "change": "1", "balance": "1", "time": "1767225600"}], market_type=C.AssetMarketType.PERPETUAL, account_scope="testnet", observed_at=NOW, source_event_prefix="account-book")

    def test_instrument_formatter_and_missing_fields_fail_closed(self):
        rows = M.normalize_gate_instruments({"data": [{"name": "BTC_USDT", "order_price_round": "0.1", "order_size_increment": "0.001", "order_size_min": "0.001", "min_notional": "5"}]}, market_type=C.AssetMarketType.SPOT, observed_at=NOW, rule_version="gate-v1")
        self.assertEqual(rows[0].tick_size, Decimal("0.1"))
        self.assertIsNone(rows[0].leverage_min)
        with self.assertRaises(M.GateReadPayloadError): M.normalize_gate_instruments([{"name": "BTC_USDT"}], market_type=C.AssetMarketType.SPOT, observed_at=NOW, rule_version="gate-v1")

    def test_futures_instrument_formatter_preserves_contract_and_leverage_rules(self):
        rows = M.normalize_gate_instruments([{
            "name": "BTC_USDT", "order_price_round": "0.1", "order_size_increment": "0.001",
            "order_size_min": "0.001", "quanto_multiplier": "0.001",
            "leverage_min": "50", "leverage_max": "100",
        }], market_type=C.AssetMarketType.PERPETUAL, observed_at=NOW, rule_version="gate-futures-v2")
        self.assertEqual(rows[0].contract_size, Decimal("0.001"))
        self.assertEqual(rows[0].leverage_min, Decimal("50"))
        self.assertEqual(rows[0].leverage_max, Decimal("100"))

    def test_spot_precision_scales_are_explicitly_normalized(self):
        rows = M.normalize_gate_instruments([{
            "id": "BTC_USDT",
            "precision": 2,
            "amount_precision": 4,
            "min_base_amount": "0.0001",
            "min_quote_amount": "1",
        }], market_type=C.AssetMarketType.SPOT, observed_at=NOW, rule_version="gate-spot-v1")
        self.assertEqual(rows[0].tick_size, Decimal("0.01"))
        self.assertEqual(rows[0].quantity_step, Decimal("0.0001"))
        self.assertEqual(rows[0].minimum_quantity, Decimal("0.0001"))
        self.assertEqual(rows[0].minimum_notional, Decimal("1"))

    def test_futures_integer_contract_capability_has_unit_step(self):
        rows = M.normalize_gate_instruments([{
            "name": "BTC_USDT",
            "order_price_round": "0.1",
            "order_size_min": "1",
            "min_quote_amount": "1",
            "enable_decimal": False,
        }], market_type=C.AssetMarketType.PERPETUAL, observed_at=NOW, rule_version="gate-futures-v1")
        self.assertEqual(rows[0].quantity_step, Decimal("1"))

    def test_futures_contract_without_separate_notional_uses_explicit_zero_fact(self):
        rows = M.normalize_gate_instruments([{
            "name": "BTC_USDT", "order_price_round": "0.1", "order_size_min": "1",
            "enable_decimal": False,
        }], market_type=C.AssetMarketType.PERPETUAL, observed_at=NOW, rule_version="gate-futures-v1")
        self.assertEqual(rows[0].minimum_notional, Decimal("0"))

    def test_invalid_spot_precision_does_not_get_a_guessed_rule(self):
        with self.assertRaises(M.GateReadPayloadError):
            M.normalize_gate_instruments([{
                "id": "BTC_USDT",
                "precision": True,
                "amount_precision": 4,
                "min_base_amount": "0.0001",
                "min_quote_amount": "1",
            }], market_type=C.AssetMarketType.SPOT, observed_at=NOW, rule_version="gate-spot-v1")

    def test_error_mapping_never_returns_not_found(self):
        self.assertEqual(M.classify_gate_response_error(401), M.GateReadErrorKind.AUTH_OR_PERMISSION)
        self.assertEqual(M.classify_gate_response_error(429), M.GateReadErrorKind.RATE_LIMIT)
        self.assertEqual(M.classify_gate_response_error(503), M.GateReadErrorKind.TEMPORARY)
        self.assertEqual(M.classify_gate_response_error(400), M.GateReadErrorKind.INVALID_RESPONSE)
        with self.assertRaises(M.GateReadPayloadError): M.classify_gate_response_error("401")

    def test_order_and_fill_formatters_require_stable_ids_and_preserve_decimal_facts(self):
        orders = M.normalize_gate_orders([{
            "contract": "BTC_USDT", "id": "order-1", "client_order_id": "paper-1",
            "side": "buy", "status": "open", "size": "2.000", "filled": "1.0",
            "avg_deal_price": "100",
        }], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW, source_event_prefix="order")
        self.assertEqual(orders[0].exchange_order_id, "order-1")
        self.assertEqual(orders[0].filled_quantity, Decimal("1"))
        finished = M.normalize_gate_orders([{
            "contract": "BTC_USDT", "id": "order-2", "side": "sell", "status": "finished",
            "finish_as": "filled", "size": "1", "filled": "1", "avg_deal_price": "101",
        }], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW, source_event_prefix="order")
        self.assertEqual(finished[0].status, M.GateOrderStatus.FILLED)
        with self.assertRaises(M.GateReadPayloadError):
            M.normalize_gate_orders([{"contract": "BTC_USDT", "id": "order-3", "side": "buy", "status": "finished", "size": "1", "filled": "0"}], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW, source_event_prefix="order")
        fills = M.normalize_gate_fills([{
            "contract": "BTC_USDT", "order_id": "order-1", "trade_id": "fill-1",
            "side": "buy", "size": "1.000", "price": "100", "fee": "0.1", "fee_asset": "usdt",
        }], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW, source_event_prefix="fill")
        self.assertEqual(fills[0].venue_fill_id, "fill-1")
        self.assertEqual(fills[0].fee_asset, "USDT")
        with self.assertRaises(M.GateReadPayloadError):
            M.normalize_gate_fills([{"contract": "BTC_USDT", "order_id": "order-1", "side": "buy", "size": "1", "price": "100"}], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW, source_event_prefix="fill")

    def test_spot_order_envelope_and_fill_amount_aliases_are_normalized(self):
        orders = M.normalize_gate_orders([{"currency_pair": "BTC_USDT", "orders": [{
            "currency_pair": "BTC_USDT", "id": "order-1", "side": "buy", "status": "open",
            "amount": "0.1", "filled_amount": "0.02", "price": "100",
        }], "total": 1}], market_type=C.AssetMarketType.SPOT, account_scope="paper", observed_at=NOW, source_event_prefix="order")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].filled_quantity, Decimal("0.02"))
        fills = M.normalize_gate_fills([{
            "currency_pair": "BTC_USDT", "order_id": "order-1", "id": "fill-1",
            "side": "buy", "amount": "0.02", "price": "100", "fee": "0.01", "fee_currency": "USDT",
        }], market_type=C.AssetMarketType.SPOT, account_scope="paper", observed_at=NOW, source_event_prefix="fill")
        self.assertEqual(fills[0].quantity, Decimal("0.02"))
        self.assertEqual(fills[0].fee_asset, "USDT")

    def test_futures_fill_uses_explicit_settlement_asset_when_venue_omits_fee_asset(self):
        fills = M.normalize_gate_fills([{
            "contract": "BTC_USDT", "order_id": "order-1", "id": "fill-1",
            "side": "sell", "size": "1", "price": "100", "fee": "0.1",
        }], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW,
            source_event_prefix="fill", default_fee_asset="USDT")
        self.assertEqual(fills[0].fee_asset, "USDT")
        self.assertEqual(fills[0].fee_amount, Decimal("0.1"))

    def test_futures_maker_rebate_preserves_signed_fee(self):
        fills = M.normalize_gate_fills([{
            "contract": "BTC_USDT", "order_id": "order-1", "id": "fill-1",
            "side": "sell", "size": "1", "price": "100", "fee": "-0.1",
        }], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW,
            source_event_prefix="fill", default_fee_asset="USDT")
        self.assertEqual(fills[0].fee_amount, Decimal("-0.1"))


if __name__ == "__main__": unittest.main()
