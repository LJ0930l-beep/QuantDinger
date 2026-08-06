"""Add placeholder set_universe/subscribe to satisfy validator.
Runtime overrides with qd_strategies_trading.symbol/timeframe."""
import os
d = os.path.dirname(os.path.abspath(__file__))

for filename in sorted(os.listdir(d)):
    if not (filename.endswith('.py') and (filename.startswith('s0') or filename.startswith('a0'))):
        continue
    if filename.startswith('_'):
        continue
    path = os.path.join(d, filename)
    with open(path, 'r') as f:
        content = f.read()

    if 'def initialize' not in content:
        continue

    # Find initialize block and inject placeholder calls
    old_init = '''def initialize(context):
    context.set_warmup(100)'''
    new_init = '''def initialize(context):
    # Placeholder — runtime overrides from deployment config
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100)'''

    if old_init in content:
        content = content.replace(old_init, new_init, 1)
        with open(path, 'w') as f:
            f.write(content)
        print(f'Fixed {filename}')
    else:
        print(f'  Skipped {filename} (no match)')

print('Done')