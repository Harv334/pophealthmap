#!/usr/bin/env python3
"""
Adopt the official ONS LSOA (2021) -> Ward (May 2024) best-fit lookup as
the authoritative LSOA->ward mapping, and remove Camden from the NWL
dataset (NWL ICS is 8 boroughs: Brent, Ealing, H&F, Harrow, Hillingdon,
Hounslow, Kensington & Chelsea, Westminster).

What this rewrites:
  index.html   : embedded LSOA_IMD geojson
                 - drop Camden LSOA features entirely (~130)
                 - overwrite ward_code / ward / borough on every remaining
                   feature with values from the ONS lookup
  ward_data.json:
                 - drop all Camden wards (~20)
                 - for every remaining ward, recompute LSOA-derived fields
                   against the new ward->LSOA membership:
                     * census_population (sum mid-2024)
                     * imd_denominator_mid2022 (sum mid-2022)
                     * imd_score + 7 IMD domain scores (pop-weighted on
                       mid-2022, matching MHCLG methodology)
                     * all 31 census_*_pct fields (pop-weighted on
                       mid-2024)
                 - preserve ward-native fields (ft_*, gp_*, pharmacy_*,
                   crime_*, is_core20) since postcode->ward attribution
                   is independent of the LSOA->ward lookup

  lsoa_data.json: untouched (LSOA rows are full-England, no NWL filter)

Inputs:
  uploads/LSOA_(2021)_to_Electoral_Ward_(2024)_to_LAD_(2024)_Best_Fit_Lookup_in_EW.csv
  uploads/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv
"""
from __future__ import annotations

import csv
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO / "index.html"
WARD_JSON = REPO / "ward_data.json"
LSOA_JSON = REPO / "lsoa_data.json"

LOOKUP_CANDIDATES = [
    REPO / "raw_data" / "LSOA_(2021)_to_Electoral_Ward_(2024)_to_LAD_(2024)_Best_Fit_Lookup_in_EW.csv",
    Path("/sessions/brave-funny-clarke/mnt/uploads/LSOA_(2021)_to_Electoral_Ward_(2024)_to_LAD_(2024)_Best_Fit_Lookup_in_EW.csv"),
]
FILE7_CANDIDATES = [
    REPO / "raw_data" / "File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv",
    Path("/sessions/brave-funny-clarke/mnt/uploads/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv"),
]

# NWL ICS = 8 boroughs (Camden excluded).
NWL_LADS = {
    "E09000005": "Brent",
    "E09000009": "Ealing",
    "E09000013": "Hammersmith & Fulham",     # ampersand preserved (UI string)
    "E09000015": "Harrow",
    "E09000017": "Hillingdon",
    "E09000018": "Hounslow",
    "E09000020": "Kensington & Chelsea",     # ampersand preserved (UI string)
    "E09000033": "City of Westminster",
}
# ONS calls them "and" - use this to map the LAD24NM column to the UI string.
LAD_NAME_REMAP = {
    "Hammersmith and Fulham": "Hammersmith & Fulham",
    "Kensington and Chelsea": "Kensington & Chelsea",
}

# File_7 columns (0-based).
COL_IMD = 4
COL_INCOME = 7
COL_EMPLOYMENT = 10
COL_EDUCATION = 13
COL_HEALTH = 16
COL_CRIME = 19
COL_BARRIERS = 22
COL_ENVIRONMENT = 25
COL_POP_MID2022 = 52

DOMAIN_FIELDS = {
    "imd_score": COL_IMD,
    "income_score": COL_INCOME,
    "employment_score": COL_EMPLOYMENT,
    "education_score": COL_EDUCATION,
    "health_score": COL_HEALTH,
    "crime_score": COL_CRIME,
    "barriers_score": COL_BARRIERS,
    "environment_score": COL_ENVIRONMENT,
}

# Census percentage fields at LSOA level (all pop-weighted on mid-2024
# at ward level).
CENSUS_PCT_FIELDS = [
    "census_under16_pct", "census_under5_pct", "census_over65_pct",
    "census_over85_pct", "census_working_age_pct",
    "census_born_outside_uk_pct", "census_white_pct", "census_asian_pct",
    "census_black_pct", "census_mixed_pct", "census_other_ethnic_pct",
    "census_non_white_pct", "census_good_health_pct",
    "census_bad_health_pct", "census_disability_lot_pct",
    "census_disability_any_pct", "census_provides_unpaid_care_pct",
    "census_no_car_pct", "census_owned_pct", "census_social_rented_pct",
    "census_private_rented_pct", "census_higher_managerial_pct",
    "census_routine_semi_routine_pct", "census_unemployed_pct",
    "census_no_qual_pct", "census_level4_qual_pct",
    "census_english_hh_all_pct", "census_english_hh_none_pct",
    "census_active_travel_pct", "census_car_to_work_pct",
    "census_public_transport_pct",
]


