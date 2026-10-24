"""The small-area additions: distance to the nearest GP practice, pharmacy and
NHS dentist (service_access), police.uk crime by LSOA, ONS mid-year
population estimates (sape), claimants aged 18 to 24 (claimant_youth), road
casualties killed or seriously injured (road_casualties) and care home beds
(care_homes).

Offline. Where the publisher's own layout is known the synthetic files copy
it, and the comment beside each says where it comes from:

  ONS SAPE     archive/patch_population_mid2024.py, which read the November
               2025 edition: sheet "Mid-2024 LSOA 2021", a header row with
               "LSOA 2021 Code" and "Total", then F0..F90 and M0..M90.
  Nomis        the CSV columns run_claimant_count already reads
               (GEOGRAPHY_CODE, DATE, OBS_VALUE), plus AGE_NAME, and the
               SDMX-JSON codelist shape Nomis serves for a dimension.
  DfT STATS19  the casualty file's column names as DfT's open data guide
               and the rOpenSci stats19 package give them (collision_year,
               casualty_severity 1 fatal 2 serious 3 slight,
               casualty_adjusted_severity_serious, lsoa_of_casualty).
  CQC          the HSCA Active Locations headings quoted in the fact-check
               note ("Location ID", "Care home?", "Care homes beds",
               "Dormant (Y/N)", "Location Postal Code"), which the note says
               must be checked on the first real fetch.

Also measures the distances for real from the London point files on disk
(data/healthcare/*.parquet and data/boundaries/lsoa.geojson) when they are
there, and checks the figures are what London should look like.

Covers the parsing, the maths (numerators, bases, the 2011 to 2021 sharing,
ward and LA figures) and the failure paths: a missing file, a missing column
or a band the publisher does not publish is reported in the manifest rather
than crashing the run or writing wrong numbers.
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import zipfile

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


def fresh_dirs():
    tmp = pathlib.Path(tempfile.mkdtemp())
    p.REPO_ROOT = tmp
    p.CACHE_DIR = tmp / "cache"
    p.DATA_DIR = tmp / "data"
    p.NATIONAL_DATA_DIR = tmp / "data"
    p.CUSTOM_DIR = tmp / "data" / "custom"
    for d in ("boundaries", "healthcare", "demographics", "economy", "environment", "crime"):
        (tmp / "data" / d).mkdir(parents=True, exist_ok=True)
    for k in ("service_access", "sape", "claimant_youth", "road_casualties", "care_homes"):
        p._SOURCE_ERROR.pop(k, None)
        p._SOURCE_PARTIAL.pop(k, None)
        p._SOURCE_NOTE.pop(k, None)
    return tmp


# A small scope: three LSOAs in two wards of one LA, and one LSOA of 2021
# drawn from a 2011 LSOA that was split.
LSOAS = ["E01000001", "E01000002", "E01000003", "E01035001"]
L2W = {"E01000001": "W1", "E01000002": "W1", "E01000003": "W2", "E01035001": "W2"}
LOOKUP = {l: (w, "E09000001") for l, w in L2W.items()}
POP = {"E01000001": 1500, "E01000002": 1000, "E01000003": 2000, "E01035001": 500}


def patch_lookups():
    p.get_lsoa_ward_lookup = lambda: dict(LOOKUP)
    p.get_lsoa_to_ward = lambda: dict(L2W)
    p._census_ward_inputs = lambda: (None, None, {})
    p.get_oa_to_ward = lambda: {}
    p._ward_to_lad = lambda: {"W1": "E09000001", "W2": "E09000001"}


patch_lookups()

# ---------------------------------------------------------------------------
print("=== service_access: finding the centroids and measuring ===")
check("the newest LSOA 2021 population-weighted centroid layer is picked from the ONS list",
      p.pick_lsoa_pwc_service(["LSOA_PopCentroids_EW_2011_V2", "LSOA_PopCentroids_EW_2021_V3",
                               "LSOA_PopCentroids_EW_2021_V4", "OA_PopCentroids_EW_2021_V1x",
                               "LSOA21_WD25_LAD25_EW_LU_v2"]) == "LSOA_PopCentroids_EW_2021_V4")
check("no centroid layer in the list is None, not a guess",
      p.pick_lsoa_pwc_service(["LSOA21_WD25_LAD25_EW_LU_v2", "Wards_May_2025"]) is None)
orig = {"A": (530000.0, 180000.0), "B": (531000.0, 180000.0)}
d = p.service_distances(orig, {"gp_dist_km": [(530300.0, 180400.0), (540000.0, 180000.0)],
                               "pharmacy_dist_km": []})
check("the distance is straight-line kilometres to the nearest site (0.5 km)",
      d["A"]["gp_dist_km"] == 0.5 and d["B"]["gp_dist_km"] == round(((700 ** 2 + 400 ** 2) ** 0.5) / 1000, 3),
      str(d))
check("a kind with no sites gives no figure", "pharmacy_dist_km" not in d["A"])

print("=== service_access: national sites from the register fetches, else the scope's own ===")
tmp = fresh_dirs()
# Two GP practices: one in scope, one just over the edge, which is nearer.
pd.DataFrame({"code": ["G1"], "lat": [51.5], "lng": [-0.10], "kind": [""]}).to_parquet(
    tmp / "data/healthcare/gp_practices.parquet", index=False)
pd.DataFrame({"code": ["P1"], "lat": [51.5], "lng": [-0.10]}).to_parquet(
    tmp / "data/healthcare/pharmacies.parquet", index=False)
pd.DataFrame({"code": ["D1", "D2"], "lat": [51.5, 51.5001], "lng": [-0.10, -0.1201],
              "kind": ["", "Orthodontics only, by referral"]}).to_parquet(
    tmp / "data/healthcare/dental_practices.parquet", index=False)
pts, where = p._service_sites("gp")
check("without a national file the scope's parquet is used, and says so", where == "scope" and len(pts) == 1)
pts_d, _ = p._service_sites("dentist")
check("referral-only dental services are not counted as a dentist", len(pts_d) == 1, str(pts_d))
p.save_national_sites("gp", [("G1", 51.5, -0.10), ("G2", 51.5, -0.12), ("G2", 51.5, -0.12), ("G3", None, None)])
pts, where = p._service_sites("gp")
check("the national file from the register fetch is used, one row per practice",
      where == "england" and len(pts) == 2, f"{where} {pts}")
p.save_national_sites("pharmacy", [])
check("an empty register writes no national file", not (tmp / "cache/service_access/pharmacy_sites.csv").exists())

print("=== service_access: the source end to end ===")
# Square LSOAs about 1.4 km across, west of the sites.
def square(lng0, lat0, s=0.02):
    return {"type": "Polygon", "coordinates": [[[lng0, lat0], [lng0 + s, lat0], [lng0 + s, lat0 + s],
                                                [lng0, lat0 + s], [lng0, lat0]]]}
(tmp / "data/boundaries/lsoa.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"LSOA21CD": c, "LSOA21NM": c}, "geometry": square(-0.20 + 0.02 * i, 51.49)}
    for i, c in enumerate(LSOAS)]}), encoding="utf-8")
p._lsoa_pwc_service = lambda: None
p.save_national_sites("pharmacy", [("P1", 51.5, -0.10)])
p.save_national_sites("dentist", [("D1", 51.5, -0.10)])
out = p.run_service_access()
check("every LSOA in scope gets the three distances",
      len(out) == 4 and all(k in out.columns and out[k].notna().all() for k in p.SERVICE_KEYS), str(out))
check("measured from the middle of each LSOA when ONS has no centroids, and recorded",
      set(out["origin"]) == {"centroid"})
check("the national files from the register fetches are used, so the source is not partial",
      "service_access" not in p._SOURCE_PARTIAL and "service_access" not in p._SOURCE_ERROR,
      p._SOURCE_PARTIAL.get("service_access", ""))
p._SOURCE_PARTIAL.clear()
(tmp / "cache/service_access/gp_sites.csv").unlink()
scope_only = p.run_service_access()
check("the nearer GP practice over the scope's edge counts (G2, nearer than the scope's own G1)",
      out["gp_dist_km"].iloc[0] < scope_only["gp_dist_km"].iloc[0] - 1,
      f'{out["gp_dist_km"].iloc[0]} against {scope_only["gp_dist_km"].iloc[0]}')
check("without a national file the source is partial, naming the edge problem",
      "near the edge" in p._SOURCE_PARTIAL.get("service_access", "")
      and "the gp distances" in p._SOURCE_PARTIAL.get("service_access", ""), p._SOURCE_PARTIAL.get("service_access", ""))
for f in ("gp_practices", "pharmacies", "dental_practices"):
    (tmp / f"data/healthcare/{f}.parquet").unlink()
for f in (tmp / "cache/service_access").iterdir():
    f.unlink()
check("no sites at all is a clear error", raises(p.run_service_access, contains="no GP, pharmacy or dental sites"))

# Population-weighted centroids from ONS, as the feature service answers.
p.get_lsoa_pwc = lambda codes: {c: (525000.0 + 1000 * i, 179000.0) for i, c in enumerate(sorted(codes))}
pd.DataFrame({"code": ["G1"], "lat": [51.5], "lng": [-0.10]}).to_parquet(tmp / "data/healthcare/gp_practices.parquet", index=False)
out2 = p.run_service_access()
check("ONS population-weighted centroids are used where ONS has them",
      set(out2["origin"]) == {"pwc"} and out2["gp_dist_km"].notna().all())
check("a register with no sites at all marks the source failed, naming it",
      "pharmacy" in p._SOURCE_ERROR.get("service_access", "") and "dentist" in p._SOURCE_ERROR.get("service_access", ""),
      p._SOURCE_ERROR.get("service_access", ""))

# ---------------------------------------------------------------------------
print("=== service_access: for real, from London's point files on disk ===")
real = all((REPO / f"data/healthcare/{f}.parquet").exists() for f in ("gp_practices", "pharmacies", "dental_practices")) \
    and (REPO / "data/boundaries/lsoa.geojson").exists()
if real:
    import shutil
    tmpr = fresh_dirs()
    shutil.copy(REPO / "data/boundaries/lsoa.geojson", tmpr / "data/boundaries/lsoa.geojson")
    for f in ("gp_practices", "pharmacies", "dental_practices"):
        shutil.copy(REPO / f"data/healthcare/{f}.parquet", tmpr / f"data/healthcare/{f}.parquet")
    codes = [f["properties"]["LSOA21CD"] for f in
             json.loads((REPO / "data/boundaries/lsoa.geojson").read_text(encoding="utf-8"))["features"]]
    saved = p.get_lsoa_ward_lookup, p.get_lsoa_pwc
    p.get_lsoa_ward_lookup = lambda: {c: ("W", "E09000001") for c in codes}
    p.get_lsoa_pwc = lambda c: {}
    lon = p.run_service_access()
    p.get_lsoa_ward_lookup, p.get_lsoa_pwc = saved
    med = {k: round(float(lon[k].median()), 2) for k in p.SERVICE_KEYS}
    print(f"          London medians: {med}; 95th percentiles: "
          f"{ {k: round(float(lon[k].quantile(0.95)), 2) for k in p.SERVICE_KEYS} }")
    check("London: every LSOA has a distance to each service", len(lon) == len(codes) and lon[list(p.SERVICE_KEYS)].notna().all().all())
    check("London: half its LSOAs are within 0.3 to 0.7 km of each service, none more than 4 km",
          all(0.3 <= v <= 0.7 for v in med.values()) and float(lon[list(p.SERVICE_KEYS)].max().max()) < 4, str(med))
else:
    print("  (skipped: the London point files are not on disk)")
patch_lookups()

# ---------------------------------------------------------------------------
print("=== crime by LSOA, from the police.uk file ===")
crime = pd.DataFrame({
    "category": ["violent-crime", "violent-crime", "shoplifting", "bicycle-theft", "anti-social-behaviour",
                 "burglary", "anti-social-behaviour", "robbery"],
    "LSOA21CD": ["E01000001", "E01000001", "E01000001", "E01000002", "E01000002", "E01000002", "E01000003", ""],
    "WD25CD": ["W1", "W1", "W1", "W1", "W1", "W1", "W2", "W2"],
})
cl = p.crime_by_lsoa(crime, POP)
check("the total leaves anti-social behaviour out (3 crimes in E01000001, 2 in E01000002)",
      cl["E01000001"]["crime_total"] == 3 and cl["E01000002"]["crime_total"] == 2, str(cl))
check("theft is police.uk's four theft categories together, as the ward key is",
      cl["E01000001"]["crime_theft_12mo"] == 1 and cl["E01000002"]["crime_theft_12mo"] == 1)
check("anti-social behaviour has its own figure", cl["E01000002"]["crime_asb_12mo"] == 1)
check("an LSOA with only anti-social behaviour on record has 0 crimes, not no figure",
      cl["E01000003"]["crime_total"] == 0 and cl["E01000003"]["crime_rate_per_1000"] == 0.0)
check("the rate is per 1,000 residents at the 2021 Census, to one place (3 / 1,500 = 2.0)",
      cl["E01000001"]["crime_rate_per_1000"] == 2.0)
check("a crime with no LSOA is not given one", "" not in cl and len(cl) == 3)
check("a category with no crimes in an LSOA reads 0", cl["E01000001"]["crime_burglary_12mo"] == 0)

# ---------------------------------------------------------------------------
print("=== sape: finding the newest single year of age file ===")
U = "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/populationandmigration/populationestimates/datasets/lowersuperoutputareamidyearpopulationestimates"
links = [f"{U}/mid2021andmid2022/sapelsoasyoatablefinal.xlsx",
         f"{U}/mid2022revisednov2025tomid2024/sapelsoasyoa20222024.xlsx",
         f"{U}/mid2022revisednov2025tomid2024/sapelsoabroadage20222024.xlsx",
         f"{U}/mid2022revisednov2025tomid2024/sapemsoasyoa20222024.xlsx"]
check("the newest LSOA single-year file is picked, not the broad ages or the MSOA one",
      p.sape_pick(links).endswith("sapelsoasyoa20222024.xlsx"), str(p.sape_pick(links)))
check("no single-year LSOA file is None", p.sape_pick(links[2:]) is None)

print("=== sape: reading the sheet ===")
AGES = list(range(0, 91))


def sape_rows(codes, pops, *, misread=False):
    head = ["LAD 2023 Code", "LAD 2023 Name", "LSOA 2021 Code", "LSOA 2021 Name", "Total"] + \
        [f"F{a}" for a in AGES] + [f"M{a}" for a in AGES]
    rows = [["Mid-2024 population estimates for Lower layer Super Output Areas in England and Wales"],
            ["Source: Office for National Statistics"], [], head]
    for c, n in zip(codes, pops):
        f = [0] * 91
        m = [0] * 91
        # 10 at each of 0 to 9, 3 at 18 to 24 for each sex, 5 at 75 and 2 at 90+, the rest at 40.
        for a in range(10):
            f[a] = m[a] = 10
        for a in range(18, 25):
            f[a] = m[a] = 3
        f[75] = m[75] = 5
        f[90] = m[90] = 2
        rest = n - sum(f) - sum(m)
        f[40] += rest // 2
        m[40] += rest - rest // 2
        tot = n + (7 if misread else 0)
        rows.append(["E09000001", "City of London", c, c + " name", tot] + f + m)
    rows.append(["W01000001", "Wales", "W01000001", "a Welsh LSOA", 1000] + [0] * 182)
    return rows


SROWS = sape_rows(LSOAS, [POP[c] for c in LSOAS])
ages, how = p.sape_age_columns(SROWS[3])
check("F0 to F90 and M0 to M90 are found as single years by sex", how == "sex" and sorted(ages) == AGES)
check("a set with an age missing is not taken", p.sape_age_columns(SROWS[3][:60])[0] == {})
sape, problem = p.sape_parse_table(SROWS)
r1 = sape.set_index("LSOA21CD").loc["E01000001"] if not problem else None
check("the sheet is read below its title rows, English LSOAs only", not problem and len(sape) == 4, problem)
check("the bands are counted from the single years",
      r1 is not None and r1["sape_population"] == 1500 and r1["sape_age0_15"] == 200
      and r1["sape_age18_24"] == 42 and r1["sape_age75plus"] == 14 and r1["sape_age65plus"] == 14
      and r1["sape_age16_64"] == 1500 - 200 - 14, str(r1))
_, prob2 = p.sape_parse_table(sape_rows(LSOAS, [POP[c] for c in LSOAS], misread=True))
check("single years that do not add up to the total are refused as a misread", "do not add up" in prob2, prob2)
_, prob3 = p.sape_parse_table([["Notes"], ["nothing here"]])
check("a sheet without the header is reported, not guessed", "no header row" in prob3, prob3)

print("=== sape: the workbook and the source ===")
tmp = fresh_dirs()
patch_lookups()
(tmp / "cache/sape").mkdir(parents=True)
xl = tmp / "cache/sape/sapelsoasyoa20222024.xlsx"
import openpyxl
wb = openpyxl.Workbook()
wb.active.title = "Contents"
wb.active.append(["Population estimates by single year of age"])
for title, scale in (("Mid-2023 LSOA 2021", 1), ("Mid-2024 LSOA 2021", 2)):
    ws = wb.create_sheet(title)
    for row in sape_rows(LSOAS, [POP[c] * scale for c in LSOAS]):
        ws.append(row)
wb.save(xl)
frame, year, prob = p.sape_read_file(xl, set(LSOAS))
check("the newest year's sheet is read (mid-2024)", year == 2024 and not prob
      and int(frame.set_index("LSOA21CD").loc["E01000001", "sape_population"]) == 3000, f"{year} {prob}")
p._ons_page_files = lambda page, sess: []
got = p.run_sape()
check("run_sape reads a file put in .cache/sape by hand when the ONS pages list none",
      len(got) == 4 and int(got["sape_year"].iloc[0]) == 2024 and "sape" not in p._SOURCE_ERROR,
      p._SOURCE_ERROR.get("sape", ""))
xl.unlink()
kept = p.run_sape()
check("with no file, the figures written last time are kept and the source marked failed with what to do",
      len(kept) == 4 and "--only sape" in p._SOURCE_ERROR.get("sape", ""), p._SOURCE_ERROR.get("sape", ""))
(tmp / "data/demographics" / p.SAPE_PARQUET).unlink()
check("no file and nothing written before is a clear error", raises(p.run_sape, contains="no LSOA population estimates file"))

# ---------------------------------------------------------------------------
print("=== claimant_youth: the 18 to 24 band, found by name ===")
CODELIST = {"structure": {"codelists": {"codelist": [{"id": "CL_162_1_AGE", "code": [
    {"value": 0, "description": {"value": "All categories: Age 16+", "lang": "en"}},
    {"value": 1, "description": {"value": "Aged 16-17", "lang": "en"}},
    {"value": 2, "description": {"value": "Aged 18-24", "lang": "en"}},
    {"value": 3, "description": {"value": "Aged 25-49", "lang": "en"}},
    {"value": 4, "description": {"value": "Aged 50+", "lang": "en"}},
]}]}}}
codes = p.nomis_codelist(CODELIST)
check("the codelist is read from Nomis's SDMX-JSON", codes.get("2") == "Aged 18-24" and len(codes) == 5, str(codes))
check("the 18 to 24 code is found by its name", p.claimant_youth_code(codes) == "2")
check("a codelist without the band gives no code", p.claimant_youth_code({"0": "All", "3": "Aged 25-49"}) is None)
CSV = pd.DataFrame({
    "DATE": ["2026-07", "2026-08", "2026-08", "2026-08", "2026-08"],
    "GEOGRAPHY_CODE": ["E01000001", "E01000001", "E01000002", "E01000003", "E01035001"],
    "AGE_NAME": ["Aged 18-24"] * 5,
    "OBS_VALUE": ["5", "10", "5", "0", ""],
})
cy, prob = p.claimant_youth_parse(CSV)
check("the latest month only, one count per LSOA, a blank count left out",
      not prob and len(cy) == 3 and cy.set_index("LSOA21CD").loc["E01000001", "claimant_count_18_24"] == 10
      and set(cy["claimant_18_24_month"]) == {"2026-08"}, str(cy))
_, prob = p.claimant_youth_parse(CSV.assign(AGE_NAME="All categories: Age 16+"))
check("rows for another age are refused, naming what came back", "no rows for ages 18 to 24" in prob, prob)

print("=== claimant_youth: the source, and what Nomis may not publish ===")
tmp = fresh_dirs()
patch_lookups()


class Resp:
    def __init__(self, status, body):
        self.status_code, self.content, self.text = status, body.encode(), body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


body = "DATE,GEOGRAPHY_CODE,AGE_NAME,OBS_VALUE\n" + "\n".join(
    f"2026-08,{c},Aged 18-24,{v}" for c, v in zip(LSOAS, [10, 5, 0, 5])) + "\n"
asked = []
p._http_json = lambda method, url, **k: CODELIST
p.requests.get = lambda url, timeout=0: (asked.append(url), Resp(200, body))[1]
got = p.run_claimant_youth()
check("run_claimant_youth asks for the band by the code it looked up, by LSOA",
      len(got) == 4 and "&age=2&" in asked[0] and "E01000001" in asked[0], asked[0] if asked else "")
check("and writes the counts for the export", (tmp / "data/economy" / p.CLAIMANT_YOUTH_PARQUET).exists())
(tmp / "cache/claimant").joinpath(f"claimant_18_24_{p.SCOPE_KEY}_latest.csv").unlink()
p.requests.get = lambda url, timeout=0: Resp(400, "Cell not available for this geography")
check("a band Nomis refuses for LSOAs is skipped, saying so",
      p.run_claimant_youth() is None and "does not publish the band for LSOAs" in p._SOURCE_NOTE.get("claimant_youth", ""),
      p._SOURCE_NOTE.get("claimant_youth", ""))
p._http_json = lambda method, url, **k: {"structure": {"codelists": {"codelist": [{"code": [
    {"value": 0, "description": {"value": "All categories: Age 16+"}}]}]}}}
check("a codelist without the band is skipped, listing the bands it has",
      p.run_claimant_youth() is None and "All categories" in p._SOURCE_NOTE.get("claimant_youth", ""))
p._http_json = lambda method, url, **k: None
check("an unreadable codelist is a failure, not a skip", raises(p.run_claimant_youth, contains="no 18 to 24 band"))
import requests as _rq
p.requests.get = _rq.get

# ---------------------------------------------------------------------------
print("=== road_casualties: the DfT casualty file ===")
check("the five-year casualty file is picked, not collisions, vehicles or one year",
      p.stats19_pick(["https://data.dft.gov.uk/road-accident-safety-data/dft-road-casualty-statistics-collision-last-5-years.csv",
                      "https://data.dft.gov.uk/road-accident-safety-data/dft-road-casualty-statistics-casualty-2025.csv",
                      "https://data.dft.gov.uk/road-accident-safety-data/dft-road-casualty-statistics-casualty-last-5-years.csv",
                      "https://data.dft.gov.uk/road-accident-safety-data/dft-road-casualty-statistics-vehicle-last-5-years.csv"])
      .endswith("casualty-last-5-years.csv"))
STATS = pd.DataFrame({
    "collision_index": [f"2025{i:08d}" for i in range(9)],
    "collision_year": [2020, 2021, 2023, 2025, 2025, 2024, 2022, 2025, 2025],
    "collision_ref_no": ["x"] * 9,
    "vehicle_reference": [1] * 9,
    "casualty_reference": [1] * 9,
    "casualty_class": [3] * 9,
    "sex_of_casualty": [1] * 9,
    "age_of_casualty": [30] * 9,
    "casualty_severity": [1, 1, 2, 3, 1, 2, 3, 2, 2],
    "casualty_adjusted_severity_serious": ["", "", "1", "0.25", "", "0.8", "0.1", "1", "1"],
    "casualty_adjusted_severity_slight": ["", "", "0", "0.75", "", "0.2", "0.9", "0", "0"],
    "lsoa_of_casualty": ["E01000001", "E01000001", "E01000001", "E01000002", "E01000002", "E01000003",
                         "-1", "E01000099", "W01000001"],
})
cols, missing = p.stats19_columns(STATS.columns)
check("the year, severity, adjusted serious and home LSOA columns are found",
      not missing and cols["year"] == "collision_year" and cols["serious_adj"] == "casualty_adjusted_severity_serious"
      and cols["lsoa"] == "lsoa_of_casualty", str(cols))
check("an older file calling it accident_year is read too",
      p.stats19_columns(STATS.rename(columns={"collision_year": "accident_year"}).columns)[0]["year"] == "accident_year")
check("a file without the adjusted column is reported",
      p.stats19_columns(STATS.drop(columns=["casualty_adjusted_severity_serious"]).columns)[1] == ["serious_adj"])
ksi, years = p.stats19_ksi_by_lsoa(STATS.astype(str), cols)
check("the latest five years only (2021 to 2025, so 2020 is left out)", years == [2021, 2022, 2023, 2024, 2025], str(years))
check("a death counts 1 and an injury its adjusted chance of being serious (1 + 1 = 2 in E01000001)",
      abs(ksi["E01000001"] - 2.0) < 1e-9 and abs(ksi["E01000002"] - 1.25) < 1e-9 and abs(ksi["E01000003"] - 0.8) < 1e-9,
      str(ksi))
check("a casualty with no English or Welsh home LSOA is not counted", "-1" not in ksi and "W01000001" not in ksi)
blank = STATS.astype(str).copy()
blank.loc[2, "casualty_adjusted_severity_serious"] = ""          # a serious injury, 2023, E01000001
blank.loc[3, "casualty_adjusted_severity_serious"] = ""          # a slight injury, 2025, E01000002
ksi_b, _ = p.stats19_ksi_by_lsoa(blank, cols)
check("a blank adjusted figure counts a serious injury as serious and a slight one as not",
      abs(ksi_b["E01000001"] - 2.0) < 1e-9 and abs(ksi_b["E01000002"] - 1.0) < 1e-9, str(ksi_b))
ROWS11 = [("E01000001", "E01000001", "U"), ("E01000002", "E01000002", "U"), ("E01000003", "E01000003", "U"),
          ("E01000099", "E01035001", "S"), ("E01000099", "E01000003", "S")]
c21, shared = p.counts_to_lsoa21({"E01000001": 2.0, "E01000099": 1.0}, set(LSOAS), ROWS11, POP)
check("a retired 2011 code is shared among its 2021 LSOAs by residents, so nothing is lost or doubled",
      shared == 1 and abs(c21["E01035001"] - 0.2) < 1e-9 and abs(c21["E01000003"] - 0.8) < 1e-9
      and abs(sum(c21.values()) - 3.0) < 1e-9, str(c21))

print("=== road_casualties: the source ===")
tmp = fresh_dirs()
patch_lookups()
(tmp / "cache/stats19").mkdir(parents=True)
big = pd.concat([STATS] * 1, ignore_index=True)
f = tmp / "cache/stats19/dft-road-casualty-statistics-casualty-last-5-years.csv"
big.to_csv(f, index=False)
with open(f, "a", encoding="utf-8") as fh:        # padded past the size check, as a real file is
    fh.write(("," * (len(big.columns) - 1) + "\n") * 120000)
pd.DataFrame({"LSOA21CD": list(POP), "census_population": list(POP.values())}).to_parquet(
    tmp / "data/demographics/census2021.parquet", index=False)
p.STATS19_URL_OVERRIDE = "https://data.dft.gov.uk/road-accident-safety-data/dft-road-casualty-statistics-casualty-last-5-years.csv"
p.get_lsoa11_lsoa21_rows = lambda: ROWS11
rc = p.run_road_casualties()
r = rc.set_index("LSOA21CD")["road_ksi_5yr"]
check("every LSOA in scope has a count, 0 where nobody was hurt, a 2011 code shared",
      len(rc) == 4 and r["E01000001"] == 2.0 and r["E01000002"] == 1.25 and abs(r["E01035001"] - 0.2) < 1e-6
      and abs(r["E01000003"] - 1.6) < 1e-6, str(r.to_dict()))
check("the years are recorded", set(rc["road_ksi_years"]) == {"2021 to 2025"})
pd.read_csv(f).drop(columns=["casualty_adjusted_severity_serious"]).to_csv(f, index=False)
with open(f, "a", encoding="utf-8") as fh:
    fh.write(("," * 10 + "\n") * 120000)
check("a file without DfT's adjusted severity is refused, not mixed",
      raises(p.run_road_casualties, contains="serious_adj"))

# ---------------------------------------------------------------------------
print("=== care_homes: the CQC file ===")
links = ["https://www.cqc.org.uk/sites/default/files/2026-10/01_October_2026_HSCA_Active_Locations.ods",
         "https://www.cqc.org.uk/sites/default/files/2026-10/1_October_2026_Care_directory_with_filters.ods",
         "https://www.cqc.org.uk/sites/default/files/2026-09/1_September_2026_Care_directory_with_filters.ods",
         "https://www.cqc.org.uk/sites/default/files/2026-10/Latest_ratings.ods"]
check("the newest care directory with filters is picked", p.cqc_pick(links) == links[1], str(p.cqc_pick(links)))
check("else the active locations file", p.cqc_pick([links[0], links[3]]) == links[0])
check("neither is None", p.cqc_pick([links[3]]) is None)
HEAD = ["Location ID", "Location ODS Code", "Location Name", "Care home?", "Location Type/Sector",
        "Dormant (Y/N)", "Care homes beds", "Location Postal Code", "Location Latitude", "Location Longitude",
        "Service type - Care home service with nursing", "Service type - Care home service without nursing",
        "Provider ID", "Provider Name"]
HOMES = [
    ["1-101", "VA1", "Oak House", "Y", "Social Care Org", "N", "40", "EC1A 1AA", "51.52", "-0.10", "Y", "", "1-9", "Oak Ltd"],
    ["1-102", "VA2", "Elm Lodge", "Y", "Social Care Org", "N", "25", "EC1A 1AB", "51.52", "-0.11", "", "Y", "1-9", "Oak Ltd"],
    ["1-103", "VA3", "Ash Court", "Y", "Social Care Org", "Y", "30", "EC1A 1AC", "51.52", "-0.12", "", "Y", "1-9", "Oak Ltd"],
    ["1-104", "VA4", "Home Care Co", "N", "Social Care Org", "N", "", "EC1A 1AD", "51.52", "-0.13", "", "", "1-9", "Oak Ltd"],
    ["1-101", "VA1", "Oak House", "Y", "Social Care Org", "N", "40", "EC1A 1AA", "51.52", "-0.10", "Y", "", "1-9", "Oak Ltd"],
    ["1-105", "VA5", "Far Away", "Y", "Social Care Org", "N", "60", "NE1 1AA", "54.97", "-1.61", "", "Y", "1-8", "Far Ltd"],
]
tmp = fresh_dirs()
patch_lookups()
(tmp / "cache/cqc").mkdir(parents=True)
# As an .ods, written the way LibreOffice writes one (a zip with content.xml).
def ods_cell(v):
    v = str(v)
    return f'<table:table-cell office:value-type="string"><text:p>{v}</text:p></table:table-cell>' if v \
        else '<table:table-cell/>'
xml = ('<?xml version="1.0" encoding="UTF-8"?><office:document-content '
       'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>'
       '<table:table table:name="Notes"><table:table-row>' + ods_cell("About this file") + '</table:table-row></table:table>'
       '<table:table table:name="HSCA_Active_Locations"><table:table-column table:style-name="co1"/>'
       '<table:table-row>' + ods_cell("Care directory with filters, 1 October 2026") + '</table:table-row>'
       + "".join('<table:table-row>' + "".join(ods_cell(c) for c in row) + '</table:table-row>' for row in [HEAD] + HOMES)
       + '</table:table><table:table table:name="Glossary"><table:table-row>' + ods_cell("Location ID: an id")
       + '</table:table-row></table:table></office:spreadsheet></office:body></office:document-content>')
ods = tmp / "cache/cqc/1_October_2026_Care_directory_with_filters.ods"
with zipfile.ZipFile(ods, "w") as z:
    z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
    z.writestr("content.xml", xml)
check("an empty .ods cell keeps its place, as one or as a repeated run",
      p.ods_parse_row(b'<table:table-cell><text:p>A</text:p></table:table-cell><table:table-cell/>'
                      b'<table:table-cell><text:p>Y</text:p></table:table-cell>'
                      b'<table:table-cell table:number-columns-repeated="2"/>'
                      b'<table:table-cell office:value-type="float" office:value="3"><text:p>3</text:p></table:table-cell>'
                      b'<table:table-cell><text:p>Smith &amp; Co</text:p></table:table-cell>')
      == ["A", "", "Y", "", "", "3", "Smith & Co"])
table = p.cqc_read_table(ods)
check("the .ods table is read from its Location ID header, and stops at the end of its sheet",
      list(table.columns[:3]) == HEAD[:3] and len(table) == len(HOMES), f"{list(table.columns)[:4]} {len(table)}")
homes = p.cqc_care_homes(table)
check("active care homes with beds, once each: not the dormant one or the home care agency",
      sorted(homes["location_id"]) == ["1-101", "1-102", "1-105"], str(homes["location_id"].tolist()))
check("beds and the nursing flag are kept", int(homes.set_index("location_id").loc["1-101", "beds"]) == 40
      and bool(homes.set_index("location_id").loc["1-101", "nursing"]) is True
      and bool(homes.set_index("location_id").loc["1-102", "nursing"]) is False, homes.to_string())
xl = tmp / "cache/cqc/care.xlsx"
pd.DataFrame(HOMES, columns=HEAD).to_excel(xl, index=False, startrow=2)
check("an .xlsx with title rows above the header reads the same", len(p.cqc_care_homes(p.cqc_read_table(xl))) == 3)
xl.unlink()
check("a file without a beds column fails naming it, rather than reporting no beds",
      raises(lambda: p.cqc_care_homes(table.drop(columns=["Care homes beds"])), contains="care home beds"))
no_head = tmp / "cache/cqc/other.csv"
no_head.write_text("Name,Postcode\nOak House,EC1A 1AA\n", encoding="utf-8")
check("a file without a Location ID header row is a clear error",
      raises(lambda: p.cqc_read_table(no_head), contains="Location ID"))
no_head.unlink()

print("=== care_homes: the source ===")
p.CQC_URL_OVERRIDE = ""
p.browser_session = lambda referer=None: (_ for _ in ()).throw(RuntimeError("no internet here"))
PCS = {"EC1A1AA": (51.52, -0.10, "E01000001", "E09000001", "W1"),
       "EC1A1AB": (51.52, -0.11, "E01000003", "E09000001", "W2"),
       "NE11AA": (54.97, -1.61, "E01099999", "E08000021", "WX")}
p.lookup_postcodes = lambda pcs, source="", owners=None: {k: v for k, v in PCS.items() if k in {p.normalise_postcode(x) for x in pcs}}
ch = p.run_care_homes()
check("run_care_homes uses the file in .cache/cqc when the page cannot be read, in scope only",
      sorted(ch["location_id"]) == ["1-101", "1-102"] and int(ch["beds"].sum()) == 65, str(ch.to_dict("records")))
check("each home carries its LSOA and the date of the file", set(ch["LSOA21CD"]) == {"E01000001", "E01000003"}
      and ch["as_at"].notna().all())
ods.unlink()
check("no CQC file is a clear error saying what to do", raises(p.run_care_homes, contains="cqc-source"))

# ---------------------------------------------------------------------------
print("=== ward and LA figures, and the payloads ===")
tmp = fresh_dirs()
patch_lookups()
d = tmp / "data"
pd.DataFrame({"LSOA21CD": LSOAS, "gp_dist_km": [0.2, 0.6, 1.0, 2.0], "pharmacy_dist_km": [0.1, 0.1, 0.5, 0.5],
              "dentist_dist_km": [0.3, 0.3, 0.3, 0.3], "origin": ["pwc"] * 4}).to_parquet(
    d / "environment" / p.SERVICE_ACCESS_PARQUET, index=False)
pd.DataFrame({"LSOA21CD": LSOAS, "sape_population": [1600, 1000, 2000, 400], "sape_age0_15": [400, 100, 200, 100],
              "sape_age16_64": [1000, 800, 1500, 250], "sape_age65plus": [200, 100, 300, 50],
              "sape_age75plus": [100, 50, 100, 20], "sape_age18_24": [100, 200, 300, 100],
              "sape_year": [2024] * 4}).to_parquet(d / "demographics" / p.SAPE_PARQUET, index=False)
pd.DataFrame({"LSOA21CD": ["E01000001", "E01000002", "E01000003"], "claimant_count_18_24": [10, 5, 30],
              "claimant_18_24_month": ["2026-08"] * 3}).to_parquet(d / "economy" / p.CLAIMANT_YOUTH_PARQUET, index=False)
pd.DataFrame({"LSOA21CD": LSOAS, "road_ksi_5yr": [3.0, 2.0, 0.0, 1.0], "road_ksi_years": ["2021 to 2025"] * 4}).to_parquet(
    d / "environment" / p.ROAD_CASUALTIES_PARQUET, index=False)
pd.DataFrame({"location_id": ["a", "b"], "beds": [30, 15], "LSOA21CD": ["E01000001", "E01000003"],
              "as_at": ["2026-10-02"] * 2}).to_parquet(d / "healthcare" / p.CARE_HOMES_PARQUET, index=False)
pd.DataFrame({"LSOA21CD": LSOAS, "census_population": [POP[c] for c in LSOAS],
              "census_over65_pct": [10.0] * 4}).to_parquet(d / "demographics" / "census2021.parquet", index=False)
pd.DataFrame({"LSOA21CD": LSOAS, "imd_score": [10.0, 20.0, 30.0, 40.0], "imd_decile": [5, 4, 3, 2]}).to_parquet(
    d / "demographics" / "imd2025.parquet", index=False)
crime.to_parquet(d / "crime" / "police_uk_crime.parquet", index=False)
(d / "boundaries" / "wards.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"WD25CD": wd, "WD25NM": f"Ward {wd}",
     "LAD25CD": "E09000001", "LAD25NM": "City of London"}} for wd in ("W1", "W2")]}), encoding="utf-8")
wv, notes = p.small_area_figures(L2W, POP, what="ward")
w1, w2 = wv["W1"], wv["W2"]
check("a ward's distance is its LSOAs' average weighted by residents ((0.2 x 1500 + 0.6 x 1000) / 2500 = 0.36)",
      w1["gp_dist_km"] == 0.36, str(w1))
check("a ward's estimated population is its LSOAs' added up", w1["sape_population"] == 2600 and w2["sape_population"] == 2400)
check("an age share is the band added up over the population added up (500 / 2600 = 19.2%)", w1["sape_age0_15_pct"] == 19.2)
check("the youth claimant rate is claimants added up over people aged 18 to 24 added up (15 / 300 = 5.0%)",
      w1["claimant_18_24_pct"] == 5.0 and w2["claimant_18_24_pct"] == 10.0, str((w1, w2)))
check("an LSOA with no claimant figure is left out of the base as well as the count (W2: 30 / 300)",
      w2["claimant_18_24_pct"] == 10.0)
check("road casualties per 1,000 residents over five years from summed counts (5 / 2,500 = 2.0)",
      w1["road_ksi_per_1000"] == 2.0 and w2["road_ksi_per_1000"] == 0.4, str((w1["road_ksi_per_1000"], w2["road_ksi_per_1000"])))
check("care home beds per 100 people aged 75 and over from summed counts (30 / 150 = 20.0)",
      w1["care_home_beds_per_100_75plus"] == 20.0 and w2["care_home_beds_per_100_75plus"] == 12.5)
check("the notes say how the distances were measured and which periods the figures cover",
      notes == {"service_access_origin": "pwc", "sape_year": 2024, "claimant_18_24_month": "2026-08",
                "road_ksi_years": "2021 to 2025", "care_homes_as_at": "2026-10-02"}, str(notes))
lv, _ = p.small_area_figures({c: "E09000001" for c in LSOAS}, POP, what="LA")
la = lv["E09000001"]
check("an LA's rate is all its LSOAs' counts over all their bases (45 / 600 = 7.5%; 45 beds / 270 = 16.7)",
      la["claimant_18_24_pct"] == 7.5 and la["care_home_beds_per_100_75plus"] == 16.7 and la["sape_population"] == 5000,
      str(la))
try:
    wd_out = p.build_ward_data()
    wi = wd_out["wards"]["W1"]["indicators"]
    check("the ward file carries them", wi.get("gp_dist_km") == 0.36 and wi.get("claimant_18_24_pct") == 5.0
          and wi.get("road_ksi_per_1000") == 2.0, str({k: wi.get(k) for k in ("gp_dist_km", "claimant_18_24_pct")}))
    check("the ward crime figures are unchanged: still counted from the ward's own points",
          wi.get("crime_total") == 5 and wd_out["wards"]["W1"]["crime_by_category"]["violent-crime"] == 2)
    check("the ward file's metadata records the notes", wd_out["metadata"].get("small_area", {}).get("sape_year") == 2024)
    bd = p.build_borough_data()
    check("the LA file carries them from all its LSOAs", bd["E09000001"].get("claimant_18_24_pct") == 7.5
          and bd["E09000001"].get("sape_population") == 5000, str(bd.get("E09000001")))
    ld = p.build_lsoa_data()
    a1 = ld.get("E01000001", {})
    check("an LSOA carries its distances, estimates and crime figures",
          a1.get("gp_dist_km") == 0.2 and a1.get("sape_population") == 1600 and a1.get("sape_age0_15_pct") == 25.0
          and a1.get("crime_total") == 3 and a1.get("crime_rate_per_1000") == 2.0, str(a1))
    check("but not the youth claimant rate, road casualties or care home beds, which are too few per LSOA",
          not any(k in a1 for k in p.SMALL_AREA_RATE_KEYS))
    periods = p.small_area_periods()
    check("the periods for indicators.js", periods.get("sape_population") == "mid-2024"
          and periods.get("claimant_18_24_pct") == "August 2026" and periods.get("road_ksi_per_1000") == "2021 to 2025"
          and periods.get("care_home_beds_per_100_75plus") == "as at Oct 2026", str(periods))
except Exception as e:  # a lookup this scratch folder does not hold
    import traceback
    traceback.print_exc()
    check("the builders run on the scratch folder", False, repr(e))
# Without the estimates there is no base for the youth rate or the beds.
(d / "demographics" / p.SAPE_PARQUET).unlink()
wv2, _ = p.small_area_figures(L2W, POP, what="ward")
check("without the population estimates the youth claimant rate and beds are left out, not divided by something else",
      "claimant_18_24_pct" not in wv2["W1"] and "care_home_beds_per_100_75plus" not in wv2["W1"]
      and wv2["W1"].get("road_ksi_per_1000") == 2.0)

print("=== registered as sources, each with its own manifest entry ===")
order = list(p.SOURCES)
check("service_access runs after the GP, pharmacy and dental registers",
      max(order.index("gp"), order.index("pharmacies"), order.index("dentists")) < order.index("service_access"), str(order))
check("the five are separate sources", all(s in order for s in ("sape", "claimant_youth", "road_casualties",
                                                                 "care_homes", "service_access")))
check("the distances are dated by the run that measured them",
      p.REGISTER_KEYS.get("service_access") == ("gp_dist_km", "pharmacy_dist_km", "dentist_dist_km"))

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
