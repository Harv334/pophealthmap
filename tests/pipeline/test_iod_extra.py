"""The IoD 2025 figures beyond the IMD (run_iod_extra, run_iod_indicators):
IDACI and IDAOPI with their ranks and deciles, the six sub-domain scores and
the underlying indicators, from synthetic copies of Files 7 and 8.

Offline. The headers are written as the publisher prints them: the IDACI one
as the fact-check note quotes it from Leicester's IoD 2025 field glossary
("Income Deprivation Affecting Children Index (IDACI) Score (rate)"), the rest
in the 2019 File 7 and File 8 layout the pipeline's comments describe
(fetch_all_data.py, SOURCE 3a). The columns are put in an awkward order on
purpose: IDACI before the Income domain and each sub-domain before its domain,
so a lookup that only asks for "income" and "score" would take the wrong one.

Covers the column matching, the scaling and the direction checks, the ward
and LA weighting (IDACI by children aged 0 to 15, IDAOPI by people aged 60 and
over, everything else by residents), the LSOA, ward and LA payloads, and the
failure paths: a missing file, a missing column and a column that runs the
wrong way are all reported in the manifest rather than crashing the run.
"""
import importlib.util
import json
import pathlib
import random
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


def raises(fn, *, contains=""):
    try:
        fn()
    except RuntimeError as e:
        return contains.lower() in str(e).lower()
    return False


# ---------------------------------------------------------------------------
# A synthetic File 7: 400 LSOAs whose measures move with their domains.
# ---------------------------------------------------------------------------
rng = random.Random(7)
N = 400
CODES = [f"E01{i:06d}" for i in range(1, N + 1)]


def ranks_of(vals):
    """1 for the highest value (the most deprived), as MHCLG ranks."""
    order = sorted(range(len(vals)), key=lambda i: -vals[i])
    r = [0] * len(vals)
    for pos, i in enumerate(order):
        r[i] = pos + 1
    return r


