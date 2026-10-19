"""Census wards and LAs from summed counts, checked offline on synthetic tables.

A census percentage for a ward or an LA is the summed numerator count of its
output areas (or LSOAs) over their summed denominator count. Averaging the
small-area percentages weighted by residents, as the build did before, is
wrong for household measures such as no car or tenure, whose base is
households. This builds Nomis-shaped tables for twelve output areas in four
LSOAs and two wards, with household sizes that differ on purpose, runs them
through the pipeline's own census rules, and checks:

  * the ward figure equals sum(num) / sum(den) taken straight from the tables,
  * it differs from the old residents-weighted average (so the test can fail),
  * re-aggregating output area counts to LSOAs reproduces the LSOA
    percentages built from LSOA tables holding the same counts,
  * the figures files keep their shape (no count columns in them),
  * LA figures are the summed counts of the output areas in their wards,
  * a counts file that is missing falls back to the residents-weighted
    average rather than to nothing,
  * build_borough_data and build_ward_data, reading the files by name,
    write the summed-count figures and record census_method, and a build
    with no counts files records "weighted" and leaves the LA file without
    Census figures.

No network: _fetch_census_table and the lookups are replaced.
"""
import importlib.util, pathlib, sys

import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

FAILS = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)

rng = np.random.default_rng(7)
OAS = [f"E00{100000 + i}" for i in range(12)]
OA_LSOA = {oa: f"E01{200000 + i // 3}" for i, oa in enumerate(OAS)}      # 3 OAs per LSOA
LSOA_WARD = {"E01200000": "E05000001", "E01200001": "E05000001",
             "E01200002": "E05000002", "E01200003": "E05000002"}
OA_WARD = {oa: LSOA_WARD[l] for oa, l in OA_LSOA.items()}
WARD_LAD = {"E05000001": "E09000001", "E05000002": "E09000001"}

# Counts per output area. Residents per household runs from 1.2 to 4.5, so a
# residents-weighted average of household percentages is visibly wrong.
n = len(OAS)
hh = rng.integers(90, 160, n)
size = np.linspace(1.2, 4.5, n)
pop = np.round(hh * size).astype(int)
no_car = np.round(hh * np.linspace(0.75, 0.10, n)).astype(int)   # small households lack cars
owned = np.round(hh * np.linspace(0.15, 0.70, n)).astype(int)
owned_out = owned // 2
social = np.round((hh - owned) * 0.6).astype(int)
private = hh - owned - social
very_good = np.round(pop * 0.45).astype(int); good = np.round(pop * 0.3).astype(int)
fair = np.round(pop * 0.15).astype(int); bad = np.round(pop * 0.07).astype(int)
very_bad = pop - very_good - good - fair - bad
u16 = np.round(pop * np.linspace(0.05, 0.35, n)).astype(int)
o65 = np.round(pop * np.linspace(0.30, 0.05, n)).astype(int)
white = np.round(pop * np.linspace(0.8, 0.3, n)).astype(int)
asian = pop - white


def tables(codes, rows):
    """Nomis-shaped tables for one geography; rows[i] are index lists into
    the output area arrays, summed, so LSOA tables hold the same counts."""
    agg = lambda a: [int(a[r].sum()) for r in rows]
    code = {"date": 2021, "geography": codes, "geography code": codes}
    return {
        "TS001": pd.DataFrame({**code, "Residence type: Total; measures: Value": agg(pop)}),
        "TS045": pd.DataFrame({**code,
            "Number of cars or vans: Total: All households": agg(hh),
            "Number of cars or vans: No cars or vans in household": agg(no_car),
            "Number of cars or vans: 1 car or van in household": agg(hh - no_car)}),
        "TS054": pd.DataFrame({**code,
            "Tenure of household: Total: All households": agg(hh),
            "Tenure of household: Owned": agg(owned),
            "Tenure of household: Owned: Owns outright": agg(owned_out),
            "Tenure of household: Owned: Owns with a mortgage or loan": agg(owned - owned_out),
            "Tenure of household: Social rented": agg(social),
            "Tenure of household: Private rented": agg(private)}),
        "TS037": pd.DataFrame({**code,
            "General health: Total: All usual residents": agg(pop),
            "General health: Very good health": agg(very_good),
            "General health: Good health": agg(good),
            "General health: Fair health": agg(fair),
            "General health: Bad health": agg(bad),
            "General health: Very bad health": agg(very_bad)}),
        "TS007A": pd.DataFrame({**code,
            "Age: Total": agg(pop),
            "Age: Aged 4 years and under": agg(u16),
            "Age: Aged 16 to 64 years": agg(pop - u16 - o65),
            "Age: Aged 65 years and over": agg(o65)}),
        # Single years of age: the children at 10, working age at 30 and the
        # rest at 70, so under 16 and 16 to 64 come out as u16 and the middle.
        "TS007": pd.DataFrame({**code, "Age: Total": agg(pop),
            **{("Age: Aged under 1 year" if a == 0 else
                f"Age: Aged {a} year{'' if a == 1 else 's'}" + (" and over" if a == 100 else "")):
               (agg(u16) if a == 10 else agg(pop - u16 - o65) if a == 30 else
                agg(o65) if a == 70 else [0] * len(rows)) for a in range(0, 101)}}),
        "TS021": pd.DataFrame({**code,
            "Ethnic group: Total: All usual residents": agg(pop),
            "Ethnic group: Asian, Asian British or Asian Welsh": agg(asian),
            "Ethnic group: White": agg(white),
            "Ethnic group: White: English, Welsh, Scottish, Northern Irish or British": agg(white)}),
    }

