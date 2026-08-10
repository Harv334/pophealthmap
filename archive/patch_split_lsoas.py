#!/usr/bin/env python3
"""
patch_split_lsoas.py
====================

One-shot patch that re-merges the 5 LSOA-level parquet feeds
(IMD, census2021, fuel_poverty, claimant_count, greenblue_lsoa, DWP benefits
and PTAL if present) into lsoa_data.json.

Motivation: 10 post-2022 boundary-revision split LSOAs (E01035713–E01035722)
in Westminster + Kensington & Chelsea were missing 35 census/fuel/claimant
fields in lsoa_data.json. Investigation showed the source parquets already
contain data for these codes (census / fuel / IMD / greenblue all have them) —
the json on disk was just stale from a prior run. Running this script reuses
the same merge logic as fetch_all_data.build_lsoa_data() without needing to
re-fetch any upstream sources.

Claimant count is NOT yet available for these split codes from NOMIS (still on
LSOA-2011 boundaries). Those fields stay null and will show as "—" in the UI.
"""
from pathlib import Path
import json
import sys

try:
    import pandas as pd
except ImportError:
    sys.exit("pandas is required; run `pip install pandas pyarrow`")

REPO = Path(__file__).parent
DATA = REPO / "data"
JSON_PATH = REPO / "lsoa_data.json"


def _rp(path: Path):
    return pd.read_parquet(path) if path.exists() else None


def rebuild() -> dict:
    out: dict[str, dict] = {}

    imd = _rp(DATA / "demographics" / "imd2025.parquet")
    if imd is not None:
        for _, row in imd.iterrows():
            code = row["LSOA21CD"]
            if not code:
                continue
            rec = {}
            for col in imd.columns:
                if col == "LSOA21CD":
                    continue
                v = row[col]
                if col in ("imd_decile", "imd_rank") and pd.notna(v):
                    rec[col] = int(v)
                elif pd.isna(v):
                    rec[col] = None
                else:
                    rec[col] = v
            out[code] = rec

    cen = _rp(DATA / "demographics" / "census2021.parquet")
    if cen is not None and not cen.empty:
        for _, row in cen.iterrows():
            code = str(row["LSOA21CD"])
            if not code or code not in out:
                continue
            rec = out[code]
            for col in cen.columns:
                if col == "LSOA21CD":
                    continue
                v = row[col]
                if pd.isna(v):
                    continue
                rec[col] = int(v) if col == "census_population" else float(v)

    fp = _rp(DATA / "demographics" / "fuel_poverty.parquet")
    if fp is not None and not fp.empty:
        for _, row in fp.iterrows():
            code = str(row["LSOA21CD"])
            v = row.get("fuel_poverty_pct")
            if code in out and pd.notna(v):
                out[code]["fuel_poverty_pct"] = round(float(v), 2)

    pt = _rp(DATA / "demographics" / "ptal.parquet")
    if pt is not None and not pt.empty:
        for _, row in pt.iterrows():
            code = str(row["LSOA21CD"])
            v = row.get("ptai_score")
            if code in out and pd.notna(v):
                out[code]["ptai_score"] = round(float(v), 2)

    cl = _rp(DATA / "economy" / "claimant_count.parquet")
    if cl is not None and not cl.empty:
        cl_cols = [c for c in ("claimant_count", "claimant_rate_pct",
                               "claimant_yoy_change", "claimant_yoy_pct")
                   if c in cl.columns]
        for _, row in cl.iterrows():
            code = str(row["LSOA21CD"])
            if code not in out:
                continue
            for c in cl_cols:
                v = row.get(c)
                if pd.isna(v):
                    continue
                out[code][c] = (int(v) if c in ("claimant_count", "claimant_yoy_change")
                                else round(float(v), 2))

    dwp = _rp(DATA / "economy" / "dwp_benefits.parquet")
    if dwp is not None and not dwp.empty:
        carry_int = [c for c in ("pip_cases", "uc_households", "esa_claimants",
                                  "carers_allowance", "pension_credit")
                     if c in dwp.columns]
        carry_float = [c for c in dwp.columns if c.endswith("_rate_pct")]
        for _, row in dwp.iterrows():
            code = str(row["LSOA21CD"])
            if code not in out:
                continue
            for c in carry_int:
                v = row.get(c)
                if pd.notna(v):
                    out[code][c] = int(v)
            for c in carry_float:
                v = row.get(c)
                if pd.notna(v):
                    out[code][c] = round(float(v), 2)

    # Green/blue space (Defra 15-min walk). Keep only the headline pct columns
    # the UI uses.
    gb = _rp(DATA / "environment" / "greenblue_lsoa.parquet")
    if gb is not None and not gb.empty:
        carry = [
            "gb_total_uprn", "gb_commitment_pct", "green_commitment_pct",
            "green_doorstep_pct", "green_local_pct", "green_neighbourhood_pct",
            "blue_commitment_pct",
        ]
        for _, row in gb.iterrows():
            code = str(row["LSOA21CD"])
            if code not in out:
                continue
            for c in carry:
                if c not in gb.columns:
                    continue
                v = row.get(c)
                if pd.isna(v):
                    continue
                out[code][c] = (int(v) if c == "gb_total_uprn" else round(float(v), 2))

    # Drop None-only IMD records (keeps json tidy)
    for code, rec in out.items():
        if all(v is None for v in rec.values()):
            rec.clear()

    return out


def main():
    if not JSON_PATH.exists():
        sys.exit(f"{JSON_PATH} not found")

    before = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    new = rebuild()

    # Preserve any previously-present keys on LSOAs not covered by this script
    # (e.g. cached vcse categories). Overlay the rebuild onto the old dict.
    out = dict(before)
    for code, rec in new.items():
        if code not in out:
            out[code] = rec
        else:
            out[code].update(rec)

    tmp = JSON_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    tmp.replace(JSON_PATH)

    # Summarise the fix
    split = [
        "E01035713", "E01035714", "E01035715", "E01035716", "E01035717",
        "E01035718", "E01035719", "E01035720", "E01035721", "E01035722",
    ]
    print(f"lsoa_data.json: {len(out):,} LSOAs written")
    print("Split-code field counts (before -> after):")
    for c in split:
        b = len(before.get(c, {}))
        a = len(out.get(c, {}))
        arrow = "OK " if a > b else "   "
        print(f"  {arrow} {c}: {b} -> {a}")


if __name__ == "__main__":
    main()