def deciles_of(rk):
    return [min(10, 1 + (x - 1) * 10 // len(rk)) for x in rk]


inc = [round(rng.uniform(0.02, 0.55), 3) for _ in CODES]
idaci = [round(min(0.95, max(0.0, r * 1.3 + rng.uniform(-0.03, 0.03))), 3) for r in inc]
idaopi = [round(min(0.95, max(0.0, r * 1.1 + rng.uniform(-0.04, 0.04))), 3) for r in inc]
edu = [rng.uniform(0, 80) for _ in CODES]
bar = [rng.uniform(5, 60) for _ in CODES]
env = [rng.uniform(1, 80) for _ in CODES]
hea = [rng.uniform(-2.5, 2.5) for _ in CODES]
kids = [rng.randint(100, 500) for _ in CODES]
older = [rng.randint(100, 600) for _ in CODES]
total = [k + o + rng.randint(600, 1500) for k, o in zip(kids, older)]
idaci_rank, idaopi_rank = ranks_of(idaci), ranks_of(idaopi)

FILE7 = pd.DataFrame({
    "LSOA code (2021)": CODES,
    "LSOA name (2021)": [f"Somewhere {i:03d}A" for i in range(N)],
    "Local Authority District code (2024)": ["E09000001"] * N,
    "Local Authority District name (2024)": ["City of London"] * N,
    "Index of Multiple Deprivation (IMD) Score": [round(r * 100, 3) for r in inc],
    "Index of Multiple Deprivation (IMD) Rank (where 1 is most deprived)": ranks_of(inc),
    "Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs)": deciles_of(ranks_of(inc)),
    # IDACI first: "income" and "score" are in this header too.
    "Income Deprivation Affecting Children Index (IDACI) Score (rate)": idaci,
    "Income Deprivation Affecting Children Index (IDACI) Rank (where 1 is most deprived)": idaci_rank,
    "Income Deprivation Affecting Children Index (IDACI) Decile (where 1 is most deprived 10% of LSOAs)": deciles_of(idaci_rank),
    "Income Score (rate)": inc,
    "Income Rank (where 1 is most deprived)": ranks_of(inc),
    "Employment Score (rate)": [round(r * 0.6, 3) for r in inc],
    "Children and Young People Sub-domain Score": [round(e / 40 - 1 + rng.uniform(-0.3, 0.3), 3) for e in edu],
    "Adult Skills Sub-domain Score": [round(e / 40 - 1 + rng.uniform(-0.3, 0.3), 3) for e in edu],
    "Education, Skills and Training Score": [round(e, 3) for e in edu],
    "Health Deprivation and Disability Score": [round(h, 3) for h in hea],
    "Crime Score": [round(rng.uniform(-2, 2), 3) for _ in CODES],
    "Geographical Barriers Sub-domain Score": [round(b / 30 - 1 + rng.uniform(-0.5, 0.5), 3) for b in bar],
    "Wider Barriers Sub-domain Score": [round(b / 30 - 1 + rng.uniform(-0.3, 0.3), 3) for b in bar],
    "Barriers to Housing and Services Score": [round(b, 3) for b in bar],
    "Indoors Sub-domain Score": [round(v / 40 - 1 + rng.uniform(-0.3, 0.3), 3) for v in env],
    "Outdoors Sub-domain Score": [round(v / 40 - 1 + rng.uniform(-0.3, 0.3), 3) for v in env],
    "Living Environment Score": [round(v, 3) for v in env],
    "Income Deprivation Affecting Older People (IDAOPI) Score (rate)": idaopi,
    "Income Deprivation Affecting Older People (IDAOPI) Rank (where 1 is most deprived)": idaopi_rank,
    "Income Deprivation Affecting Older People (IDAOPI) Decile (where 1 is most deprived 10% of LSOAs)": deciles_of(idaopi_rank),
    "Total population: mid 2022 (excluding prisoners)": total,
    "Dependent Children aged 0-15: mid 2022 (excluding prisoners)": kids,
    "Population aged 16-59: mid 2022 (excluding prisoners)": [t - k - o for t, k, o in zip(total, kids, older)],
    "Older population aged 60 and over: mid 2022 (excluding prisoners)": older,
    "Working age population 18-66: for use with Employment Deprivation Domain (excluding prisoners)": [t - k for t, k in zip(total, kids)],
})

print("=== header matching ===")
check("headers are normalised: sub-domain, BOM and 0-15",
      p.iod_norm("﻿Children and Young People Sub-domain Score") == "children and young people sub domain score"
      and "aged 0 15" in p.iod_norm("Dependent Children aged 0-15: mid 2022"))
dom = p.iod_domain_cols(FILE7.columns)
check("the Income domain is not the IDACI column, though IDACI comes first",
      dom["income_score"] == "Income Score (rate)", str(dom["income_score"]))
check("the Education domain is not a sub-domain", dom["education_score"] == "Education, Skills and Training Score",
      str(dom["education_score"]))
check("the Barriers domain is not geographical or wider barriers",
      dom["barriers_score"] == "Barriers to Housing and Services Score", str(dom["barriers_score"]))
check("the Living Environment domain is not indoors or outdoors",
      dom["environment_score"] == "Living Environment Score", str(dom["environment_score"]))
check("the IMD score is the score, not its rank or decile",
      dom["imd_score"] == "Index of Multiple Deprivation (IMD) Score", str(dom["imd_score"]))

print("=== run_imd2025 rebuilds from File 7 without taking IDACI for income ===")
tmp = pathlib.Path(tempfile.mkdtemp())
p.REPO_ROOT = tmp
p.CACHE_DIR = tmp / "cache"
p.IOD_CACHE = p.CACHE_DIR / "iod2025"
p.DATA_DIR = tmp / "data"
p.NATIONAL_DATA_DIR = tmp / "data"
p.CUSTOM_DIR = tmp / "data" / "custom"
(p.CACHE_DIR / "imd2025").mkdir(parents=True)
FILE7.to_csv(p.CACHE_DIR / "imd2025" / "File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv",
             index=False, encoding="utf-8-sig")
imd = p.run_imd2025()
check("income_score is the Income domain's rate", list(imd["income_score"]) == inc)
check("barriers_score is the domain, not a sub-domain",
      list(imd["barriers_score"].round(3)) == [round(b, 3) for b in bar])

print("=== File 7: IDACI, IDAOPI, sub-domains and denominators ===")
out, problems, matched = p.iod_parse_file7(FILE7)
want = [f[0] for f in p.IOD_FILE7_FIELDS] + [d[0] for d in p.IOD_FILE7_DENOMINATORS]
check("every field and denominator is found, with nothing reported",
      all(k in out.columns for k in want) and not problems, "; ".join(problems))
row = out.set_index("LSOA21CD").loc[CODES[0]]
check("IDACI is stored as a percentage of children (rate times 100)",
      abs(row["idaci_pct"] - idaci[0] * 100) < 1e-9, f"{row['idaci_pct']} vs {idaci[0]}")
check("the IDACI rank and decile are MHCLG's own", row["idaci_rank"] == idaci_rank[0]
      and row["idaci_decile"] == deciles_of(idaci_rank)[0])
check("the denominators are children aged 0 to 15 and people aged 60 and over",
      matched["iod_pop_children"].startswith("Dependent Children aged 0-15")
      and matched["iod_pop_older"].startswith("Older population aged 60"), str(matched))
check("the sub-domains come from their own columns",
      matched["indoors_score"] == "Indoors Sub-domain Score"
      and matched["cyp_education_score"] == "Children and Young People Sub-domain Score", str(matched))
check("rows that are not English LSOAs are dropped",
      len(p.iod_parse_file7(pd.concat([FILE7, pd.DataFrame({"LSOA code (2021)": ["Notes: see the technical report"]})]))[0]) == N)

print("=== File 7: what is missing or wrong is reported, not guessed ===")
less = FILE7.drop(columns=["Income Deprivation Affecting Older People (IDAOPI) Rank (where 1 is most deprived)",
                           "Dependent Children aged 0-15: mid 2022 (excluding prisoners)"])
out2, problems2, _ = p.iod_parse_file7(less)
check("a missing rank is reported and the rest kept",
      any(x.startswith("idaopi_rank") for x in problems2) and "idaopi_pct" in out2.columns
      and "idaopi_rank" not in out2.columns, "; ".join(problems2))
check("a missing denominator is reported", any(x.startswith("iod_pop_children") for x in problems2))
flipped = FILE7.copy()
flipped["Indoors Sub-domain Score"] = -flipped["Indoors Sub-domain Score"]
flipped["Income Deprivation Affecting Children Index (IDACI) Rank (where 1 is most deprived)"] = \
    N + 1 - flipped["Income Deprivation Affecting Children Index (IDACI) Rank (where 1 is most deprived)"]
out3, problems3, _ = p.iod_parse_file7(flipped)
check("a sub-domain running against its domain is not used",
      "indoors_score" not in out3.columns and any("indoors_score" in x and "not used" in x for x in problems3),
      "; ".join(problems3))
check("a rank that rises with the rate (1 the least deprived) is not used",
      "idaci_rank" not in out3.columns and any("idaci_rank" in x for x in problems3))
pct = FILE7.copy()
pct["Income Deprivation Affecting Children Index (IDACI) Score (rate)"] *= 100
check("a rate already published as a percentage is not multiplied again",
      abs(p.iod_parse_file7(pct)[0].set_index("LSOA21CD").loc[CODES[0], "idaci_pct"] - idaci[0] * 100) < 1e-6)
check("no LSOA code column is a clear error",
      raises(lambda: p.iod_parse_file7(FILE7.drop(columns=["LSOA code (2021)"])), contains="no LSOA code column"))

# ---------------------------------------------------------------------------
# A synthetic File 8 workbook, one sheet per domain, in the 2019 layout.
# ---------------------------------------------------------------------------
PARENT = pd.DataFrame({"LSOA21CD": CODES, "health_score": hea, "education_score": edu,
                       "environment_score": env})
lo_edu = [e / 100 for e in edu]
HEALTH = pd.DataFrame({
    "LSOA code (2021)": CODES,
    "LSOA name (2021)": [f"Somewhere {i:03d}A" for i in range(N)],
    "Years of potential life lost indicator": [round(30 + 20 * h + rng.uniform(-5, 5), 1) for h in hea],
    "Comparative illness and disability ratio indicator": [round(1 + 0.3 * h + rng.uniform(-0.1, 0.1), 3) for h in hea],
    "Acute morbidity indicator": [round(1 + 0.2 * h + rng.uniform(-0.1, 0.1), 3) for h in hea],
    "Mood and anxiety disorders indicator": [round(0.4 * h + rng.uniform(-0.3, 0.3), 3) for h in hea],
})
EDU = pd.DataFrame({
    "LSOA code (2021)": CODES,
    "Key Stage 2 attainment (average points score) indicator": [round(110 - 0.2 * e + rng.uniform(-2, 2), 2) for e in edu],
    "Key Stage 4 attainment (average capped points score) indicator": [round(380 - 2 * e + rng.uniform(-10, 10), 1) for e in edu],
    "Secondary school absence indicator": [round(0.05 + x / 10, 3) for x in lo_edu],
    "Staying on in education post 16 indicator": [round(0.05 + 0.3 * x, 3) for x in lo_edu],
    "Entry to higher education indicator": [round(0.3 + 0.5 * x, 3) for x in lo_edu],
    "Adult skills: proportion of adults aged 25 to 66 with no or low qualifications": [round(0.1 + 0.4 * x, 3) for x in lo_edu],
    "English language proficiency indicator": [round(0.01 + 0.1 * x + rng.uniform(0, 0.02), 3) for x in lo_edu],
})
LIVING = pd.DataFrame({
    "LSOA code (2021)": CODES,
    "Housing in poor condition indicator": [round(0.05 + v / 400, 3) for v in env],
    "Houses without central heating indicator": [round(0.005 + v / 4000, 4) for v in env],
    "Road traffic accidents indicator": [round(0.5 + v / 80 + rng.uniform(-0.2, 0.2), 3) for v in env],
    "Nitrogen dioxide (component of air quality indicator)": [round(20 + v / 4, 2) for v in env],
    "Air quality indicator": [round(0.8 + v / 100 + rng.uniform(-0.1, 0.1), 3) for v in env],
})
SHEETS = {"IoD2025 Health Domain": HEALTH, "IoD2025 Education Domain": EDU, "IoD2025 Living Env Domain": LIVING}

print("=== File 8: the underlying indicators ===")
ind, problems8, matched8 = p.iod_parse_file8(SHEETS, PARENT)
keys8 = [s[0] for s in p.IOD_FILE8_SPECS]
check("all fourteen indicators are found, with nothing reported",
      all(k in ind.columns for k in keys8) and not problems8, "; ".join(problems8))
r8 = ind.set_index("LSOA21CD").loc[CODES[0]]
check("a share published from 0 to 1 is stored as a percentage",
      abs(r8["iod_housing_poor"] - LIVING["Housing in poor condition indicator"][0] * 100) < 1e-9)
check("a ratio published with England at 1 is stored with England at 100",
      abs(r8["iod_cidr"] - HEALTH["Comparative illness and disability ratio indicator"][0] * 100) < 1e-9)
check("attainment, where higher is better, is kept as published",
      r8["iod_ks2"] == EDU["Key Stage 2 attainment (average points score) indicator"][0])
check("air quality is the indicator, not one of its components",
      matched8["iod_air_quality"].endswith(": Air quality indicator"), matched8.get("iod_air_quality"))
check("adult qualifications and English proficiency are told apart",
      "qualifications" in matched8["iod_adult_quals"] and "English language" in matched8["iod_english"], str(matched8))
check("absence is not taken for any indicator",
      not any("absence" in v.lower() for v in matched8.values()))

print("=== File 8: missing, ambiguous, wrong-scale and wrong-way columns ===")
s2 = {k: v.copy() for k, v in SHEETS.items()}
s2["IoD2025 Living Env Domain"] = s2["IoD2025 Living Env Domain"].drop(columns=["Houses without central heating indicator"])
s2["IoD2025 Living Env Domain"]["Air quality indicator (modelled 2025)"] = LIVING["Air quality indicator"]
s2["IoD2025 Education Domain"]["Key Stage 2 attainment (average points score) indicator"] *= -1
s2["IoD2025 Health Domain"]["Acute morbidity indicator"] *= 1000
ind2, problems8b, _ = p.iod_parse_file8(s2, PARENT)
check("a missing indicator (central heating) is reported",
      any(x.startswith("iod_no_central_heating: no File 8 column") for x in problems8b), "; ".join(problems8b))
check("two columns matching one indicator: neither is used",
      "iod_air_quality" not in ind2.columns and any(x.startswith("iod_air_quality: 2 File 8 columns") for x in problems8b))
check("attainment running the deprivation way round is not used (its colours would be backwards)",
      "iod_ks2" not in ind2.columns and any("iod_ks2" in x and "direction" in x for x in problems8b))
check("a ratio on no recognisable scale is not used",
      "iod_acute_morbidity" not in ind2.columns and any("iod_acute_morbidity" in x and "ratio" in x for x in problems8b))
check("the others are still kept", "iod_ypll" in ind2.columns and "iod_ks4" in ind2.columns)
check("no sheet with LSOA codes is a clear error",
      raises(lambda: p.iod_parse_file8({"Notes": pd.DataFrame({"About": ["x"]})}, PARENT), contains="LSOA codes"))
_, problems_np, _ = p.iod_parse_file8(SHEETS, None)
check("without the IMD parquet the directions are not checked, and nothing fails", not problems_np)

print("=== finding the files on the release page ===")
FILES = [
    {"title": "File 1: index of multiple deprivation", "url": "https://assets.publishing.service.gov.uk/media/a/File_1_IoD2025_Index_of_Multiple_Deprivation.xlsx"},
    {"title": "File 7: all ranks, scores, deciles and population denominators", "url": "https://assets.publishing.service.gov.uk/media/b/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.xlsx"},
    {"title": "", "url": "https://assets.publishing.service.gov.uk/media/c/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv"},
    {"title": "File 10: local authority district summaries", "url": "https://assets.publishing.service.gov.uk/media/d/File_10_IoD2025_LAD_Summaries.xlsx"},
    {"title": "File 8: underlying indicators", "url": "https://assets.publishing.service.gov.uk/media/e/File_8_IoD2025_Underlying_Indicators.xlsx"},
]
check("File 7 is the CSV when there is one", p.iod_pick_file(FILES, 7, (".csv", ".xlsx")).endswith("Denominators.csv"))
check("File 8 by its title", p.iod_pick_file(FILES, 8, (".xlsx",)).endswith("Underlying_Indicators.xlsx"))
check("File 1 is not File 10", p.iod_pick_file(FILES, 1, (".xlsx",)).endswith("Multiple_Deprivation.xlsx"))
check("a title without the number is found by its words",
      p.iod_pick_file([{"title": "Underlying indicators", "url": "https://x/u.xlsx"}], 8, (".xlsx",),
                      (("underlying", "indicators"),)) == "https://x/u.xlsx")
check("nothing matching is None, not a guess", p.iod_pick_file(FILES[:1], 8, (".xlsx",)) is None)

print("=== wards and LAs: IDACI by children, IDAOPI by older people, the rest by residents ===")
E7 = pd.DataFrame({"LSOA21CD": ["A1", "A2", "B1"], "idaci_pct": [10.0, 30.0, 20.0],
                   "idaopi_pct": [5.0, 15.0, 8.0], "idaci_decile": [8, 2, 5],
                   "indoors_score": [1.0, 3.0, 2.0],
                   "iod_pop_children": [100, 300, 50], "iod_pop_older": [300, 100, 50]})
L2W = {"A1": "W1", "A2": "W1", "B1": "W2"}
POP = {"A1": 3000, "A2": 1000, "B1": 1000}
vals, used = p.iod_area_figures([E7], L2W, POP)
check("a ward's IDACI is its income-deprived children over its children (25%, not 15%)",
      vals["W1"]["idaci_pct"] == 25.0, str(vals["W1"]))
check("a ward's IDAOPI is weighted by its people aged 60 and over (7.5%)", vals["W1"]["idaopi_pct"] == 7.5)
check("a sub-domain score is weighted by residents (1.5)", vals["W1"]["indoors_score"] == 1.5)
check("deciles and the denominators are not carried up",
      "idaci_decile" not in vals["W1"] and not any(k.startswith("iod_pop_") for k in vals["W1"]))
check("the weights are recorded", used == {"idaci_pct": "children", "idaopi_pct": "older", "indoors_score": "residents"},
      str(used))
vals_r, used_r = p.iod_area_figures([E7.drop(columns=["iod_pop_children"])], L2W, POP)
check("without the File 7 denominator IDACI falls back to residents, and says so",
      vals_r["W1"]["idaci_pct"] == 15.0 and used_r["idaci_pct"] == "residents", str((vals_r["W1"], used_r)))

print("=== the ward, LA and LSOA payloads, from the files ===")
d = tmp / "data" / "demographics"
E7.to_parquet(d / p.IOD_EXTRA_PARQUET, index=False)
pd.DataFrame({"LSOA21CD": ["A1", "A2", "B1"], "iod_ks2": [104.0, 100.0, 102.0]}).to_parquet(
    d / p.IOD_INDICATORS_PARQUET, index=False)
pd.DataFrame({"LSOA21CD": list(POP), "census_population": list(POP.values())}).to_parquet(
    d / "census2021.parquet", index=False)
pd.DataFrame({"LSOA21CD": list(POP), "imd_score": [10.0, 20.0, 5.0], "imd_decile": [5, 2, 7]}).to_parquet(
    d / "imd2025.parquet", index=False)
(tmp / "data" / "boundaries").mkdir(parents=True, exist_ok=True)
(tmp / "data" / "boundaries" / "wards.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"WD25CD": wd, "WD25NM": f"Ward {wd}",
     "LAD25CD": "E09000001", "LAD25NM": "City of London"}} for wd in ("W1", "W2")]}), encoding="utf-8")
