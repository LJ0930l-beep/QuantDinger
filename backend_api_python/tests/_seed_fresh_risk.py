"""Seed fresh authoritative risk facts into the live DB."""
import uuid, os
from datetime import datetime, timezone
from hashlib import sha256

os.environ["DATABASE_URL"] = "postgresql://postgres:123456@127.0.0.1:5432/quantdinger_v8"
import psycopg2
import psycopg2.extras

psycopg2.extras.register_uuid()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.autocommit = False
cur = conn.cursor()
cur.execute("SELECT user_id FROM qd_exchange_credentials WHERE id=3896")
uid = cur.fetchone()[0]
cid = 3896
scope = "spot"
at = datetime.now(timezone.utc)
print(f"uid={uid}, cid={cid}, scope={scope}, at={at}")

suffix = uuid.uuid4().hex[:16]

def fp(label):
    return sha256(f"{label}|{suffix}|{at.isoformat()}".encode()).hexdigest()

cur.execute("""
    INSERT INTO qd_authoritative_risk_policies
    (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,strategy_scope,
     policy_identity,policy_version,policy_fingerprint,observed_at,max_age_seconds,reservation_ttl_seconds,
     valuation_currency,max_gross_notional,max_net_notional,max_instrument_notional,max_leverage,
     minimum_available_margin,max_daily_loss,max_drawdown_ratio)
    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,3600,30,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, market_type,
                 strategy_scope, policy_identity, policy_version, policy_fingerprint) DO NOTHING
""", (str(uuid.uuid4()), uid, cid, scope, "BTC_USDT", "spot", "__NON_STRATEGY__",
      "policy", "v1", fp("policy"), at, "USDT", 160, 700, 600, 4, 100, 100, 0.2))

cur.execute("""
    INSERT INTO qd_authoritative_instrument_risk_rules
    (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
     source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,quantity_to_quote_multiplier,initial_margin_ratio)
    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,3600,1,0.25)
    ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, market_type,
                 source_identity, source_version, source_fingerprint) DO NOTHING
""", (str(uuid.uuid4()), uid, cid, scope, "BTC_USDT", "spot", "USDT", "rule", "v1", fp("rule"), at))

cur.execute("""
    INSERT INTO qd_authoritative_account_risk_facts
    (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
     source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,gross_notional,net_notional,
     instrument_notional,available_margin,equity,peak_equity,daily_realized_pnl,account_facts_verified)
    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,3600,100,100,100,800,500,500,0,true)
    ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, market_type,
                 source_identity, source_version, source_fingerprint) DO NOTHING
""", (str(uuid.uuid4()), uid, cid, scope, "BTC_USDT", "spot", "USDT", "account", "v1", fp("account"), at))

for k in ("GLOBAL", "ACCOUNT", "STRATEGY"):
    cur.execute("""
        INSERT INTO qd_authoritative_kill_switch_observations
        (id,contract_version,tenant_id,credential_id,account_scope,strategy_scope,scope_kind,source_identity,
         source_version,source_fingerprint,observed_at,max_age_seconds,switch_version,enabled,mode)
        VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,3600,1,false,NULL)
        ON CONFLICT (tenant_id, credential_id, account_scope, strategy_scope, scope_kind,
                     source_identity, source_version, source_fingerprint) DO NOTHING
    """, (str(uuid.uuid4()), uid, cid, scope, "__NON_STRATEGY__", k, f"switch-{k.lower()}", "v1", fp(f"switch-{k}"), at))

cur.execute("""
    INSERT INTO qd_authoritative_market_observations
    (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
     price_type,price,source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,market_data_health)
    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,%s,%s,50,%s,%s,%s,%s,3600,'FRESH')
    ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, market_type,
                 valuation_currency, price_type, source_identity, source_version, source_fingerprint) DO NOTHING
""", (str(uuid.uuid4()), uid, cid, scope, "BTC_USDT", "spot", "USDT", "MARK", "market", "v1", fp("market"), at))

conn.commit()
print("ALL FRESH RISK FACTS SEEDED OK")
