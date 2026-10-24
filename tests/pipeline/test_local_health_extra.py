"""Low birth weight and cancer incidence from Local Health (fingertips_msoa_extra).

93092 (low birth weight of live babies) and 93234 to 93238 (cancer incidence:
all cancers, breast, colorectal, lung, prostate) are fetched at MSOA and LA in
a source of their own, and each series ships only if ft_msoa_series_check
passes it: figures for at least half the scope's MSOAs once OHID's withheld
values are taken out, and a median 95% interval no wider either side than the
figure. A dropped series writes no rows and is listed as a known gap; a
series that could not be downloaded keeps last run's figures (partial) or
fails the source, and none of it can touch the established Local Health
sources.

Offline. The downloads are synthetic CSVs in the Fingertips all_data/csv
layout (the column names run_fingertips_msoa and _ft_value_with_ci read:
Indicator Name, Area Code, Area Type, Sex, Age, Time period, Value, Lower CI
95.0 limit, Upper CI 95.0 limit, Value note, Compared to England value or
percentiles, Time period Sortable). The names are OHID's, from
scripts/fingertips_metadata.json; 93092's period and range follow the
September 2026 snapshot (2018 - 22, 2.7 to 14.2%).
"""
import importlib.util
import io
import json
import pathlib
import random
import sys
import tempfile
from contextlib import redirect_stdout

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


rng = random.Random(9)
# 40 MSOAs of two LSOAs each, the last few with 2021-only codes.
MSOAS = [f"E02{6900 + i:06d}" for i in range(40)]
L2M = {f"E01{(2 * i + k):06d}": m for i, m in enumerate(MSOAS) for k in (0, 1)}
LADS = ["E09000005", "E09000009"]
L2LAD = {l: LADS[0] if int(l[3:]) < 40 else LADS[1] for l in L2M}
L2W = {l: f"E0500{int(l[3:]) // 4:04d}" for l in L2M}
POP = {l: 1500 + 10 * (int(l[3:]) % 7) for l in L2M}
COLS = ["Indicator ID", "Indicator Name", "Parent Code", "Parent Name", "Area Code", "Area Name", "Area Type",
        "Sex", "Age", "Category Type", "Category", "Time period", "Value", "Lower CI 95.0 limit",
        "Upper CI 95.0 limit", "Count", "Denominator", "Value note",
        "Compared to England value or percentiles", "Time period Sortable"]


def csv_rows(ind, name, sex="Persons", area_type="MSOA", codes=MSOAS, value=None, half=0.1, period="2018 - 22",
             blank=0.0, note=""):
    rows = []
    for i, c in enumerate(codes):
        v = value(i) if value else 100 + rng.uniform(-30, 30)
        r = {"Indicator ID": ind, "Indicator Name": name, "Parent Code": "E92000001", "Parent Name": "England",
             "Area Code": c, "Area Name": c, "Area Type": area_type, "Sex": sex, "Age": "All ages",
             "Category Type": "", "Category": "", "Time period": period, "Value": round(v, 4),
             "Lower CI 95.0 limit": round(v * (1 - half), 4), "Upper CI 95.0 limit": round(v * (1 + half), 4),
             "Count": "", "Denominator": "", "Value note": note,
             "Compared to England value or percentiles": "Similar", "Time period Sortable": "20180000"}
        if i < blank * len(codes):
            r.update({"Value": "", "Lower CI 95.0 limit": "", "Upper CI 95.0 limit": "",
                      "Value note": "Value suppressed for disclosure control due to small count",
                      "Compared to England value or percentiles": "Not compared"})
        rows.append(r)
    return rows


def write_csv(path, rows):
    pd.DataFrame(rows, columns=COLS).to_csv(path, index=False)


