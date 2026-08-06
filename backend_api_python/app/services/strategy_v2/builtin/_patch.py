"""Inject placeholder universe/subscribe for any warmup value."""
import os, re
d = os.path.dirname(os.path.abspath(__file__))

for filename in sorted(os.listdir(d)):
    if not (filename.endswith('.py') and (filename.startswith('s0') or filename.startswith('a0'))):
        continue
    if filename.startswith('_'):
        continue
    path = os.path.join(d, filename)
    with open(path, 'r') as f:
        content = f.read()

    if 'def initialize' not in content or 'context.set_universe' in content:
        continue

    # Match any "def initialize(context):\n    context.set_warmup(N)"
    pattern = re.compile(r'(def initialize\(context\):\n)\s*context\.set_warmup\(\d+\)')
    new_init = r'\1    # Placeholder — runtime overrides from deployment config\n    context.set_universe(["Crypto:BTC/USDT@spot"])\n    context.subscribe(frequency="15m")\n    context.set_warmup(100)'

    new_content, count = pattern.subn(new_init, content)
    if count > 0:
        with open(path, 'w') as f:
            f.write(new_content)
        print(f'Fixed {filename}')

print('Done')