OA_T = tables(OAS, [[i] for i in range(n)])
LSOAS = sorted(set(OA_LSOA.values()))
LSOA_T = tables(LSOAS, [[i for i, oa in enumerate(OAS) if OA_LSOA[oa] == l] for l in LSOAS])
p._fetch_census_table = lambda tab, level="lsoa": {"oa": OA_T, "lsoa": LSOA_T}.get(level, {}).get(tab)
p.info = p.warn = p.ok = lambda *a, **k: None
# Twelve made-up areas are not England, so the check against ONS's shares is
# tested on its own (test_fingertips_headline.py), and the age bands record
# goes to a scratch folder rather than the repo.
p._census_age_check = lambda *a, **k: None
import tempfile
p.DATA_DIR = pathlib.Path(tempfile.mkdtemp())

print("=== census figures from summed counts ===")
oa_full = p._census_frame("oa")
lsoa_full = p._census_frame("lsoa")
check("under 16 and working age built from single years are recorded as 0-15",
      p.census_age_bands() == "0-15")
oa_figs, oa_cnt = p._census_split(oa_full, "LSOA21CD")
lsoa_figs, lsoa_cnt = p._census_split(lsoa_full, "LSOA21CD")

check("figures frames carry no count columns",
      not [c for c in list(oa_figs.columns) + list(lsoa_figs.columns) if c.endswith(("__num", "__den"))])
KEYS = ["census_no_car_pct", "census_owned_pct", "census_social_rented_pct",
        "census_private_rented_pct", "census_good_health_pct", "census_bad_health_pct",
        "census_under16_pct", "census_over65_pct", "census_working_age_pct",
        "census_white_pct", "census_asian_pct", "census_non_white_pct"]
check("every percentage has its two counts",
      all(k + "__num" in oa_cnt.columns and k + "__den" in oa_cnt.columns for k in KEYS),
      [k for k in KEYS if k + "__num" not in oa_cnt.columns])

ward = p.census_group_figures(oa_figs, oa_cnt, OA_WARD)
truth = {
    "census_no_car_pct": (no_car, hh), "census_owned_pct": (owned, hh),
    "census_social_rented_pct": (social, hh), "census_private_rented_pct": (private, hh),
    "census_good_health_pct": (very_good + good, pop), "census_bad_health_pct": (bad + very_bad, pop),
    "census_under16_pct": (u16, pop), "census_over65_pct": (o65, pop),
    "census_working_age_pct": (pop - u16 - o65, pop),
    "census_white_pct": (white, pop), "census_asian_pct": (asian, pop),
    "census_non_white_pct": (pop - white, pop),
}
wards = sorted(set(OA_WARD.values()))
bad_keys = []
for k, (num, den) in truth.items():
    for wd in wards:
        idx = [i for i, oa in enumerate(OAS) if OA_WARD[oa] == wd]
        want = round(num[idx].sum() / den[idx].sum() * 100, 2)
        if ward.get(wd, {}).get(k) != want:
            bad_keys.append(f"{wd} {k}: {ward.get(wd, {}).get(k)} != {want}")
check("ward figure = sum(num) / sum(den) for every percentage", not bad_keys, "; ".join(bad_keys[:4]))
idx = [i for i, oa in enumerate(OAS) if OA_WARD[oa] == wards[0]]
check("ward population is summed", ward[wards[0]]["census_population"] == int(pop[idx].sum()))

old = p.census_group_figures(oa_figs, None, OA_WARD)
diff = abs(old[wards[0]]["census_no_car_pct"] - ward[wards[0]]["census_no_car_pct"])
check("the old residents-weighted average differs for a household measure (test has teeth)",
      diff > 1, f"difference only {diff:.2f} points")
oa_pct = oa_figs.set_index("LSOA21CD")
w = pop[idx]; v = oa_pct.loc[[OAS[i] for i in idx], "census_no_car_pct"].values
check("no counts file falls back to the residents-weighted average",
      abs(old[wards[0]]["census_no_car_pct"] - round(float((v * w).sum() / w.sum()), 2)) < 1e-9)

re_lsoa = p.census_group_figures(oa_figs, oa_cnt, OA_LSOA)
lp = lsoa_figs.set_index("LSOA21CD")
miss = [f"{l} {k}: {re_lsoa[l][k]} vs {lp.loc[l, k]}" for l in LSOAS for k in KEYS
        if abs(re_lsoa[l][k] - float(lp.loc[l, k])) > 0.0051]