p._census_ward_inputs = lambda: (None, None, {})
p.get_oa_to_ward = lambda: {}
p.get_lsoa_to_ward = lambda: dict(L2W)
p.get_lsoa_ward_lookup = lambda: {l: (wd, "E09000001") for l, wd in L2W.items()}
p._ward_to_lad = lambda: {"W1": "E09000001", "W2": "E09000001"}
try:
    wd_out = p.build_ward_data()
    w1 = wd_out["wards"]["W1"]["indicators"]
    check("the ward file carries IDACI weighted by children", w1.get("idaci_pct") == 25.0, str(w1))
    check("and an underlying indicator weighted by residents", w1.get("iod_ks2") == 103.0, str(w1.get("iod_ks2")))
    check("the ward file says what each was weighted by",
          wd_out["metadata"].get("iod_weights", {}).get("idaci_pct") == "children", str(wd_out["metadata"].get("iod_weights")))
    check("no ward carries a decile or a denominator",
          "idaci_decile" not in w1 and "iod_pop_children" not in w1)
    bd = p.build_borough_data()
    la = bd.get("E09000001", {})
    # (10 x 100 + 30 x 300 + 20 x 50) / 450
    check("the LA's IDACI is all its LSOAs weighted by children (24.44)",
          la.get("idaci_pct") == round((1000 + 9000 + 1000) / 450, 2), str(la))
    ld = p.build_lsoa_data()
    a2 = ld.get("A2", {})
    check("an LSOA carries its own IDACI, decile and underlying indicator",
          a2.get("idaci_pct") == 30.0 and a2.get("idaci_decile") == 2 and isinstance(a2.get("idaci_decile"), int)
          and a2.get("iod_ks2") == 100.0, str(a2))
    check("an LSOA does not carry the File 7 denominators", "iod_pop_children" not in a2 and "iod_pop_older" not in a2)
