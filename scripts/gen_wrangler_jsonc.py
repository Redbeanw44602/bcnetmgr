import json5
import json

with open('wrangler.real.jsonc', 'r', encoding='utf-8') as wrangler_real:
    data = json5.load(wrangler_real)

# https://developers.cloudflare.com/workers/platform/limits/#environment-variables
workers_limit = 5 * 1024

print(f'workers limit: {workers_limit} bytes')

new_vars = {}

for key, value in data['vars'].items():
    if not key.startswith('_'):
        new_vars[key] = value
    else:
        new_value = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
        new_value_size = len(new_value.encode('utf-8'))
        print(f'encoding {key}: {new_value_size} bytes')
        if new_value_size > workers_limit:
            print(f"warning! --- {key} may be exceeded cloudflare worker's limit!")
        new_vars[key] = new_value

data['vars'] = new_vars

with open('wrangler.jsonc', 'w') as wrangler:
    wrangler.write(json.dumps(data, indent=4))
