"""The Census 2021 additions and fixes, checked offline on synthetic tables.

Builds Nomis-shaped tables for twelve output areas in four LSOAs and two
wards, runs them through the pipeline's own census rules (_census_frame,
census_group_figures, _census_ward_inputs, census_la_figures) and checks:

  * routine and semi-routine is NS-SEC L12 and L13 only. The old keywords
    ("l7 ", "l8 ") also took in L7 Intermediate occupations and L8 and L9
    Small employers, which roughly doubled the figure,
  * NS-SEC L14 (never worked and long-term unemployed) over residents 16+,
  * household deprivation comes from TS011, not TS044: any dimension is the
    total less "not deprived in any dimension", two or more is two, three
    and four added up,
  * TS003: one-person households aged 66 and over, lone parents with
    dependent children (not the non-dependent ones), and one-person
    households counted once whether the table gives a parent or a Total,
    with or without the hyphen in "One-person",
  * TS029 cannot speak English well or at all over residents aged 3+, and
    when the output area file carries only the main language split (as the
    fact-check found), wards and LAs are built from the LSOA counts,
  * TS052 overcrowded (-1 and -2 or less) and under-occupied (+2 or more),
    never reading "+1" as "-1",
  * TS039 50 or more hours of care, without the 20 to 49 bands,
  * TS066 economically inactive because long-term sick or disabled,
  * six TS021 detailed groups matched on parent and leaf, so Mixed "White and
    Black African" is not counted as Black African,
  * every new ward and LA figure is sum(num) / sum(den) of its areas,
  * the failure path: a label that does not match, or a table that did not
    download, writes no figure, is recorded, and marks the census source
    partial in the manifest; the OA build is not marked partial for keys ONS
    may not publish for output areas,
  * census_routine_classes reaches indicators.js only when every level was
    built since the fix, and England takes it only when every region has it.

Column names: TS062's are as the task gives them (the NS-SEC labels ONS
prints); TS011, TS029, TS039 and TS052 follow the ONS category lists quoted in
the indicator fact-check (scratchpad report data.json, "note"); TS003 uses
ONS's hyphenated "One-person household" with a second, hyphen-less variant;
TS021 follows the parent labels already in fetch_all_data.py with the leaf
names ONS uses. No network: _fetch_census_table and the lookups are replaced.
"""
import importlib.util, json, pathlib, re, sys, tempfile

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

WARNED = []
p.info = p.ok = lambda *a, **k: None
p.warn = lambda msg, *a, **k: WARNED.append(str(msg))
p._census_age_check = lambda *a, **k: None

rng = np.random.default_rng(11)
OAS = [f"E00{300000 + i}" for i in range(12)]
OA_LSOA = {oa: f"E01{400000 + i // 3}" for i, oa in enumerate(OAS)}
LSOAS = sorted(set(OA_LSOA.values()))
LSOA_WARD = {LSOAS[0]: "E05100001", LSOAS[1]: "E05100001",
             LSOAS[2]: "E05100002", LSOAS[3]: "E05100002"}
OA_WARD = {oa: LSOA_WARD[l] for oa, l in OA_LSOA.items()}
WARD_LAD = {"E05100001": "E09000002", "E05100002": "E09000002"}
WARDS = sorted(set(OA_WARD.values()))
n = len(OAS)

def ints(lo, hi):
    return rng.integers(lo, hi, n)

