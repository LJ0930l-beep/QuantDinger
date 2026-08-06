"""Refactor strategies: remove hardcoded symbol/timeframe from initialize.
Runtime will inject from qd_strategies_trading.symbol and timeframe fields.
"""
import os, re
d = os.path.dirname(os.path.abspath(__file__))

for filename in os.listdir(d):
    if not (filename.endswith('.py') and (filename.startswith('s0') or filename.startswith('a0'))):
        continue
    if filename.startswith('_'):
        continue
    path = os.path.join(d, filename)
    with open(path, 'r') as f:
        content = f.read()

    # Replace initialize body — remove hardcoded set_universe/subscribe, let runtime inject
    old_init_pattern = r'def initialize\(context\):.*?(?=\ndef handle_data)'
    new_init = '''def initialize(context):
    # Universe and frequency are injected at runtime from deployment config.
    # User chooses symbol (BTC/ETH/etc) and timeframe (15m/1h/4h) at backtest/live setup.
    context.set_warmup(100)'''

    new_content = re.sub(old_init_pattern, new_init, content, count=1, flags=re.DOTALL)
    if new_content != content:
        with open(path, 'w') as f:
            f.write(new_content)
        print(f'  Simplified initialize in {filename}')
    else:
        print(f'  No change in {filename}')

print('Done')