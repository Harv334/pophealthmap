#!/usr/bin/env python3
"""Diagnose why specific wards have no Fingertips data.

For each ward of interest, walks the LSOA-bridge join step by step:
    Ward → its LSOAs → their MSOA21 codes → are those codes in Fingertips raw?

Tells you exactly which step the chain is breaking at.

Usage:
    python scripts/diagnose_missing_wards.py "Brentford East" "Wembley Park"
"""
import csv
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

if len(sys.argv) < 2:
    print("Usage: diagnose_missing_wards.py 'Ward Name 1' 'Ward Name 2' ...")
    sys.exit(1)

target_names = [a for a in sys.argv[1:]]
target_lower = [t.lower() for t in target_names]

# 1. LSOA → MSOA21 from our slim lookup
with open(REPO / 'scripts' / 'lsoa_to_msoa.csv') as f:
    rdr = csv.DictReader(f)
    lsoa_to_msoa = {r['LSOA21CD']: r['MSOA21CD'] for r in rdr}

# 2. LSOA → ward (code + name) from index.html LSOA_IMD
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

# 3. Raw Fingertips MSOA codes (read scripts/fingertips_msoa_raw.csv)
raw_path = REPO / 'scripts' / 'fingertips_msoa_raw.csv'
ft_msoa_codes = set()
ft_indicators = set()
if raw_path.exists():
    with open(raw_path, encoding='utf-8') as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            ft_msoa_codes.add(r['msoa_code'])
            ft_indicators.add(r['indicator_id'])
print(f"Loaded {len(ft_msoa_codes)} unique MSOA codes from Fingertips raw "
      f"({len(ft_indicators)} indicators)")
print()

# 4. For each target ward, walk the chain
for tname in target_names:
    print(f"━━━ {tname} ━━━")
    # Find the ward code(s) matching this name (could be multiple if name ambiguous)
    matches = [(wc, wn) for wc, wn in ward_code_to_name.items()
               if wn.lower() == tname.lower()]
    if not matches:
        # Try contains
        matches = [(wc, wn) for wc, wn in ward_code_to_name.items()
                   if tname.lower() in wn.lower()]
    if not matches:
        print(f"  ✗ no ward found matching '{tname}'")
        print(f"    suggested: {sorted(set(ward_code_to_name.values()))[:8]} … etc")
        print()
        continue
    for wcode, wname in matches:
        # Find LSOAs in this ward
        lsoas = [l for l, w in lsoa_to_ward_code.items() if w == wcode]
        print(f"  ward_code={wcode}  name={wname!r}")
        print(f"  LSOAs in ward: {len(lsoas)}")
        if not lsoas:
            print(f"  ✗ NO LSOAs map to this ward_code in LSOA_IMD")
            continue
        # Each LSOA → MSOA21
        msoas21 = {lsoa_to_msoa.get(l) for l in lsoas if lsoa_to_msoa.get(l)}
        msoas21.discard(None)
        print(f"  MSOA21 codes covering ward: {sorted(msoas21)}")
        # Which of those MSOAs are present in Fingertips data?
        present = msoas21 & ft_msoa_codes
        missing = msoas21 - ft_msoa_codes
        print(f"  ✓ in Fingertips raw: {sorted(present) if present else '(none)'}")
        print(f"  ✗ missing from FT:   {sorted(missing) if missing else '(none)'}")
        if missing and not present:
            print(f"  → ALL of this ward's MSOAs are missing from Fingertips.")
            print(f"    Likely cause: MSOA boundaries changed between 2011 and 2021;")
            print(f"    Fingertips publishes at MSOA 2011 codes which differ from these.")
        elif missing:
            n_lsoa_missing = sum(1 for l in lsoas
                                 if lsoa_to_msoa.get(l) in missing)
            n_lsoa_present = sum(1 for l in lsoas
                                 if lsoa_to_msoa.get(l) in present)
            print(f"  → Partial: {n_lsoa_present}/{len(lsoas)} LSOAs hit, "
                  f"{n_lsoa_missing} LSOAs miss. Ward value computed from "
                  f"{n_lsoa_present} LSOAs only.")
    print()

# 5. Hint at the fix
print("━━━ next step ━━━")
print("If MSOA21 codes don't match what Fingertips publishes (MSOA 2011),")
print("we need a MSOA21→MSOA11 best-fit lookup from ONS:")
print("  https://geoportal.statistics.gov.uk/  → search 'MSOA 2011 2021 lookup'")
print("Save as scripts/msoa21_to_msoa11.csv (cols: MSOA21CD, MSOA11CD), then")
print("re-run fetch_fingertips.py — it'll use it to bridge the boundary change.")
