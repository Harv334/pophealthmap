#!/usr/bin/env python3
"""Bring the shipped data files in line with the corrected pipeline, from what
is on disk, until the next refresh rebuilds them properly.

One-off and repeatable: running it twice gives the same files. It uses the
pipeline's own helpers (fetch_all_data.py), so the arithmetic is the one the
next refresh will do.

    python3 scripts/patch_shipped_round2.py [PACK ...]

PACK is a pack root: the repo root for London (the default) or a region
folder such as data/regions/north-east. For each pack it:

  - rebuilds the ward Local Health figures (ft_<id>) from msoa_data.json,
    weighted by Census residents instead of one per LSOA, and leaves a ward
    blank where the MSOAs with a figure hold under 80% of its residents,
    recording the share in ward["cover"] where it is under 100%;
  - the same 80% rule for the ward PTAL figure;
  - writes each ward's share of residents in Core20 LSOAs (core20_pop_pct);
  - marks each charity in vcse_data.json with nr (serves the area, by the
    Near list's rule) and np (not pinned: council office or land only);
  - writes lsoa_census_counts.json for Custom area;
  - re-measures the ward facts in indicators.js for the series it changed;
  - in London, where the Fingertips parquets are on disk, carries OHID's
    value notes into fingertips.parquet and borough_data.json (ft_notes).

What it cannot do from disk: convert PTAL's 2011 LSOA codes (the ONS lookup
is not on disk), or check whether a charity's accounts are overdue (the
register's reporting status is not in the parquet). The next refresh does both.
Run scripts/split_ward_data.py afterwards for London; this script does it.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import fetch_all_data as F  # noqa: E402


def read_js_geojson(path: Path) -> dict:
    s = path.read_text(encoding="utf-8")
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def write_json(path: Path, data) -> None:
    F.write_json_atomic(path, data)


def pack_paths(pack: Path) -> dict:
    london = pack.resolve() == ROOT.resolve()
    return {
        "london": london,
        "lsoa_imd": pack / ("data/map/lsoa_imd.js" if london else "map/lsoa_imd.js"),
        "ward": pack / "ward_data.json",
        "lsoa": pack / "lsoa_data.json",
        "msoa": pack / "msoa_data.json",
        "borough": pack / "borough_data.json",
        "vcse": pack / "vcse_data.json",
        "counts_out": pack / "lsoa_census_counts.json",
        "indicators": pack / ("data/map/indicators.js" if london else "map/indicators.js"),
    }


def patch_wards(p: dict, l2m: dict) -> None:
    gj = read_js_geojson(p["lsoa_imd"])
    props = [f["properties"] for f in gj["features"]]
    l2w = {q["code"]: q["ward_code"] for q in props if q.get("ward_code")}
    dec = {q["code"]: q.get("imd_decile") for q in props}
    lsoa = json.loads(p["lsoa"].read_text(encoding="utf-8"))
    lsoa = lsoa.get("lsoas", lsoa)
    pop = {c: float(r.get("census_population") or 0) for c, r in lsoa.items()}
    msoa = json.loads(p["msoa"].read_text(encoding="utf-8"))
    msoa = msoa.get("msoas", msoa)
    doc = json.loads(p["ward"].read_text(encoding="utf-8"))
    wards = doc["wards"]

    # Local Health, residents-weighted, with coverage.
    keys = sorted({k for m in msoa.values() for k in m
                   if k.startswith("ft_") and k[3:].isdigit()})
    n_vals = n_part = n_blank = 0
    for k in keys:
        per_lsoa = {lc: msoa[ms][k] for lc, ms in l2m.items()
                    if lc in l2w and isinstance(msoa.get(ms, {}).get(k), (int, float))}
        vals, cover = F.bridge_to_wards(per_lsoa, l2w, pop)
        for wd, w in wards.items():
            w.get("indicators", {}).pop(k, None)
        for wd, v in vals.items():
            if wd in wards:
                wards[wd]["indicators"][k] = round(v, 4)
                n_vals += 1
        n_part += F.record_cover(wards, cover, k, vals)
        n_blank += sum(1 for wd in cover if wd not in vals and wd in wards)
    print(f"  Local Health: {len(keys)} series, {n_vals:,} ward values, "
          f"{n_part:,} partly covered, {n_blank:,} left blank under 80%")

    # PTAL, where the pack has it.
    ptal = {c: r["ptal_score"] for c, r in lsoa.items()
            if isinstance(r.get("ptal_score"), (int, float))}
    if ptal:
        vals, cover = F.bridge_to_wards(ptal, l2w, pop)
        before = sum(1 for w in wards.values() if w.get("indicators", {}).get("ptal_score") is not None)
        for wd, w in wards.items():
            w.get("indicators", {}).pop("ptal_score", None)
        for wd, v in vals.items():
            if wd in wards:
                wards[wd]["indicators"]["ptal_score"] = round(v, 2)
        F.record_cover(wards, cover, "ptal_score", vals)
        after = sum(1 for w in wards.values() if w.get("indicators", {}).get("ptal_score") is not None)
        print(f"  PTAL: {len(ptal):,} of {len(l2w):,} LSOAs have a figure; wards with one {before} -> {after}")

    # Share of residents in Core20 LSOAs.
    tot: dict = {}
    c20: dict = {}
    for lc, wd in l2w.items():
        tot[wd] = tot.get(wd, 0.0) + pop.get(lc, 0.0)
        d = dec.get(lc)
        if isinstance(d, (int, float)) and d in (1, 2):
            c20[wd] = c20.get(wd, 0.0) + pop.get(lc, 0.0)
    n = 0
    for wd, w in wards.items():
        if tot.get(wd, 0) > 0:
            w["core20_pop_pct"] = round(100 * c20.get(wd, 0.0) / tot[wd], 1)
            n += 1
        else:
            w.pop("core20_pop_pct", None)
    print(f"  Core20: residents' share written for {n} wards")
    write_json(p["ward"], doc)


def patch_stats(p: dict) -> None:
    """Re-measure the ward facts in indicators.js for the series changed above,
    by build_indicator_stats' rule, leaving everything else as it was."""
    path = p["indicators"]
    s = path.read_text(encoding="utf-8")
    i, j = s.index("{"), s.rindex("}") + 1
    blob = json.loads(s[i:j])
    wards = json.loads(p["ward"].read_text(encoding="utf-8"))["wards"]
    keys = [k for k in blob.get("stats", {})
            if (k.startswith("ft_") and k[3:].isdigit()) or k == "ptal_score"]
    for k in keys:
        vals = sorted(float(w["indicators"][k]) for w in wards.values()
                      if isinstance(w.get("indicators", {}).get(k), (int, float))
                      and not isinstance(w["indicators"][k], bool))
        lv = blob["stats"][k].setdefault("lv", {})
        if len(vals) < 2:
            lv.pop("ward", None)
            continue
        lv["ward"] = {"n": len(vals), "d": len(set(vals)),
                      "p5": round(vals[int(len(vals) * 0.05)], 4),
                      "p95": round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 4)}
    body = json.dumps(blob, separators=(",", ":"), ensure_ascii=False)
    F.write_atomic(path, s[:i] + body + s[j:])
    print(f"  indicators.js: ward facts re-measured for {len(keys)} series")