print("=== the per-series check ===")
good = [{"value": 7.0, "lower_ci": 6.0, "upper_ci": 8.0, "vs_england": "S"} for _ in range(30)]
ok_, why, facts = p.ft_msoa_series_check(good, 40)
check("a series with figures for 75% of MSOAs and narrow intervals ships", ok_ and facts["cover"] == 0.75, why)
ok_, why, _ = p.ft_msoa_series_check([], 40)
check("a series with no rows here does not ship", not ok_ and "no figures" in why, why)
ok_, why, _ = p.ft_msoa_series_check([{"value": None, "vs_england": ""}] * 40, 40)
check("a series every figure of which is withheld does not ship", not ok_ and "withheld" in why, why)
thin = good[:10] + [{"value": None}] * 30
ok_, why, facts = p.ft_msoa_series_check(thin, 40)
check("a series with figures for a quarter of MSOAs does not ship, and says how many were withheld",
      not ok_ and "10 of the 40" in why and facts["withheld"] == 30, why)
zeros = good[:15] + [{"value": 0.0, "vs_england": ""}] * 25
ok_, why, _ = p.ft_msoa_series_check(zeros, 40)
check("a 0 with no England comparison counts as withheld (ft_msoa_drop_bare_zeros)", not ok_, why)
wide = [{"value": 100.0, "lower_ci": 0.0, "upper_ci": 260.0, "vs_england": "S"} for _ in range(40)]
ok_, why, facts = p.ft_msoa_series_check(wide, 40)
check("a series whose intervals are wider than the figures does not ship",
      not ok_ and "interval" in why and facts["ci_half_width"] == 1.3, why)
check("without limits the interval test is not applied",
      p.ft_msoa_series_check([{"value": 5.0, "vs_england": "S"}] * 40, 40)[0])

print("=== OHID's blanks become no figure ===")
df = pd.DataFrame([
    {"Area Code": MSOAS[0], "Value": "8.1", "Lower CI 95.0 limit": "6.0", "Upper CI 95.0 limit": "10.0",
     "Value note": "", "Compared to England value or percentiles": "Worse", "Time period": "2018 - 22",
     "Time period Sortable": "20180000"},
    {"Area Code": MSOAS[1], "Value": "0", "Lower CI 95.0 limit": "0", "Upper CI 95.0 limit": "-1",
     "Value note": "", "Compared to England value or percentiles": "", "Time period": "2018 - 22",
     "Time period Sortable": "20180000"},
    {"Area Code": MSOAS[2], "Value": "5.5", "Lower CI 95.0 limit": "4", "Upper CI 95.0 limit": "7",
     "Value note": "Value cannot be calculated", "Compared to England value or percentiles": "",
     "Time period": "2018 - 22", "Time period Sortable": "20180000"},
], dtype=str)
p.get_msoa21_to_msoa11 = lambda: {}
rows = []
p._ft_msoa_variant(93092, df, "Low birth weight of live babies", set(MSOAS), rows, set(), detail=True)
by = {r["MSOA21CD"]: r for r in rows}
check("a figure keeps its 95% limits and OHID's comparison",
      by[MSOAS[0]]["value"] == 8.1 and by[MSOAS[0]]["lower_ci"] == 6.0 and by[MSOAS[0]]["vs_england"] == "W")
check("a placeholder whose limits run backwards is no figure", by[MSOAS[1]]["value"] is None)
check("a value whose note says it cannot be calculated is no figure", by[MSOAS[2]]["value"] is None)
rows_plain = []
p._ft_msoa_variant(93092, df, "x", set(MSOAS), rows_plain, set())
check("the established series' rows are written as before", set(rows_plain[0]) ==
      {"MSOA21CD", "indicator_id", "indicator_name", "value", "period", "vs_england", "estimated"})

print("=== the source, end to end ===")
tmp = pathlib.Path(tempfile.mkdtemp())
p.REPO_ROOT = tmp
p.CACHE_DIR = tmp / "cache"
p.DATA_DIR = tmp / "data"
p.NATIONAL_DATA_DIR = tmp / "data"
p.CUSTOM_DIR = tmp / "data" / "custom"
(p.DATA_DIR / "outcomes").mkdir(parents=True)
(p.DATA_DIR / "demographics").mkdir(parents=True)
cache = p.CACHE_DIR / "fingertips_msoa_extra"
cache.mkdir(parents=True)
p.SCOPE_LADS = set(LADS)
p.LAD_NAMES = {LADS[0]: "Brent", LADS[1]: "Ealing"}
p.get_lsoa_to_msoa = lambda: dict(L2M)
NAMES = {i: n for i, n, _ in p.FINGERTIPS_MSOA_EXTRA}
# 93092: a full series of percentages, as the snapshot holds.
write_csv(cache / "ind_93092_p15.csv", csv_rows(93092, NAMES[93092], value=lambda i: 2.7 + i * 0.29, half=0.3))
# 93234: almost every MSOA withheld for small counts.
write_csv(cache / "ind_93234_p15.csv", csv_rows(93234, "Incidence of all cancers, standardised incidence ratio",
                                                blank=0.8))