pop = ints(250, 420)
adults = (pop * 0.8).astype(int)
hh = ints(100, 170)
# NS-SEC, residents 16+: nine groups that add up to the total.
ns = {k: ints(5, 40) for k in ("l1", "l4", "l7", "l8", "l10", "l12", "l13", "l14", "l15")}
ns_tot = sum(ns.values())
# Household deprivation in 0 to 4 dimensions.
dep = {d: ints(2, 40) for d in range(5)}
dep_tot = sum(dep.values())
# Household composition.
one66, one_oth = ints(5, 25), ints(10, 30)
lp_dep, lp_nondep = ints(3, 15), ints(2, 10)
fam66, couple_dep, other_dep, other_rest = ints(3, 12), ints(10, 30), ints(1, 6), ints(2, 10)
comp_tot = one66 + one_oth + lp_dep + lp_nondep + fam66 + couple_dep + other_dep + other_rest
# Proficiency in English, residents 3+.
eng_main, very_well, well, not_well, cannot = ints(150, 300), ints(10, 40), ints(5, 20), ints(1, 9), ints(0, 4)
eng_tot = eng_main + very_well + well + not_well + cannot
# Occupancy rating for bedrooms.
occ = {k: ints(2, 40) for k in ("+2", "+1", "0", "-1", "-2")}
occ_tot = sum(occ.values())
# Unpaid care, residents 5+.
care = {k: ints(2, 20) for k in ("19", "20-34", "35-49", "50")}
care_none = ints(150, 300)
care_tot = care_none + sum(care.values())
# Economic activity, residents 16+.
ea = {k: ints(3, 60) for k in ("emp", "unemp", "retired", "sick", "home")}
ea_tot = sum(ea.values())
# Ethnic group.
eth = {k: ints(0, 30) for k in ("british", "irish", "gypsy", "roma", "oth_white", "bangladeshi",
                                "pakistani", "indian", "african", "caribbean", "oth_black",
                                "wb_african", "wb_caribbean", "arab")}
eth_tot = sum(eth.values())

NS = "National Statistics Socio-economic Classification (NS-SEC): "
OCC = "Occupancy rating for bedrooms: "
NOT_ENG = "Proficiency in English language: Main language is not English (English or Welsh in Wales)"
WHITE, ASIAN = "Ethnic group: White", "Ethnic group: Asian, Asian British or Asian Welsh"
BLACK = "Ethnic group: Black, Black British, Black Welsh, Caribbean or African"
MIXED, OTHER = "Ethnic group: Mixed or Multiple ethnic groups", "Ethnic group: Other ethnic group"


