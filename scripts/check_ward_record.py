#!/usr/bin/env python3
"""Check what's in ward_data.json for specific wards.

Usage:
    python scripts/check_ward_record.py "Brentford East" "Wembley Park"
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
wd = json.loads((REPO / 'ward_data.json').read_text(encoding='utf-8'))

if len(sys.argv) < 2:
    sys.exit("Usage: check_ward_record.py 'Ward Name 1' 'Ward Name 2'")

for tname in sys.argv[1:]:
    print(f"\n━━━ {tname} ━━━")
    # Match by .name field (case-insensitive)
    matches = [(c, w) for c, w in wd['wards'].items()
               if (w.get('name') or '').lower() == tname.lower()]
    if not matches:
        # Fuzzy
        matches = [(c, w) for c, w in wd['wards'].items()
                   if tname.lower() in (w.get('name') or '').lower()]
    if not matches:
        print(f"  ✗ No ward record found in ward_data.json with name='{tname}'")
        # Show all ward names containing the first word
        first = tname.split()[0].lower()
        similar = sorted({w.get('name', '?') for c, w in wd['wards'].items()
                          if first in (w.get('name') or '').lower()})
        if similar:
            print(f"    similar names: {similar[:8]}")
        continue
    for code, w in matches:
        inds = w.get('indicators', {})
        ft = {k: v for k, v in inds.items() if k.startswith('ft_')}
        ft_id = {k: v for k, v in ft.items() if k[3:].isdigit()}     # ft_93283
        ft_named = {k: v for k, v in ft.items() if not k[3:].isdigit()}  # ft_life_expectancy_male
        non_ft = {k: v for k, v in inds.items() if not k.startswith('ft_')}
        print(f"  key in ward_data.json: {code!r}")
        print(f"  name in record:        {w.get('name')!r}")
        print(f"  borough (lad):         {w.get('lad')!r}")
        print(f"  ft_* fields total:     {len(ft)}")
        print(f"    ft_{{id}} (ours):     {len(ft_id)}")
        print(f"    ft_named (other):    {len(ft_named)}")
        print(f"  other indicators:      {len(non_ft)}")
        if ft_id:
            print(f"  sample ft_{{id}}:")
            for k, v in list(ft_id.items())[:5]:
                print(f"    {k}: {v}")
        if ft_named:
            print(f"  sample ft_named:")
            for k, v in list(ft_named.items())[:5]:
                print(f"    {k}: {v}")
        if not ft_id:
            print(f"  ✗ NO ft_{{id}} fields — our wirer points the dashboard at ft_{{id}} keys,")
            print(f"    so the dashboard will fail to render Fingertips data for this ward.")