check("output area counts re-aggregated to LSOAs reproduce the LSOA percentages", not miss, "; ".join(miss[:4]))
check("LSOA counts equal the summed output area counts",
      all(abs(lsoa_cnt.set_index("LSOA21CD").loc[l, "census_no_car_pct__den"]
              - hh[[i for i, oa in enumerate(OAS) if OA_LSOA[oa] == l]].sum()) < 1e-9 for l in LSOAS))

# LSOA fallback: the same wards from LSOA counts give the same figures.
from_lsoa = p.census_group_figures(lsoa_figs, lsoa_cnt, LSOA_WARD)
check("wards from LSOA counts match wards from output area counts here (whole LSOAs per ward)",
      all(from_lsoa[wd][k] == ward[wd][k] for wd in wards for k in KEYS))

# LA figures: the summed counts of all the output areas in the LA's wards.
ORIG_WARD_INPUTS = p._census_ward_inputs
p._census_ward_inputs = lambda: (oa_figs, oa_cnt, OA_WARD)
p._ward_to_lad = lambda: WARD_LAD
la = p.census_la_figures()
want = round(no_car.sum() / hh.sum() * 100, 2)
check("LA figure = summed counts of its output areas",
      la.get("E09000001", {}).get("census_no_car_pct") == want, f"{la.get('E09000001')} vs {want}")
check("LA records leave population to the map", "census_population" not in la.get("E09000001", {}))

# The wiring: build_borough_data and build_ward_data, reading the figures and
# counts as the data build does (by file name, through _read_parquet_opt) from
# a scratch data folder that holds only two ward boundaries. The lookups are
# replaced, so nothing is fetched.
print("=== the LA and ward builds, from the files ===")
import json, tempfile
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "boundaries").mkdir()
(tmp / "boundaries" / "wards.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"WD25CD": wd, "WD25NM": f"Ward {i}",
     "LAD25CD": "E09000001", "LAD25NM": "City of London"}} for i, wd in enumerate(wards)]}), encoding="utf-8")
p._census_ward_inputs = ORIG_WARD_INPUTS
p.DATA_DIR = tmp
FT = pd.DataFrame({"LAD25CD": ["E09000001"], "indicator_short": ["90366"], "value": [80.5]})
def files(with_counts=True):
    f = {"census2021_oa.parquet": oa_figs.rename(columns={"LSOA21CD": "OA21CD"}),
         "census2021.parquet": lsoa_figs, "fingertips.parquet": FT}
    if with_counts:
        f["census2021_oa_counts.parquet"] = oa_cnt.rename(columns={"LSOA21CD": "OA21CD"})
        f["census2021_counts.parquet"] = lsoa_cnt
    return f
FILES = files()
p._read_parquet_opt = lambda path: FILES.get(pathlib.Path(path).name)
p.get_oa_to_ward = lambda: OA_WARD
p.get_lsoa_to_ward = lambda: LSOA_WARD
p.get_lsoa_ward_lookup = lambda: {l: (wd, WARD_LAD[wd]) for l, wd in LSOA_WARD.items()}
p._ward_to_lad = lambda: WARD_LAD

bd = p.build_borough_data()
rec = bd.get("E09000001", {})
check("build_borough_data writes the LA's Census figure from summed counts",
      rec.get("census_no_car_pct") == round(no_car.sum() / hh.sum() * 100, 2), str(rec)[:200])
check("and leaves population out of the LA record", "census_population" not in rec)
check("and keeps the published LA figures beside it", rec.get("ft_90366") == 80.5, str(rec.get("ft_90366")))

try:
    wd_out = p.build_ward_data()
    meta = wd_out.get("metadata", {})
    w0 = wd_out.get("wards", {}).get(wards[0], {}).get("indicators", {})
    check("build_ward_data records census_method 'counts'", meta.get("census_method") == "counts", str(meta.get("census_method")))
    check("and its ward figure is the summed counts",
          w0.get("census_no_car_pct") == ward[wards[0]]["census_no_car_pct"], str(w0.get("census_no_car_pct")))
    # A stale build with no counts files: the wards are averaged by residents
    # and say so, and the LA file carries no Census figure, so the map works
    # one out from the wards and its card says that too.
    FILES = files(with_counts=False)
    stale = p.build_ward_data()
    check("with no counts files, build_ward_data records census_method 'weighted'",
          stale.get("metadata", {}).get("census_method") == "weighted", str(stale.get("metadata", {}).get("census_method")))
    bd_stale = p.build_borough_data()
    check("and the LA file carries no Census figures",
          not any(k.startswith("census_") for k in bd_stale.get("E09000001", {})), str(bd_stale.get("E09000001")))
    stats = p.build_indicator_stats(wd_out, {}, {}, bd)
    check("the indicator stats tell the methodology page both methods",
          stats.get("census_method") == "counts" and stats.get("census_la_method") == "counts",
          str({k: stats.get(k) for k in ("census_method", "census_la_method")}))
except Exception as e:  # a lookup this scratch folder does not hold
    check("build_ward_data runs on the scratch folder", False, repr(e))

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
