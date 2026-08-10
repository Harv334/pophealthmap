#!/usr/bin/env python3
"""Patch fingertips_suppressed lists in ward_data.json.

Reads scripts/fingertips_msoa_raw.csv (already fetched) and re-derives the
per-ward suppression flags using the new "row-omission" rule: an MSOA that
appears in the dataset for OTHER indicators but is missing from a given
indicator is treated as suppressed for that indicator (Fingertips omits
suppressed rows below their privacy/quality threshold rather than including
them with NaN).

Wards whose serving MSOAs ALL fall into that category get added to
fingertips_suppressed, so the dashboard hover tooltip says "Suppressed by
Fingertips" instead of generic "no data".

Run after a successful fetch_fingertips.py run if you don't want to re-fetch
just to refresh the suppression flags.
"""
import csv
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 1. LSOA→MSOA21
with open(REPO / 'scripts' / 'lsoa_to_msoa.csv') as f:
    lsoa_to_msoa = {r['LSOA21CD']: r['MSOA21CD'] for r in csv.DictReader(f)}

# 2. LSOA→ward_code from index.html LSOA_IMD
html = (REPO / 'index.html').read_text(encoding='utf-8')
m = re.search(r'const LSOA_IMD\s*=\s*(\{.*?\});', html, re.DOTALL)
gj = json.loads(m.group(1))
lsoa_to_ward = {}
for feat in gj['features']:
    p = feat['properties']
    if p.get('code') and p.get('ward_code'):
        lsoa_to_ward[p['code']] = p['ward_code']

# 3. Load raw Fingertips data per (indicator, MSOA)
msoa_data = {}      # iid → {msoa_code: True (had a numeric row)}
known_msoas = set()  # all MSOAs Fingertips returned ANY data for
with open(REPO / 'scripts' / 'fingertips_msoa_raw.csv') as f:
    for row in csv.DictReader(f):
        try:
            iid = int(row['indicator_id'])
        except (ValueError, TypeError):
            continue
        msoa = row['msoa_code']
        if not msoa.startswith('E02'):
            continue
        known_msoas.add(msoa)
        try:
            v = float(row['value'])
            if v != v:  # NaN
                continue
        except (ValueError, TypeError):
            continue
        msoa_data.setdefault(iid, {})[msoa] = True

print(f"raw CSV: {len(known_msoas)} unique MSOAs, {len(msoa_data)} indicators")

# 4. For each (indicator, ward), check if all serving MSOAs are missing
#    a value AND the MSOAs themselves are known to Fingertips.
suppressed = {}  # iid → set(ward_code)
ward_msoas = {}
for lsoa, wcode in lsoa_to_ward.items():
    msoa = lsoa_to_msoa.get(lsoa)
    if msoa:
        ward_msoas.setdefault(wcode, set()).add(msoa)

for iid, vals in msoa_data.items():
    for wcode, msoas in ward_msoas.items():
        # MSOAs known to Fingertips overall but missing for THIS indicator
        relevant = {m for m in msoas if m in known_msoas}
        if not relevant:
            continue
        missing = {m for m in relevant if m not in vals}
        if missing == relevant:  # all serving MSOAs lack a value
            suppressed.setdefault(iid, set()).add(wcode)

# 5. Patch ward_data.json
wd_path = REPO / 'ward_data.json'
wd = json.loads(wd_path.read_text(encoding='utf-8'))

# Reset existing fingertips_suppressed lists
for w in wd['wards'].values():
    w.pop('fingertips_suppressed', None)

# Build the per-ward keys to flag
total = 0
for iid, wards in suppressed.items():
    key = f'ft_{iid}'
    for wcode in wards:
        if wcode in wd['wards']:
            wd['wards'][wcode].setdefault('fingertips_suppressed', []).append(key)
            total += 1

# Sort each list for deterministic output
for w in wd['wards'].values():
    if 'fingertips_suppressed' in w:
        w['fingertips_suppressed'] = sorted(w['fingertips_suppressed'])

# Write back. allow_nan=False so NaN can never sneak in and break browser parse.
wd_path.write_text(
    json.dumps(wd, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
    encoding='utf-8',
)

flagged_wards = sum(1 for w in wd['wards'].values() if w.get('fingertips_suppressed'))
print(f"patched: {total} (ward × indicator) suppression flags across {flagged_wards} wards")

# Spot-check the wards we know are problematic
for tname in ('Brentford East', 'Wembley Park'):
    rec = next((w for w in wd['wards'].values() if w.get('name') == tname), None)
    if rec:
        sl = rec.get('fingertips_suppressed', [])
        print(f"  {tname}: {len(sl)} suppression flags  e.g. {sl[:5]}{'…' if len(sl)>5 else ''}")