def tables(codes, rows, level, hyphen=True, ts029_detail=True, drop=()):
    agg = lambda a: [int(a[r].sum()) for r in rows]
    code = {"date": 2021, "geography": codes, "geography code": codes}
    one = "One-person household" if hyphen else "One person household"
    t = {
        "TS001": pd.DataFrame({**code, "Residence type: Total; measures: Value": agg(pop)}),
        "TS062": pd.DataFrame({**code,
            NS + "Total: All usual residents aged 16 years and over": agg(ns_tot),
            NS + "L1, L2 and L3 Higher managerial, administrative and professional occupations": agg(ns["l1"]),
            NS + "L4, L5 and L6 Lower managerial, administrative and professional occupations": agg(ns["l4"]),
            NS + "L7 Intermediate occupations": agg(ns["l7"]),
            NS + "L8 and L9 Small employers and own account workers": agg(ns["l8"]),
            NS + "L10 and L11 Lower supervisory and technical occupations": agg(ns["l10"]),
            NS + "L12 Semi-routine occupations": agg(ns["l12"]),
            NS + "L13 Routine occupations": agg(ns["l13"]),
            NS + "L14.1 and L14.2 Never worked and long-term unemployed": agg(ns["l14"]),
            NS + "L15 Full-time students": agg(ns["l15"])}),
        "TS011": pd.DataFrame({**code,
            "Household deprivation: Total: All households": agg(dep_tot),
            "Household deprivation: Household is not deprived in any dimension": agg(dep[0]),
            "Household deprivation: Household is deprived in one dimension": agg(dep[1]),
            "Household deprivation: Household is deprived in two dimensions": agg(dep[2]),
            "Household deprivation: Household is deprived in three dimensions": agg(dep[3]),
            "Household deprivation: Household is deprived in four dimensions": agg(dep[4])}),
        "TS003": pd.DataFrame({**code,
            # The refresh showed the real file has no "All households" here;
            # both spellings are tried.
            ("Household composition: Total: All households" if hyphen else
             "Household composition: Total; measures: Value"): agg(comp_tot),
            # The hyphen-less variant gives the group as a Total column; the
            # hyphenated one as a parent above its leaves.
            (f"Household composition: {one}" if hyphen else f"Household composition: {one}: Total"):
                agg(one66 + one_oth),
            f"Household composition: {one}: Aged 66 years and over": agg(one66),
            f"Household composition: {one}: Other": agg(one_oth),
            "Household composition: Single family household: All aged 66 years and over": agg(fam66),
            "Household composition: Single family household: Married or civil partnership couple: Dependent children": agg(couple_dep),
            "Household composition: Single family household: Lone parent family": agg(lp_dep + lp_nondep),
            "Household composition: Single family household: Lone parent family: With dependent children": agg(lp_dep),
            "Household composition: Single family household: Lone parent family: All children non-dependent": agg(lp_nondep),
            "Household composition: Other household types: With dependent children": agg(other_dep),
            "Household composition: Other household types: Other, including all full-time students and all aged 66 years and over": agg(other_rest)}),
        "TS029": pd.DataFrame({**code,
            "Proficiency in English language: Total: All usual residents aged 3 years and over": agg(eng_tot),
            "Proficiency in English language: Main language is English (English or Welsh in Wales)": agg(eng_main),
            NOT_ENG: agg(very_well + well + not_well + cannot),
            **({NOT_ENG + ": Can speak English very well": agg(very_well),
                NOT_ENG + ": Can speak English well": agg(well),
                NOT_ENG + ": Cannot speak English well": agg(not_well),
                NOT_ENG + ": Cannot speak English": agg(cannot)} if ts029_detail else {})}),
        "TS052": pd.DataFrame({**code,
            OCC + "Total: All households": agg(occ_tot),
            OCC + "Occupancy rating of bedrooms: +2 or more": agg(occ["+2"]),
            OCC + "Occupancy rating of bedrooms: +1": agg(occ["+1"]),
            OCC + "Occupancy rating of bedrooms: 0": agg(occ["0"]),
            OCC + "Occupancy rating of bedrooms: -1": agg(occ["-1"]),
            OCC + "Occupancy rating of bedrooms: -2 or less": agg(occ["-2"])}),
        "TS039": pd.DataFrame({**code,
            "Provision of unpaid care: Total: All usual residents aged 5 years and over": agg(care_tot),
            "Provision of unpaid care: Provides no unpaid care": agg(care_none),
            "Provision of unpaid care: Provides 19 hours or less unpaid care a week": agg(care["19"]),
            "Provision of unpaid care: Provides 20 to 49 hours unpaid care a week": agg(care["20-34"] + care["35-49"]),
            "Provision of unpaid care: Provides 20 to 49 hours unpaid care a week: Provides 20 to 34 hours unpaid care a week": agg(care["20-34"]),
            "Provision of unpaid care: Provides 20 to 49 hours unpaid care a week: Provides 35 to 49 hours unpaid care a week": agg(care["35-49"]),
            "Provision of unpaid care: Provides 50 or more hours unpaid care a week": agg(care["50"])}),
        "TS066": pd.DataFrame({**code,
            "Economic activity status: Total: All usual residents aged 16 years and over": agg(ea_tot),
            "Economic activity status: Economically active (excluding full-time students): In employment": agg(ea["emp"]),
            "Economic activity status: Economically active (excluding full-time students): Unemployed": agg(ea["unemp"]),
            "Economic activity status: Economically inactive: Retired": agg(ea["retired"]),
            "Economic activity status: Economically inactive: Long-term sick or disabled": agg(ea["sick"]),
            "Economic activity status: Economically inactive: Looking after home or family": agg(ea["home"])}),
        "TS021": pd.DataFrame({**code,
            "Ethnic group: Total: All usual residents": agg(eth_tot),
            WHITE: agg(eth["british"] + eth["irish"] + eth["gypsy"] + eth["roma"] + eth["oth_white"]),
            WHITE + ": English, Welsh, Scottish, Northern Irish or British": agg(eth["british"]),
            WHITE + ": Irish": agg(eth["irish"]),
            WHITE + ": Gypsy or Irish Traveller": agg(eth["gypsy"]),
            WHITE + ": Roma": agg(eth["roma"]),
            WHITE + ": Other White": agg(eth["oth_white"]),
            ASIAN: agg(eth["bangladeshi"] + eth["pakistani"] + eth["indian"]),
            ASIAN + ": Bangladeshi": agg(eth["bangladeshi"]),
            ASIAN + ": Pakistani": agg(eth["pakistani"]),
            ASIAN + ": Indian": agg(eth["indian"]),
            BLACK: agg(eth["african"] + eth["caribbean"] + eth["oth_black"]),
            BLACK + ": African": agg(eth["african"]),
            BLACK + ": Caribbean": agg(eth["caribbean"]),
            BLACK + ": Other Black": agg(eth["oth_black"]),
            MIXED: agg(eth["wb_african"] + eth["wb_caribbean"]),
            MIXED + ": White and Black African": agg(eth["wb_african"]),
            MIXED + ": White and Black Caribbean": agg(eth["wb_caribbean"]),
            OTHER: agg(eth["arab"]),
            OTHER + ": Arab": agg(eth["arab"])}),
    }
    for tab, col in drop:
        if col is None:
            t.pop(tab, None)
        else:
            t[tab] = t[tab].drop(columns=[col])
    return t

