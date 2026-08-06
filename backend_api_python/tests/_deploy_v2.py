"""Deploy 15 optimized strategies to DB."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2

os.environ['DATABASE_URL'] = 'postgresql://postgres:123456@127.0.0.1:5432/quantdinger_v8'
conn = psycopg2.connect(os.environ['DATABASE_URL'])
conn.autocommit = False
cur = conn.cursor()

from app.services.strategy_v2.builtin import ALL_STRATEGIES, STRATEGY_META

# Clear old
cur.execute("DELETE FROM qd_strategy_commands")
cur.execute("DELETE FROM qd_strategies_trading")
cur.execute("DELETE FROM qd_script_sources WHERE user_id = 1")

# Insert new
for mod in ALL_STRATEGIES:
    key = mod.__name__.split(".")[-1]
    meta = STRATEGY_META[key]
    code = mod.STRATEGY_CODE

    cur.execute('''
        INSERT INTO qd_script_sources (user_id, name, description, code, asset_type, template_key, param_schema, visibility, status, metadata)
        VALUES (1, %s, %s, %s, 'script', %s, '{}', 'private', 'active', %s)
        RETURNING id
    ''', (meta["name"], f'Suggested: {meta["timeframe"]} | Market: {",".join(meta["market"])} | Risk: {meta["risk"]}',
          code, key, json.dumps({"market_suitable": meta["market"], "suggested_timeframe": meta["timeframe"], "risk_level": meta["risk"]})))
    sid = cur.fetchone()[0]

    tc = json.dumps({"script_source_id": sid, "api_version": 2, "initial_capital": 1000.0, "leverage": 1, "leverage_enabled": False, "params": {}})
    ec = json.dumps({"exchange_id": "gate", "credential_id": 3896})
    tf = meta["timeframe"]
    symbol = "USStock:SPY" if "us_stock" in meta["market"] and "crypto" not in meta["market"] else "Crypto:BTC/USDT@spot"
    cur.execute('''
        INSERT INTO qd_strategies_trading (user_id, strategy_name, execution_mode, market_type, symbol, timeframe, trading_config, exchange_config, status)
        VALUES (1, %s, 'paper', 'spot', %s, %s, %s, %s, 'running')
        RETURNING id
    ''', (meta["name"], symbol, tf, tc, ec))
    print(f'  {key}: source={sid}')

conn.commit()
cur.close(); conn.close()
print(f'Deployed {len(ALL_STRATEGIES)} strategies')