except Exception as e:  # a lookup this scratch folder does not hold
    check("the builders run on the scratch folder", False, repr(e))

print("=== the sources end to end, and their failure paths ===")
for f in (d / p.IOD_EXTRA_PARQUET, d / p.IOD_INDICATORS_PARQUET):
    f.unlink()
p._SOURCE_ERROR.clear()
# File 7 is read from the IMD's drop folder, where it already is.
extra = p.run_iod_extra()
check("run_iod_extra writes every column from the raw File 7",
      all(k in extra.columns for k in want) and len(extra) == N and "iod_extra" not in p._SOURCE_ERROR,
      str(p._SOURCE_ERROR.get("iod_extra")))
check("and the parquet is on disk", (d / p.IOD_EXTRA_PARQUET).exists())
# File 8 as a workbook with a title row above each header, as MHCLG lays them out.
p.IOD_CACHE.mkdir(parents=True, exist_ok=True)
x8 = p.IOD_CACHE / "File_8_IoD2025_Underlying_Indicators.xlsx"
with pd.ExcelWriter(x8) as xw:
    pd.DataFrame({"Notes": ["English Indices of Deprivation 2025: underlying indicators"]}).to_excel(
        xw, sheet_name="Notes", index=False)
    for name, frame in SHEETS.items():
        pd.DataFrame([[name]]).to_excel(xw, sheet_name=name[:31], index=False, header=False)
        frame.to_excel(xw, sheet_name=name[:31], index=False, startrow=2)
