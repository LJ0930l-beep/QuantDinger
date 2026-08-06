"""Deploy 10 strategies to DB."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2

os.environ['DATABASE_URL'] = 'postgresql://postgres:123456@127.0.0.1:5432/quantdinger_v8'
conn = psycopg2.connect(os.environ['DATABASE_URL'])
conn.autocommit = False
cur = conn.cursor()

from app.services.strategy_v2.builtin import BUILTIN_DSL_SOURCES

strategies = [
    ('bband-rsi', 'Bollinger Bands + RSI', 'Bollinger Bands mean reversion', '15m'),
    ('ema-crossover', 'EMA 8/21 Crossover', 'Fast/slow EMA with trend filter', '15m'),
    ('macd-strategy', 'MACD Crossover', 'MACD line crossing signal line', '15m'),
    ('supertrend-adx', 'SuperTrend + ADX', 'Supertrend flip with ADX filter', '15m'),
    ('ichimoku-cloud', 'Ichimoku Cloud', 'Tenkan/Kijun cross + cloud', '15m'),
    ('dual-ema-volume', 'Dual EMA + Volume', 'EMA cross + volume surge', '15m'),
    ('parabolic-sar', 'Parabolic SAR + EMA', 'SAR flip with MA filter', '15m'),
    ('keltner-breakout', 'Keltner Channel Breakout', 'Volatility expansion breakout', '15m'),
    ('rsi-scalper', 'RSI Scalper 5m', 'Pure RSI mean reversion', '5m'),
    ('turtle-trading', 'Turtle Trading', 'Donchian channel breakout', '15m'),
]

for key, name, desc, tf in strategies:
    code = BUILTIN_DSL_SOURCES.get(key, '')
    if not code:
        print(f'  SKIP {key}: no DSL source')
        continue

    cur.execute('''
        INSERT INTO qd_script_sources (user_id, name, description, code, asset_type, template_key, param_schema, visibility, status)
        VALUES (1, %s, %s, %s, 'script', %s, '{}', 'private', 'active')
        RETURNING id
    ''', (name, desc, code, key))
    source_id = cur.fetchone()[0]

    trading_config = json.dumps({
        'script_source_id': source_id, 'api_version': 2,
        'initial_capital': 1000.0, 'leverage': 1, 'leverage_enabled': False, 'params': {},
    })
    exchange_config = json.dumps({'exchange_id': 'gate', 'credential_id': 3896})

    cur.execute('''
        INSERT INTO qd_strategies_trading (user_id, strategy_name, execution_mode, market_type, symbol, timeframe, trading_config, exchange_config, status)
        VALUES (1, %s, 'paper', 'spot', 'BTC/USDT', %s, %s, %s, 'running')
        RETURNING id
    ''', (name, tf, trading_config, exchange_config))
    sid = cur.fetchone()[0]
    print(f'  {key}: source={source_id} strategy={sid}')

conn.commit()
cur.close()
conn.close()
print(f'Deployed {len(strategies)} strategies')
