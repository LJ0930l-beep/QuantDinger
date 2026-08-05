"""Fix initialize() — remove context.params usage (forbidden by validator)."""
import os, re
d = os.path.dirname(os.path.abspath(__file__))

# Default symbol/timeframe per strategy type
DEFAULTS = {
    's01': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    's02': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    's03': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '5m'},
    's04': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '4h'},
    's05': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '1h'},
    'a01': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    'a02': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    'a03': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    'a04': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    'a05': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    'a06': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '4h'},
    'a07': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    'a08': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '4h'},
    'a09': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
    'a10': {'symbol': 'Crypto:BTC/USDT@spot', 'freq': '15m'},
}

for key, defaults in DEFAULTS.items():
    for filename in os.listdir(d):
        if filename.startswith(f'{key}_') and filename.endswith('.py'):
            path = os.path.join(d, filename)
            with open(path, 'r') as f:
                content = f.read()

            # Replace initialize body — remove context.params usage
            # Original: context.set_universe([str(context.params.get("symbol", "..."))])
            # New: hardcoded default, no context.params in initialize
            old_init = re.search(r'def initialize\(context\):.*?(?=\ndef handle_data)', content, re.DOTALL)
            if old_init:
                old_text = old_init.group(0)
                # Replace context.params.get("symbol", ...) with hardcoded default
                new_text = (
                    'def initialize(context):\n'
                    f'    context.set_universe(["{defaults["symbol"]}"])\n'
                    f'    context.subscribe(frequency="{defaults["freq"]}")\n'
                    '    context.set_warmup(100)'
                )
                content = content.replace(old_text, new_text, 1)
                print(f'Fixed initialize in {filename}')
            else:
                print(f'  No initialize match in {filename}')

            # In handle_data, replace context.params.get("frequency", "...") with the default
            content = content.replace(
                'str(context.params.get("frequency", "X"))',
                f'"{defaults["freq"]}"'
            )

            with open(path, 'w') as f:
                f.write(content)

print('Done')