pd.DataFrame({"LSOA21CD": CODES, "health_score": hea, "education_score": edu,
              "environment_score": env}).to_parquet(d / "imd2025.parquet", index=False)
ind_run = p.run_iod_indicators()
check("run_iod_indicators reads the workbook below its title rows",
      all(k in ind_run.columns for k in keys8) and "iod_indicators" not in p._SOURCE_ERROR,
      str(p._SOURCE_ERROR.get("iod_indicators")))
x8.unlink()

# Complete committed parquets: used as they are, with no download.
p._iod_download = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not download"))
for f in (p.CACHE_DIR / "imd2025").iterdir():
    f.unlink()
try:
    again = p.run_iod_extra()
    check("a complete committed parquet is used with no download", len(again) == N)
except AssertionError as e:
    check("a complete committed parquet is used with no download", False, str(e))

# An incomplete committed parquet and no way to download: the old figures are
# kept and the source is marked failed in the manifest, saying what to do.
p._iod_download = lambda *a, **k: None
pd.read_parquet(d / p.IOD_EXTRA_PARQUET).drop(columns=["idaopi_rank"]).to_parquet(d / p.IOD_EXTRA_PARQUET, index=False)
p._SOURCE_ERROR.clear()
kept = p.run_iod_extra()
msg = p._SOURCE_ERROR.get("iod_extra", "")
check("a failed download keeps the committed figures", len(kept) == N)
check("and marks the source failed with what to do", "could not be found or downloaded" in msg
      and "--only iod_extra" in msg, msg)
