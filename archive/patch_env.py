"""Patch lsoa_data.json + ward_data.json with fuel poverty + PTAL.

Reads whatever is in:
    .cache/fuel_poverty/*.xlsx   (DESNZ sub-regional, LSOA tab)
    .cache/ptal/*.csv            (GLA LSOA Atlas)

Adds:
    fuel_poverty_pct  (households in fuel poverty, LILEE, %)
    ptai_score        (average PTAI score per LSOA)

Ward-level values are computed as population-weighted means of the LSOAs in
the ward (using census_population already on each lsoa_data.json record).
Ward -> LSOA mapping is extracted from the LSOA_IMD GeoJSON embedded in
index.html (same technique as patch_demographics.py).

Run:
    python patch_env.py
"""
import json
import os
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"


# ---------- helpers ----------------------------------------------------------

def _find_col(df: pd.DataFrame, *kws, exclude=()):
    for c in df.columns:
        if not isinstance(c, str):
            continue
        lc = c.lower()
        if all(k in lc for k in kws) and not any(e in lc for e in exclude):
            return c
    return None


# ---------- fuel poverty (DESNZ) --------------------------------------------

def load_fuel_poverty() -> dict[str, float]:
    d = CACHE / "fuel_poverty"
    if not d.exists():
        print(f"[fuel_poverty] no cache dir at {d} — skipping")
        return {}
    xlsxs = sorted(d.glob("*.xlsx"), key=lambda p: p.stat().st_size, reverse=True)
    if not xlsxs:
        print(f"[fuel_poverty] no XLSX in {d}/. "
              "Download from https://www.gov.uk/government/collections/"
              "fuel-poverty-sub-regional-statistics and retry.")
        return {}
    src = xlsxs[0]
    print(f"[fuel_poverty] reading {src.name}")
    xl = pd.ExcelFile(src)

    # Find an LSOA sheet. The LSOA tab has drifted from "Table 3" (earlier
    # releases) to "Table 4" (2023 release), so scan every "Table *" sheet.
    sheets = [s for s in xl.sheet_names
              if "lsoa" in s.lower() or s.lower().startswith("table ")]
    if not sheets:
        print(f"  no LSOA-looking sheet in {xl.sheet_names}")
        return {}

    df = None
    for sh in sheets:
        for hdr in range(0, 6):
            try:
                t = pd.read_excel(src, sheet_name=sh, header=hdr, dtype=str)
            except Exception:
                continue
            if any("lsoa" in str(c).lower() and "code" in str(c).lower()
                   for c in t.columns):
                df = t
                print(f"  sheet={sh!r}, header_row={hdr}")
                break
        if df is not None:
            break
    if df is None:
        print("  could not locate LSOA code column")
        return {}

    code_col = _find_col(df, "lsoa", "code")
    pct_col = (_find_col(df, "proportion", "fuel", "poor")
               or _find_col(df, "%", "fuel", "poor")
               or _find_col(df, "percentage", "fuel", "poor"))
    if not code_col or not pct_col:
        print(f"  columns not found: code={code_col!r}, pct={pct_col!r}")
        return {}
    print(f"  code_col={code_col!r}, pct_col={pct_col!r}")

    out: dict[str, float] = {}
    vals = pd.to_numeric(df[pct_col], errors="coerce")
    # Some DESNZ tabs express fuel poverty as a fraction (0-1). Auto-scale.
    if vals.dropna().max() is not None and vals.dropna().max() <= 1.5:
        vals = vals * 100
    for code, v in zip(df[code_col].astype(str).str.strip(), vals):
        if not code or pd.isna(v):
            continue
        out[code] = round(float(v), 2)
    print(f"  {len(out):,} LSOA values")
    return out


# ---------- PTAL (GLA LSOA Atlas) -------------------------------------------