def patch_vcse(p: dict) -> None:
    if not p["vcse"].exists():
        return
    recs = json.loads(p["vcse"].read_text(encoding="utf-8"))
    for r in recs:
        F.apply_charity_flags(r)
    write_json(p["vcse"], recs)
    hq = [r for r in recs if r.get("hq")]
    print(f"  Charities: {sum(1 for r in hq if r.get('nr')):,} of {len(hq):,} based here serve "
          f"the area by the Near list's rule; {sum(1 for r in recs if r.get('np')):,} not pinned")


def patch_counts(p: dict) -> None:
    lsoa = json.loads(p["lsoa"].read_text(encoding="utf-8"))
    lsoa = lsoa.get("lsoas", lsoa)
    cnt = pd.read_parquet(ROOT / "data" / "demographics" / "census2021_counts.parquet")
    out = F.lsoa_census_counts(cnt, lsoa)
    if out:
        write_json(p["counts_out"], out)
        print(f"  Census counts: {len(out['rows']):,} LSOAs, {len(out['keys'])} measures")


def note_rows() -> pd.DataFrame:
    """fingertips.parquet with a value_note column, matched from the profiles
    parquet (which held the note under the name 'unit').

    A row is matched on its LA, indicator, period, age and value. The profiles
    pull did not keep the sex, so where the value does not match (the men's
    life expectancy), a note every row of that LA, indicator and period shares
    is carried over when it is about the data rather than the value:
    provisional figures, or two areas combined."""
    ft = pd.read_parquet(ROOT / "data" / "outcomes" / "fingertips.parquet")
    pr = pd.read_parquet(ROOT / "data" / "outcomes" / "fingertips_profiles.parquet")
    col = "value_note" if "value_note" in pr.columns else "unit"
    pr = pr[pr["indicator_id"].isin(ft["indicator_id"].unique())]
    notes = []
    for _, r in ft.iterrows():
        c = pr[(pr.LAD25CD == r.LAD25CD) & (pr.indicator_id == r.indicator_id) &
               (pr.period == r.period) & (pr.age == r.age)]
        if pd.isna(r.value):
            m = c[c.value.isna()]
        else:
            m = c[(c.value - r.value).abs() < 1e-6]
        got = sorted({F._ft_note(x) for x in m[col]} - {""})
        if not got and m.empty and len(c):
            shared = {F._ft_note(x) for x in c[col]}
            if len(shared) == 1:
                s = next(iter(shared))
                if "provisional" in s.lower() or "combined" in s.lower():
                    got = [s]
        notes.append(got[0] if len(got) == 1 else "")
    ft["value_note"] = notes
    return ft