# 93235: women only, as breast cancer is published.
write_csv(cache / "ind_93235_p15.csv", csv_rows(93235, NAMES[93235], sex="Female", half=0.25))
# 93236: intervals far wider than the ratios.
write_csv(cache / "ind_93236_p15.csv", csv_rows(93236, NAMES[93236], half=1.6))
# 93237: the id now holds something else.
write_csv(cache / "ind_93237_p15.csv", csv_rows(93237, "Emergency hospital admissions: all causes"))
# 93238: no cache, and Fingertips answers with a header row only (no MSOA rows).


class R:
    def __init__(self, content):
        self.content = content


calls = []


def fake_get(url, timeout=60):
    calls.append(url)
    if "indicator_ids=93238" in url:
        return R(b"Indicator ID,Indicator Name,Area Code\n")
    raise RuntimeError("no network in this test")


p._ft_get = fake_get
# OHID's own LA figures for low birth weight (area type 501).
write_csv(cache / "ind_93092_la501.csv", csv_rows(93092, NAMES[93092], area_type="Districts & UAs",
                                                  codes=LADS + ["E92000001"], value=lambda i: 6.5 + i))
p._SOURCE_ERROR.clear(); p._SOURCE_PARTIAL.clear(); p._SOURCE_SKIPPED.clear()
log = io.StringIO()
with redirect_stdout(log):
    out = p.run_fingertips_msoa_extra()
text = log.getvalue()
ids = sorted(out["indicator_id"].unique())
check("low birth weight and breast cancer ship; the withheld, too-wide, misnamed and empty ones do not",
      ids == [93092, 93235], str(ids))
check("each shipped series is in the scope's MSOAs", set(out["MSOA21CD"]) <= set(MSOAS))
check("the figures and limits are OHID's",
      abs(out[(out.indicator_id == 93092) & (out.MSOA21CD == MSOAS[0])]["value"].iloc[0] - 2.7) < 1e-9
      and out["lower_ci"].notna().all())
checks = json.loads((p.DATA_DIR / "outcomes" / p.FT_MSOA_EXTRA_CHECKS).read_text(encoding="utf-8"))["series"]
check("every series' verdict is recorded with its reason",
      checks["93092"]["ships"] and not checks["93234"]["ships"] and "withheld" in checks["93234"]["why"]
      and "interval" in checks["93236"]["why"] and checks["93237"]["kind"] == "name"
      and checks["93238"]["why"] == "Fingertips returned no MSOA rows", json.dumps(checks)[:600])
check("the log says what shipped and why the rest did not",
      "fingertips msoa extra 93092 (Low birth weight of live babies): ships" in text
      and "93236" in text and "not shipped" in text, text[-1500:])
check("a longer form of the cancer name is accepted (it was withheld, not misnamed)",
      checks["93234"]["kind"] == "publisher")
la = pd.read_parquet(p.DATA_DIR / "outcomes" / p.FT_MSOA_EXTRA_LA_PARQUET)
check("OHID's LA figures are kept for the series that ship, for the scope's LAs only",
      sorted(la["indicator_id"].unique()) == [93092] and set(la["LAD25CD"]) == set(LADS), la.to_string()[:300])
check("only 93092's LA file was needed, and no LA file was asked for a dropped series",
      not any("child_area_type_id=501" in u and ("93234" in u or "93236" in u) for u in calls), str(calls))
check("a misnamed id fails the source, with the reason", "93237" in p._SOURCE_ERROR.get("fingertips_msoa_extra", ""))
check("the dropped ones are known gaps for the region check",
      p.local_health_known_gaps() == ["ft_93234", "ft_93236", "ft_93238"], str(p.local_health_known_gaps()))