# Nothing on disk at all: a clear error, which main() records as a failed source.
(d / p.IOD_INDICATORS_PARQUET).unlink()
check("no file and no parquet is a clear error, not a crash",
      raises(p.run_iod_indicators, contains="File 8 of the English Indices of Deprivation 2025"))
# A raw File 8 missing an indicator: written, and the source marked failed.
s3 = {k: v.copy() for k, v in SHEETS.items()}
s3["IoD2025 Health Domain"] = s3["IoD2025 Health Domain"].drop(columns=["Acute morbidity indicator"])
with pd.ExcelWriter(x8) as xw:
    for name, frame in s3.items():
        frame.to_excel(xw, sheet_name=name[:31], index=False)
p._SOURCE_ERROR.clear()
p._SOURCE_NOTE.clear()
part = p.run_iod_indicators()
check("an indicator the file does not print is noted, not failed, and the rest is written",
      "iod_acute_morbidity" not in part.columns and "iod_ypll" in part.columns
      and "iod_indicators" not in p._SOURCE_ERROR
      and "iod_acute_morbidity" in p._SOURCE_NOTE.get("iod_indicators", ""),
      p._SOURCE_ERROR.get("iod_indicators", "") + " | " + p._SOURCE_NOTE.get("iod_indicators", ""))
