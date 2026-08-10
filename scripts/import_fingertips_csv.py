#!/usr/bin/env python3
"""Import a manually-downloaded Fingertips CSV and aggregate to NWL wards.

This is the simpler alternative to fetch_fingertips.py — no API calls, no
fingertips_py dependency. Just point it at a CSV you downloaded from
https://fingertips.phe.org.uk/profile/local-health (Data → Download), MSOA
area type, NWL local authorities.

Usage:
    pip install pandas

    # Single CSV:
    python scripts/import_fingertips_csv.py scripts/fingertips_msoa.csv

    # Folder of per-borough CSVs (every .csv in the folder is concatenated):
    python scripts/import_fingertips_csv.py scripts/fingertips/

    # Dry run — print what it would do without modifying ward_data.json:
    python scripts/import_fingertips_csv.py scripts/fingertips/ --dry-run

What it does:
    1. Reads the CSV. The Fingertips export schema has columns like
       'Indicator ID', 'Indicator Name', 'Area Code', 'Area Name',
       'Area Type', 'Time period', 'Value', 'Lower CI 95.0 limit', etc.
    2. Filters to MSOA rows (Area Code starts with E02).
    3. For each (indicator, MSOA) keeps the most recent time period.
    4. Loads the LSOA→MSOA lookup from a sidecar CSV (you download once
       from ONS Open Geography Portal, see download instructions in the
       README at the bottom of this docstring).
    5. Loads LSOA→ward and LSOA populations from your existing dashboard
       data (lsoa_data.json + the embedded LSOA_IMD inside index.html).
    6. Aggregates each indicator from MSOA → ward via the LSOA-bridge
       population-weighted mean.
    7. Patches ward_data.json with new fields keyed `ft_{indicator_id}`.
    8. Writes scripts/fingertips_metadata.json so the UI wiring step
       (wire_fingertips_ui.py) can populate CATS / OV_META / dropdowns.

The LSOA→MSOA lookup file (one-time download):
    Go to https://geoportal.statistics.gov.uk/ and search for
    "LSOA (2021) to MSOA (2021) to LAD lookup". Download the CSV.
    Save it as scripts/lsoa_to_msoa.csv. It needs columns LSOA21CD and
    MSOA21CD (any other columns are ignored).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parent.parent

POLARITY_TO_WH = {
    'low is good':         True,
    'high is good':        False,
    'rag - low is good':   True,
    'rag - high is good':  False,
    'no judgement':        None,
    'not applicable':      None,
}


def _resolve_col(df_columns, candidates):
    """Find the first matching column name (case-insensitive, ignoring
    whitespace/punctuation differences). Fingertips exports have varied
    column casings over the years."""
    norm = lambda s: re.sub(r'[^a-z0-9]+', '', s.lower())
    cols_norm = {norm(c): c for c in df_columns}
    for cand in candidates:
        if norm(cand) in cols_norm:
            return cols_norm[norm(cand)]
    return None


def load_lsoa_msoa_lookup():
    """Load LSOA→MSOA from scripts/lsoa_to_msoa.csv. The user downloads this
    once from ONS Open Geography Portal."""
    p = REPO / 'scripts' / 'lsoa_to_msoa.csv'
    if not p.exists():
        print(f"\nERROR: missing LSOA→MSOA lookup at {p}", file=sys.stderr)
        print("Download from https://geoportal.statistics.gov.uk/ ", file=sys.stderr)
        print("(search 'LSOA 2021 to MSOA 2021 to LAD'), save as scripts/lsoa_to_msoa.csv", file=sys.stderr)
        sys.exit(2)
    import pandas as pd
    df = pd.read_csv(p, dtype=str, encoding='utf-8-sig')
    lsoa_col = _resolve_col(df.columns, ['LSOA21CD', 'LSOA11CD', 'lsoa_code'])
    msoa_col = _resolve_col(df.columns, ['MSOA21CD', 'MSOA11CD', 'msoa_code'])
    if not lsoa_col or not msoa_col:
        raise SystemExit(f"Lookup CSV is missing LSOA or MSOA column. Have: {list(df.columns)}")
    return dict(zip(df[lsoa_col], df[msoa_col]))


def load_lsoa_ward_and_pop():
    """LSOA→ward from index.html LSOA_IMD; LSOA pop from lsoa_data.json."""
    html = (REPO / 'index.html').read_text(encoding='utf-8')
    m = re.search(r'const LSOA_IMD\s*=\s*(\{.*?\});', html, re.DOTALL)
    if not m:
        raise SystemExit("LSOA_IMD not found in index.html")
    gj = json.loads(m.group(1))
    lsoa_to_ward = {}
    for f in gj.get('features', []):
        p = f.get('properties') or {}
        c, w = p.get('code'), p.get('ward_code')
        if c and w:
            lsoa_to_ward[c] = w
    ld = json.loads((REPO / 'lsoa_data.json').read_text(encoding='utf-8'))
    lsoa_pop = {}
    for code, row in ld.items():
        ind = row.get('indicators') if isinstance(row, dict) and 'indicators' in row else row
        if not isinstance(ind, dict):
            continue
        v = ind.get('imd_denominator_mid2022')
        if v is None:
            v = ind.get('census_population')
        if v is not None:
            try:
                lsoa_pop[code] = float(v)
            except (ValueError, TypeError):
                pass
    return lsoa_to_ward, lsoa_pop


def parse_fingertips_csv(csv_path):
    """Read one OR a folder of Fingertips export CSVs, return:
        msoa_data:  {indicator_id: {msoa_code: {value, period, lower, upper}}}
        meta_by_id: {indicator_id: {name, unit, polarity}}
    Keeps only the most recent period per (indicator, MSOA). When a folder is
    given, every .csv inside is concatenated (handy when you've downloaded
    one CSV per borough from the Fingertips data page).
    """
    import pandas as pd
    if csv_path.is_dir():
        csvs = sorted(csv_path.glob('*.csv'))
        if not csvs:
            raise SystemExit(f"No .csv files found in {csv_path}")
        print(f"  reading {len(csvs)} CSV files from folder")
        frames = []
        for f in csvs:
            try:
                d = pd.read_csv(f, dtype=str, encoding='utf-8-sig', low_memory=False)
                d['_source_file'] = f.name
                frames.append(d)
                print(f"    {f.name}: {len(d)} rows")
            except Exception as e:
                print(f"    {f.name}: SKIPPED ({e})")
        df = pd.concat(frames, ignore_index=True)
        print(f"  concatenated total: {len(df)} rows")
    else:
        df = pd.read_csv(csv_path, dtype=str, encoding='utf-8-sig', low_memory=False)
    iid_col   = _resolve_col(df.columns, ['Indicator ID', 'IndicatorID', 'indicator_id'])
    iname_col = _resolve_col(df.columns, ['Indicator Name', 'IndicatorName', 'indicator_name'])
    code_col  = _resolve_col(df.columns, ['Area Code', 'AreaCode'])
    type_col  = _resolve_col(df.columns, ['Area Type', 'AreaType'])
    period_col= _resolve_col(df.columns, ['Time period', 'TimePeriod', 'Time_period'])
    val_col   = _resolve_col(df.columns, ['Value'])
    lo_col    = _resolve_col(df.columns, ['Lower CI 95.0 limit', 'Lower CI', 'lower'])
    hi_col    = _resolve_col(df.columns, ['Upper CI 95.0 limit', 'Upper CI', 'upper'])
    unit_col  = _resolve_col(df.columns, ['Value note', 'Unit', 'Indicator unit'])
    pol_col   = _resolve_col(df.columns, ['Polarity'])

    if not all([iid_col, code_col, val_col]):
        raise SystemExit(
            f"CSV is missing required columns. Have: {list(df.columns)}\n"
            f"Need at least Indicator ID, Area Code, Value."
        )

    msoa_data = {}
    meta_by_id = {}
    for _, row in df.iterrows():
        try:
            iid = int(row[iid_col])
        except (ValueError, TypeError):
            continue
        code = str(row[code_col] or '').strip()
        if not code.startswith('E02'):  # MSOA codes only
            continue
        try:
            v = float(row[val_col])
        except (ValueError, TypeError):
            continue
        period = str(row[period_col] or '') if period_col else ''
        rec_existing = msoa_data.setdefault(iid, {}).get(code)
        if rec_existing and period < rec_existing.get('period', ''):
            continue
        msoa_data[iid][code] = {
            'value':  v,
            'period': period,
            'lower':  row[lo_col] if lo_col else '',
            'upper':  row[hi_col] if hi_col else '',
        }
        if iid not in meta_by_id:
            meta_by_id[iid] = {
                'name':     str(row[iname_col]).strip() if iname_col else f'Indicator {iid}',
                'unit':     str(row[unit_col]).strip() if unit_col else '',
                'polarity': str(row[pol_col]).strip() if pol_col else '',
            }
    return msoa_data, meta_by_id


def aggregate_to_ward(msoa_data, lsoa_to_msoa, lsoa_to_ward, lsoa_pop):
    out = {}
    for iid, msoa_vals in msoa_data.items():
        ward_acc = {}
        for lsoa, ward_code in lsoa_to_ward.items():
            msoa = lsoa_to_msoa.get(lsoa)
            if not msoa:
                continue
            rec = msoa_vals.get(msoa)
            if not rec:
                continue
            pop = lsoa_pop.get(lsoa)
            if pop is None or pop <= 0:
                continue
            slot = ward_acc.setdefault(ward_code, [0.0, 0.0])
            slot[0] += pop * rec['value']
            slot[1] += pop
        out[iid] = {w: (n / d) for w, (n, d) in ward_acc.items() if d > 0}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('csv', help='path to Fingertips MSOA CSV (or a folder of CSVs)')
    ap.add_argument('--dry-run', action='store_true', help='do not modify ward_data.json')
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found", file=sys.stderr)
        sys.exit(2)

    print(f"\n=== STEP 1: parse {csv_path}{' (folder)' if csv_path.is_dir() else ''} ===")
    msoa_data, meta_by_id = parse_fingertips_csv(csv_path)
    print(f"  parsed {len(msoa_data)} indicators across "
          f"{sum(len(v) for v in msoa_data.values())} (indicator × MSOA) rows")

    print("\n=== STEP 2: load LSOA→MSOA lookup ===")
    lsoa_to_msoa = load_lsoa_msoa_lookup()
    print(f"  {len(lsoa_to_msoa)} LSOA→MSOA entries")

    print("\n=== STEP 3: load LSOA→ward + populations ===")
    lsoa_to_ward, lsoa_pop = load_lsoa_ward_and_pop()
    print(f"  {len(lsoa_to_ward)} LSOA→ward entries, {len(lsoa_pop)} LSOAs with population")

    print("\n=== STEP 4: aggregate MSOA → ward via LSOA bridge ===")
    ward_data = aggregate_to_ward(msoa_data, lsoa_to_msoa, lsoa_to_ward, lsoa_pop)
    n_with = sum(1 for v in ward_data.values() if v)
    print(f"  produced ward aggregates for {n_with}/{len(ward_data)} indicators")

    print("\n=== STEP 5: write metadata sidecar ===")
    meta_out = REPO / 'scripts' / 'fingertips_metadata.json'
    meta_records = []
    for iid in sorted(ward_data.keys()):
        m = meta_by_id.get(iid, {})
        polarity = m.get('polarity', '') or ''
        meta_records.append({
            'indicator_id': iid,
            'name':         m.get('name', f'Indicator {iid}'),
            'unit':         m.get('unit', ''),
            'polarity':     polarity,
            'wh':           POLARITY_TO_WH.get(polarity.lower()),
        })
    meta_out.write_text(json.dumps(meta_records, indent=2), encoding='utf-8')
    print(f"  wrote {meta_out}")

    if args.dry_run:
        print("\n=== --dry-run: skipping ward_data.json patch ===")
        # Print sample rows for sanity check
        print("\nSample (first 5 indicators × 3 wards each):")
        for iid in list(ward_data.keys())[:5]:
            print(f"  ft_{iid}  ({meta_by_id.get(iid, {}).get('name','?')[:50]})")
            for wcode in list(ward_data[iid].keys())[:3]:
                print(f"    {wcode}: {ward_data[iid][wcode]:.3f}")
        return

    print("\n=== STEP 6: patch ward_data.json ===")
    wpath = REPO / 'ward_data.json'
    wd = json.loads(wpath.read_text(encoding='utf-8'))
    n_cells = 0
    n_wards = set()
    for iid, ward_vals in ward_data.items():
        key = f'ft_{iid}'
        for wcode, v in ward_vals.items():
            if v is None or wcode not in wd['wards']:
                continue
            wd['wards'][wcode].setdefault('indicators', {})
            wd['wards'][wcode]['indicators'][key] = round(v, 4)
            n_cells += 1
            n_wards.add(wcode)
    wd.setdefault('metadata', {})
    wd['metadata']['fingertips_added']           = datetime.utcnow().isoformat() + 'Z'
    wd['metadata']['fingertips_indicator_count'] = len(ward_data)
    wd['metadata']['fingertips_source_csv']      = str(csv_path.name)
    wpath.write_text(json.dumps(wd, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"  patched {n_cells} cells across {len(n_wards)} wards")
    print(f"\nDONE. Now run: python scripts/wire_fingertips_ui.py")


if __name__ == '__main__':
    main()