OA_ROWS = [[i] for i in range(n)]
LSOA_ROWS = [[i for i, oa in enumerate(OAS) if OA_LSOA[oa] == l] for l in LSOAS]
STATE = {"oa": tables(OAS, OA_ROWS, "oa", ts029_detail=False),   # as the fact-check found
         "lsoa": tables(LSOAS, LSOA_ROWS, "lsoa"),
         "msoa": tables(["E02000900"], [list(range(n))], "msoa")}
p._fetch_census_table = lambda tab, level="lsoa": STATE.get(level, {}).get(tab)
TMP = pathlib.Path(tempfile.mkdtemp())
p.DATA_DIR = TMP

# What each new key should be, per output area: (numerator, denominator).
TRUTH = {
    "census_routine_semi_routine_pct": (ns["l12"] + ns["l13"], ns_tot),
    "census_never_worked_ltu_pct": (ns["l14"], ns_tot),
    "census_higher_managerial_pct": (ns["l1"], ns_tot),
    "census_hh_deprived_any_pct": (dep_tot - dep[0], dep_tot),
    "census_hh_deprived_2plus_pct": (dep[2] + dep[3] + dep[4], dep_tot),
    "census_pensioner_alone_pct": (one66, comp_tot),
    "census_lone_parent_dep_pct": (lp_dep, comp_tot),
    "census_one_person_hh_pct": (one66 + one_oth, comp_tot),
    "census_poor_english_pct": (not_well + cannot, eng_tot),
    "census_overcrowded_pct": (occ["-1"] + occ["-2"], occ_tot),
    "census_underoccupied_pct": (occ["+2"], occ_tot),
    "census_unpaid_care_50h_pct": (care["50"], care_tot),
    "census_provides_unpaid_care_pct": (care_tot - care_none, care_tot),
    "census_inactive_ltsick_pct": (ea["sick"], ea_tot),
    "census_eth_gypsy_traveller_pct": (eth["gypsy"], eth_tot),
    "census_eth_roma_pct": (eth["roma"], eth_tot),
    "census_eth_bangladeshi_pct": (eth["bangladeshi"], eth_tot),
    "census_eth_pakistani_pct": (eth["pakistani"], eth_tot),
    "census_eth_black_african_pct": (eth["african"], eth_tot),
    "census_eth_black_caribbean_pct": (eth["caribbean"], eth_tot),
}
NEW = [k for k in TRUTH if k not in ("census_higher_managerial_pct", "census_provides_unpaid_care_pct")]

print("=== the new and fixed percentages, per area ===")
oa_full = p._census_frame("oa")
oa_miss = list(p._CENSUS_MISSES.get("oa", []))
lsoa_full = p._census_frame("lsoa")
lsoa_miss = list(p._CENSUS_MISSES.get("lsoa", []))
oa_figs, oa_cnt = p._census_split(oa_full, "LSOA21CD")
lsoa_figs, lsoa_cnt = p._census_split(lsoa_full, "LSOA21CD")

