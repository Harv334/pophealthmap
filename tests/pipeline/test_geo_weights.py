"""How LSOA figures are combined into wards and LAs: residents-weighted
means that refuse to run without populations, fuel poverty weighted by
households, and custom _count columns added up rather than averaged.
Offline: small hand-built inputs, and build_ward_data and
build_borough_data run on a scratch folder with the lookups replaced.
"""
import importlib.util
import json
import pathlib
import sys
import tempfile

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def raises(fn):
    try:
        fn()
    except RuntimeError:
        return True
    return False


L2W = {"A1": "W1", "A2": "W1", "B1": "W2", "B2": "W2"}
POP = {"A1": 1000, "A2": 3000, "B1": 2000, "B2": 2000}

print("=== lsoa_means_to_areas: weighted by residents ===")
out = p.lsoa_means_to_areas({"A1": 10.0, "A2": 20.0, "B1": 4.0, "B2": 8.0}, L2W, POP)
check("W1 is the residents-weighted mean (17.5, not 15)", abs(out["W1"] - 17.5) < 1e-9, str(out))
check("W2 has equal residents, so the plain mean (6)", abs(out["W2"] - 6.0) < 1e-9, str(out))
check("an LSOA outside the lookup is ignored",
      p.lsoa_means_to_areas({"A1": 10.0, "Z9": 99.0}, L2W, POP) == {"W1": 10.0})

print("=== lsoa_means_to_areas: no populations stop the build ===")
check("no populations at all raise", raises(lambda: p.lsoa_means_to_areas({"A1": 1.0}, L2W, {})))
check("populations that are all zero raise",
      raises(lambda: p.lsoa_means_to_areas({"A1": 1.0}, L2W, {"A1": 0, "A2": 0})))

print("=== lsoa_means_to_areas: one LSOA with no population ===")
# 200 LSOAs in one ward, one of them with no population. It is left out of
# the average with a warning, not given a weight of 1. A single one is always
# let through; more than one stops the build only above 1% of the LSOAs.
l2w = {f"L{i}": "W" for i in range(200)}
pop = {f"L{i}": 1000 for i in range(1, 200)}
vals = {f"L{i}": 10.0 for i in range(1, 200)}
vals["L0"] = 1000.0
out = p.lsoa_means_to_areas(vals, l2w, pop)
check("an LSOA with no population has no say", abs(out["W"] - 10.0) < 1e-9, str(out))
out = p.lsoa_means_to_areas({"A1": 5.0, "B1": 7.0}, L2W, {"A1": 0, "A2": 3000, "B1": 2000, "B2": 2000})
check("a ward whose only LSOA with a figure has no population gets no figure",
      "W1" not in out and abs(out.get("W2", 0) - 7.0) < 1e-9, str(out))
check("two or more LSOAs without a population, over 1% of them, raise (lookup and Census out of step)",
      raises(lambda: p.lsoa_means_to_areas({"A1": 1.0, "A2": 2.0, "B1": 3.0}, L2W,
                                           {"A1": 1000, "B2": 2000})))
vals["L1"] = 1000.0
pop.pop("L1")
check("two of 200 is still within 1%, so it warns", abs(p.lsoa_means_to_areas(vals, l2w, pop)["W"] - 10.0) < 1e-9)

print("=== lsoa_sums_to_areas: counts are added up ===")
check("counts sum per ward", p.lsoa_sums_to_areas({"A1": 3, "A2": 4, "B1": 5}, L2W) == {"W1": 7.0, "W2": 5.0})
check("custom_is_count follows the _count rule",
      p.custom_is_count("food_bank_count") and not p.custom_is_count("food_bank_rate"))

print("=== fuel poverty: weighted by households ===")
cnt = pd.DataFrame({"LSOA21CD": ["A1", "A2", "B1", "B2"],
                    "census_no_car_pct__num": [10, 10, 10, 10],
                    "census_no_car_pct__den": [900, 300, 500, 500]})
hh = p.census_households_by_lsoa(cnt)
check("households come from the car or van base", hh == {"A1": 900, "A2": 300, "B1": 500, "B2": 500}, str(hh))
check("no counts give no households", p.census_households_by_lsoa(None) == {} and
      p.census_households_by_lsoa(pd.DataFrame({"LSOA21CD": ["A1"]})) == {})