# The same file again, when the committed parquet had that indicator: lost, so failed.
pd.read_parquet(d / p.IOD_INDICATORS_PARQUET).assign(iod_acute_morbidity=1.0).to_parquet(
    d / p.IOD_INDICATORS_PARQUET, index=False)
p._SOURCE_ERROR.clear()
p._SOURCE_NOTE.clear()
p.run_iod_indicators()
check("an indicator the committed parquet had and this file lacks is a failure",
      "iod_acute_morbidity" in p._SOURCE_ERROR.get("iod_indicators", ""), p._SOURCE_ERROR.get("iod_indicators", ""))
check("the rule: fewer than half found fails; a wrong-way column fails; a missing new one does not",
      p.iod_problems_fail(["a", "b", "c", "d"], ["a"], ["b: no File 8 column with 'b'"], None, True)
      and p.iod_problems_fail(["a", "b"], ["a"], ["b ('s': 'c') should rise as x rises; not used"], None, True)
      and not p.iod_problems_fail(["a", "b", "c"], ["a", "b"], ["c: no File 8 column with 'c'"], None, True)
      and p.iod_problems_fail(["a", "b", "c"], ["a", "b"], ["c: no File 8 column with 'c'"], None, False))
# A header that names its sub-domain is still read (only a sub-domain score is not).
s4 = {k: v.copy() for k, v in SHEETS.items()}
s4["IoD2025 Education Domain"] = s4["IoD2025 Education Domain"].rename(columns={
    "Key Stage 2 attainment (average points score) indicator":
    "Children and Young People Sub-domain: Key Stage 2 attainment (average points score) indicator"})
with pd.ExcelWriter(x8) as xw:
    for name, frame in s4.items():
        frame.to_excel(xw, sheet_name=name[:31], index=False)
(d / p.IOD_INDICATORS_PARQUET).unlink()
p._SOURCE_ERROR.clear()
sub = p.run_iod_indicators()
check("a header naming its sub-domain is read", "iod_ks2" in sub.columns, ", ".join(sub.columns))

print("=== registered as sources, after the IMD ===")
order = list(p.SOURCES)
check("iod_extra and iod_indicators run after imd",
      order.index("imd") < order.index("iod_extra") < order.index("iod_indicators"), str(order))

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