def want(k, idx):
    num, den = TRUTH[k]
    return num[idx].sum() / den[idx].sum() * 100

bad = []
for k in TRUTH:
    if k == "census_poor_english_pct":
        continue                       # not at OA level here; see below
    for i, oa in enumerate(OAS):
        got = float(oa_figs.set_index("LSOA21CD").loc[oa, k]) if k in oa_figs.columns else None
        if got is None or abs(got - round(want(k, [i]), 2)) > 0.006:
            bad.append(f"{oa} {k}: {got} vs {want(k, [i]):.2f}")
            break
check("every new and fixed output area percentage is its numerator over its base", not bad, "; ".join(bad[:4]))
bad = []
for k in TRUTH:
    for j, l in enumerate(LSOAS):
        got = float(lsoa_figs.set_index("LSOA21CD").loc[l, k]) if k in lsoa_figs.columns else None
        if got is None or abs(got - round(want(k, LSOA_ROWS[j]), 2)) > 0.006:
            bad.append(f"{l} {k}: {got} vs {want(k, LSOA_ROWS[j]):.2f}")
            break
check("and every LSOA one", not bad, "; ".join(bad[:4]))

old_cols = []
t62 = STATE["lsoa"]["TS062"]
for kws in [("l7 ",), ("l8 ",), ("routine occupations",), ("semi-routine",)]:
    old_cols += [c for c in p._cen_findall(t62, *kws) if c not in old_cols]
check("the old routine keywords took in L7 Intermediate and L8 and L9 Small employers (test has teeth)",
      any("L7 Intermediate" in c for c in old_cols) and any("L8 and L9" in c for c in old_cols), str(old_cols))
old = (ns["l7"] + ns["l8"] + ns["l12"] + ns["l13"]).sum() / ns_tot.sum() * 100
new = (ns["l12"] + ns["l13"]).sum() / ns_tot.sum() * 100
got_all = lsoa_cnt["census_routine_semi_routine_pct__num"].sum() / lsoa_cnt["census_routine_semi_routine_pct__den"].sum() * 100
check("routine and semi-routine now counts L12 and L13 only", abs(got_all - new) < 1e-9 and abs(got_all - old) > 5,
      f"{got_all:.2f} vs new {new:.2f}, old {old:.2f}")
check("the counts behind each new percentage are kept", all(k + "__num" in lsoa_cnt.columns and k + "__den" in lsoa_cnt.columns for k in NEW),
      [k for k in NEW if k + "__num" not in lsoa_cnt.columns])
check("the lone parent figure leaves out lone parents whose children are all non-dependent",
      abs(float(lsoa_cnt["census_lone_parent_dep_pct__num"].sum()) - lp_dep.sum()) < 1e-9)
check("one-person households are the group counted once, not the group plus its leaves",
      abs(float(lsoa_cnt["census_one_person_hh_pct__num"].sum()) - (one66 + one_oth).sum()) < 1e-9)
check("overcrowding never reads +1 as -1",
      abs(float(lsoa_cnt["census_overcrowded_pct__num"].sum()) - (occ["-1"] + occ["-2"]).sum()) < 1e-9)
check("50 or more hours of care leaves out the 20 to 49 bands",
      abs(float(lsoa_cnt["census_unpaid_care_50h_pct__num"].sum()) - care["50"].sum()) < 1e-9)
check("Black African is the Black group's leaf, not Mixed White and Black African",
      abs(float(lsoa_cnt["census_eth_black_african_pct__num"].sum()) - eth["african"].sum()) < 1e-9)
check("household deprivation no longer looks for TS044, and the old key is gone",
      "TS044" not in p.CENSUS_TABLES and "TS011" in p.CENSUS_TABLES and
      "census_housing_deprived_pct" not in lsoa_figs.columns)
