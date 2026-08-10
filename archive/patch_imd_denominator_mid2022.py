#!/usr/bin/env python3
"""
Add the MHCLG mid-2022 IMD population denominator to each LSOA, and
re-aggregate ward IMD scores using that denominator (matching ICHT's
methodology).

Why two populations?
    * census_population = ONS mid-2024 single-year-of-age totals, used
      for profile / report views (latest authoritative figure).
    * imd_denominator_mid2022 = MHCLG's File_7 "Total population: mid
      2022" column, the denominator the IMD2025 scores are themselves
      built on. Using this keeps ward-level aggregation consistent with
      ICHT's published NWL figures.

Ward output fields (all pop-weighted on mid-2022 denominator):
    imd_score, income_score, employment_score, education_score,
    health_score, crime_score, barriers_score, environment_score,
    imd_denominator_mid2022 (sum of the denominator across the ward).

Usage:
    python scripts/patch_imd_denominator_mid2022.py

Inputs:
    uploads/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv
      (or alongside the repo at ./raw_data/ with the same filename)
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LSOA_JSON = REPO / "lsoa_data.json"
WARD_JSON = REPO / "ward_data.json"
INDEX_HTML = REPO / "index.html"

# Look in two places: raw_data/ (repo-local, git-ignored) and the
# sandbox uploads mount (when running inside Cowork).
CANDIDATES = [
    REPO / "raw_data" / "File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv",
    Path("/sessions/brave-funny-clarke/mnt/uploads/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv"),
]

# File_7 column indices (0-based, verified against the 2025 release).
COL_CODE = 0
COL_IMD_SCORE = 4
COL_INCOME = 7
COL_EMPLOYMENT = 10
COL_EDUCATION = 13
COL_HEALTH = 16
COL_CRIME = 19
COL_BARRIERS = 22
COL_ENVIRONMENT = 25
COL_POP_MID2022 = 52

DOMAIN_FIELDS = {
    "imd_score": COL_IMD_SCORE,
    "income_score": COL_INCOME,
    "employment_score": COL_EMPLOYMENT,
    "education_score": COL_EDUCATION,
    "health_score": COL_HEALTH,
    "crime_score": COL_CRIME,
    "barriers_score": COL_BARRIERS,
    "environment_score": COL_ENVIRONMENT,
}

WEIGHT_SOURCE_LABEL = "ONS mid-2022 (MHCLG IMD2025 denominator)"
WEIGHT_SOURCE_YEAR = "2022"


def _safe_write(path: Path, text: str) -> None:
    """Stage via /tmp then copy across virtiofs, asserting final size."""
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


def find_source_csv() -> Path:
    for p in CANDIDATES:
        if p.exists():
            return p
    sys.exit(
        "File_7 CSV not found. Place it under raw_data/ or uploads/.\n"
        f"Looked in: {[str(p) for p in CANDIDATES]}"
    )


def load_file7(path: Path) -> dict:
    """Return {lsoa_code: {imd_score, income_score, ..., pop_mid2022}}."""
    out = {}
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r)  # header
        for row in r:
            code = row[COL_CODE]
            if not code.startswith("E01"):
                continue
            try:
                entry = {
                    field: float(row[col])
                    for field, col in DOMAIN_FIELDS.items()
                }
                entry["pop_mid2022"] = int(row[COL_POP_MID2022])
            except (ValueError, IndexError):
                continue
            out[code] = entry
    return out


def patch_lsoa_json(file7: dict) -> tuple[int, int]:
    data = json.loads(LSOA_JSON.read_text(encoding="utf-8"))
    updated = missing = 0
    for code, entry in data.items():
        if code in file7:
            entry["imd_denominator_mid2022"] = file7[code]["pop_mid2022"]
            updated += 1
        else:
            missing += 1
    _safe_write(LSOA_JSON, json.dumps(data, indent=2))
    return updated, missing


def ward_to_lsoa_map() -> dict:
    """Pull LSOA_IMD geojson out of index.html, return {ward_code: [lsoa_codes]}."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"const LSOA_IMD = (\{.*?\});", html, re.DOTALL)
    if not m:
        sys.exit("LSOA_IMD constant not found in index.html")
    geo = json.loads(m.group(1))
    mapping = defaultdict(list)
    for feat in geo["features"]:
        p = feat["properties"]
        wc = p.get("ward_code")
        lc = p.get("code")
        if wc and lc:
            mapping[wc].append(lc)
    return dict(mapping)


def patch_ward_json(file7: dict, ward_lsoa: dict) -> tuple[int, int]:
    ward_doc = json.loads(WARD_JSON.read_text(encoding="utf-8"))
    wards = ward_doc["wards"]
    updated = skipped = 0

    for wc, ward in wards.items():
        codes = ward_lsoa.get(wc, [])
        total_pop = 0
        weighted = {k: 0.0 for k in DOMAIN_FIELDS}
        for c in codes:
            f7 = file7.get(c)
            if not f7:
                continue
            pop = f7["pop_mid2022"]
            if pop <= 0:
                continue
            total_pop += pop
            for field in DOMAIN_FIELDS:
                weighted[field] += f7[field] * pop

        if total_pop <= 0:
            skipped += 1
            continue

        ind = ward.setdefault("indicators", {})
        for field in DOMAIN_FIELDS:
            ind[field] = round(weighted[field] / total_pop, 4)
        ind["imd_denominator_mid2022"] = total_pop
        updated += 1

    meta = ward_doc.setdefault("metadata", {})
    meta["imd_weight_basis"] = WEIGHT_SOURCE_LABEL
    meta["imd_weight_year"] = WEIGHT_SOURCE_YEAR

    _safe_write(WARD_JSON, json.dumps(ward_doc, indent=2))
    return updated, skipped


def main() -> None:
    src = find_source_csv()
    print(f"reading {src.name} ...", flush=True)
    file7 = load_file7(src)
    print(f"  {len(file7):,} LSOA rows parsed")

    print(f"patching {LSOA_JSON.name} ...", flush=True)
    u, m = patch_lsoa_json(file7)
    print(f"  added imd_denominator_mid2022 to {u:,} LSOAs ({m:,} not in MHCLG file)")

    print("building ward->LSOA map from index.html ...", flush=True)
    ward_lsoa = ward_to_lsoa_map()
    print(f"  {len(ward_lsoa)} wards mapped")

    print(f"re-aggregating {WARD_JSON.name} ...", flush=True)
    u, s = patch_ward_json(file7, ward_lsoa)
    print(f"  updated {u} wards, {s} skipped (no LSOA match or zero pop)")

    print("\ndone. Ward IMD now population-weighted on mid-2022 denominator.")
    print("      LSOA/ward census_population still reflects ONS mid-2024.")


if __name__ == "__main__":
    main()
