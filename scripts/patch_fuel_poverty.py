#!/usr/bin/env python3
"""Bring the shipped fuel poverty ward and LA figures in line with the
corrected pipeline, from what is on disk, until the next refresh rebuilds
them.

Fuel poverty is a share of households, so a ward's figure is its LSOAs'
figures weighted by their households at the 2021 Census, as Custom area
already weights it, rather than by their residents. Each LA gets its own
figure from all of its LSOAs in the same way (borough_data.json), so the map
no longer averages the ward figures by residents.

One-off and repeatable: running it twice gives the same files. It uses the
pipeline's own helpers (fetch_all_data.fuel_poverty_by_area and
census_households_by_lsoa), so the arithmetic is the one the next refresh
will do.

    python3 scripts/patch_fuel_poverty.py [PACK ...]

PACK is a pack root: the repo root for London (the default) or a region
folder such as data/regions/north-east. The LSOA figures and household
counts are read from the repo's data/demographics parquets, which cover all
of England, and each LSOA's ward from the pack's lsoa_imd.js. For London it
then runs scripts/split_ward_data.py.
"""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import fetch_all_data as F  # noqa: E402

KEY = "fuel_poverty_pct"


def read_js_geojson(path: Path) -> dict:
    s = path.read_text(encoding="utf-8")
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def measure(vals: list) -> dict:
    """build_indicator_stats' facts for one level."""
    s = sorted(vals)
    return {"n": len(s), "d": len(set(s)),
            "p5": round(s[int(len(s) * 0.05)], 4),
            "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 4)}


def patch(pack: Path, fp: pd.DataFrame, households: dict) -> None:
    london = pack.resolve() == ROOT.resolve()
    lsoa_imd = pack / ("data/map/lsoa_imd.js" if london else "map/lsoa_imd.js")
    ind_path = pack / ("data/map/indicators.js" if london else "map/indicators.js")
    ward_path, la_path = pack / "ward_data.json", pack / "borough_data.json"

    props = [f["properties"] for f in read_js_geojson(lsoa_imd)["features"]]
    l2w = {q["code"]: q["ward_code"] for q in props if q.get("ward_code")}
    doc = json.loads(ward_path.read_text(encoding="utf-8"))
    wards = doc["wards"]
    w2l = {wd: w.get("lad_code") for wd, w in wards.items() if w.get("lad_code")}
    l2la = {lc: w2l[wd] for lc, wd in l2w.items() if wd in w2l}
    fp = fp[fp["LSOA21CD"].isin(l2w)]

    by_ward = F.fuel_poverty_by_area(fp, l2w, households, {}, what=f"{pack.name} ward {KEY}")
    by_la = F.fuel_poverty_by_area(fp, l2la, households, {}, what=f"{pack.name} LA {KEY}")

    shown = []
    for wd, w in wards.items():
        ind = w.setdefault("indicators", {})
        old = ind.get(KEY)
        new = round(by_ward[wd], 2) if wd in by_ward else None
        if new is None:
            ind.pop(KEY, None)
        else:
            ind[KEY] = new
        shown.append((abs((new or 0) - (old or 0)), w.get("name", wd), old, new))
    F.write_json_atomic(ward_path, doc)
    shown.sort(key=lambda t: -t[0])
    print(f"  wards: {len(by_ward)} with a figure, {sum(1 for t in shown if t[0] > 0)} changed")
    for _d, name, old, new in shown[:3]:
        print(f"    {name}: {old} -> {new}")

    bd = json.loads(la_path.read_text(encoding="utf-8"))
    for lad, v in by_la.items():
        if lad in bd:
            bd[lad][KEY] = round(v, 2)
    F.write_json_atomic(la_path, bd)
    print(f"  LAs: {sum(1 for lad in by_la if lad in bd)} given their own figure")

    s = ind_path.read_text(encoding="utf-8")
    i, j = s.index("{"), s.rindex("}") + 1
    blob = json.loads(s[i:j])
    lv = blob.setdefault("stats", {}).setdefault(KEY, {}).setdefault("lv", {})
    wv = [w["indicators"][KEY] for w in wards.values() if isinstance(w["indicators"].get(KEY), (int, float))]
    lv_vals = [r[KEY] for r in bd.values() if isinstance(r.get(KEY), (int, float))]
    if len(wv) >= 2:
        lv["ward"] = measure(wv)
    if len(lv_vals) >= 2:
        lv["borough"] = measure(lv_vals)
    F.write_atomic(ind_path, s[:i] + json.dumps(blob, separators=(",", ":"), ensure_ascii=False) + s[j:])
    print("  indicators.js: fuel poverty ward and LA facts re-measured")

    if london:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "split_ward_data.py")], check=True)


def main() -> None:
    demo = ROOT / "data" / "demographics"
    fp = pd.read_parquet(demo / "fuel_poverty.parquet")
    households = F.census_households_by_lsoa(pd.read_parquet(demo / "census2021_counts.parquet"))
    if not households:
        raise SystemExit("census2021_counts.parquet has no household counts")
    for pack in [Path(a) for a in sys.argv[1:]] or [ROOT]:
        print(f"{pack}:")
        patch(pack, fp, households)


if __name__ == "__main__":
    main()
