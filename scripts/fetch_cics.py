#!/usr/bin/env python3
"""Fetch + geocode + filter Community Interest Companies (CICs) for NW London.

Input:
    .cache/companies_house/BasicCompanyDataAsOneFile-YYYY-MM-DD.zip
        (Companies House monthly snapshot, all UK companies — ~5GB unzipped.
         Download from http://download.companieshouse.gov.uk/en_output.html)

    .cache/onspd/ONSPD_<MONTH>_<YEAR>_UK.zip
        (already used by fetch_all_data.py for postcode → LSOA/LAD/ward)

Output:
    cics.json
        Array of records mirroring vcse_data.json's shape (subset of fields
        applicable to CICs — no income or thematic descriptions, but a SIC-
        derived "what" bucket so they render with the same colour palette
        as charities).

Usage:
    python scripts/fetch_cics.py
    python scripts/fetch_cics.py --bulk path/to/BasicCompanyDataAsOneFile.zip
    python scripts/fetch_cics.py --include-dissolved   # keep dissolved/struck-off

A CIC by definition is a community-interest entity (limited company with
mission lock + asset lock). Companies House publishes the live register as
part of its monthly bulk product; this script extracts that subset.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from functools import lru_cache

REPO = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO / '.cache'

# ── NW London scope (mirrors fetch_all_data.py) ────────────────────────────
BOROUGHS = [
    ("Brent",                 "E09000005", ["HA", "NW", "W"]),
    ("Ealing",                "E09000009", ["W", "UB", "TW", "NW"]),
    ("Hammersmith & Fulham",  "E09000013", ["W", "SW"]),
    ("Harrow",                "E09000015", ["HA", "NW"]),
    ("Hillingdon",            "E09000017", ["UB", "HA", "TW"]),
    ("Hounslow",              "E09000018", ["TW", "W", "UB"]),
    ("Kensington & Chelsea",  "E09000020", ["W", "SW"]),
    ("City of Westminster",   "E09000033", ["W", "NW", "WC", "SW"]),
]
NWL_LADS = {b[1] for b in BOROUGHS}
LAD_NAMES = {b[1]: b[0] for b in BOROUGHS}
POSTCODE_AREAS = sorted({p for b in BOROUGHS for p in b[2]})

# ── SIC → cause bucket. Mirrors VCSE_BUCKET_RULES in index.html so CICs use
# the same colour palette as charities. Keyword match on SIC description text
# (column SicText_1..4) — Companies House publishes the SIC text inline so we
# don't need a separate SIC code lookup table.
BUCKET_RULES = [
    # (bucket_id, [keywords searched in SIC text, lowercased])
    ('health_disability',   ['health', 'medical', 'hospital', 'care of the elderly',
                             'residential care', 'social work', 'mental health',
                             'physio', 'pharmac', 'dental', 'nursing', 'disability']),
    ('social_determinants', ['housing', 'food', 'temporary accommodation',
                             'social services', 'employment activities',
                             'support services', 'welfare']),
    ('education',           ['education', 'training', 'school', 'pre-primary',
                             'primary education', 'secondary education',
                             'tuition', 'tutoring', 'youth work', 'libraries']),
    ('arts_sport_rec',      ['arts', 'creative', 'museums', 'cultural',
                             'sport', 'recreation', 'fitness', 'leisure',
                             'performing', 'theatre', 'music']),
    ('community_faith',     ['religious', 'community', 'membership organi',
                             'civic', 'voluntary', 'churches']),
]
DEFAULT_BUCKET = 'other'


def normalise_postcode(s: str) -> str:
    return re.sub(r'\s+', '', (s or '').upper())


def postcode_area(pc_no_space: str) -> str:
    """First 1-2 letters of postcode (e.g. 'NW1 2AB' -> 'NW')."""
    m = re.match(r'^([A-Z]+)', pc_no_space)
    return m.group(1) if m else ''


@lru_cache(maxsize=1)
def get_postcode_lookup() -> dict:
    """Returns {postcode_no_spaces: (lat, lng, LSOA21CD, LAD25CD, WD25CD)}.
    Loads only postcodes in NW London postcode areas to keep memory small."""
    cache = CACHE_DIR / 'onspd'
    zips = sorted(cache.glob('ONSPD_*_UK.zip'))
    if not zips:
        raise FileNotFoundError(
            f"No ONSPD zip in {cache}.\n"
            "Download from https://geoportal.statistics.gov.uk/ "
            f"(search 'ONS Postcode Directory' full zip) and drop in {cache}."
        )
    path = zips[-1]
    print(f"  ONSPD: loading from {path.name}", file=sys.stderr)
    lk: dict = {}
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            if not member.endswith('.csv') or '/multi_csv/' not in member:
                continue
            stem = Path(member).stem
            area = stem.split('_')[-1]
            if area not in POSTCODE_AREAS:
                continue
            with z.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding='utf-8')
                r = csv.DictReader(text)
                for row in r:
                    if (row.get('doterm') or '').strip():
                        continue
                    try:
                        lat = float(row['lat']); lng = float(row['long'])
                    except (ValueError, TypeError, KeyError):
                        continue
                    if lat == 99.999999:
                        continue
                    pcd = normalise_postcode(row.get('pcds', ''))
                    if not pcd:
                        continue
                    lk[pcd] = (lat, lng,
                               row.get('lsoa21cd', ''),
                               row.get('lad25cd', ''),
                               row.get('wd25cd', ''))
    print(f"  ONSPD: loaded {len(lk):,} postcodes (areas: {', '.join(POSTCODE_AREAS)})",
          file=sys.stderr)
    return lk


def cause_bucket(sic_texts: list[str]) -> str:
    """Map a list of SIC description strings to one of our cause buckets."""
    blob = ' '.join(t.lower() for t in sic_texts if t)
    for bucket, kws in BUCKET_RULES:
        if any(kw in blob for kw in kws):
            return bucket
    return DEFAULT_BUCKET


def find_bulk_zip(arg_path: str | None) -> Path:
    """Locate the Companies House bulk zip — explicit path, cache dir, or uploads."""
    if arg_path:
        p = Path(arg_path)
        if not p.exists():
            sys.exit(f"--bulk path does not exist: {p}")
        return p
    candidates = []
    for d in (CACHE_DIR / 'companies_house', CACHE_DIR, REPO):
        if d.exists():
            candidates.extend(sorted(d.glob('BasicCompanyDataAsOneFile-*.zip')))
    # Also accept a raw uploads folder if user dropped it there
    uploads = REPO.parent / 'uploads'
    if uploads.exists():
        candidates.extend(sorted(uploads.glob('BasicCompanyDataAsOneFile-*.zip')))
    if not candidates:
        sys.exit(
            "No BasicCompanyDataAsOneFile-*.zip found.\n"
            f"Drop the Companies House monthly bulk zip into {CACHE_DIR / 'companies_house'} "
            "or pass --bulk <path>.\n"
            "Free download: http://download.companieshouse.gov.uk/en_output.html"
        )
    return candidates[-1]  # latest by name (date-sorted)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bulk', help='path to BasicCompanyDataAsOneFile-*.zip')
    ap.add_argument('--include-dissolved', action='store_true',
                    help='keep dissolved/struck-off CICs (default: live only)')
    ap.add_argument('--out', default=str(REPO / 'cics.json'),
                    help='output path (default: cics.json)')
    args = ap.parse_args()

    bulk = find_bulk_zip(args.bulk)
    print(f"bulk file: {bulk}", file=sys.stderr)

    pc_lk = get_postcode_lookup()

    # Stream CSV row-by-row — never load 5GB into memory.
    n_total = 0
    n_cics = 0
    n_active = 0
    n_in_nwl = 0
    out = []
    with zipfile.ZipFile(bulk) as z:
        # The "as one file" variant has exactly one CSV inside.
        members = [m for m in z.namelist() if m.lower().endswith('.csv')]
        if not members:
            sys.exit("No CSV inside the zip — wrong file?")
        member = members[0]
        with z.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding='utf-8', errors='replace')
            r = csv.DictReader(text)
            # Strip whitespace from header keys (Companies House CSVs have them)
            r.fieldnames = [h.strip() for h in (r.fieldnames or [])]
            for row in r:
                n_total += 1
                if n_total % 500_000 == 0:
                    print(f"  scanned {n_total:,} rows · CICs found {n_cics:,} · "
                          f"NWL {n_in_nwl:,}", file=sys.stderr)

                cat = (row.get('CompanyCategory') or '').strip()
                if cat != 'Community Interest Company':
                    continue
                n_cics += 1

                status = (row.get('CompanyStatus') or '').strip()
                live = status in ('Active', 'Active - Proposal to Strike off')
                if not live and not args.include_dissolved:
                    continue
                if live:
                    n_active += 1

                pc_raw = (row.get('RegAddress.PostCode') or '').strip()
                pc = normalise_postcode(pc_raw)
                if not pc:
                    continue
                # Cheap pre-filter: postcode area must be one of NWL's
                if postcode_area(pc) not in POSTCODE_AREAS:
                    continue
                # Geocode via ONSPD
                hit = pc_lk.get(pc)
                if not hit:
                    continue
                lat, lng, lsoa, lad, wd = hit
                if lad not in NWL_LADS:
                    continue
                n_in_nwl += 1

                # SIC text → cause bucket
                sic_texts = [
                    (row.get('SICCode.SicText_1') or '').strip(),
                    (row.get('SICCode.SicText_2') or '').strip(),
                    (row.get('SICCode.SicText_3') or '').strip(),
                    (row.get('SICCode.SicText_4') or '').strip(),
                ]
                sic_texts = [s for s in sic_texts if s]
                bucket = cause_bucket(sic_texts)

                # Address — concatenate the bits Companies House publishes
                addr_parts = [
                    (row.get('RegAddress.AddressLine1') or '').strip(),
                    (row.get('RegAddress.AddressLine2') or '').strip(),
                    (row.get('RegAddress.PostTown') or '').strip(),
                    pc_raw,
                ]
                addr = ', '.join(p for p in addr_parts if p)

                out.append({
                    'n':   row.get('CompanyName', '').strip(),
                    'no':  row.get('CompanyNumber', '').strip(),  # CIC reference
                    'a':   addr,
                    'pc':  pc_raw,
                    'lat': round(lat, 6),
                    'lng': round(lng, 6),
                    'lad': lad,
                    'lsoa': lsoa,
                    'ward': wd,
                    'hq':  True,            # registered office IS the HQ
                    'cv':  [LAD_NAMES[lad]],  # we know the LAD it sits in; not coverage
                    'sc':  'explicit',      # treated as pinned by default in UI
                    'reg': (row.get('IncorporationDate') or '').strip(),
                    'st':  status,
                    'wt':  [bucket],        # one cause tag per CIC
                    'wd':  sic_texts[:2],   # first two SIC descriptions for context
                    'sic': [(row.get(f'SICCode.SicText_{i}') or '').split(' - ')[0].strip()
                            for i in range(1, 5)
                            if (row.get(f'SICCode.SicText_{i}') or '').strip()],
                })

    print(f"\nscanned {n_total:,} companies", file=sys.stderr)
    print(f"  CICs nationally:       {n_cics:,}", file=sys.stderr)
    print(f"  active CICs:           {n_active:,}", file=sys.stderr)
    print(f"  CICs in NW London:     {n_in_nwl:,}", file=sys.stderr)
    print(f"  written to {args.out}", file=sys.stderr)

    # Distribution by LAD and bucket — useful sanity check
    from collections import Counter
    by_lad = Counter(LAD_NAMES.get(r['lad'], r['lad']) for r in out)
    by_bucket = Counter(r['wt'][0] if r.get('wt') else 'other' for r in out)
    print("\nby borough:", file=sys.stderr)
    for lad, n in by_lad.most_common():
        print(f"  {lad:<25} {n:>4}", file=sys.stderr)
    print("\nby bucket:", file=sys.stderr)
    for b, n in by_bucket.most_common():
        print(f"  {b:<22} {n:>4}", file=sys.stderr)

    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, allow_nan=False) + '\n',
        encoding='utf-8',
    )


if __name__ == '__main__':
    main()