def patch_notes(p: dict) -> None:
    ft = note_rows()
    F.write_parquet_atomic(ROOT / "data" / "outcomes" / "fingertips.parquet", ft)
    pr_path = ROOT / "data" / "outcomes" / "fingertips_profiles.parquet"
    pr = pd.read_parquet(pr_path)
    if "unit" in pr.columns and "value_note" not in pr.columns:
        pr = pr.rename(columns={"unit": "value_note"})
        F.write_parquet_atomic(pr_path, pr)
    bd = json.loads(p["borough"].read_text(encoding="utf-8"))
    for rec in bd.values():
        rec.pop("ft_notes", None)
    n = 0
    for _, r in ft.iterrows():
        if pd.isna(r.value) or not r.value_note:
            continue
        lad = str(r.LAD25CD)
        if lad not in bd:
            continue
        F.ft_note_check(lad, r.indicator_short, r.value_note)
        bd[lad].setdefault("ft_notes", {})[f"ft_{r.indicator_short}"] = r.value_note
        n += 1
    write_json(p["borough"], bd)
    print(f"  OHID notes: {n} LA figures carry one")


def main() -> None:
    packs = [Path(a) for a in sys.argv[1:]] or [ROOT]
    l2m = {r["LSOA21CD"]: r["MSOA21CD"]
           for r in csv.DictReader(open(ROOT / "scripts" / "lsoa_to_msoa.csv", encoding="utf-8"))}
    for pack in packs:
        p = pack_paths(pack)
        print(f"{pack}:")
        patch_wards(p, l2m)
        patch_stats(p)
        patch_vcse(p)
        patch_counts(p)
        if p["london"]:
            patch_notes(p)
            subprocess.run([sys.executable, str(ROOT / "scripts" / "split_ward_data.py")], check=True)


if __name__ == "__main__":
    main()
