"""Diagnostic: project authority facts then admit OPEN/PAPER."""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://quantdinger_test:quantdinger_test@127.0.0.1:5432/quantdinger_test",
)
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.autocommit = False
cur = conn.cursor()
suffix = uuid.uuid4().hex[:10]
cur.execute(
    "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
    (f"diag_{suffix}", "x"),
)
uid = cur.fetchone()[0]
cur.execute(
    "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) VALUES (%s, 'gate', '{}') RETURNING id",
    (uid,),
)
cid = cur.fetchone()[0]
scope = f"account-{suffix}"
at = datetime(2026, 8, 5, tzinfo=timezone.utc)
values = (uid, cid, scope, "BTC_USDT", "spot")
cur.execute("""
    INSERT INTO qd_authoritative_risk_policies
    (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,strategy_scope,
     policy_identity,policy_version,policy_fingerprint,observed_at,max_age_seconds,reservation_ttl_seconds,
     valuation_currency,max_gross_notional,max_net_notional,max_instrument_notional,max_leverage,
     minimum_available_margin,max_daily_loss,max_drawdown_ratio)
    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'__NON_STRATEGY__','policy','v1',%s,%s,60,30,'USDT',160,700,600,4,100,100,0.2)
""", (str(uuid.uuid4()), *values, "a" * 64, at))
cur.execute("""
    INSERT INTO qd_authoritative_instrument_risk_rules
    (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
     source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,quantity_to_quote_multiplier,initial_margin_ratio)
    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','rule','v1',%s,%s,60,1,0.25)
""", (str(uuid.uuid4()), *values, "b" * 64, at))
cur.execute("""
    INSERT INTO qd_authoritative_account_risk_facts
    (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
     source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,gross_notional,net_notional,
     instrument_notional,available_margin,equity,peak_equity,daily_realized_pnl,account_facts_verified)
    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','account','v1',%s,%s,60,100,100,100,800,500,500,0,true)
""", (str(uuid.uuid4()), *values, "c" * 64, at))
cur.execute("""
    INSERT INTO qd_reconciliation_checkpoints
    (id,tenant_id,credential_id,exchange,market_type,account_scope,instrument_id,status,evidence_hash,version,updated_at,risk_max_age_seconds)
    VALUES (%s,%s,%s,'gate','spot',%s,'BTC_USDT','HEALTHY',%s,1,%s,60)
""", (str(uuid.uuid4()), uid, cid, scope, "d" * 64, at))
for kind, letter in (("GLOBAL", "e"), ("ACCOUNT", "f"), ("STRATEGY", "0")):
    cur.execute("""
        INSERT INTO qd_authoritative_kill_switch_observations
        (id,contract_version,tenant_id,credential_id,account_scope,strategy_scope,scope_kind,source_identity,
         source_version,source_fingerprint,observed_at,max_age_seconds,switch_version,enabled,mode)
        VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,'__NON_STRATEGY__',%s,%s,'v1',%s,%s,60,1,false,NULL)
    """, (str(uuid.uuid4()), uid, cid, scope, kind, f"switch-{kind.lower()}", letter * 64, at))
cur.execute("""
    INSERT INTO qd_authoritative_market_observations
    (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
     price_type,price,source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,market_data_health)
    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','MARK',50,'market','v1',%s,%s,60,'FRESH')
""", (str(uuid.uuid4()), *values, "1" * 64, at))
conn.commit()

from app.domain.gate_read_snapshot_contracts import build_gate_read_snapshot
from app.domain.gate_vertical_read_contracts import (
    GateAuthFacts,
    GateInstrumentRuleSnapshot,
    GatePermission,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment

obs = datetime(2026, 8, 5, tzinfo=timezone.utc)
auth = GateAuthFacts(
    "gate", AssetMarketType.SPOT, CapabilityEnvironment.TESTNET, scope,
    f"credential-{cid}", (GatePermission.READ_ACCOUNT,), "gate-private-read-v1", obs,
)
inst = (
    GateInstrumentRuleSnapshot(
        "gate", AssetMarketType.SPOT, "BTC_USDT", Decimal("0.1"), Decimal("0.000001"),
        Decimal("0.00001"), Decimal("3"), "gate-private-read-instrument-v1", obs,
    ),
)
snap = build_gate_read_snapshot(auth, balances=(), instruments=inst, positions=(), observed_at=obs)

from app.domain.runtime_entry_authority_projection_contracts import (
    build_instrument_authority_facts,
    build_instrument_rule_snapshot_facts,
    build_scope_binding_facts,
)
from app.services.runtime_entry_authority_facts_repository import RuntimeEntryAuthorityFactsRepository

repo = RuntimeEntryAuthorityFactsRepository()
repo.persist_scope_binding(conn, build_scope_binding_facts(snap, tenant_id=uid, credential_id=cid))
for r in build_instrument_rule_snapshot_facts(snap):
    repo.persist_instrument_rule_snapshot(conn, r)
for a in build_instrument_authority_facts(
    snap, build_instrument_rule_snapshot_facts(snap), tenant_id=uid, credential_id=cid, account_scope=scope,
):
    repo.persist_instrument_authority(conn, a)
conn.commit()
print("PROJECTION OK")

payload = {
    "source": "REST",
    "mode": "PAPER",
    "credential_id": cid,
    "instrument_id": "BTC_USDT",
    "market_type": "spot",
    "action": "OPEN",
    "side": "BUY",
    "quantity": "0.001",
    "quantity_semantics": "ABSOLUTE",
    "execution_kind": "MARKET",
    "position_side": "NET",
    "reduce_only": False,
    "close_all": False,
    "idempotency_key": f"k-{uuid.uuid4().hex[:8]}",
    "correlation_id": f"c-{uuid.uuid4().hex[:8]}",
    "occurred_at": "2026-08-05T00:00:00+00:00",
}
from app.services.runtime_entry_admission_http_service import admit_runtime_entry_payload_caller_owned

try:
    result, graph = admit_runtime_entry_payload_caller_owned(conn, payload, tenant_id=uid, actor_id=str(uid))
    print("DISPOSITION:", result.disposition)
    conn.commit()
except Exception:
    conn.rollback()
    import traceback

    traceback.print_exc()
