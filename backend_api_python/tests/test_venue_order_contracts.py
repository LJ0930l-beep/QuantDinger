from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
_MISSING = object()
_SANDBOXED_MODULES = (
    "app",
    "app.domain",
    "app.domain.decimal_values",
    "app.domain.venue_order_contracts",
    "pr04_venue_capabilities",
)


@contextmanager
def _isolated_sys_modules(module_names):
    original = {name: sys.modules.get(name, _MISSING) for name in module_names}
    try:
        yield
    finally:
        for name, previous in original.items():
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_subject():
    """Bind pure subject references without leaking test modules globally."""

    with _isolated_sys_modules(_SANDBOXED_MODULES):
        app_package = types.ModuleType("app")
        app_package.__path__ = [str(BACKEND / "app")]
        domain_package = types.ModuleType("app.domain")
        domain_package.__path__ = [str(BACKEND / "app" / "domain")]
        sys.modules["app"] = app_package
        sys.modules["app.domain"] = domain_package
        decimal_values = _load_module(
            "app.domain.decimal_values", BACKEND / "app" / "domain" / "decimal_values.py"
        )
        domain = _load_module(
            "app.domain.venue_order_contracts", BACKEND / "app" / "domain" / "venue_order_contracts.py"
        )
        capabilities = _load_module(
            "pr04_venue_capabilities", BACKEND / "app" / "services" / "live_trading" / "capabilities.py"
        )
        return decimal_values, domain, capabilities


decimal_values, domain, capabilities = _load_subject()

BINANCE_USDM_CLIENT_ID_PATTERN = domain.BINANCE_USDM_CLIENT_ID_PATTERN
SubmissionAttemptIdentity = domain.SubmissionAttemptIdentity
UnsupportedVenueCapability = domain.UnsupportedVenueCapability
VenueContractError = domain.VenueContractError
generate_venue_client_order_id = domain.generate_venue_client_order_id
validate_binance_usdm_client_order_id = domain.validate_binance_usdm_client_order_id
VenueFillIdentity = domain.VenueFillIdentity
VenueOrderScope = domain.VenueOrderScope
FillFee = domain.FillFee
OrderQueryReference = domain.OrderQueryReference
OrderQueryRequest = domain.OrderQueryRequest
OrderQueryStatus = domain.OrderQueryStatus
VenueQueryFailureKind = domain.VenueQueryFailureKind
found_order_query_result = domain.found_order_query_result
query_failure_result = domain.query_failure_result
Quantity = decimal_values.Quantity
Price = decimal_values.Price
FeeAmount = decimal_values.FeeAmount


def identity(**overrides):
    values = dict(
        economic_order_id="00000000-0000-0000-0000-000000000001",
        child_seq=1,
        attempt_no=1,
        exchange_id="binance",
        market_type="swap",
        broker_prefix="x-broker",
    )
    values.update(overrides)
    return SubmissionAttemptIdentity(**values)


def profile(market_type="swap"):
    return capabilities.get_venue_capability_profile("binance", market_type)


