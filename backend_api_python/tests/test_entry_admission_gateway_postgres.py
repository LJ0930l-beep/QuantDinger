"""PostgreSQL caller-owned composition checks for Canonical Entry V2 adapters.

This is deliberately not a gateway or runtime test: it verifies that the
durable entry, hard-risk V2, and admission-outbox adapters can share exactly
one caller-owned transaction.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import unittest
from uuid import uuid4

from tests.pr12c_admission_loader import load_pr12c_admission


m = load_pr12c_admission()
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
INIT_SQL = MIGRATIONS / "init.sql"
_FIXTURE_ID_PREFIX = "entry-admission-v2-"
_FIXTURE_ID_SUFFIX_LENGTH = 20
_QD_USER_USERNAME_MAX_LENGTH = 50
_QD_EXCHANGE_ID_MAX_LENGTH = 50


def _fixture_identity() -> str:
    """Return one CI fixture identifier accepted by both legacy VARCHAR(50) columns."""
    return f"{_FIXTURE_ID_PREFIX}{uuid4().hex[:_FIXTURE_ID_SUFFIX_LENGTH]}"


class _Provider:
    def __init__(self, outcome):
        self.outcome = outcome

    def prepare(self, _connection, _graph):
        return self.outcome


class EntryAdmissionFixtureIdentityTests(unittest.TestCase):
    def test_fixture_identity_fits_legacy_user_and_exchange_limits(self):
        identity = _fixture_identity()

        self.assertLessEqual(len(identity), _QD_USER_USERNAME_MAX_LENGTH)
        self.assertLessEqual(len(identity), _QD_EXCHANGE_ID_MAX_LENGTH)


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class EntryAdmissionV2PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2

        cls.psycopg2 = psycopg2
        connection = cls._connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                for migration in sorted(MIGRATIONS.glob("2026*.sql")):
                    cursor.execute(migration.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def _connection(cls):
        connection = cls.psycopg2.connect(os.environ["DATABASE_URL"])
        connection.autocommit = False
        return connection

    def setUp(self):
        self.connection = self._connection()
        identity = _fixture_identity()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
                (identity, "test"),
            )
            self.tenant_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) "
                "VALUES (%s, %s, %s) RETURNING id",
                (self.tenant_id, identity, "{}"),
            )
            self.credential_id = cursor.fetchone()[0]
        self.connection.commit()

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def _graph(self, action=None):
        action = action or m.order.OrderAction.OPEN
        reducing = action in {
            m.order.OrderAction.REDUCE, m.order.OrderAction.CLOSE,
            m.order.OrderAction.EMERGENCY_CLOSE, m.order.OrderAction.PROTECTION,
        }
        if action is m.order.OrderAction.CANCEL:
            intent = m.entry_v2.CanonicalEconomicIntentV2(
                cancel_target_kind=m.entry_v2.CancelTargetKind.CLIENT_ORDER_ID,
                cancel_target_id="venue-client-1",
            )
            actor = m.entry.EntryActorContext(m.order.Actor.HUMAN, "human-1", m.entry.EntrySource.REST)
            effect = m.order.RiskEffect.NEUTRAL
            subject = m.entry_v2.CancelTargetSubject(
                m.entry_v2.CancelTargetKind.CLIENT_ORDER_ID, "venue-client-1",
            )
        else:
            intent = m.entry_v2.CanonicalEconomicIntentV2(
                side=m.entry.OrderSide.BUY,
                quantity=None if reducing else m.decimal.Quantity("1"),
                quantity_semantics=None if reducing else m.entry_v2.QuantitySemantics.ABSOLUTE,
                execution_kind=m.entry.ExecutionKind.MARKET,
                reduce_only=reducing,
                target_position_id="position-1" if reducing else None,
                close_quantity=m.decimal.Quantity("1") if reducing else None,
                position_side=m.entry.PositionSide.NET,
            )
            protection = action is m.order.OrderAction.PROTECTION
            actor = m.entry.EntryActorContext(
                m.order.Actor.PROTECTION if protection else m.order.Actor.HUMAN,
                "protection-1" if protection else "human-1",
                m.entry.EntrySource.PROTECTION if protection else m.entry.EntrySource.REST,
            )
            effect = m.order.RiskEffect.REDUCE_RISK if reducing else m.order.RiskEffect.INCREASE_RISK
            subject = m.entry_v2.EconomicOrderSubject(uuid4())
        specification = m.entry_v2.CanonicalEntryRequestV2(
            self.tenant_id, self.credential_id, "account-1", "BTCUSDT", "swap",
            action, intent, actor, effect, f"entry-admission-{uuid4().hex}",
            "corr-1", datetime(2026, 7, 29, tzinfo=timezone.utc), m.entry.EntryMode.PAPER,
        )
        return m.entry_v2.DurableEntryGraphV2(uuid4(), specification, subject)

    def _inputs(self, value, *, denied=False, reconciliation_unhealthy=False):
        increasing = value.specification.action in {
            m.order.OrderAction.OPEN, m.order.OrderAction.INCREASE,
        }
        demand = (
            m.hard_risk.RiskReservationDemand(
                "provider-demand", value.specification.account_scope,
                value.specification.instrument_id, "USDT", "100", "100", "100", "25",
            )
            if increasing
            else None
        )
        kinds = [
            m.authoritative_risk_facts.RiskFactSourceKind.POLICY,
            m.authoritative_risk_facts.RiskFactSourceKind.ACCOUNT,
            m.authoritative_risk_facts.RiskFactSourceKind.INSTRUMENT_RULES,
            m.authoritative_risk_facts.RiskFactSourceKind.RECONCILIATION,
            m.authoritative_risk_facts.RiskFactSourceKind.KILL_SWITCH_GLOBAL,
            m.authoritative_risk_facts.RiskFactSourceKind.KILL_SWITCH_ACCOUNT,
            m.authoritative_risk_facts.RiskFactSourceKind.KILL_SWITCH_STRATEGY,
            m.authoritative_risk_facts.RiskFactSourceKind.ACTIVE_RESERVATIONS,
        ]
        if increasing:
            kinds.append(m.authoritative_risk_facts.RiskFactSourceKind.MARKET)
        provenance = tuple(
            m.authoritative_risk_facts.RiskFactProvenance(
                kind, f"test-{kind.value.lower()}", "v1", f"{index:x}" * 64,
                datetime(2026, 7, 29, tzinfo=timezone.utc), 60,
            )
            for index, kind in enumerate(kinds)
        )
        return m.admission.DurableRiskAdmissionInputs(
            m.hard_risk.RiskLimitPolicy(
                "policy-1", "USDT", m.decimal.QuoteAmount("1000"), m.decimal.QuoteAmount("700"),
                m.decimal.QuoteAmount("600"), "4", m.decimal.QuoteAmount("100"),
                m.decimal.QuoteAmount("100"), "0.20",
            ),
            m.hard_risk.RiskExposureSnapshot(
                value.specification.account_scope, value.specification.instrument_id, "USDT",
                "100", "100", "100", "800", "500", "500", "0",
                (
                    m.order.ReconciliationHealth.UNHEALTHY
                    if reconciliation_unhealthy
                    else m.order.ReconciliationHealth.HEALTHY
                ),
                m.hard_risk.MarketDataHealth.FRESH,
                True,
            ),
            m.hard_risk.KillSwitchSnapshot(*(
                m.hard_risk.KillSwitchState(
                    1,
                    denied,
                    m.hard_risk.KillSwitchMode.OPEN_BLOCKED if denied else None,
                )
                for _ in range(3)
            )),
            m.hard_risk.HardRiskRequest(
                value.specification.action,
                value.specification.actor.actor_type,
                m.order.RiskEffect.REDUCE_RISK if value.specification.action is m.order.OrderAction.PROTECTION else None,
                "100" if increasing else "0", "100" if increasing else "0",
                "100" if increasing else "0", "25" if increasing else "0",
            ),
            datetime(2026, 7, 29, tzinfo=timezone.utc), reservation_demand=demand, provenance=provenance,
        )

    def _gateway(self, value, *, denied=False, reconciliation_unhealthy=False, durable_entries=None, durable_risk=None, outbox=None):
        return m.gateway.CanonicalEntryAdmissionGateway(
            durable_entries=durable_entries or m.durable_entry_repository.DurableEntryRepository(),
            durable_risk=durable_risk or m.adapters.DurableRiskAdmissionAdapter(
                provider=_Provider(self._inputs(value, denied=denied, reconciliation_unhealthy=reconciliation_unhealthy)),
            ),
            outbox=outbox or m.adapters.AdmissionOutboxAdapter(),
        )

    def _seed_authoritative_risk_facts(self, value):
        """Seed only persisted RF-01 facts; no runtime or exchange fixture."""
        at = value.specification.occurred_at
        values = (self.tenant_id, self.credential_id, "account-1", "BTCUSDT", "swap")
        with self.connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO qd_authoritative_risk_policies
                (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,strategy_scope,
                 policy_identity,policy_version,policy_fingerprint,observed_at,max_age_seconds,reservation_ttl_seconds,
                 valuation_currency,max_gross_notional,max_net_notional,max_instrument_notional,max_leverage,
                 minimum_available_margin,max_daily_loss,max_drawdown_ratio)
                VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'__NON_STRATEGY__','policy','v1',%s,%s,60,30,'USDT',160,700,600,4,100,100,0.2)
            """, (uuid4(), *values, "a" * 64, at))
            cursor.execute("""
                INSERT INTO qd_authoritative_instrument_risk_rules
                (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
                 source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,quantity_to_quote_multiplier,initial_margin_ratio)
                VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','rule','v1',%s,%s,60,1,0.25)
            """, (uuid4(), *values, "b" * 64, at))
            cursor.execute("""
                INSERT INTO qd_authoritative_account_risk_facts
                (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
                 source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,gross_notional,net_notional,
                 instrument_notional,available_margin,equity,peak_equity,daily_realized_pnl,account_facts_verified)
                VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','account','v1',%s,%s,60,100,100,100,800,500,500,0,true)
            """, (uuid4(), *values, "c" * 64, at))
            cursor.execute("""
                INSERT INTO qd_reconciliation_checkpoints
                (id,tenant_id,credential_id,exchange,market_type,account_scope,instrument_id,status,evidence_hash,version,updated_at,risk_max_age_seconds)
                VALUES (%s,%s,%s,'fixture','swap','account-1','BTCUSDT','HEALTHY',%s,1,%s,60)
            """, (uuid4(), self.tenant_id, self.credential_id, "d" * 64, at))
            for kind, letter in (("GLOBAL", "e"), ("ACCOUNT", "f"), ("STRATEGY", "0")):
                cursor.execute("""
                    INSERT INTO qd_authoritative_kill_switch_observations
                    (id,contract_version,tenant_id,credential_id,account_scope,strategy_scope,scope_kind,source_identity,
                     source_version,source_fingerprint,observed_at,max_age_seconds,switch_version,enabled,mode)
                    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,'account-1','__NON_STRATEGY__',%s,%s,'v1',%s,%s,60,1,false,NULL)
                """, (uuid4(), self.tenant_id, self.credential_id, kind, f"switch-{kind.lower()}", letter * 64, at))
            cursor.execute("""
                INSERT INTO qd_authoritative_market_observations
                (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
                 price_type,price,source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,market_data_health)
                VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','MARK',50,'market','v1',%s,%s,60,'FRESH')
            """, (uuid4(), *values, "1" * 64, at))

    def test_authoritative_provider_persists_provenance_and_replays_without_reselecting_facts(self):
        value = self._graph()
        self._seed_authoritative_risk_facts(value)
        class CountingProvider(m.authoritative_risk_provider.AuthoritativeRiskFactsProvider):
            calls = 0
            def prepare(self, connection, graph):
                self.calls += 1
                return super().prepare(connection, graph)

        provider = CountingProvider()
        gateway = self._gateway(
            value,
            durable_risk=m.adapters.DurableRiskAdmissionAdapter(
                provider=provider,
            ),
        )
        first = gateway.admit(self.connection, value)
        self.assertTrue(first.risk_decision_id)
        self.connection.commit()
        self.assertEqual(9, self._count("qd_durable_risk_fact_provenance", first.risk_decision_id, "risk_decision_id"))
        replay = gateway.admit(self.connection, value)
        self.assertEqual(m.admission.EntryAdmissionDisposition.REPLAYED, replay.disposition)
        self.assertEqual(1, provider.calls)

    def test_authoritative_provider_serializes_account_capacity_across_two_connections(self):
        seed = self._graph()
        self._seed_authoritative_risk_facts(seed)
        self.connection.commit()
        barrier = threading.Barrier(2, timeout=10)
        results, failures = [], []
        result_lock = threading.Lock()

        def worker():
            connection = self._connection()
            try:
                value = self._graph()
                gateway = self._gateway(
                    value,
                    durable_risk=m.adapters.DurableRiskAdmissionAdapter(
                        provider=m.authoritative_risk_provider.AuthoritativeRiskFactsProvider(),
                    ),
                )
                barrier.wait()
                result = gateway.admit(connection, value)
                connection.commit()
                with result_lock:
                    results.append(result)
            except Exception as exc:  # test captures raw errors explicitly
                connection.rollback()
                with result_lock:
                    failures.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=15)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([], failures)
        self.assertEqual(2, len(results))
        self.assertEqual(1, sum(item.risk_decision_status == "ALLOW" for item in results))
        self.assertEqual(1, sum(item.risk_decision_status in {"DENY", "RECONCILIATION_REQUIRED"} for item in results))

    def _count(self, table, identifier, column):
        with self.connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (identifier,))
            return cursor.fetchone()[0]

    def _outbox_event(self, event_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT aggregate_type, aggregate_id, aggregate_version,
                       event_type, schema_version, payload_json
                  FROM qd_transactional_outbox
                 WHERE event_id = %s
                """,
                (event_id,),
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        return m.outbox.OutboxEvent(*row)

    def test_outer_rollback_erases_all_admission_facts_and_connection_remains_usable(self):
        value = self._graph()
        result = self._gateway(value).admit(self.connection, value)
        self.assertEqual(m.admission.EntryAdmissionDisposition.CREATED, result.disposition)
        self.assertIsNotNone(result.reservation_id)
        self.assertIsNotNone(result.outbox_event_id)
        self.connection.rollback()
        self.assertEqual(0, self._count("qd_durable_entry_specifications", value.command_id, "command_id"))
        self.assertEqual(0, self._count("qd_durable_risk_decisions", result.risk_decision_id, "id"))
        self.assertEqual(0, self._count("qd_transactional_outbox", result.outbox_event_id, "event_id"))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(1, cursor.fetchone()[0])

    def test_outer_commit_then_exact_replay_creates_no_duplicate_facts(self):
        value = self._graph()
        result = self._gateway(value).admit(self.connection, value)
        self.connection.commit()
        self.assertEqual(m.admission.EntryAdmissionDisposition.CREATED, result.disposition)
        replayed = self._gateway(value).admit(self.connection, value)
        self.assertEqual(m.admission.EntryAdmissionDisposition.REPLAYED, replayed.disposition)
        self.assertEqual(result.risk_decision_id, replayed.risk_decision_id)
        self.assertEqual(result.outbox_event_id, replayed.outbox_event_id)
        self.assertEqual(
            m.admission.parse_admission_outbox_event(self._outbox_event(result.outbox_event_id)),
            m.admission.parse_admission_outbox_event(self._outbox_event(replayed.outbox_event_id)),
        )
        self.connection.rollback()

    def test_crash_recovery_completes_only_missing_facts(self):
        entry_only = self._graph()
        m.durable_entry_repository.DurableEntryRepository().persist_durable_entry(self.connection, entry_only)
        self.connection.commit()
        resumed = self._gateway(entry_only).admit(self.connection, entry_only)
        self.assertEqual(m.admission.EntryAdmissionDisposition.CREATED, resumed.disposition)
        self.assertIsNotNone(resumed.risk_decision_id)
        self.assertIsNotNone(resumed.outbox_event_id)
        self.connection.commit()

        entry_and_risk = self._graph()
        durable = m.durable_entry_repository.DurableEntryRepository().persist_durable_entry(
            self.connection, entry_and_risk,
        )
        risk = m.adapters.DurableRiskAdmissionAdapter(
            provider=_Provider(self._inputs(entry_and_risk)),
        ).evaluate_and_persist(self.connection, entry_and_risk)
        self.assertEqual(m.durable_entry.DurableEntryPersistDisposition.CREATED, durable.disposition)
        self.assertEqual(m.durable_risk.DurableRiskPersistDisposition.CREATED, risk.disposition)
        self.connection.commit()
        resumed = self._gateway(entry_and_risk).admit(self.connection, entry_and_risk)
        self.assertEqual(m.admission.EntryAdmissionDisposition.CREATED, resumed.disposition)
        self.assertIsNotNone(resumed.outbox_event_id)
        self.connection.commit()
        replayed = self._gateway(entry_and_risk).admit(self.connection, entry_and_risk)
        self.assertEqual(m.admission.EntryAdmissionDisposition.REPLAYED, replayed.disposition)
        self.assertEqual(
            m.admission.parse_admission_outbox_event(self._outbox_event(resumed.outbox_event_id)),
            m.admission.parse_admission_outbox_event(self._outbox_event(replayed.outbox_event_id)),
        )

    def test_denied_risk_persists_no_reservation_and_no_outbox(self):
        value = self._graph()
        result = self._gateway(value, denied=True).admit(self.connection, value)
        self.assertEqual(m.admission.EntryAdmissionDisposition.RISK_REJECTED, result.disposition)
        self.assertIsNone(result.reservation_id)
        self.assertIsNone(result.outbox_event_id)
        self.connection.commit()
        self.assertEqual(0, self._count("qd_durable_risk_reservations", result.risk_decision_id, "decision_id"))
        self.assertEqual(0, self._count("qd_transactional_outbox", value.command_id, "aggregate_id"))

    def test_reconciliation_required_persists_decision_without_reservation_or_outbox(self):
        value = self._graph()
        result = self._gateway(value, reconciliation_unhealthy=True).admit(self.connection, value)
        self.assertEqual(m.admission.EntryAdmissionDisposition.RISK_REJECTED, result.disposition)
        self.assertEqual("RECONCILIATION_REQUIRED", result.risk_decision_status)
        self.assertIsNone(result.reservation_id)
        self.assertIsNone(result.outbox_event_id)
        self.connection.commit()
        self.assertEqual(0, self._count("qd_durable_risk_reservations", result.risk_decision_id, "decision_id"))
        self.assertEqual(0, self._count("qd_transactional_outbox", value.command_id, "aggregate_id"))

    def test_reducing_action_has_decision_and_outbox_but_never_reservation(self):
        value = self._graph(m.order.OrderAction.CLOSE)
        result = self._gateway(value).admit(self.connection, value)
        self.assertEqual(m.admission.EntryAdmissionDisposition.CREATED, result.disposition)
        self.assertEqual("ALLOW", result.risk_decision_status)
        self.assertIsNone(result.reservation_id)
        self.assertIsNotNone(result.outbox_event_id)
        self.connection.commit()
        self.assertEqual(0, self._count("qd_durable_risk_reservations", result.risk_decision_id, "decision_id"))

    def test_cancel_persists_only_typed_entry_and_command_outbox(self):
        value = self._graph(m.order.OrderAction.CANCEL)

        class NeverRisk:
            def evaluate_and_persist(self, *_args, **_kwargs):
                raise AssertionError("CANCEL must not call durable risk")

        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM qd_order_intents_v2")
            intents_before = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM qd_economic_orders")
            economic_orders_before = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM qd_order_commands")
            legacy_commands_before = cursor.fetchone()[0]
        result = self._gateway(value, durable_risk=NeverRisk()).admit(self.connection, value)
        self.assertEqual(m.admission.EntryAdmissionDisposition.CREATED, result.disposition)
        self.assertIsNone(result.economic_order_id)
        self.assertIsNone(result.risk_decision_id)
        self.assertIsNone(result.reservation_id)
        self.assertIsNotNone(result.outbox_event_id)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT action, economic_order_id FROM qd_durable_entry_specifications WHERE command_id = %s",
                (value.command_id,),
            )
            self.assertEqual(("CANCEL", None), cursor.fetchone())
            cursor.execute("SELECT count(*) FROM qd_order_intents_v2")
            self.assertEqual(intents_before, cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM qd_economic_orders")
            self.assertEqual(economic_orders_before, cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM qd_order_commands")
            self.assertEqual(legacy_commands_before, cursor.fetchone()[0])
            cursor.execute(
                "SELECT aggregate_type FROM qd_transactional_outbox WHERE event_id = %s",
                (result.outbox_event_id,),
            )
            self.assertEqual(("DURABLE_ENTRY_COMMAND",), cursor.fetchone())
        parsed = m.admission.parse_admission_outbox_event(self._outbox_event(result.outbox_event_id))
        self.assertIsInstance(parsed.subject, m.entry_v2.CancelTargetSubject)
        self.assertIsNone(parsed.risk_decision_id)

    def test_g4a_admitted_action_matrix_persists_and_parses_typed_events(self):
        actions = (
            m.order.OrderAction.OPEN,
            m.order.OrderAction.INCREASE,
            m.order.OrderAction.REDUCE,
            m.order.OrderAction.CLOSE,
            m.order.OrderAction.EMERGENCY_CLOSE,
            m.order.OrderAction.PROTECTION,
        )
        for action in actions:
            with self.subTest(action=action):
                value = self._graph(action)
                result = self._gateway(value).admit(self.connection, value)
                self.assertEqual(m.admission.EntryAdmissionDisposition.CREATED, result.disposition)
                self.assertIsNotNone(result.outbox_event_id)
                parsed = m.admission.parse_admission_outbox_event(self._outbox_event(result.outbox_event_id))
                self.assertIs(parsed.action, action)
                self.assertEqual(parsed.command_id, value.command_id)
                self.assertIsNotNone(parsed.risk_decision_id)
                if action in {m.order.OrderAction.OPEN, m.order.OrderAction.INCREASE}:
                    self.assertIsNotNone(parsed.reservation_id)
                else:
                    self.assertIsNone(parsed.reservation_id)
                self.connection.commit()

    def test_typed_risk_or_outbox_failure_rolls_back_the_partial_chain(self):
        value = self._graph()

        class FailingEntry:
            def persist_durable_entry(self, *_args, **_kwargs):
                raise m.admission.EntryAdmissionConflict("injected entry conflict")

        with self.assertRaises(m.admission.EntryAdmissionConflict):
            self._gateway(value, durable_entries=FailingEntry()).admit(self.connection, value)
        self.connection.rollback()
        self.assertEqual(0, self._count("qd_durable_entry_specifications", value.command_id, "command_id"))

        class FailingRisk:
            def evaluate_and_persist(self, *_args, **_kwargs):
                raise m.admission.EntryAdmissionConflict("injected risk conflict")

        with self.assertRaises(m.admission.EntryAdmissionConflict):
            self._gateway(value, durable_risk=FailingRisk()).admit(self.connection, value)
        self.connection.rollback()
        self.assertEqual(0, self._count("qd_durable_entry_specifications", value.command_id, "command_id"))

        class FailingOutbox:
            def persist_admission(self, *_args, **_kwargs):
                raise m.admission.EntryAdmissionConflict("injected outbox conflict")

        with self.assertRaises(m.admission.EntryAdmissionConflict):
            self._gateway(value, outbox=FailingOutbox()).admit(self.connection, value)
        self.connection.rollback()
        self.assertEqual(0, self._count("qd_durable_entry_specifications", value.command_id, "command_id"))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual((1,), cursor.fetchone())

    def test_two_connections_create_once_then_replay_without_raw_database_error(self):
        value = self._graph()
        outcomes = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def admit_once():
            connection = self._connection()
            try:
                barrier.wait(timeout=10)
                result = self._gateway(value).admit(connection, value)
                connection.commit()
                outcome = result.disposition
            except Exception as exc:
                connection.rollback()
                outcome = exc
            finally:
                connection.close()
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=admit_once, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), "gateway concurrency test timed out")
        self.assertCountEqual(
            outcomes,
            [m.admission.EntryAdmissionDisposition.CREATED, m.admission.EntryAdmissionDisposition.REPLAYED],
        )
        self.assertEqual(1, self._count("qd_durable_entry_specifications", value.command_id, "command_id"))

    def test_conflicting_durable_entry_facts_are_typed_and_leave_no_second_graph(self):
        value = self._graph()
        self._gateway(value).admit(self.connection, value)
        self.connection.commit()
        conflicting_specification = replace(value.specification, correlation_id="corr-conflict")
        conflicting = m.entry_v2.DurableEntryGraphV2(
            value.command_id,
            conflicting_specification,
            value.subject,
        )
        with self.assertRaises(m.durable_entry.DurableEntryConflict):
            self._gateway(conflicting).admit(self.connection, conflicting)
        self.connection.rollback()
        self.assertEqual(1, self._count("qd_durable_entry_specifications", value.command_id, "command_id"))


if __name__ == "__main__":
    unittest.main()
