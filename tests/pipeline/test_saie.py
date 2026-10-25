"""ONS small area income estimates (run_saie): mean household income by MSOA.

The FYE 2023 edition is on the 2021 MSOAs (the fact-check note) and is found
from the ONS dataset landing page by its edition, never from a remembered
asset URL. Three measures are read, each with its 95% limits: total annual
household income, and net household income before and after housing costs
(equivalised). Wards are the household-weighted mean of their LSOAs, each
LSOA taking its MSOA's figure, since MSOAs do not nest in wards; LAs are the
household-weighted mean of their MSOAs, which do nest in them. The limits
stay at MSOA.

Offline. The workbook is synthetic, laid out as earlier SAIE editions are (a
sheet per measure, title rows above a header of MSOA code, MSOA name, Local
authority code and name, Region code and name, the figure in pounds, Upper
and Lower confidence limit (£) and Confidence interval (£)); the note says the
FYE 2023 sheet names were not checked, so a single combined sheet is tested
too. The ONS pages are answered by a fake session in the shape of ONS's own
<page>/data JSON (a landing page's "datasets", an edition's "downloads").
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


rng = random.Random(23)
# 30 English MSOAs, some with 2021-only codes, and two Welsh ones.
MSOAS = [f"E02{6920 + i:06d}" for i in range(30)]
WALES = ["W02000001", "W02000002"]
TOTAL = {m: round(rng.uniform(35_000, 95_000), -1) for m in MSOAS + WALES}
BHC = {m: round(v * 0.62, -1) for m, v in TOTAL.items()}
AHC = {m: round(v * rng.uniform(0.7, 0.9), -1) for m, v in BHC.items()}


def sheet(measure_head, vals, widen=0.1):
    codes = MSOAS + WALES
    return pd.DataFrame({
        "MSOA code": codes, "MSOA name": [f"Place {i:03d}" for i in range(len(codes))],
        "Local authority code": ["E09000005"] * len(MSOAS) + ["W06000015"] * 2,
        "Local authority name": ["Brent"] * len(MSOAS) + ["Cardiff"] * 2,
        "Region code": ["E12000007"] * len(MSOAS) + ["W92000004"] * 2,
        "Region name": ["London"] * len(MSOAS) + ["Wales"] * 2,
        measure_head: [vals[c] for c in codes],
        "Upper confidence limit (£)": [round(vals[c] * (1 + widen), -1) for c in codes],
        "Lower confidence limit (£)": [round(vals[c] * (1 - widen), -1) for c in codes],
        "Confidence interval (£)": [round(vals[c] * 2 * widen, -1) for c in codes],
    })


SHEETS = {
    "Total annual income": sheet("Total annual income (£)", TOTAL),
    "Net annual income": sheet("Net annual income (£)", {m: v * 0.8 for m, v in TOTAL.items()}),
    "Net income before housing costs": sheet("Net annual income before housing costs (£)", BHC),
    "Net income after housing costs": sheet("Net annual income after housing costs (£)", AHC, widen=0.15),
}


def workbook(path, sheets):
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame({"Contents": ["Income estimates for small areas, England and Wales: financial year ending 2023"]}
                     ).to_excel(xw, sheet_name="Contents", index=False)
        for name, df in sheets.items():
            pd.DataFrame([[name], ["Financial year ending 2023"], ["Source: Office for National Statistics"]]
                         ).to_excel(xw, sheet_name=name[:31], index=False, header=False)
            df.to_excel(xw, sheet_name=name[:31], index=False, startrow=4)


tmp = pathlib.Path(tempfile.mkdtemp())
src = tmp / "saiefy2223download.xlsx"
workbook(src, SHEETS)

print("=== reading the workbook ===")
tables = p.ons_tables(src)
check("every sheet with an area code header is read, below its title rows",
      sorted(t[0] for t in tables) == sorted(SHEETS), str([t[0] for t in tables]))
out, problems, matched = p.saie_parse(tables)
check("all three measures and their limits are found, with nothing reported",
      all(c in out.columns for k in p.SAIE_KEYS for c in (k, k + p.CI_LOWER, k + p.CI_UPPER)) and not problems,
      "; ".join(problems))
r = out.set_index("MSOA21CD").loc[MSOAS[0]]
check("before housing costs is the BHC sheet's figure, and its limits are the lower and upper ones",
      r["saie_net_bhc"] == BHC[MSOAS[0]] and r["saie_net_bhc_lci"] < r["saie_net_bhc"] < r["saie_net_bhc_uci"])
check("net annual income with no housing costs in its name is not taken for any measure",
      not any("Net annual income:" in v or v.startswith("Net annual income: ") for v in matched.values())
      and matched["saie_net_bhc"].startswith("Net income before"), str(matched))
check("the confidence interval's width is not taken for a limit", "Confidence interval" not in str(matched))
check("Welsh MSOAs are left out", not out["MSOA21CD"].str.startswith("W").any() and len(out) == len(MSOAS))

one = pd.DataFrame({"MSOA21CD": MSOAS,
                    "Total annual income (£)": [TOTAL[m] for m in MSOAS],
                    "Total annual income lower 95% limit (£)": [TOTAL[m] * 0.9 for m in MSOAS],
                    "Total annual income upper 95% limit (£)": [TOTAL[m] * 1.1 for m in MSOAS],
                    "Net annual income before housing costs (£)": [BHC[m] for m in MSOAS],
                    "Net annual income before housing costs lower 95% limit (£)": [BHC[m] * 0.9 for m in MSOAS],
                    "Net annual income before housing costs upper 95% limit (£)": [BHC[m] * 1.1 for m in MSOAS],
                    "Net annual income after housing costs (£)": [f"{AHC[m]:,.0f}" for m in MSOAS],
                    "Net annual income after housing costs lower 95% limit (£)": [AHC[m] * 0.85 for m in MSOAS],
                    "Net annual income after housing costs upper 95% limit (£)": [AHC[m] * 1.15 for m in MSOAS]})
out1, prob1, _ = p.saie_parse([("Data", "", one)])
check("one sheet holding every measure is read the same way, and '42,350' is read as a number",
      all(k in out1.columns for k in p.SAIE_KEYS) and not prob1
      and out1.set_index("MSOA21CD").loc[MSOAS[1], "saie_net_ahc"] == AHC[MSOAS[1]], "; ".join(prob1))

print("=== what is wrong is reported, not guessed ===")
old = {k: v.assign(**{"MSOA code": [f"E02{i:06d}" for i in range(1, len(v) + 1)]}) for k, v in SHEETS.items()}
check("a file on the 2011 MSOAs (no code above E02006934) is refused",
      raises(lambda: p.saie_parse([(n, "", d) for n, d in old.items()]), contains="2011 MSOAs"))
weekly = dict(SHEETS)
weekly["Total annual income"] = sheet("Total annual income (£)", {m: v / 52 for m, v in TOTAL.items()})
o2, pr2, _ = p.saie_parse([(n, "", d) for n, d in weekly.items()])
check("weekly figures are not taken for annual ones", "saie_total_income" not in o2.columns
      and any("not pounds a year" in x for x in pr2), "; ".join(pr2))
swapped = dict(SHEETS)
swapped["Net income before housing costs"] = sheet("Net annual income before housing costs (£)", AHC)
swapped["Net income after housing costs"] = sheet("Net annual income after housing costs (£)", BHC)
o3, pr3, _ = p.saie_parse([(n, "", d) for n, d in swapped.items()])
check("after housing costs above before housing costs: neither is used",
      "saie_net_bhc" not in o3.columns and "saie_net_ahc" not in o3.columns and "saie_total_income" in o3.columns
      and any("wrong way round" in x for x in pr3), "; ".join(pr3))
# As the first real refresh found: after housing costs above before in some
# MSOAs (13% there), which the two equivalence scales allow where housing is
# cheap. Both are still used.
cross = dict(AHC)
for m in list(MSOAS)[: max(1, len(MSOAS) // 8)]:
    cross[m] = BHC[m] + 500
mixed = dict(SHEETS)
mixed["Net income after housing costs"] = sheet("Net annual income after housing costs (£)", cross)
o4, pr4, _ = p.saie_parse([(n, "", d) for n, d in mixed.items()])
check("after housing costs above before in a few MSOAs: both are still used",
      "saie_net_bhc" in o4.columns and "saie_net_ahc" in o4.columns
      and not any("wrong way round" in x for x in pr4), "; ".join(pr4))
two = dict(SHEETS)
two["Total income, second table"] = sheet("Total annual income (£)", TOTAL)
o4, pr4, _ = p.saie_parse([(n, "", d) for n, d in two.items()])
check("a measure two columns match is not used", "saie_total_income" not in o4.columns
      and any(x.startswith("saie_total_income: 2 columns") for x in pr4), "; ".join(pr4))
noci = {k: v.drop(columns=["Upper confidence limit (£)", "Lower confidence limit (£)"]) for k, v in SHEETS.items()}
o5, pr5, _ = p.saie_parse([(n, "", d) for n, d in noci.items()])
check("without limits the figures are still read, and the missing limits are reported",
      "saie_net_bhc" in o5.columns and "saie_net_bhc_lci" not in o5.columns and any("limits" in x for x in pr5))
check("no English MSOA codes is a clear error",
      raises(lambda: p.saie_parse([("Wales", "", SHEETS["Total annual income"].iloc[-2:])]), contains="English MSOA"))

print("=== periods and editions ===")
check("financial year ending 2023 is 2022/23", p.ons_period("Financial year ending 2023") == "2022/23")
check("and so is an edition path", p.ons_period("/datasets/x/financialyearending2023") == "2022/23")
check("2019 to 2023 is written as the map writes pooled years", p.ons_period("2019 to 2023") == "2019 - 23")
check("a text with no period gives none", p.ons_period("Notes and definitions") == "")
FILES = [
    {"edition": "Financial year ending 2020", "uri": "/d/x/financialyearending2020", "file": "saiefy1920.xlsx",
     "url": "https://www.ons.gov.uk/file?uri=/d/x/financialyearending2020/saiefy1920.xlsx", "release_date": "2023-09-28"},
    {"edition": "Financial year ending 2023", "uri": "/d/x/financialyearending2023", "file": "saiefy2223.csv",
     "url": "https://www.ons.gov.uk/file?uri=/d/x/financialyearending2023/saiefy2223.csv", "release_date": "2025-12-10"},
    {"edition": "Financial year ending 2023", "uri": "/d/x/financialyearending2023", "file": "saiefy2223.xlsx",
     "url": "https://www.ons.gov.uk/file?uri=/d/x/financialyearending2023/saiefy2223.xlsx", "release_date": "2025-12-10"},
    {"edition": "Financial year ending 2023", "uri": "/d/x/financialyearending2023", "file": "notes.pdf",
     "url": "https://www.ons.gov.uk/file?uri=/d/x/financialyearending2023/notes.pdf", "release_date": "2025-12-10"},
]
pick = p.ons_pick_latest(FILES, (".xlsx", ".xls", ".csv"))
check("the newest edition is taken, as a workbook before a CSV", pick and pick["file"] == "saiefy2223.xlsx", str(pick))
notes_first = [dict(FILES[2], file="saiefy2223technicalnotes.xlsx")] + FILES
check("within an edition, the data before a technical notes workbook",
      p.ons_pick_latest(notes_first, (".xlsx",))["file"] == "saiefy2223.xlsx")
check("nothing in the formats wanted is None, not a guess", p.ons_pick_latest(FILES[3:], (".xlsx",)) is None)

print("=== finding the file from the landing page ===")
LAND = p.SAIE_LANDING[0]
PAGES = {
    LAND + "/data": {"type": "dataset_landing_page",
                     "description": {"title": "Income estimates for small areas, England and Wales"},
                     "datasets": [{"uri": LAND + "/financialyearending2020"}, {"uri": LAND + "/financialyearending2023"}]},
    LAND + "/financialyearending2020/data": {"description": {"edition": "Financial year ending 2020", "releaseDate": "2023-09-28"},
                                             "downloads": [{"file": "saiefy1920finalqaddownload280923.xlsx"}]},
    LAND + "/financialyearending2023/data": {"description": {"edition": "Financial year ending 2023", "releaseDate": "2025-12-10"},
                                             "downloads": [{"file": "saiefy2223download.xlsx"}]},
}


class Resp:
    def __init__(self, url, body=None, text="", status=200, content=b""):
        self.url, self.body, self.text, self.status_code, self.content = url, body, text, status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code} for {self.url}")

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


class Sess:
    def __init__(self, pages, html=None, files=None):
        self.pages, self.html, self.files, self.asked = pages, html or {}, files or {}, []

    def get(self, url, timeout=60, params=None):
        path = url.replace(p.ONS_BASE, "").split("?")[0] if url.startswith(p.ONS_BASE) else url
        self.asked.append(url + ("?" + json.dumps(params) if params else ""))
        if url.startswith(p.ONS_BASE + "/file?uri="):
            return Resp(url, content=self.files.get(url.split("uri=", 1)[1], b""), status=200 if url.split("uri=", 1)[1] in self.files else 404)
        if url == p.ONS_SEARCH_API:
            return Resp(url, body=self.pages.get("search", {"items": []}))
        if path in self.pages:
            return Resp(url, body=self.pages[path])
        if path in self.html:
            return Resp(url, text=self.html[path])
        return Resp(url, status=404)


sess = Sess(PAGES)
p.browser_session = lambda referer=None: sess
files = p.ons_dataset_files(p.SAIE_LANDING, p.SAIE_BULLETINS, p.SAIE_SEARCH, p.SAIE_TITLE_WORDS)
best = p.ons_pick_latest(files, (".xlsx", ".csv"))
check("the landing page's editions and their files are found",
      len(files) == 2 and best["file"] == "saiefy2223download.xlsx"
      and best["url"] == f"{p.ONS_BASE}/file?uri={LAND}/financialyearending2023/saiefy2223download.xlsx", str(files))
html_only = Sess({}, html={LAND: f'<a href="/file?uri={LAND}/financialyearending2023/saiefy2223download.xlsx">xlsx (3 MB)</a>'})
p.browser_session = lambda referer=None: html_only
files_h = p.ons_dataset_files(p.SAIE_LANDING)
check("without the JSON, the landing page's own links are read",
      len(files_h) == 1 and files_h[0]["edition"] == "financialyearending2023", str(files_h))
OTHER = "/peoplepopulationandcommunity/somewhere/datasets/incomeestimatesforsmallareasenglandandwales"
moved = Sess({p.SAIE_BULLETINS[0] + "/data": {"relatedData": [{"uri": "/some/datasets/householdincomebydecile"},
                                                               {"uri": OTHER}]},
              "/some/datasets/householdincomebydecile/data": {"description": {"title": "Household income by decile"},
                                                              "datasets": [{"uri": "/some/datasets/householdincomebydecile/2023"}]},
              OTHER + "/data": {"description": {"title": "Income estimates for small areas, England and Wales"},
                                "datasets": [{"uri": OTHER + "/financialyearending2023"}]},
              OTHER + "/financialyearending2023/data": {"description": {"edition": "Financial year ending 2023"},
                                                        "downloads": [{"file": "saie.xlsx"}]}})
p.browser_session = lambda referer=None: moved
files_b = p.ons_dataset_files(p.SAIE_LANDING, p.SAIE_BULLETINS, "", p.SAIE_TITLE_WORDS)
check("a moved landing page is found from the bulletin's related datasets, by its title",
      len(files_b) == 1 and files_b[0]["uri"] == OTHER + "/financialyearending2023", str(files_b))
page_only = Sess({OTHER + "/data": moved.pages[OTHER + "/data"],
                  OTHER + "/financialyearending2023/data": moved.pages[OTHER + "/financialyearending2023/data"]},
                 html={p.SAIE_BULLETINS[0]: f'<p>Related data</p><a href="https://www.ons.gov.uk{OTHER}">Income estimates</a>'})
p.browser_session = lambda referer=None: page_only
files_p = p.ons_dataset_files(p.SAIE_LANDING, p.SAIE_BULLETINS, "", p.SAIE_TITLE_WORDS)
check("and from the bulletin page's own links when its JSON does not answer", len(files_p) == 1, str(page_only.asked))
searched = Sess({"search": {"items": [{"uri": OTHER, "title": "Income estimates for small areas"}]},
                 OTHER + "/data": moved.pages[OTHER + "/data"],
                 OTHER + "/financialyearending2023/data": moved.pages[OTHER + "/financialyearending2023/data"]})
p.browser_session = lambda referer=None: searched
files_s = p.ons_dataset_files(p.SAIE_LANDING, (), p.SAIE_SEARCH, p.SAIE_TITLE_WORDS)
check("and from ONS's search when the bulletin does not answer", len(files_s) == 1, str(searched.asked))
p.browser_session = lambda referer=None: Sess({})
check("nothing found is an empty list", p.ons_dataset_files(p.SAIE_LANDING, p.SAIE_BULLETINS, p.SAIE_SEARCH,
                                                            p.SAIE_TITLE_WORDS) == [])

print("=== run_saie, end to end ===")
p.REPO_ROOT = tmp
p.CACHE_DIR = tmp / "cache"
p.SAIE_CACHE = p.CACHE_DIR / "saie"
p.DATA_DIR = tmp / "data"
p.NATIONAL_DATA_DIR = tmp / "data"
p.CUSTOM_DIR = tmp / "data" / "custom"
demo = p.DATA_DIR / "demographics"
demo.mkdir(parents=True)
blob = src.read_bytes()
sess = Sess(PAGES, files={f"{LAND}/financialyearending2023/saiefy2223download.xlsx": blob})
p.browser_session = lambda referer=None: sess
p._SOURCE_ERROR.clear()
log = io.StringIO()
with redirect_stdout(log):
    got = p.run_saie()
text = log.getvalue()
check("the FYE 2023 file is found on the landing page and downloaded",
      any("financialyearending2023/saiefy2223download.xlsx" in u for u in sess.asked) and
      not any("financialyearending2020/saie" in u and "file?uri" in u for u in sess.asked), "\n".join(sess.asked))
check("the parquet holds every measure and limit for the English MSOAs, with the period 2022/23",
      (demo / p.SAIE_PARQUET).exists() and all(k in got.columns for k in p.SAIE_KEYS)
      and set(got["period"]) == {"2022/23"} and len(got) == len(MSOAS) and "saie" not in p._SOURCE_ERROR,
      str(p._SOURCE_ERROR.get("saie")))
check("the log names the edition and every column it read",
      "newest edition is 'Financial year ending 2023'" in text and "saie_net_ahc <- " in text, text[-1200:])
import zipfile as _zf
zbuf = io.BytesIO()
with _zf.ZipFile(zbuf, "w") as z:
    z.writestr("readme_notes.xlsx", b"x" * 20_000)
    z.writestr("saie/saiefy2223.xlsx", blob)
zsess = Sess({}, files={"/d/x/fye2023/saie.zip": zbuf.getvalue()})
p.browser_session = lambda referer=None: zsess
zdir = tmp / "zipcache"
got_z = p._ons_download({"url": f"{p.ONS_BASE}/file?uri=/d/x/fye2023/saie.zip", "file": "saie.zip",
                         "edition": "Financial year ending 2023"}, zdir, "saie")
check("a zipped release gives its spreadsheet, not its notes", got_z is not None and got_z.name == "saiefy2223.xlsx"
      and p._ons_release(got_z).get("edition") == "Financial year ending 2023", str(got_z))
p.browser_session = lambda referer=None: sess
p._ons_download = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not download"))
for f in p.SAIE_CACHE.glob("*"):
    f.unlink()
try:
    with redirect_stdout(io.StringIO()):
        again = p.run_saie()
    check("a complete committed parquet is used with no download", len(again) == len(MSOAS))
except AssertionError as e:
    check("a complete committed parquet is used with no download", False, str(e))
p._ons_download = lambda *a, **k: None
pd.read_parquet(demo / p.SAIE_PARQUET).drop(columns=["saie_net_ahc"]).to_parquet(demo / p.SAIE_PARQUET, index=False)
p._SOURCE_ERROR.clear()
with redirect_stdout(io.StringIO()):
    kept = p.run_saie()
msg = p._SOURCE_ERROR.get("saie", "")
check("a file that cannot be found keeps the committed figures and fails the source, saying what to do",
      len(kept) == len(MSOAS) and "could not be found or downloaded" in msg and "--only saie" in msg, msg)
(demo / p.SAIE_PARQUET).unlink()
check("with no parquet either, it is a clear error", raises(p.run_saie, contains="small area income estimates"))
p.SAIE_CACHE.mkdir(parents=True, exist_ok=True)
workbook(p.SAIE_CACHE / "saie_partial.xlsx", {k: v for k, v in SHEETS.items() if "after" not in k})
p._SOURCE_ERROR.clear()
with redirect_stdout(io.StringIO()):
    part = p.run_saie()
check("a measure missing from the file is a source failure that still writes the rest",
      "saie_net_ahc" not in part.columns and "saie_total_income" in part.columns
      and "saie_net_ahc" in p._SOURCE_ERROR.get("saie", ""), p._SOURCE_ERROR.get("saie", ""))
(p.SAIE_CACHE / "saie_partial.xlsx").unlink()
workbook(p.SAIE_CACHE / "saie.xlsx", SHEETS)
p._SOURCE_ERROR.clear()
with redirect_stdout(io.StringIO()):
    full = p.run_saie()

print("=== wards and LAs weighted by households, the limits at MSOA ===")
# Two LSOAs per MSOA; wards are made of LSOAs 0 to 2, 3 to 5 and so on, so
# they cross MSOA edges.
L2M = {f"E01{(2 * i + k):06d}": m for i, m in enumerate(MSOAS) for k in (0, 1)}
LSOAS = sorted(L2M)
L2W = {l: f"E05{(i // 3):06d}" for i, l in enumerate(LSOAS)}
L2LAD = {l: "E09000005" if i < 30 else "E09000009" for i, l in enumerate(LSOAS)}
HH = {l: 400 + 37 * i for i, l in enumerate(LSOAS)}
POP = {l: 1000 + 5 * i for i, l in enumerate(LSOAS)}
pd.DataFrame({"LSOA21CD": LSOAS, "census_population": [POP[l] for l in LSOAS]}).to_parquet(demo / "census2021.parquet", index=False)
pd.DataFrame({"LSOA21CD": LSOAS, "census_no_car_pct__num": [10] * len(LSOAS),
              "census_no_car_pct__den": [HH[l] for l in LSOAS]}).to_parquet(demo / "census2021_counts.parquet", index=False)
wards = sorted(set(L2W.values()))
(p.DATA_DIR / "boundaries").mkdir(parents=True, exist_ok=True)
(p.DATA_DIR / "boundaries" / "wards.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"WD25CD": wd, "WD25NM": f"Ward {wd}",
     "LAD25CD": "E09000005", "LAD25NM": "Brent"}} for wd in wards]}), encoding="utf-8")
p._census_ward_inputs = lambda: (None, None, {})
p.get_oa_to_ward = lambda: {}
p.get_lsoa_to_ward = lambda: dict(L2W)
p.get_lsoa_to_msoa = lambda: dict(L2M)
p.get_lsoa_ward_lookup = lambda: {l: (L2W[l], L2LAD[l]) for l in LSOAS}
p._ward_to_lad = lambda: {wd: "E09000005" for wd in wards}
p.SCOPE_LADS = {"E09000005", "E09000009"}
wd = p.build_ward_data()
w0 = wd["wards"][wards[0]]["indicators"]
l0, l1, l2 = LSOAS[:3]
exp = round((BHC[MSOAS[0]] * (HH[l0] + HH[l1]) + BHC[MSOAS[1]] * HH[l2]) / (HH[l0] + HH[l1] + HH[l2]))
check("a ward across two MSOAs is their household-weighted blend, in whole pounds",
      w0.get("saie_net_bhc") == exp and isinstance(w0.get("saie_net_bhc"), int), f"{w0.get('saie_net_bhc')} vs {exp}")
check("no ward carries the limits", not any(p.is_interval_key(k) for w in wd["wards"].values() for k in w["indicators"]))
check("the ward file says the weight was households", wd["metadata"].get("saie_weight") == "households")
bd = p.build_borough_data()
la_ls = [l for l in LSOAS if L2LAD[l] == "E09000005"]
exp_la = round(sum(BHC[L2M[l]] * HH[l] for l in la_ls) / sum(HH[l] for l in la_ls))
check("an LA is the household-weighted mean of its MSOAs", bd["E09000005"].get("saie_net_bhc") == exp_la,
      f"{bd['E09000005'].get('saie_net_bhc')} vs {exp_la}")
md = p.build_msoa_data()
check("an MSOA carries ONS's own figure and its 95% limits",
      md[MSOAS[0]]["saie_net_bhc"] == BHC[MSOAS[0]] and md[MSOAS[0]]["saie_net_bhc_lci"] < BHC[MSOAS[0]]
      < md[MSOAS[0]]["saie_net_bhc_uci"], str(md.get(MSOAS[0])))
p.get_lsoa_to_msoa = lambda: {l: m for l, m in L2M.items() if m in MSOAS[:10]}
check("only the scope's MSOAs are written", len(p.build_msoa_data()) == 10)
p.get_lsoa_to_msoa = lambda: dict(L2M)
stats = p.build_indicator_stats(wd, {}, md, bd)
check("indicators.js measures each measure at MSOA, ward and LA, with its period, and not its limits",
      set(stats["stats"]["saie_net_ahc"]["lv"]) == {"msoa", "ward", "borough"}
      and stats["stats"]["saie_net_ahc"].get("yr") == "2022/23"
      and not any(p.is_interval_key(k) for k in stats["stats"]), str(stats["stats"].get("saie_net_ahc")))
(demo / "census2021_counts.parquet").unlink()
wd2 = p.build_ward_data()
check("without household counts the wards are weighted by residents, and the file says so",
      wd2["metadata"].get("saie_weight") == "residents" and wd2["wards"][wards[0]]["indicators"].get("saie_net_bhc"))

print("=== registered as a source ===")
check("saie is in SOURCES", p.SOURCES.get("saie") is p.run_saie)

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