class VenueOrderContractTests(unittest.TestCase):
    def test_futures_id_is_deterministic_and_matches_official_rule(self):
        first = generate_venue_client_order_id(identity(), capability=profile())
        self.assertEqual(first, generate_venue_client_order_id(identity(), capability=profile()))
        self.assertLessEqual(len(first), 36)
        self.assertRegex(first, BINANCE_USDM_CLIENT_ID_PATTERN)

    def test_order_attempt_prefix_and_algorithm_are_canonical_identity_inputs(self):
        base = generate_venue_client_order_id(identity(), capability=profile())
        self.assertNotEqual(base, generate_venue_client_order_id(identity(attempt_no=2), capability=profile()))
        self.assertNotEqual(base, generate_venue_client_order_id(identity(economic_order_id="00000000-0000-0000-0000-000000000002"), capability=profile()))
        self.assertNotEqual(base, generate_venue_client_order_id(identity(broker_prefix="x-other"), capability=profile()))
        # An explicitly supplied historical prefix is unaffected by any later
        # runtime configuration change because this function reads no config.
        self.assertEqual(base, generate_venue_client_order_id(identity(broker_prefix="x-broker"), capability=profile()))
        with self.assertRaisesRegex(VenueContractError, "normalization version"):
            identity(prefix_normalization_version="future-v2")

    def test_submission_attempt_identity_canonicalizes_only_valid_identity_inputs(self):
        canonical = identity()
        equivalent = identity(
            economic_order_id=" 00000000-0000-0000-0000-000000000001 ",
            exchange_id=" BINANCE ",
            market_type=" SWAP ",
        )
        uppercase_uuid = identity(
            economic_order_id="00000000-0000-0000-0000-000000000001".upper(),
        )
        self.assertEqual(canonical, equivalent)
        self.assertEqual(canonical, uppercase_uuid)
        self.assertEqual(
            generate_venue_client_order_id(canonical, capability=profile()),
            generate_venue_client_order_id(equivalent, capability=profile()),
        )
        for overrides, message in (
            ({"economic_order_id": "not-a-uuid"}, "must be a UUID"),
            ({"child_seq": True}, "child_seq must be an int"),
            ({"attempt_no": False}, "attempt_no must be an int"),
            ({"child_seq": -1}, "child_seq cannot be negative"),
            ({"exchange_id": "   "}, "exchange_id is required"),
            ({"market_type": 7}, "market_type must be a string"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(VenueContractError, message):
                    identity(**overrides)

    def test_spot_generation_fails_closed_without_inheriting_swap_rules(self):
        spot = profile("spot")
        self.assertTrue(spot.accepts_external_client_order_id)
        self.assertFalse(spot.can_generate_safe_client_order_id)
        self.assertIsNone(spot.client_id_max_length)
        self.assertIsNone(spot.client_id_pattern)
        self.assertTrue(spot.query_by_exchange_order_id)
        self.assertTrue(spot.query_by_client_order_id)
        with self.assertRaises(UnsupportedVenueCapability):
            generate_venue_client_order_id(identity(market_type="spot"), capability=spot)

    def test_unknown_profile_fails_closed(self):
        unknown = capabilities.VenueCapabilityProfile("unknown", "swap")
        with self.assertRaises(UnsupportedVenueCapability):
            generate_venue_client_order_id(identity(exchange_id="unknown"), capability=unknown)

    def test_prefix_rejects_ambiguity_unicode_and_sensitive_markers_without_leaking_value(self):
        for prefix in (" x-broker", "x-broker ", "x\u200bbroker", "x-代理", "apiKey-secret-value"):
            with self.subTest(prefix=prefix):
                with self.assertRaisesRegex(VenueContractError, "invalid broker_prefix") as caught:
                    generate_venue_client_order_id(identity(broker_prefix=prefix), capability=profile())
                self.assertNotIn("secret-value", str(caught.exception))

    def test_prefix_is_never_silently_truncated(self):
        with self.assertRaisesRegex(VenueContractError, "violates"):
            generate_venue_client_order_id(identity(broker_prefix="x-" + "a" * 40), capability=profile())
        with self.assertRaisesRegex(VenueContractError, "violates"):
            validate_binance_usdm_client_order_id("x-" + "a" * 40)

    def test_query_failures_are_explicit_and_never_not_found_by_accident(self):
        request = OrderQueryRequest(
            OrderQueryReference.CLIENT_ORDER_ID, "binance", "swap", "scope-a", "BTCUSDT", client_order_id="cid-1"
        )
        self.assertEqual(query_failure_result(request, VenueQueryFailureKind.TIMEOUT).status, OrderQueryStatus.TEMPORARY_FAILURE)
        self.assertEqual(query_failure_result(request, VenueQueryFailureKind.RATE_LIMITED).status, OrderQueryStatus.TEMPORARY_FAILURE)
        self.assertEqual(query_failure_result(request, VenueQueryFailureKind.SERVER_ERROR).status, OrderQueryStatus.TEMPORARY_FAILURE)
        self.assertEqual(query_failure_result(request, VenueQueryFailureKind.AUTH_OR_PERMISSION).status, OrderQueryStatus.AUTH_OR_PERMISSION_FAILURE)
        self.assertEqual(query_failure_result(request, VenueQueryFailureKind.NOT_FOUND).status, OrderQueryStatus.NOT_FOUND)

    def test_found_query_requires_identity_known_state_and_matching_scope(self):
        request = OrderQueryRequest(
            OrderQueryReference.EXCHANGE_ORDER_ID, "binance", "swap", "scope-a", "BTCUSDT", exchange_order_id="oid-1"
        )
        result = found_order_query_result(
            request,
            response_venue="binance",
            response_market_type="swap",
            response_account_scope="scope-a",
            response_instrument="BTCUSDT",
            exchange_order_id="oid-1",
            client_order_id="cid-1",
            normalized_state="SUBMITTED",
            raw_state="NEW",
        )
        self.assertEqual(result.status, OrderQueryStatus.FOUND)
        with self.assertRaisesRegex(VenueContractError, "scope mismatch"):
            found_order_query_result(
                request, response_venue="binance", response_market_type="swap", response_account_scope="scope-b",
                response_instrument="BTCUSDT", exchange_order_id="oid-1", normalized_state="SUBMITTED", raw_state="NEW"
            )
        with self.assertRaisesRegex(VenueContractError, "unknown normalized"):
            found_order_query_result(
                request, response_venue="binance", response_market_type="swap", response_account_scope="scope-a",
                response_instrument="BTCUSDT", exchange_order_id="oid-1", normalized_state="UNKNOWN", raw_state="???"
            )

    def test_query_scope_canonicalization_requires_typed_reference(self):
        request = OrderQueryRequest(
            OrderQueryReference.EXCHANGE_ORDER_ID,
            " BINANCE ",
            " SWAP ",
            " scope-a ",
            " btcusdt ",
            exchange_order_id=" oid-1 ",
        )
        self.assertEqual(
            (request.venue, request.market_type, request.account_scope, request.instrument, request.exchange_order_id),
            ("binance", "swap", "scope-a", "BTCUSDT", "oid-1"),
        )
        result = found_order_query_result(
            request,
            response_venue=" binance ",
            response_market_type=" SWAP ",
            response_account_scope=" scope-a ",
            response_instrument=" btcusdt ",
            exchange_order_id=" oid-1 ",
            normalized_state="SUBMITTED",
            raw_state="NEW",
        )
        self.assertEqual(result.status, OrderQueryStatus.FOUND)
        for args, message in (
            (("EXCHANGE_ORDER_ID", "binance", "swap", "scope-a", "BTCUSDT"), "OrderQueryReference"),
            ((OrderQueryReference.EXCHANGE_ORDER_ID, "binance", "swap", "scope-a", "BTCUSDT"), "requires only"),
            ((OrderQueryReference.EXCHANGE_ORDER_ID, "binance", "swap", "scope-a", "BTCUSDT", " "), "required"),
            ((OrderQueryReference.EXCHANGE_ORDER_ID, "binance", "swap", "scope-a", 7, "oid-1"), "instrument must be a string"),
        ):
            with self.subTest(args=args):
                with self.assertRaisesRegex(VenueContractError, message):
                    OrderQueryRequest(*args)

    def test_fill_key_is_scoped_requires_stable_id_and_preserves_fees_by_asset(self):
        scope = VenueOrderScope("binance", "swap", "scope-a", "BTCUSDT", "order-1")
        fill = VenueFillIdentity(
            scope, "trade-1", Quantity("1"), Price("2"),
            (FillFee("USDT", FeeAmount("0.1")), FillFee("BNB", FeeAmount("0.01"))),
        )
        self.assertNotEqual(fill.canonical_key, VenueFillIdentity(VenueOrderScope("binance", "swap", "scope-b", "BTCUSDT", "order-1"), "trade-1", Quantity("1"), Price("2")).canonical_key)
        self.assertNotEqual(fill.canonical_key, VenueFillIdentity(VenueOrderScope("binance", "spot", "scope-a", "BTCUSDT", "order-1"), "trade-1", Quantity("1"), Price("2")).canonical_key)
        self.assertEqual(fill.canonical_key, VenueFillIdentity(scope, "trade-1", Quantity("1"), Price("2"), fill.fees).canonical_key)
        self.assertEqual([fee.asset for fee in fill.fees], ["USDT", "BNB"])
        with self.assertRaisesRegex(VenueContractError, "stable venue_fill_id"):
            VenueFillIdentity(scope, "", Quantity("1"), Price("2"))
        with self.assertRaisesRegex(VenueContractError, "PR-01"):
            VenueFillIdentity(scope, "trade-1", 1.0, Price("2"))
        with self.assertRaisesRegex(VenueContractError, "fill scope mismatch"):
            VenueFillIdentity.from_venue_fact(
                scope,
                venue="binance",
                market_type="swap",
                account_scope="scope-b",
                instrument="BTCUSDT",
                exchange_order_id="order-1",
                venue_fill_id="trade-1",
                quantity=Quantity("1"),
                price=Price("2"),
            )

    def test_fill_identity_canonicalizes_permitted_scope_variation(self):
        canonical_scope = VenueOrderScope("binance", "swap", "Account-A", "BTCUSDT", "Order-1")
        equivalent_scope = VenueOrderScope(" BINANCE ", " SWAP ", " Account-A ", " btcusdt ", " Order-1 ")
        canonical = VenueFillIdentity(
            canonical_scope,
            "Trade-1",
            Quantity("1.25"),
            Price("123.45"),
            (FillFee("bnb", FeeAmount("0.01")),),
        )
        equivalent = VenueFillIdentity(
            equivalent_scope,
            " Trade-1 ",
            Quantity("1.25"),
            Price("123.45"),
            (FillFee(" BNB ", FeeAmount("0.01")),),
        )
        self.assertEqual(canonical.canonical_key, equivalent.canonical_key)
        self.assertEqual(equivalent.order_scope.instrument, "BTCUSDT")
        self.assertEqual(equivalent.fees[0].asset, "BNB")
        self.assertEqual(equivalent.quantity.value, Quantity("1.25").value)
        self.assertEqual(equivalent.price.value, Price("123.45").value)
        self.assertNotEqual(
            canonical.canonical_key,
            VenueFillIdentity(
                VenueOrderScope("binance", "swap", "Account-B", "BTCUSDT", "Order-1"),
                "Trade-1", Quantity("1.25"), Price("123.45"),
            ).canonical_key,
        )
        for constructor, message in (
            (lambda: VenueOrderScope(" ", "swap", "scope", "BTCUSDT", "order"), "venue is required"),
            (lambda: VenueOrderScope("binance", "swap", "scope", 7, "order"), "instrument must be a string"),
            (lambda: VenueOrderScope("binance", "swap", "scope", "BTCUSDT", " "), "exchange_order_id is required"),
            (lambda: VenueFillIdentity(canonical_scope, " ", Quantity("1"), Price("2")), "stable venue_fill_id is required"),
            (lambda: FillFee(" ", FeeAmount("0.1")), "fee asset is required"),
            (lambda: FillFee(7, FeeAmount("0.1")), "fee asset must be a string"),
        ):
            with self.subTest(constructor=constructor):
                with self.assertRaisesRegex(VenueContractError, message):
                    constructor()

    def test_subject_loading_restores_exact_modules_after_success_and_failure(self):
        original_decimal = types.ModuleType("app.domain.decimal_values")
        original_venue = types.ModuleType("app.domain.venue_order_contracts")
        previous = {name: sys.modules.get(name, _MISSING) for name in _SANDBOXED_MODULES}
        try:
            sys.modules["app.domain.decimal_values"] = original_decimal
            sys.modules["app.domain.venue_order_contracts"] = original_venue
            expected = {name: sys.modules.get(name, _MISSING) for name in _SANDBOXED_MODULES}
            _load_subject()
            _load_subject()
            self.assertIs(sys.modules["app.domain.decimal_values"], original_decimal)
            self.assertIs(sys.modules["app.domain.venue_order_contracts"], original_venue)
            self.assertEqual({name: sys.modules.get(name, _MISSING) for name in _SANDBOXED_MODULES}, expected)
            with self.assertRaises(RuntimeError):
                with _isolated_sys_modules(_SANDBOXED_MODULES):
                    sys.modules["app.domain.decimal_values"] = types.ModuleType("fake.decimal")
                    raise RuntimeError("injected load failure")
            self.assertEqual({name: sys.modules.get(name, _MISSING) for name in _SANDBOXED_MODULES}, expected)
        finally:
            for name, original in previous.items():
                if original is _MISSING:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = original


if __name__ == "__main__":
    unittest.main()
