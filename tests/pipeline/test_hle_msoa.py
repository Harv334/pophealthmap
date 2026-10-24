"""ONS healthy and disability-free life expectancy at birth by MSOA (run_hle_msoa).

"Health state life expectancy estimates for Middle layer Super Output Areas,
England and Wales: 2019 to 2023" (the fact-check note: Census 2021 based, by
sex, with confidence intervals; not comparable with the earlier 2009 to 2013
MSOA estimates). Four figures are read, HLE and DFLE at birth for women and
men, each with its 95% limits at MSOA. Wards are the residents-weighted mean
of their LSOAs, each taking its MSOA's figure, as the Local Health life
expectancy reaches wards. LA figures are ONS's own where the file has LA
rows, and are otherwise left to the map's estimate from wards.

Offline. The note did not see the file's layout, so both of the layouts ONS
uses for health state life expectancies are tested: a long table (Area code,
Area name, Sex, Age group, then LE, HLE and DFLE with their lower and upper
95% confidence limits, as the national HSLE tables are laid out) and a wide
one with the sex in each header. The landing page is the one the note names
(datasets/healthstatelifeexpectancyestimatesformiddlelayersuperoutputareasenglandandwales).
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


def raises(fn, contains=""):
    try:
        fn()
    except RuntimeError as e:
        return contains.lower() in str(e).lower()
    return False


rng = random.Random(5)
MSOAS = [f"E02{6925 + i:06d}" for i in range(24)]
LAS = ["E09000005", "E09000009"]
FIG = {}
for c in MSOAS + LAS + ["W02000010"]:
    for sex in ("Female", "Male"):
        le = rng.uniform(78, 88) if sex == "Female" else rng.uniform(74, 84)
        hle = le - rng.uniform(14, 22)
        dfle = le - rng.uniform(16, 24)
        FIG[(c, sex)] = (round(le, 2), round(hle, 2), round(dfle, 2))


def long_table(codes=MSOAS + LAS + ["W02000010"], ages=("<1", "65-69")):
    rows = []
    for c in codes:
        for sex in ("Female", "Male"):
            for age in ages:
                le, hle, dfle = FIG[(c, sex)]
                if age != ages[0]:
                    le, hle, dfle = le - 65, hle - 60, dfle - 60
                rows.append({"Area code": c, "Area name": c, "Sex": sex, "Age group": age,
                             "Life expectancy (LE)": le, "LE lower 95% confidence limit": le - 1.5,
                             "LE upper 95% confidence limit": le + 1.5,
                             "Healthy life expectancy (HLE)": hle, "HLE lower 95% confidence limit": hle - 3,
                             "HLE upper 95% confidence limit": hle + 3,
                             "Proportion of life spent in good health (%)": round(100 * hle / le, 1),
                             "Disability-free life expectancy (DFLE)": dfle, "DFLE lower 95% confidence limit": dfle - 3.5,
                             "DFLE upper 95% confidence limit": dfle + 3.5,
                             "Proportion of life spent free of disability (%)": round(100 * dfle / le, 1)})
    return pd.DataFrame(rows)


print("=== a long table, by sex and age ===")
msoa, la, problems, matched = p.hle_parse([("Table 1", "Health state life expectancies at birth and age 65, 2019 to 2023",
                                            long_table())])
check("all four figures and their limits are found, with nothing reported",
      all(c in msoa.columns for k in p.HLE_KEYS for c in (k, k + p.CI_LOWER, k + p.CI_UPPER)) and not problems,
      "; ".join(problems))
r = msoa.set_index("MSOA21CD").loc[MSOAS[0]]
check("women's HLE at birth is the HLE column of the Female, under-1 rows",
      r["hle_female"] == FIG[(MSOAS[0], "Female")][1] and r["dfle_male"] == FIG[(MSOAS[0], "Male")][2])
check("life expectancy and the proportions are not taken for HLE or DFLE",
      "Life expectancy" not in matched["hle_female"] and "Proportion" not in matched["dfle_female"], str(matched))
check("the at-65 rows are left out", r["hle_female"] > 40)
check("only English MSOAs are MSOA rows", list(msoa["MSOA21CD"]) == MSOAS)
check("ONS's LA rows are kept as LA figures", sorted(la["LAD25CD"]) == LAS
      and la.set_index("LAD25CD").loc[LAS[0], "hle_male"] == FIG[(LAS[0], "Male")][1], la.to_string()[:300])

print("=== a wide table, the sex in each header ===")
wide = pd.DataFrame({"MSOA code": MSOAS, "MSOA name": MSOAS})
for sex, word in (("Female", "Females"), ("Male", "Males")):
    wide[f"{word}: healthy life expectancy at birth"] = [FIG[(c, sex)][1] for c in MSOAS]
    wide[f"{word}: HLE lower 95% CI"] = [FIG[(c, sex)][1] - 3 for c in MSOAS]
    wide[f"{word}: HLE upper 95% CI"] = [FIG[(c, sex)][1] + 3 for c in MSOAS]
    wide[f"{word}: disability-free life expectancy at birth"] = [FIG[(c, sex)][2] for c in MSOAS]
    wide[f"{word}: DFLE lower 95% CI"] = [FIG[(c, sex)][2] - 3 for c in MSOAS]
    wide[f"{word}: DFLE upper 95% CI"] = [FIG[(c, sex)][2] + 3 for c in MSOAS]
m2, l2, pr2, _ = p.hle_parse([("Data", "", wide)])
check("read the same, with females never taken for males",
      not pr2 and m2.set_index("MSOA21CD").loc[MSOAS[3], "hle_male"] == FIG[(MSOAS[3], "Male")][1]
      and m2.set_index("MSOA21CD").loc[MSOAS[3], "hle_female"] == FIG[(MSOAS[3], "Female")][1], "; ".join(pr2))
check("with no LA rows, no LA figures", l2.empty)

print("=== what is wrong is reported, not guessed ===")
old = long_table().assign(**{"Area code": lambda d: d["Area code"].str.replace(r"^E0200(\d{2})", r"E0200\1", regex=True)})
old["Area code"] = [f"E02{(i % 30) + 1:06d}" if c.startswith("E02") else c for i, c in enumerate(old["Area code"])]
check("a file on the 2011 MSOAs is refused", raises(lambda: p.hle_parse([("T", "", old)]), contains="2011 MSOAs"))
at65 = long_table(ages=("65-69", "70-74"))
check("an age column with no at-birth rows is reported and the table not read",
      raises(lambda: p.hle_parse([("T", "", at65)]), contains="English MSOA codes at birth"))
pct = long_table()
pct["Healthy life expectancy (HLE)"] = pct["Healthy life expectancy (HLE)"] / 100
_, _, pr3, _ = p.hle_parse([("T", "", pct)])
check("a figure that is not years of life is not used", any("hle_female" in x and "not years" in x for x in pr3), "; ".join(pr3))
noci = long_table().drop(columns=["DFLE lower 95% confidence limit", "DFLE upper 95% confidence limit"])
m4, _, pr4, _ = p.hle_parse([("T", "", noci)])
check("without limits the figures are still read, and the missing limits are reported",
      "dfle_female" in m4.columns and "dfle_female_lci" not in m4.columns and any("limits" in x for x in pr4))

print("=== run_hle_msoa, end to end ===")
tmp = pathlib.Path(tempfile.mkdtemp())
p.REPO_ROOT = tmp
p.CACHE_DIR = tmp / "cache"
p.HLE_CACHE = p.CACHE_DIR / "hle_msoa"
p.DATA_DIR = tmp / "data"
p.NATIONAL_DATA_DIR = tmp / "data"
p.CUSTOM_DIR = tmp / "data" / "custom"
(p.DATA_DIR / "outcomes").mkdir(parents=True)
(p.DATA_DIR / "demographics").mkdir(parents=True)
LAND = p.HLE_LANDING[0]
xl = tmp / "hsle_msoa_2019to2023.xlsx"
with pd.ExcelWriter(xl) as xw:
    pd.DataFrame({"Contents": ["Health state life expectancy estimates for MSOAs, England and Wales: 2019 to 2023"]}
                 ).to_excel(xw, sheet_name="Contents", index=False)
    pd.DataFrame([["Table 1: Health state life expectancies, MSOAs, 2019 to 2023"]]).to_excel(
        xw, sheet_name="Table 1", index=False, header=False)
    long_table().to_excel(xw, sheet_name="Table 1", index=False, startrow=2)
PAGES = {LAND + "/data": {"description": {"title": "Health state life expectancy estimates for Middle layer Super Output Areas, England and Wales"},
                          "datasets": [{"uri": LAND + "/2019to2023"}]},
         LAND + "/2019to2023/data": {"description": {"edition": "2019 to 2023", "releaseDate": "2026-03-01"},
                                     "downloads": [{"file": "hsle_msoa_2019to2023.xlsx"}]}}


class Resp:
    def __init__(self, url, body=None, status=200, content=b""):
        self.url, self.body, self.status_code, self.content, self.text = url, body, status, content, ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


class Sess:
    asked = []

    def get(self, url, timeout=60, params=None):
        self.asked.append(url)
        if "/file?uri=" in url:
            return Resp(url, content=xl.read_bytes())
        path = url.replace(p.ONS_BASE, "")
        return Resp(url, body=PAGES[path]) if path in PAGES else Resp(url, status=404)


p.browser_session = lambda referer=None: Sess()
p._SOURCE_ERROR.clear()
log = io.StringIO()
with redirect_stdout(log):
    got = p.run_hle_msoa()
text = log.getvalue()
check("the file is found from the landing page and read",
      all(k in got.columns for k in p.HLE_KEYS) and len(got) == len(MSOAS) and "hle_msoa" not in p._SOURCE_ERROR,
      str(p._SOURCE_ERROR.get("hle_msoa")) + text[-800:])
check("the period is written as the map writes pooled years, and the edition kept",
      set(got["period"]) == {"2019 - 23"} and set(got["edition"]) == {"2019 to 2023"})
check("the log names the edition", "edition '2019 to 2023'" in text, text[-600:])
la_file = p.DATA_DIR / "outcomes" / p.HLE_LA_PARQUET
check("ONS's LA rows are written beside it", la_file.exists() and len(pd.read_parquet(la_file)) == 2)
p._ons_download = lambda *a, **k: None
for f in p.HLE_CACHE.glob("*"):
    f.unlink()
(p.DATA_DIR / "outcomes" / p.HLE_PARQUET).unlink()
check("no file and no parquet is a clear error, not a crash",
      raises(p.run_hle_msoa, contains="health state life expectancy file could not be found"))
p.HLE_CACHE.mkdir(parents=True, exist_ok=True)
(p.HLE_CACHE / "hsle.xlsx").write_bytes(xl.read_bytes())
with redirect_stdout(io.StringIO()):
    p.run_hle_msoa()

print("=== wards by residents, LAs from ONS, the limits at MSOA ===")
L2M = {f"E01{(2 * i + k):06d}": m for i, m in enumerate(MSOAS) for k in (0, 1)}
LSOAS = sorted(L2M)
L2W = {l: f"E05{(i // 3):06d}" for i, l in enumerate(LSOAS)}
L2LAD = {l: LAS[0] if i < 24 else LAS[1] for i, l in enumerate(LSOAS)}
POP = {l: 1200 + 11 * i for i, l in enumerate(LSOAS)}
pd.DataFrame({"LSOA21CD": LSOAS, "census_population": [POP[l] for l in LSOAS]}).to_parquet(
    p.DATA_DIR / "demographics" / "census2021.parquet", index=False)
wards = sorted(set(L2W.values()))
(p.DATA_DIR / "boundaries").mkdir(parents=True, exist_ok=True)
(p.DATA_DIR / "boundaries" / "wards.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"WD25CD": wd, "WD25NM": f"Ward {wd}",
     "LAD25CD": LAS[0], "LAD25NM": "Brent"}} for wd in wards]}), encoding="utf-8")
p._census_ward_inputs = lambda: (None, None, {})
p.get_oa_to_ward = lambda: {}
p.get_lsoa_to_ward = lambda: dict(L2W)
p.get_lsoa_to_msoa = lambda: dict(L2M)
p.get_lsoa_ward_lookup = lambda: {l: (L2W[l], L2LAD[l]) for l in LSOAS}
p._ward_to_lad = lambda: {wd: LAS[0] for wd in wards}
p.SCOPE_LADS = set(LAS)
wd = p.build_ward_data()
w0 = wd["wards"][wards[0]]["indicators"]
l0, l1, l2 = LSOAS[:3]
exp = round((FIG[(MSOAS[0], "Female")][1] * (POP[l0] + POP[l1]) + FIG[(MSOAS[1], "Female")][1] * POP[l2])
            / (POP[l0] + POP[l1] + POP[l2]), 2)
check("a ward across two MSOAs blends their HLE by residents", w0.get("hle_female") == exp, f"{w0.get('hle_female')} vs {exp}")
bd = p.build_borough_data()
check("an LA shows ONS's own LA figure", bd[LAS[0]].get("dfle_male") == FIG[(LAS[0], "Male")][2], str(bd.get(LAS[0])))
md = p.build_msoa_data()
check("an MSOA carries ONS's figure and its limits",
      md[MSOAS[0]]["hle_male"] == FIG[(MSOAS[0], "Male")][1] and md[MSOAS[0]]["hle_male_lci"] == round(FIG[(MSOAS[0], "Male")][1] - 3, 2))
stats = p.build_indicator_stats(wd, {}, md, bd)
check("indicators.js measures them at MSOA, ward and LA with the period, and not their limits",
      set(stats["stats"]["dfle_female"]["lv"]) == {"msoa", "ward", "borough"} and stats["stats"]["dfle_female"]["yr"] == "2019 - 23"
      and "hle_female_lci" not in stats["stats"], str(stats["stats"].get("dfle_female")))

print("=== registered as a source ===")
check("hle_msoa is in SOURCES", p.SOURCES.get("hle_msoa") is p.run_hle_msoa)

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