# Only the tables this test builds; the others are simply not supplied here.
mine = lambda ms, lv: [m for m in ms if re.split(r"[:/]", m)[0] in STATE[lv]]
lsoa_miss, oa_miss = mine(lsoa_miss, "lsoa"), mine(oa_miss, "oa")
check("nothing was missed at LSOA level", not lsoa_miss, "; ".join(lsoa_miss))
check("at output area level only TS029's proficiency bands were missing, as ONS may publish",
      oa_miss and all("census_poor_english_pct" in m for m in oa_miss) and
      all(any(k in m for k in p.CENSUS_OA_MAY_LACK) for m in oa_miss), "; ".join(oa_miss))
check("so census_poor_english_pct is not in the output area figures", "census_poor_english_pct" not in oa_figs.columns)

# Without the hyphen, and with the group given as a Total column.
STATE["lsoa"] = tables(LSOAS, LSOA_ROWS, "lsoa", hyphen=False)
alt = p._census_frame("lsoa")
check("TS003 without the hyphen and with a Total column gives the same three figures",
      all(abs(float(alt.set_index("LSOA21CD").loc[l, k]) - float(lsoa_figs.set_index("LSOA21CD").loc[l, k])) < 1e-9
          for l in LSOAS for k in ("census_pensioner_alone_pct", "census_lone_parent_dep_pct", "census_one_person_hh_pct")))
# As the October 2026 refresh found it: the group as a parent above its
# parts, and every column ending "; measures: Value". The parent must still
# be counted once, not with its parts as well (that gave wards up to 125%).
nomis = tables(LSOAS, LSOA_ROWS, "lsoa")
nomis["TS003"] = nomis["TS003"].rename(columns=lambda c: c if c in ("date", "geography", "geography code")
                                       else c.replace("One-person", "One person").replace(
                                           "Total: All households", "Total") + "; measures: Value")
STATE["lsoa"] = nomis
alt = p._census_frame("lsoa")
check("TS003 with '; measures: Value' on every column counts the one person group once",
      all(abs(float(alt.set_index("LSOA21CD").loc[l, k]) - float(lsoa_figs.set_index("LSOA21CD").loc[l, k])) < 1e-9
          for l in LSOAS for k in ("census_pensioner_alone_pct", "census_lone_parent_dep_pct", "census_one_person_hh_pct")),
      str(alt.set_index("LSOA21CD")["census_one_person_hh_pct"].head(3).tolist()) + " vs " +
      str(lsoa_figs.set_index("LSOA21CD")["census_one_person_hh_pct"].head(3).tolist()))
STATE["lsoa"] = tables(LSOAS, LSOA_ROWS, "lsoa")

print("=== wards and LAs, from the counts ===")
FILES = {"census2021_oa.parquet": oa_figs.rename(columns={"LSOA21CD": "OA21CD"}),
         "census2021_oa_counts.parquet": oa_cnt.rename(columns={"LSOA21CD": "OA21CD"}),
         "census2021.parquet": lsoa_figs, "census2021_counts.parquet": lsoa_cnt}
p._read_parquet_opt = lambda path: FILES.get(pathlib.Path(path).name)
p.get_oa_to_ward = lambda: OA_WARD
p.get_lsoa_to_ward = lambda: LSOA_WARD
p._ward_to_lad = lambda: WARD_LAD
figs, cnt, lookup = p._census_ward_inputs()
ward = p.census_group_figures(figs, cnt, lookup)
bad = []
for k in TRUTH:
    for wd in WARDS:
        idx = [i for i, oa in enumerate(OAS) if OA_WARD[oa] == wd]
        w = round(want(k, idx), 2)
        if ward.get(wd, {}).get(k) != w:
            bad.append(f"{wd} {k}: {ward.get(wd, {}).get(k)} != {w}")
check("each ward figure is sum(num) / sum(den), census_poor_english_pct from its LSOAs", not bad, "; ".join(bad[:4]))
idx0 = [i for i, oa in enumerate(OAS) if OA_WARD[oa] == WARDS[0]]
check("ward population is still the output areas' residents, not counted again from LSOAs",
      ward[WARDS[0]]["census_population"] == int(pop[idx0].sum()), str(ward[WARDS[0]].get("census_population")))
la = p.census_la_figures().get("E09000002", {})
bad = [f"{k}: {la.get(k)} != {round(want(k, list(range(n))), 2)}" for k in TRUTH
       if la.get(k) != round(want(k, list(range(n))), 2)]
