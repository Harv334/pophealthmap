"""Patch lsoa_data.json + ward_data.json with Census 2021 age profile
(TS007A: "Age by five-year age bands", LSOA-level).

What to download
----------------
  1. Go to https://www.nomisweb.co.uk/sources/census_2021_bulk
  2. Find "TS007A - Age by five-year age bands"
  3. Download the bulk CSV zip. The file is usually called
     'census2021-ts007a.zip' (~10 MB).
  4. Save it as:
     .cache/census2021/ts007a.zip
     (same folder where the other TS tables already live)

Then run:
  python patch_age.py

This writes:
  census_under5_pct       (% of residents under 5)
  census_under16_pct      (% under 16)
  census_working_age_pct  (100 - under16 - over65)
  census_over65_pct       (% 65+)
  census_over85_pct       (% 85+)
to every LSOA in lsoa_data.json, and a population-weighted mean of each
to every ward in ward_data.json.
"""
import json
import os
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache" / "census2021"


def _load_lsoa_csv(zip_name: str) -> pd.DataFrame | None:
    p = CACHE / zip_name
    if not p.exists():
        return None
    try:
        with zipfile.ZipFile(p) as zf:
            inner = next((n for n in zf.namelist() if "lsoa" in n.lower()),
                         None)
            if not inner:
                return None
            with zf.open(inner) as f:
                return pd.read_csv(f, dtype=str, low_memory=False)
    except zipfile.BadZipFile:
        return None


def _code_col(df: pd.DataFrame) -> str:
    for c in df.columns:
        if c.strip().lower() in ("geography code", "lsoa code",
                                 "geographycode", "lsoa21cd",
                                 "2021 super output area - lower layer"):
            return c
    raise RuntimeError(f"no LSOA code column in {list(df.columns)[:5]}...")


def _total_col(df: pd.DataFrame) -> str:
    return next(c for c in df.columns if "total" in c.lower())


def _find_age_cols(df, kw_tuples, exclude=()) -> list[str]:
    cols: list[str] = []
    for kwset in kw_tuples:
        for c in df.columns:
            cl = c.lower()
            if (all(k.lower() in cl for k in kwset)
                    and not any(e.lower() in cl for e in exclude)
                    and c not in cols):
                cols.append(c)
    # Drop any column that's a strict descendant of another in the list,
    # which happens when both "parent" and "sub-category" rows are present.
    return [c for c in cols if not any(
        other != c and c.startswith(other + ": ") for other in cols)]


def load_age_table() -> pd.DataFrame | None:
    for candidate in ("ts007a.zip", "ts007.zip", "ts009.zip"):
        df = _load_lsoa_csv(candidate)
        if df is not None:
            print(f"[age] reading {candidate}")
            return df
    return None