fp = pd.DataFrame({"LSOA21CD": ["A1", "A2", "B1", "B2"], "fuel_poverty_pct": [10.0, 30.0, 8.0, None]})
out = p.fuel_poverty_by_area(fp, L2W, hh, POP)
# W1: fuel-poor households 90 + 90 over 1,200 households = 15%, where the
# residents-weighted mean (1,000 and 3,000 people) would say 25%.
check("W1 is fuel-poor households over households (15%, not 25%)", abs(out["W1"] - 15.0) < 1e-9, str(out))
check("W2 rests on the LSOA that has a figure", abs(out["W2"] - 8.0) < 1e-9, str(out))
out = p.fuel_poverty_by_area(fp, L2W, {}, POP)
check("without household counts it falls back to residents (25%)", abs(out["W1"] - 25.0) < 1e-9, str(out))

print("=== build_ward_data and build_borough_data, from the files ===")
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "boundaries").mkdir()
(tmp / "boundaries" / "wards.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"WD25CD": wd, "WD25NM": f"Ward {wd}",
     "LAD25CD": "E09000001", "LAD25NM": "City of London"}} for wd in ("W1", "W2")]}), encoding="utf-8")
p.DATA_DIR = tmp
CEN = pd.DataFrame({"LSOA21CD": list(POP), "census_population": list(POP.values())})
FILES = {
    "census2021.parquet": CEN,
    "census2021_counts.parquet": cnt,
    "fuel_poverty.parquet": fp,
    "imd2025.parquet": pd.DataFrame({"LSOA21CD": ["A1", "A2", "B1", "B2"], "imd_score": [10.0, 20.0, 4.0, 8.0]}),
    "custom_lsoa.parquet": pd.DataFrame({"LSOA21CD": ["A1", "A2", "B1", "B2"],
                                         "food_bank_count": [2, 3, 1, 0],
                                         "my_rate": [10.0, 20.0, 4.0, 8.0]}),
}
p._read_parquet_opt = lambda path: FILES.get(pathlib.Path(path).name)
p._census_ward_inputs = lambda: (None, None, {})
p.get_oa_to_ward = lambda: {}
p.get_lsoa_to_ward = lambda: dict(L2W)
p.get_lsoa_ward_lookup = lambda: {l: (wd, "E09000001") for l, wd in L2W.items()}
p._ward_to_lad = lambda: {"W1": "E09000001", "W2": "E09000001"}
# The LSOA to MSOA lookup is asked for once the national MSOA files (ONS
# income, healthy life expectancy) are committed; none is needed here.
p.get_lsoa_to_msoa = lambda: {}
try:
    wd_out = p.build_ward_data()
    w1 = wd_out["wards"]["W1"]["indicators"]
    w2 = wd_out["wards"]["W2"]["indicators"]
    check("a ward's IMD score is weighted by residents", w1.get("imd_score") == 17.5, str(w1.get("imd_score")))
    check("a ward's fuel poverty is weighted by households", w1.get("fuel_poverty_pct") == 15.0,
          str(w1.get("fuel_poverty_pct")))
    check("a custom _count column is added up into wards",
          w1.get("food_bank_count") == 5 and w2.get("food_bank_count") == 1,
          str((w1.get("food_bank_count"), w2.get("food_bank_count"))))
    check("a custom rate is still weighted by residents", w1.get("my_rate") == 17.5, str(w1.get("my_rate")))
    bd = p.build_borough_data()
    # The LA: (90 + 90 + 40) fuel-poor households over the 1,700 households
    # of the three LSOAs with a figure.
    want = round(100 * (90 + 90 + 40) / 1700, 2)
    check("the LA's fuel poverty is its LSOAs weighted by households",
          bd.get("E09000001", {}).get("fuel_poverty_pct") == want, str(bd.get("E09000001")))
    FILES["census2021.parquet"] = pd.DataFrame({"LSOA21CD": list(POP)})
    check("with no Census populations the ward build stops instead of averaging LSOAs",
          raises(p.build_ward_data))
except Exception as e:  # a lookup this scratch folder does not hold
    check("build_ward_data runs on the scratch folder", False, repr(e))

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