def load_ptal() -> dict[str, float]:
    d = CACHE / "ptal"
    if not d.exists():
        print(f"[ptal] no cache dir at {d} — skipping")
        return {}
    csvs = sorted(d.glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    if not csvs:
        print(f"[ptal] no CSV in {d}/. "
              "Download from https://data.london.gov.uk/dataset/lsoa-atlas "
              "and retry.")
        return {}
    src = csvs[0]
    print(f"[ptal] reading {src.name}")

    df = None
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(src, dtype=str, encoding=enc, low_memory=False)
            break
        except UnicodeDecodeError:
            continue
    if df is None or df.empty:
        print("  empty or unreadable CSV")
        return {}

    code_col = (_find_col(df, "lower super output area")
                or _find_col(df, "lsoa", "code")
                or _find_col(df, "codes"))
    ptai_col = (_find_col(df, "average", "ptai")
                or _find_col(df, "ptai", "score")
                or _find_col(df, "ptai"))
    if not code_col or not ptai_col:
        print(f"  columns not found: code={code_col!r}, ptai={ptai_col!r}")
        return {}
    print(f"  code_col={code_col!r}, ptai_col={ptai_col!r}")

    out: dict[str, float] = {}
    for code, v in zip(df[code_col].astype(str).str.strip(),
                       pd.to_numeric(df[ptai_col], errors="coerce")):
        if not code or not code.startswith("E01") or pd.isna(v):
            continue
        out[code] = round(float(v), 2)
    print(f"  {len(out):,} LSOA values")
    return out


# ---------- patch lsoa_data.json --------------------------------------------

def patch_lsoa(fp: dict, pt: dict) -> dict:
    path = ROOT / "lsoa_data.json"
    with open(path) as f:
        lsoa = json.load(f)

    hit_fp = hit_pt = 0
    for code, rec in lsoa.items():
        if code in fp:
            rec["fuel_poverty_pct"] = fp[code]
            hit_fp += 1
        if code in pt:
            rec["ptai_score"] = pt[code]
            hit_pt += 1
    print(f"[lsoa_data.json] fuel_poverty: {hit_fp:,} / {len(lsoa):,}")
    print(f"[lsoa_data.json] ptai_score : {hit_pt:,} / {len(lsoa):,}")

    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(lsoa, f, separators=(",", ":"))
    os.replace(tmp, path)
    return lsoa


# ---------- ward aggregation (reuse LSOA_IMD ward map) ----------------------

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
        rec = lsoa.get(code) or {}
        v = rec.get("census_population")
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def ward_mean(codes: list[str], key: str):
        num = den = 0.0
        for c in codes:
            rec = lsoa.get(c) or {}
            v = rec.get(key)
            if v is None:
                continue
            w = pop(c)
            if w <= 0:
                w = 1.0  # fall back to unweighted if no pop on that LSOA
            num += float(v) * w
            den += w
        return round(num / den, 2) if den > 0 else None

    counts = defaultdict(int)
    for wcode, wobj in wards.items():
        nm = wobj.get("name")
        lad = wobj.get("lad")
        imd_name = manual_map.get((nm, lad), nm)
        codes = by_ward_imd.get(imd_name) or []
        if not codes:
            continue
        ind = wobj.setdefault("indicators", {})
        for key in ("fuel_poverty_pct", "ptai_score"):
            v = ward_mean(codes, key)
            if v is not None:
                ind[key] = v
                counts[key] += 1

    for k, n in counts.items():
        print(f"[ward_data.json] {k}: {n} wards patched")

    tmp = ward_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(ward_doc, f, separators=(",", ":"))
    os.replace(tmp, ward_path)

    # Spot-checks: ward extremes on each indicator
    for key, label in (("fuel_poverty_pct", "Fuel poverty"),
                       ("ptai_score", "PTAI")):
        vals = [(w["name"], w.get("indicators", {}).get(key))
                for w in wards.values()
                if w.get("indicators", {}).get(key) is not None]
        if not vals:
            continue
        vals.sort(key=lambda t: t[1])
        print(f"[{label}] lowest 3: {vals[:3]}")
        print(f"[{label}] highest 3: {vals[-3:]}")


# ---------- main ------------------------------------------------------------

if __name__ == "__main__":
    fp = load_fuel_poverty()
    pt = load_ptal()
    if not fp and not pt:
        print("Nothing to patch - drop source files into "
              ".cache/fuel_poverty/*.xlsx and/or .cache/ptal/*.csv and re-run.")
        raise SystemExit(1)
    lsoa = patch_lsoa(fp, pt)
    patch_wards(lsoa)
    print("Done.")
