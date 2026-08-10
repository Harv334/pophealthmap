"""Repair lsoa_data.json: salvage parseable records, overlay authoritative IMD,
rewrite atomically with byte-count verification. Handles virtiofs short-write bug."""
import json, os, sys
import pandas as pd

PATH = 'lsoa_data.json'

# ---- Step 1: salvage parseable records ----
with open(PATH, 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()

dec = json.JSONDecoder()
result = {}
n = len(text)

def skip_ws(s, i):
    while i < n and s[i] in ' \t\n\r':
        i += 1
    return i

start = text.find('{')
p = skip_ws(text, start + 1)
records_ok = 0
while p < n and text[p] != '}':
    if text[p] != '"':
        break
    try:
        key, end = dec.raw_decode(text[p:])
    except Exception:
        break
    if not isinstance(key, str):
        break
    p += end
    p = skip_ws(text, p)
    if p >= n or text[p] != ':':
        break
    p += 1
    p = skip_ws(text, p)
    try:
        val, end = dec.raw_decode(text[p:])
    except Exception:
        break
    p += end
    result[key] = val
    records_ok += 1
    p = skip_ws(text, p)
    if p < n and text[p] == ',':
        p += 1
        p = skip_ws(text, p)
    else:
        break

print(f'salvaged records: {records_ok}')
print(f'last salvaged key: {list(result.keys())[-1] if result else None}')

# ---- Step 2: overlay IMD from parquet ----
df = pd.read_parquet('data/demographics/imd2025.parquet')
imd_cols = ['imd_score','imd_decile','imd_rank','income_score','employment_score',
            'education_score','health_score','crime_score','barriers_score','environment_score']

added = 0
for _, row in df.iterrows():
    code = row['LSOA21CD']
    rec = result.get(code)
    if rec is None:
        rec = {}
        result[code] = rec
        added += 1
    for c in imd_cols:
        v = row[c]
        if c in ('imd_decile','imd_rank'):
            rec[c] = int(v)
        else:
            rec[c] = round(float(v), 3)

print(f'imd overwritten for all {len(df)} LSOAs (new stubs: {added})')
print(f'final record count: {len(result)}')

for k in ['E01000001','E01035712','E01035713','E01035722']:
    if k in result:
        r = result[k]
        print(f'  {k}: imd_decile={r.get("imd_decile")} health_score={r.get("health_score")} education_score={r.get("education_score")} keys={len(r)}')

# ---- Step 3: atomic write with byte-count verification + retry ----
payload = json.dumps(result, separators=(',',':'), ensure_ascii=False).encode('utf-8')
expected = len(payload)
tmp = PATH + '.tmp'

for attempt in range(4):
    with open(tmp, 'wb') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    actual = os.path.getsize(tmp)
    print(f'attempt {attempt+1}: expected={expected} actual={actual}')
    if actual == expected:
        break
else:
    os.unlink(tmp)
    print('FAILED — short write persisted after 4 attempts')
    sys.exit(1)

# Verify by reparsing tmp
with open(tmp, 'r', encoding='utf-8') as f:
    test = json.load(f)
print(f'reparse ok: {len(test)} records')

os.replace(tmp, PATH)
final = os.path.getsize(PATH)
print(f'REPLACED {PATH}  final size: {final}')
if final != expected:
    print(f'WARNING: final size {final} != expected {expected}')
    sys.exit(2)
print('OK')
