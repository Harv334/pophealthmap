#!/usr/bin/env python3
"""Verify Fingertips data made it into ward_data.json.

Usage:
    python scripts/check_fingertips_data.py
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
wd = json.loads((REPO / 'ward_data.json').read_text(encoding='utf-8'))
meta = json.loads((REPO / 'scripts' / 'fingertips_metadata.json').read_text(encoding='utf-8'))

n_wards = len(wd['wards'])
print(f"Total wards in ward_data.json: {n_wards}")
print(f"Indicators in metadata sidecar: {len(meta)}")
print()

# Per-indicator coverage
print(f"{'Field':>12}  {'Coverage':>10}  Sample values  →  Indicator name")
print('-' * 110)
for m in meta[:20]:
    iid = m['indicator_id']
    field = f'ft_{iid}'
    vals = []
    for code, w in wd['wards'].items():
        v = (w.get('indicators') or {}).get(field)
        if v is not None:
            vals.append((code, v))
    cov = f"{len(vals)}/{n_wards}"
    samples = ', '.join(f'{c}={v}' for c, v in vals[:3]) or '(none)'
    name = (m.get('name') or '')[:50]
    print(f"  {field:>10}  {cov:>10}  {samples[:50]}  ←  {name}")

if len(meta) > 20:
    print(f"...and {len(meta)-20} more")

# Total indicator × ward cells populated
n_cells = sum(
    1 for w in wd['wards'].values()
      for k in (w.get('indicators') or {}).keys()
      if k.startswith('ft_')
)
print(f"\nTotal ft_* cells populated: {n_cells}")
print(f"Expected (33 × 168):         {33 * 168}")