check("each LA figure is the summed counts of its areas", not bad, "; ".join(bad[:4]))
plain =p._census_add_lsoa_only(FILES["census2021_oa.parquet"].rename(columns={"OA21CD": "LSOA21CD"}),
                                FILES["census2021_oa_counts.parquet"].rename(columns={"OA21CD": "LSOA21CD"}).assign(
                                    census_poor_english_pct__num=1.0, census_poor_english_pct__den=10.0),
                                OA_WARD)
check("with every key at output area level, no LSOA rows are added", len(plain[0]) == n and plain[2] is OA_WARD)

print("=== the failure path ===")
# The L13 column is renamed out of reach, and TS052 did not download.
STATE["lsoa"] = tables(LSOAS, LSOA_ROWS, "lsoa", drop=[("TS062", NS + "L13 Routine occupations"), ("TS052", None)])
STATE["msoa"] = tables(["E02000900"], [list(range(n))], "msoa", drop=[("TS062", NS + "L13 Routine occupations"), ("TS052", None)])
p.get_lsoa_to_msoa = lambda: {l: "E02000900" for l in LSOAS}
p._SOURCE_PARTIAL.clear()
WARNED.clear()
real_write, real_root = p.write_parquet_guarded, p.REPO_ROOT
p.REPO_ROOT = TMP
p.write_parquet_guarded = lambda path, df, source: df
broken = p.run_census2021()
p.write_parquet_guarded, p.REPO_ROOT = real_write, real_root
check("a routine label that does not match writes no routine figure",
      "census_routine_semi_routine_pct" not in broken.columns and "census_never_worked_ltu_pct" in broken.columns)
check("and no overcrowding figure when TS052 is missing", "census_overcrowded_pct" not in broken.columns)
msg = p._SOURCE_PARTIAL.get("census", "")
check("the census source is marked partial and says what was not built",
      "census_routine_semi_routine_pct" in msg and "TS052: no LSOA file" in msg and "expected 2" in msg, msg)
check("and the run log says so", any("census_routine_semi_routine_pct" in w for w in WARNED))

print("=== census_routine_classes ===")
rec = json.loads((TMP / "demographics" / "census2021_nssec.json").read_text())
check("a level built without the routine figure records no classes", rec.get("lsoa") == "" and rec.get("oa") == "L12-L13", str(rec))
check("so indicators.js does not say the figures were rebuilt", p.census_routine_classes() == "")
STATE["lsoa"] = tables(LSOAS, LSOA_ROWS, "lsoa")
STATE["msoa"] = tables(["E02000900"], [list(range(n))], "msoa")
p._census_frame("lsoa"); p._census_frame("msoa")
check("every level rebuilt since the fix gives L12-L13", p.census_routine_classes() == "L12-L13",
      (TMP / "demographics" / "census2021_nssec.json").read_text())
(TMP / "demographics" / "census2021_age_bands.json").write_text(json.dumps({"lsoa": "0-14", "msoa": "0-14", "oa": "0-14", "x": "0-14"}))
check("a level the age bands record lists but the NS-SEC record does not (built before the fix) gives ''",
      p.census_routine_classes() == "")
(TMP / "demographics" / "census2021_age_bands.json").write_text(json.dumps({"lsoa": "0-14", "msoa": "0-14", "oa": "0-14"}))
stats = p.build_indicator_stats({"wards": {}}, {}, {}, {})
check("build_indicator_stats carries it to indicators.js", stats.get("census_routine_classes") == "L12-L13", str(stats.get("census_routine_classes")))
spec2 = importlib.util.spec_from_file_location("be", REPO / "scripts" / "build_england.py")
BE = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(BE)
check("England takes it only when every region has it",
      BE.ind_flags([{"census_routine_classes": "L12-L13"}, {"census_routine_classes": "L12-L13"}])["census_routine_classes"] == "L12-L13" and
      BE.ind_flags([{"census_routine_classes": "L12-L13"}, {}])["census_routine_classes"] == "")

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
