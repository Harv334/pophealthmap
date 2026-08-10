#!/usr/bin/env python3
"""Diagnose why a specific (ward, indicator) cell is empty.

Walks the full chain: ward → LSOAs → MSOA21 codes → fingertips raw value
for the chosen indicator → did aggregation produce a value?

Usage:
    # Indicator can be the numeric ID or a substring of its name
    python scripts/diagnose_indicator_ward.py "Brentford East" 93098
    python scripts/diagnose_indicator_ward.py "Wembley Park" "long-term unemployment"
"""
import csv
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

if len(sys.argv) < 3:
    sys.exit("Usage: diagnose_indicator_ward.py 'Ward Name' <indicator_id_or_name_substring>")

ward_name = sys.argv[1]
ind_query = sys.argv[2]

# 1. LSOA→MSOA
with open(REPO / 'scripts' / 'lsoa_to_msoa.csv') as f:
    rdr = csv.DictReader(f)
    lsoa_to_msoa = {r['LSOA21CD']: r['MSOA21CD'] for r in rdr}

# 2. LSOA→ward_code from index.html
html = (REPO / 'index.html').read_text(encoding='utf-8')
m = re.search(r'const LSOA_IMD\s*=\s*(\{.*?\});', html, re.DOTALL)
gj = json.loads(m.group(1))
lsoa_to_ward_code = {}
ward_code_to_name = {}
for feat in gj['features']:
    p = feat['properties']
    code = p.get('code')
    wcode = p.get('ward_code')
    wname = p.get('ward_name') or p.get('ward')
    if code and wcode:
        lsoa_to_ward_code[code] = wcode
    if wcode and wname:
        ward_code_to_name[wcode] = wname

# 3. LSOA populations from lsoa_data.json
ld = json.loads((REPO / 'lsoa_data.json').read_text(encoding='utf-8'))
lsoa_pop = {}
for code, row in ld.items():
    ind = row.get('indicators') if isinstance(row, dict) and 'indicators' in row else row
    if isinstance(ind, dict):
        v = ind.get('imd_denominator_mid2022') or ind.get('census_population')
        if v is not None:
            try: lsoa_pop[code] = float(v)
            except (ValueError, TypeError): pass

# 4. Resolve indicator
meta = json.loads((REPO / 'scripts' / 'fingertips_metadata.json').read_text(encoding='utf-8'))
match = None
if ind_query.isdigit():
    iid_target = int(ind_query)
    match = next((m for m in meta if m['indicator_id'] == iid_target), None)
else:
    q = ind_query.lower()
    for m in meta:
        if q in (m.get('name') or '').lower():
            match = m
            break
if not match:
    print(f"No indicator found matching {ind_query!r}")
    print("Available indicators:")
    for m in meta[:40]:
        print(f"  {m['indicator_id']:>6}  {m.get('name','?')[:80]}")
    sys.exit(1)
iid = match['indicator_id']
print(f"Resolved indicator: ft_{iid}  ({match.get('name')})")
print()

# 5. Resolve ward
matches = [(wc, wn) for wc, wn in ward_code_to_name.items()
           if wn.lower() == ward_name.lower()]
if not matches:
    matches = [(wc, wn) for wc, wn in ward_code_to_name.items()
               if ward_name.lower() in wn.lower()]
if not matches:
    print(f"No ward matching {ward_name!r}")
    sys.exit(1)
wcode, wname = matches[0]
print(f"Resolved ward: {wname}  (code={wcode})")
lsoas = [l for l, w in lsoa_to_ward_code.items() if w == wcode]
print(f"  LSOAs in ward: {lsoas}")
msoas = sorted({lsoa_to_msoa.get(l) for l in lsoas if lsoa_to_msoa.get(l)})
print(f"  MSOA21 codes:  {msoas}")
print()

# 6. Read fingertips_msoa_raw.csv and look up rows for these MSOAs × this indicator
raw_path = REPO / 'scripts' / 'fingertips_msoa_raw.csv'
print(f"Looking up ft_{iid} values in {raw_path.name} …")
hits = []
with open(raw_path, encoding='utf-8') as f:
    rdr = csv.DictReader(f)
    for row in rdr:
        if str(row['indicator_id']) != str(iid):
            continue
        if row['msoa_code'] not in msoas:
            continue
        hits.append(row)

if not hits:
    print(f"  ✗ NO ROWS in raw CSV for ft_{iid} × these MSOAs.")
    print(f"    Either the indicator is missing data for these MSOAs entirely,")
    print(f"    or the MSOAs were suppressed at source by Fingertips.")
else:
    print(f"  ✓ Found {len(hits)} rows:")
    for h in hits:
        v = h.get('value', '')
        try: vf = float(v)
        except (ValueError, TypeError): vf = None
        flag = ''
        if vf is None:
            flag = '  ← NULL/non-numeric'
        elif vf != vf:
            flag = '  ← NaN (suppressed)'
        print(f"    {h['msoa_code']:>12}  value={v!r:>10}  period={h.get('period','?')}{flag}")

# 7. Try the aggregation for this indicator + ward
print()
print("Aggregation:")
num, den = 0.0, 0.0
n_lsoa_used, n_lsoa_skipped = 0, 0
for lsoa in lsoas:
    msoa = lsoa_to_msoa.get(lsoa)
    if not msoa:
        n_lsoa_skipped += 1
        continue
    rec = next((h for h in hits if h['msoa_code'] == msoa), None)
    if not rec:
        n_lsoa_skipped += 1
        continue
    try: v = float(rec['value'])
    except (ValueError, TypeError):
        n_lsoa_skipped += 1
        continue
    if v != v:    # NaN
        n_lsoa_skipped += 1
        continue
    pop = lsoa_pop.get(lsoa, 0)
    if pop <= 0:
        n_lsoa_skipped += 1
        continue
    num += pop * v
    den += pop
    n_lsoa_used += 1
    print(f"    {lsoa}  pop={pop:>7.0f}  msoa_value={v:>8.3f}  → contributes")
if den > 0:
    print(f"  → ward value = {num/den:.4f}  (used {n_lsoa_used}/{len(lsoas)} LSOAs, "
          f"skipped {n_lsoa_skipped})")
else:
    print(f"  → no ward value computable. {n_lsoa_skipped} LSOAs all skipped (no MSOA data, "
          f"NaN, or zero population)")