check("the established Local Health list does not hold the new ids",
      not set(NAMES) & set(p.FINGERTIPS_MSOA_INDICATORS))
check("nothing was written for the established sources", not (p.DATA_DIR / "outcomes" / "fingertips_msoa.parquet").exists())

print("=== the builders read the new series beside the established ones ===")
pd.DataFrame([{"MSOA21CD": m, "indicator_id": 93283, "indicator_name": "Life expectancy at birth (women)",
               "value": 84.0, "period": "2019 - 23", "vs_england": "S", "estimated": False} for m in MSOAS]
             ).to_parquet(p.DATA_DIR / "outcomes" / "fingertips_msoa.parquet", index=False)
pd.DataFrame([{"LAD25CD": LADS[0], "indicator_id": 93283, "value": 83.5, "period": "2019 - 23"}]
             ).to_parquet(p.DATA_DIR / "outcomes" / "fingertips_msoa_la.parquet", index=False)
both = p.local_health_msoa_frame()
check("one frame holds both", {93283, 93092, 93235} <= set(both["indicator_id"].unique()))
md = p.build_msoa_data()
check("msoa_data.json carries ft_93092 and its England comparison",
      md[MSOAS[0]].get("ft_93092") == 2.7 and md[MSOAS[0]].get("ft_93092_vs") == "S" and "ft_93283" in md[MSOAS[0]],
      str(md.get(MSOAS[0])))
pd.DataFrame({"LSOA21CD": list(POP), "census_population": list(POP.values())}).to_parquet(
    p.DATA_DIR / "demographics" / "census2021.parquet", index=False)
(p.DATA_DIR / "boundaries").mkdir(parents=True, exist_ok=True)
wards = sorted(set(L2W.values()))
(p.DATA_DIR / "boundaries" / "wards.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"WD25CD": wd, "WD25NM": f"Ward {wd}",
     "LAD25CD": LADS[0], "LAD25NM": "Brent"}} for wd in wards]}), encoding="utf-8")
p._census_ward_inputs = lambda: (None, None, {})
p.get_oa_to_ward = lambda: {}
p.get_lsoa_to_ward = lambda: dict(L2W)
p.get_lsoa_ward_lookup = lambda: {l: (L2W[l], L2LAD[l]) for l in L2M}
p._ward_to_lad = lambda: {wd: LADS[0] for wd in wards}
wd = p.build_ward_data()
w0 = wd["wards"][wards[0]]["indicators"]
# Ward 0 holds LSOAs 0 to 3, so MSOAs 0 and 1: (2.7 x 3000 + 2.99 x 3040) / 6040.
exp = round((2.7 * (POP["E01000000"] + POP["E01000001"]) + 2.99 * (POP["E01000002"] + POP["E01000003"]))
            / sum(POP[f"E01{i:06d}"] for i in range(4)), 4)
check("a ward's low birth weight blends its MSOAs by residents", w0.get("ft_93092") == exp, f"{w0.get('ft_93092')} vs {exp}")
bd = p.build_borough_data()
check("an LA carries OHID's own LA figure under the same key",
      bd[LADS[0]].get("ft_93092") == 6.5 and bd[LADS[0]].get("ft_93283") == 83.5, str(bd.get(LADS[0])))
stats = p.build_indicator_stats(wd, {}, md, bd)
check("indicators.js lists the dropped series as known gaps", stats["known_gaps"] == ["ft_93234", "ft_93236", "ft_93238"])
check("and measures the shipped ones at MSOA, ward and LA",
      set(stats["stats"]["ft_93092"]["lv"]) == {"msoa", "ward", "borough"}, str(stats["stats"].get("ft_93092")))

print("=== nothing to ship is skipped, not failed, and clears the old file ===")
for f in list(cache.glob("ind_9309*")) + list(cache.glob("ind_93235*")):
    f.unlink()