def _safe_write(path: Path, text: str) -> None:
    """Stage via /tmp + chunked copy to survive virtiofs truncation."""
    expected = len(text.encode("utf-8"))
    tmp = Path(tempfile.gettempdir()) / (path.name + ".staging")
    tmp.write_text(text, encoding="utf-8")
    assert tmp.stat().st_size == expected, (
        f"staging write short: {tmp.stat().st_size} vs {expected}"
    )
    with open(tmp, "rb") as src, open(path, "wb") as dst:
        while chunk := src.read(4 * 1024 * 1024):
            dst.write(chunk)
    got = path.stat().st_size
    assert got == expected, f"virtiofs truncated {path.name}: {got} vs {expected}"
    tmp.unlink()


def find_first_existing(paths: list[Path], label: str) -> Path:
    for p in paths:
        if p.exists():
            return p
    sys.exit(f"{label} not found; looked in:\n  " + "\n  ".join(str(p) for p in paths))


def load_lookup() -> tuple[dict, dict]:
    """
    Load ONS lookup filtered to NWL ex-Camden.
    Returns:
      lsoa_to_assign: {LSOA21CD: {ward_code, ward, lad_code, borough}}
      ward_info:      {WD24CD: {name, lad_code, borough}}
    """
    path = find_first_existing(LOOKUP_CANDIDATES, "ONS LSOA->Ward lookup")
    lsoa_to_assign = {}
    ward_info = {}
    with open(path, encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        for row in r:
            lad_code = row["LAD24CD"]
            if lad_code not in NWL_LADS:
                continue
            lad_name = LAD_NAME_REMAP.get(row["LAD24NM"], row["LAD24NM"])
            lsoa_to_assign[row["LSOA21CD"]] = {
                "ward_code": row["WD24CD"],
                "ward": row["WD24NM"],
                "lad_code": lad_code,
                "borough": lad_name,
            }
            ward_info[row["WD24CD"]] = {
                "name": row["WD24NM"],
                "lad_code": lad_code,
                "lad": lad_name,
            }
    return lsoa_to_assign, ward_info


def load_file7() -> dict:
    """Return {lsoa_code: {imd fields, pop_mid2022}}."""
    path = find_first_existing(FILE7_CANDIDATES, "File_7 IMD2025")
    out = {}
    with open(path, newline="") as f:
        r = csv.reader(f); next(r)
        for row in r:
            code = row[0]
            if not code.startswith("E01"):
                continue
            try:
                entry = {field: float(row[col]) for field, col in DOMAIN_FIELDS.items()}
                entry["pop_mid2022"] = int(row[COL_POP_MID2022])
            except (ValueError, IndexError):
                continue
            out[code] = entry
    return out


def rewrite_index_geojson(lookup: dict) -> tuple[int, int, int]:
    """
    Update LSOA_IMD geojson in index.html:
      - drop Camden/outside-NWL features
      - overwrite ward_code/ward/borough from ONS lookup
    Returns (dropped, overwritten, unchanged).
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"const LSOA_IMD = (\{.*?\});", html, re.DOTALL)
    if not m:
        sys.exit("LSOA_IMD const not found in index.html")
    geo = json.loads(m.group(1))

    kept = []
    dropped = overwritten = unchanged = 0
    for feat in geo["features"]:
        code = feat["properties"].get("code")
        assign = lookup.get(code)
        if not assign:
            dropped += 1
            continue
        p = feat["properties"]
        prev = (p.get("ward_code"), p.get("ward"), p.get("borough"))
        new = (assign["ward_code"], assign["ward"], assign["borough"])
        if prev != new:
            overwritten += 1
        else:
            unchanged += 1
        p["ward_code"] = assign["ward_code"]
        p["ward"] = assign["ward"]
        p["borough"] = assign["borough"]
        kept.append(feat)

    geo["features"] = kept
    # Preserve original JSON encoding style - compact, no extra whitespace.
    new_blob = json.dumps(geo, separators=(",", ":"))
    new_html = (
        html[:m.start(1)] + new_blob + html[m.end(1):]
    )
    _safe_write(INDEX_HTML, new_html)
    return dropped, overwritten, unchanged


def rebuild_ward_json(lookup: dict, ward_info: dict, file7: dict) -> dict:
    """Drop Camden wards + re-aggregate LSOA-derived fields."""
    lsoa = json.loads(LSOA_JSON.read_text(encoding="utf-8"))
    ward_doc = json.loads(WARD_JSON.read_text(encoding="utf-8"))
    wards = ward_doc["wards"]

    # Drop any ward whose code isn't in the new ONS ward set.
    new_ward_codes = set(ward_info.keys())
    to_drop = [wc for wc in wards if wc not in new_ward_codes]
    for wc in to_drop:
        del wards[wc]

    # Build ward -> [lsoa codes] using the new ONS lookup.
    ward_to_lsoa = defaultdict(list)
    for lc, assign in lookup.items():
        ward_to_lsoa[assign["ward_code"]].append(lc)

    # For each retained ward, rebuild LSOA-derived fields.
    rebuilt = 0
    for wc, codes in ward_to_lsoa.items():
        if wc not in wards:
            # ONS has a ward I don't have - keep shell.
            wards[wc] = {
                "name": ward_info[wc]["name"],
                "lad": ward_info[wc]["lad"],
                "lad_code": ward_info[wc]["lad_code"],
                "indicators": {},
            }
        ward = wards[wc]
        # Force-refresh name/lad to lookup values (handles borough name remap).
        ward["name"] = ward_info[wc]["name"]
        ward["lad"] = ward_info[wc]["lad"]
        ward["lad_code"] = ward_info[wc]["lad_code"]

        ind = ward.setdefault("indicators", {})

        # census_population: sum mid-2024 populations from LSOA rows.
        mid2024_sum = 0
        for c in codes:
            e = lsoa.get(c)
            if e and isinstance(e.get("census_population"), (int, float)):
                mid2024_sum += int(e["census_population"])
        ind["census_population"] = mid2024_sum

        # IMD aggregation: pop-weighted on mid-2022.
        total_pop2022 = 0
        weighted_imd = {k: 0.0 for k in DOMAIN_FIELDS}
        for c in codes:
            f = file7.get(c)
            if not f: continue
            pop = f["pop_mid2022"]
            if pop <= 0: continue
            total_pop2022 += pop
            for k in DOMAIN_FIELDS:
                weighted_imd[k] += f[k] * pop
        if total_pop2022 > 0:
            for k in DOMAIN_FIELDS:
                ind[k] = round(weighted_imd[k] / total_pop2022, 4)
            ind["imd_denominator_mid2022"] = total_pop2022

        # Census percentages: pop-weighted on mid-2024.
        # (Historically weighted on Census 2021; mid-2024 is a close proxy
        # and keeps the weighting consistent with the new census_population.)
        for field in CENSUS_PCT_FIELDS:
            num = den = 0.0
            for c in codes:
                e = lsoa.get(c)
                if not e: continue
                v = e.get(field)
                p = e.get("census_population")
                if not isinstance(v, (int, float)) or not isinstance(p, (int, float)):
                    continue
                num += v * p
                den += p
            if den > 0:
                ind[field] = round(num / den, 2)
        rebuilt += 1

    # Metadata
    meta = ward_doc.setdefault("metadata", {})
    meta["lsoa_to_ward_lookup"] = "ONS LSOA (2021) -> Electoral Ward (May 2024) best-fit"
    meta["lsoa_to_ward_lookup_year"] = "2024"
    meta["scope"] = "NW London ICS (8 boroughs, Camden excluded)"
    meta["scope_lads"] = sorted(NWL_LADS.values())

    _safe_write(WARD_JSON, json.dumps(ward_doc, indent=2))
    return {"rebuilt": rebuilt, "dropped": len(to_drop), "wards_total": len(wards)}


def main() -> None:
    print("loading ONS LSOA->Ward (2024) lookup ...", flush=True)
    lookup, ward_info = load_lookup()
    print(f"  {len(lookup)} NWL LSOAs (ex-Camden), {len(ward_info)} wards")

    print("loading File_7 (IMD2025) ...", flush=True)
    file7 = load_file7()
    print(f"  {len(file7)} LSOA rows")

    print("rewriting index.html geojson ...", flush=True)
    d, o, u = rewrite_index_geojson(lookup)
    print(f"  dropped {d} features (Camden + outside-NWL)")
    print(f"  overwrote {o} ward/borough properties")
    print(f"  left {u} features unchanged")

    print("rebuilding ward_data.json ...", flush=True)
    stats = rebuild_ward_json(lookup, ward_info, file7)
    print(f"  retained {stats['wards_total']} wards, dropped {stats['dropped']} (Camden)")
    print(f"  rebuilt LSOA-derived fields on {stats['rebuilt']} wards")

    print("\nDone. Dataset scope: NW London ICS (8 boroughs, Camden excluded).")
    print("      LSOA->ward assignment: ONS official WD24 best-fit lookup.")


if __name__ == "__main__":
    main()