def compute_age_pcts(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    code_c = _code_col(df)
    tot_c = _total_col(df)
    den = pd.to_numeric(df[tot_c], errors="coerce").replace(0, pd.NA)

    # TS007A "Age by five-year age bands" - typical column names:
    #   "Age (18 categories): Aged 4 years and under"
    #   "Aged 5 to 9 years", "Aged 10 to 14 years", "Aged 15 years",
    #   "Aged 65 to 69", ..., "Aged 85 years and over"
    # Older TS009 has single-year bands; we try both shapes.
    under5 = _find_age_cols(
        df, [("aged 4 years and under",)], exclude=("and over",))
    under16 = _find_age_cols(df, [
        ("aged 0 to 15",),
        ("aged 4 years and under",), ("aged 5 to 9",),
        ("aged 10 to 14",), ("aged 15 years",),
    ], exclude=("and over",))
    over65 = _find_age_cols(df, [
        ("aged 65",), ("aged 70 to 74",), ("aged 75 to 79",),
        ("aged 80 to 84",), ("aged 85 years and over",),
    ])
    over85 = _find_age_cols(df, [("aged 85 years and over",)])

    def sum_pct(cols: list[str]) -> pd.Series:
        if not cols:
            return pd.Series([pd.NA] * len(df))
        num = df[cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
        return (num / den) * 100

    out: dict[str, dict[str, float]] = {}
    for key, cols in [
        ("census_under5_pct", under5),
        ("census_under16_pct", under16),
        ("census_over65_pct", over65),
        ("census_over85_pct", over85),
    ]:
        print(f"  {key:26s} -> {len(cols)} cols")
        if not cols:
            continue
        vals = sum_pct(cols)
        for code, v in zip(df[code_c].astype(str).str.strip(), vals):
            if pd.isna(v):
                continue
            out.setdefault(code, {})[key] = round(float(v), 1)

    # Derived working-age band.
    for code, rec in out.items():
        u16 = rec.get("census_under16_pct")
        o65 = rec.get("census_over65_pct")
        if u16 is not None and o65 is not None:
            rec["census_working_age_pct"] = round(
                max(0.0, 100.0 - u16 - o65), 1)
    return out


def patch_lsoa(age: dict) -> dict:
    path = ROOT / "lsoa_data.json"
    with open(path) as f:
        lsoa = json.load(f)
    hits = defaultdict(int)
    for code, rec in lsoa.items():
        updates = age.get(code)
        if not updates:
            continue
        for k, v in updates.items():
            rec[k] = v
            hits[k] += 1
    for k, n in hits.items():
        print(f"[lsoa_data.json] {k}: {n:,} / {len(lsoa):,}")
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(lsoa, f, separators=(",", ":"))
    os.replace(tmp, path)
    return lsoa


def _load_lsoa_ward_map() -> dict[str, list[str]]:
    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const LSOA_IMD\s*=\s*\{", idx)
    start = m.end() - 1
    depth = 0
    i = start
    N = len(idx)
    in_str = None
    esc = False
    while i < N:
        ch = idx[i]
        if esc:
            esc = False
        elif in_str:
            if ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
        else:
            if ch in "\"'":
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
        i += 1
    blob = json.loads(idx[start:i])

    by_ward: dict[str, list[str]] = defaultdict(list)
    for f in blob["features"]:
        p = f.get("properties") or {}
        wn = p.get("ward")
        code = p.get("code")
        if wn and code:
            by_ward[wn].append(code)
    return dict(by_ward)


def patch_wards(lsoa: dict) -> None:
    ward_path = ROOT / "ward_data.json"
    with open(ward_path) as f:
        ward_doc = json.load(f)
    wards = ward_doc["wards"]
    by_ward_imd = _load_lsoa_ward_map()

    manual_map = {
        ("Kilburn", "Brent"):  "Kilburn (Brent)",
        ("Kilburn", "Camden"): "Kilburn (Camden)",
        ("Regent's Park", "Camden"): "Regent's Park (Camden)",
        ("Regent's Park", "Westminster"): "Regent's Park",
    }

    def pop(code: str) -> float:
        v = (lsoa.get(code) or {}).get("census_population")
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def ward_mean(codes, key):
        num = den = 0.0
        for c in codes:
            v = (lsoa.get(c) or {}).get(key)
            if v is None:
                continue
            w = pop(c) or 1.0
            num += float(v) * w
            den += w
        return round(num / den, 1) if den > 0 else None

    keys = [
        "census_under5_pct", "census_under16_pct",
        "census_working_age_pct", "census_over65_pct", "census_over85_pct",
    ]
    counts = defaultdict(int)
    for wcode, wobj in wards.items():
        nm = wobj.get("name")
        lad = wobj.get("lad")
        imd_name = manual_map.get((nm, lad), nm)
        codes = by_ward_imd.get(imd_name) or []
        if not codes:
            continue
        ind = wobj.setdefault("indicators", {})
        for key in keys:
            v = ward_mean(codes, key)
            if v is not None:
                ind[key] = v
                counts[key] += 1
    for k, n in counts.items():
        print(f"[ward_data.json] {k}: {n} wards")

    tmp = ward_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(ward_doc, f, separators=(",", ":"))
    os.replace(tmp, ward_path)

    # Spot checks: highest/lowest NW ward on each band
    for key in ("census_under16_pct", "census_over65_pct",
                "census_over85_pct"):
        vals = [(w["name"], w.get("indicators", {}).get(key))
                for w in wards.values()
                if w.get("indicators", {}).get(key) is not None]
        if not vals:
            continue
        vals.sort(key=lambda t: t[1])
        print(f"[{key}] lowest 3:  {vals[:3]}")
        print(f"[{key}] highest 3: {vals[-3:]}")


if __name__ == "__main__":
    df = load_age_table()
    if df is None:
        print("ERROR: no LSOA-level age table cached.")
        print("Download TS007A from https://www.nomisweb.co.uk/sources/"
              "census_2021_bulk and save as "
              ".cache/census2021/ts007a.zip, then re-run.")
        raise SystemExit(1)
    age = compute_age_pcts(df)
    if not age:
        print("ERROR: couldn't parse any age columns.")
        raise SystemExit(1)
    lsoa = patch_lsoa(age)
    patch_wards(lsoa)
    print("Done.")