write_csv(cache / "ind_93092_p15.csv", csv_rows(93092, NAMES[93092], blank=0.9))
(cache / "ind_93237_p15.csv").unlink()
write_csv(cache / "ind_93237_p15.csv", csv_rows(93237, NAMES[93237], blank=1.0))
write_csv(cache / "ind_93235_p15.csv", csv_rows(93235, NAMES[93235], sex="Female", blank=1.0))
p._SOURCE_ERROR.clear(); p._SOURCE_PARTIAL.clear(); p._SOURCE_SKIPPED.clear()
with redirect_stdout(io.StringIO()):
    res = p.run_fingertips_msoa_extra()
check("returns no frame, so main() marks it skipped", res is None)
check("with the reasons as its note", "no series shipped" in p._SOURCE_SKIPPED.get("fingertips_msoa_extra", ""))
check("and no failure or partial", not p._SOURCE_ERROR and not p._SOURCE_PARTIAL, str((p._SOURCE_ERROR, p._SOURCE_PARTIAL)))
check("the old rows are gone, so the map drops the series",
      len(pd.read_parquet(p.DATA_DIR / "outcomes" / p.FT_MSOA_EXTRA_PARQUET)) == 0)

print("=== a failed download keeps last run's figures (partial), never the checks' drops ===")
for f in cache.glob("*"):
    f.unlink()
prev = pd.DataFrame([{"MSOA21CD": m, "indicator_id": 93092, "indicator_name": NAMES[93092], "value": 7.0,
                      "period": "2018 - 22", "vs_england": "S", "estimated": False, "lower_ci": 6.0,
                      "upper_ci": 8.0, "value_note": ""} for m in MSOAS])
prev.to_parquet(p.DATA_DIR / "outcomes" / p.FT_MSOA_EXTRA_PARQUET, index=False)
p._ft_get = lambda url, timeout=60: (_ for _ in ()).throw(RuntimeError("Fingertips is down"))
p._SOURCE_ERROR.clear(); p._SOURCE_PARTIAL.clear(); p._SOURCE_SKIPPED.clear()
with redirect_stdout(io.StringIO()):
    kept = p.run_fingertips_msoa_extra()
check("last run's low birth weight is kept", kept is not None and set(kept["indicator_id"]) == {93092})
check("the source is partial, not failed: the lost series has earlier figures, and the rest were "
      "dropped by the last check, whose verdict stands",
      "fingertips_msoa_extra" in p._SOURCE_PARTIAL and "fingertips_msoa_extra" not in p._SOURCE_ERROR,
      str((p._SOURCE_PARTIAL, p._SOURCE_ERROR)))
again = json.loads((p.DATA_DIR / "outcomes" / p.FT_MSOA_EXTRA_CHECKS).read_text(encoding="utf-8"))["series"]
check("the kept verdicts say this run could not download them, and they stay known gaps",
      "could not download" in again["93236"]["why"] and "ft_93236" in p.local_health_known_gaps(), again["93236"]["why"])
(p.DATA_DIR / "outcomes" / p.FT_MSOA_EXTRA_PARQUET).unlink()
(p.DATA_DIR / "outcomes" / p.FT_MSOA_EXTRA_CHECKS).unlink()
p._SOURCE_ERROR.clear(); p._SOURCE_PARTIAL.clear()
try:
    with redirect_stdout(io.StringIO()):
        p.run_fingertips_msoa_extra()
    raised = ""
except RuntimeError as e:
    raised = str(e)
check("with nothing earlier to keep, it is a clear failure", "could be downloaded" in raised, raised)

print("=== main() records a skipped source with its reason ===")
captured = {}
p.SOURCES = {"fingertips_msoa_extra": lambda: (p._SOURCE_SKIPPED.__setitem__("fingertips_msoa_extra", "no series shipped: x"), None)[1]}
p.write_manifest = lambda recs: captured.update(recs)
p.export_all = lambda: None
argv = sys.argv
sys.argv = ["fetch_all_data.py", "--only", "fingertips_msoa_extra"]
try:
    with redirect_stdout(io.StringIO()):
        p.main()
finally:
    sys.argv = argv
rec = captured.get("fingertips_msoa_extra", {})
check("status skipped, note the reason", rec.get("status") == "skipped" and rec.get("notes") == "no series shipped: x", str(rec))

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
