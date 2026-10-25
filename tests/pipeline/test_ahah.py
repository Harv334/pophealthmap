"""Access to Healthy Assets and Hazards (run_ahah): GeoDS's index of how
healthy the places around each LSOA are, its four domains, and the drive time
to the nearest fast food outlet, pub, off-licence, gambling outlet and
tobacconist.

Offline. The synthetic files are laid out as earlier AHAH releases are, as
best recalled: one row per LSOA under an "lsoa21" code column, then columns
with a release prefix (ah3gp, ah3ffood, ah3h, ah3ahah, with _rnk and _pct
beside them). The fact-check note says version 5.1 has an overall index,
four domains and 15 inputs, each with values, ranks and percentiles, and that
its column names were not seen, so a spelled-out layout is tested too. Both
are unverified here. GeoDS is a CKAN portal (its /dataset/groups/ pages say
so), so the record is answered in CKAN's package_show shape.

Covers the matching of columns, the maths (deciles from the index across
England, ward and LA averages weighted by residents), the checks (a column
matched twice, scores running the wrong way, 2011 codes, an old release),
the manual file in .cache/ahah/, the skip with no file, the skip with no open
licence, a login page in place of the file, and the figures kept from the
last run.
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


def raises(fn, contains=""):
    try:
        fn()
    except RuntimeError as e:
        return contains.lower() in str(e).lower()
    return False


def fresh():
    tmp = pathlib.Path(tempfile.mkdtemp())
    p.REPO_ROOT = tmp
    p.CACHE_DIR = tmp / "cache"
    p.DATA_DIR = tmp / "data"
    p.NATIONAL_DATA_DIR = tmp / "data"
    p.CUSTOM_DIR = tmp / "data" / "custom"
    for d in ("boundaries", "environment", "demographics", "outcomes", "healthcare", "economy"):
        (tmp / "data" / d).mkdir(parents=True, exist_ok=True)
    for dct in (p._SOURCE_ERROR, p._SOURCE_PARTIAL, p._SOURCE_NOTE, p._SOURCE_SKIPPED):
        dct.pop("ahah", None)
    return tmp


class Resp:
    def __init__(self, status=200, body=b"", js=None):
        self.status_code, self.content, self._js = status, body, js
        self.ok = status < 400
        self.text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class Sess:
    """Answers each URL from a dict; anything else is no internet."""
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kw):
        for k, v in self.routes.items():
            if url.startswith(k):
                return v
        raise RuntimeError(f"no internet here ({url})")


rng = random.Random(7)
# 40 English LSOAs, ten of them with codes drawn in 2021, two Welsh, one
# Scottish data zone.
ENG = [f"E0100{i:04d}" for i in range(1, 31)] + [f"E0103{3780 + i}" for i in range(10)]
OTHER = ["W01000001", "W01000002", "S01006506"]
SCOPE = ENG[:6]
L2W = {SCOPE[0]: "W1", SCOPE[1]: "W1", SCOPE[2]: "W1", SCOPE[3]: "W2", SCOPE[4]: "W2", SCOPE[5]: "W2"}
POP = {SCOPE[0]: 1000, SCOPE[1]: 3000, SCOPE[2]: 1000, SCOPE[3]: 2000, SCOPE[4]: 2000, SCOPE[5]: 1000}


def patch_lookups():
    p.get_lsoa_ward_lookup = lambda: {l: (w, "E09000001") for l, w in L2W.items()}
    p.get_lsoa_to_ward = lambda: dict(L2W)
    p.get_lsoa_to_msoa = lambda: {l: "E02000001" for l in L2W}
    p._census_ward_inputs = lambda: (None, None, {})
    p.get_oa_to_ward = lambda: {}
    p._ward_to_lad = lambda: {"W1": "E09000001", "W2": "E09000001"}


def prefixed(codes, flip=False):
    """An AHAH table in the prefixed layout: drive times, the four domains
    and the index, each value with a rank and a percentile beside it."""
    rows = []
    for i, c in enumerate(codes):
        near = (i % 10) / 10                    # 0 near the high street, 0.9 far from it
        times = {k: round(1 + 12 * near + rng.uniform(0, 1), 2) for k in ("ffood", "pubs", "off", "gamb", "tob")}
        retail = (10 - 9 * near) if not flip else (1 + 9 * near)
        health = 3 + (i % 7)
        air = 5 + (i % 3)
        green = 2 + (i % 5)
        index = round((retail + health + air + green) / 4, 3)
        row = {"lsoa21": c, "ah5gp": round(0.5 + near, 2), **{f"ah5{k}": v for k, v in times.items()},
               "ah5h": health, "ah5r": round(retail, 3), "ah5e": air, "ah5g": green, "ah5ahah": index}
        for k in ("h", "r", "e", "g", "ahah"):
            row[f"ah5{k}_rnk"] = i + 1
            row[f"ah5{k}_pct"] = round(100 * (i + 1) / len(codes), 1)
        rows.append(row)
    return pd.DataFrame(rows)


print("=== the words of a header ===")
for head, want in (("ah5ffood", ("", "ffood")), ("ah3ffood_rnk", ("rank", "ffood")), ("ah5ahah_pct", ("pct", "ahah")),
                   ("ah5h", ("", "h")), ("Fast food (minutes by car)", ("", "fastfood")),
                   ("Off-licences: mean drive time", ("", "offlicences")), ("Health services domain score", ("", "healthservices")),
                   ("AHAH index decile", ("dec", "ahahindex")), ("ah5gpas", ("", "gpas"))):
    check(f"places_words({head!r}) is {want}", p.places_words(head) == want, str(p.places_words(head)))
check("a code header is known by its words", all(p.places_code_header(h) for h in ("lsoa21", "LSOA21CD", "LSOA code", "LSOA 2021 Code", "geo_code", "MSOA21CD"))
      and not any(p.places_code_header(h) for h in ("LSOA name", "ah5ahah", "Local authority")))
check("the release is read from a file name", p.places_version("AHAH_V5_1.csv") == "5.1" and p.places_version("SAMHI_v4_00.csv") == "4.00"
      and p.places_version("ahah_lsoa21.csv") == "" and p.places_version("PPFI v2.1 (2024).csv") == "2.1")

print("=== AHAH: the prefixed layout ===")
tab = prefixed(ENG + OTHER)
nat, problems, matched = p.ahah_parse([("AHAH_V5_1", "", tab.astype(str))])
check("every measure is found, from its value column (not its rank or percentile)",
      set(p.AHAH_KEYS) <= set(nat.columns) and matched["ahah_retail"].endswith("ah5r")
      and matched["ahah_fast_food_min"].endswith("ah5ffood") and matched["ahah_index"].endswith("ah5ahah"),
      f"{problems} {matched}")
check("only English LSOAs are kept", set(nat["LSOA_CODE"]) == set(ENG), str(len(nat)))
check("nothing is reported for a file that reads cleanly", not problems, str(problems))
nat = nat.set_index("LSOA_CODE")
hi = nat["ahah_index"].idxmax()
lo = nat["ahah_index"].idxmin()
check("decile 1 is the least healthy tenth (the highest index) and 10 the healthiest",
      nat.loc[hi, "ahah_decile"] == 1 and nat.loc[lo, "ahah_decile"] == 10
      and sorted(nat["ahah_decile"].value_counts().tolist()) == [4] * 10, str(nat["ahah_decile"].value_counts().to_dict()))

print("=== AHAH: a spelled-out layout ===")
spelled = tab.rename(columns={"lsoa21": "LSOA21CD", "ah5ffood": "Fast food (minutes by car)", "ah5pubs": "Pubs, bars and nightclubs (minutes)",
                              "ah5off": "Off-licences (minutes)", "ah5gamb": "Gambling (minutes)", "ah5tob": "Tobacconists and vape shops",
                              "ah5h": "Health services domain", "ah5r": "Retail domain", "ah5e": "Air quality domain",
                              "ah5g": "Green and blue space domain", "ah5ahah": "AHAH index"})
spelled = spelled[[c for c in spelled.columns if not c.startswith("ah5")]]
nat2, prob2, m2 = p.ahah_parse([("data", "", spelled.astype(str))])
check("spelled-out headers are matched to the same measures", all(k in nat2.columns for k in p.AHAH_KEYS),
      f"{prob2} {m2}")
check("tobacconists and vape shops counts as the tobacconist measure", m2.get("ahah_tobacco_min", "").endswith("vape shops"))

print("=== AHAH: what is refused ===")
dup = tab.assign(**{"ah5ffood_time": tab["ah5ffood"]})
_n, prob3, _m = p.ahah_parse([("x", "", dup.astype(str))])
check("a measure matched by two columns is reported and not used",
      "ahah_fast_food_min" not in _n.columns and any("2 columns match" in x for x in prob3), str(prob3))
rev, prob4, _m = p.ahah_parse([("x", "", prefixed(ENG, flip=True).astype(str))])
check("scores that rise with the drive times to outlets (the wrong way round) are not used, and say why",
      not any(k in rev.columns for k in p.AHAH_SCORES) and "ahah_fast_food_min" in rev.columns
      and any("documented way" in x for x in prob4), str(prob4))
check("a file on the 2011 LSOAs is refused", raises(lambda: p.ahah_parse([("x", "", prefixed(ENG[:30]).astype(str))]), "2011"))
check("a file with no English LSOA codes is refused", raises(lambda: p.ahah_parse([("x", "", prefixed(OTHER).astype(str))]), "English LSOA"))
km = tab.copy()
for k in ("ffood", "pubs", "off", "gamb", "tob"):
    km[f"ah5{k}"] = km[f"ah5{k}"] * 200
_n, prob5, _m = p.ahah_parse([("x", "", km.astype(str))])
check("drive times far beyond minutes (another unit) are not used", "ahah_pubs_min" not in _n.columns
      and any("not minutes by car" in x for x in prob5), str(prob5))

print("=== AHAH: finding the files on GeoDS ===")
res = [{"name": "AHAH V5.1 LSOA21", "url": "https://data.geods.ac.uk/dataset/x/resource/1/download/ahah_v5_1_lsoa21.csv", "format": "CSV"},
       {"name": "AHAH V5.1 LSOA21", "url": "https://data.geods.ac.uk/dataset/x/resource/2/download/ahah_v5_1_lsoa21.parquet", "format": "Parquet"},
       {"name": "AHAH V5.1 Data Zones", "url": "https://data.geods.ac.uk/dataset/x/resource/3/download/ahah_v5_1_dz22.csv", "format": "CSV"},
       {"name": "Variable Dictionary", "url": "https://data.geods.ac.uk/dataset/x/resource/4/download/ahah_dictionary.csv", "format": "CSV"},
       {"name": "Technical Methodology Report", "url": "https://data.geods.ac.uk/dataset/x/resource/5/download/report.pdf", "format": "PDF"}]
picked = p.ahah_pick(res)
check("the LSOA file is taken once, as a CSV, and not the data zones, the dictionary or the report",
      picked == [res[0]["url"]], str(picked))
# A data file whose description points to the methodology is still a data
# file (the first refresh took none of GeoDS's ten; descriptions are the
# likely reason), so only its name and address are read.
desc = [dict(r, description="Read the technical report and methodology before use.") for r in res]
check("a data file whose description mentions the methodology is still taken",
      p.ahah_pick(desc) == [res[0]["url"]], str(p.ahah_pick(desc)))
# GeoDS's record as the October 2026 refresh logged it: names and formats,
# no addresses. One is made from each resource's id, and the index file is
# the one taken, not the geofile, the summary, the dictionary or the report.
geods = [{"id": f"r{i}", "name": n, "format": f} for i, (n, f) in enumerate([
    ("Data: AHAH Overall Index, Domains and Components (v5.1)", "CSV"),
    ("Data: Geofile for AHAH Overall Index, Domains and Components (v5.1)", "parquet"),
    ("Data Summary", "CSV"), ("Variable Dictionary", "CSV"), ("Technical Report: Methodology", "PDF"),
    ("Source Code: AHAH (GitHub Repository)", "HTML"), ("Map: Mapmaker", "HTML"),
    ("Related Record: Previous versions of AHAH", "HTML")])]


class _R:
    ok = True
    def json(self):
        return {"success": True, "result": {"name": "access-to-healthy-assets-hazards-ahah", "license_title":
                "UK Open Government Licence (OGL)", "resources": [dict(x) for x in geods]}}


p.browser_session = lambda **k: type("S", (), {"get": lambda self, url, timeout=None: _R()})()
urls, lic, _v = p.ahah_find()
check("GeoDS files with no address get one from their id, and only the index file is taken",
      len(urls) == 1 and "/resource/r0/download/" in urls[0] and urls[0].endswith(".csv") and lic, str(urls))

print("=== AHAH: the source ===")
tmp = fresh()
patch_lookups()
(tmp / "cache" / "ahah").mkdir(parents=True)
f = tmp / "cache" / "ahah" / "AHAH_V5_1.csv"
tab.to_csv(f, index=False)
p.browser_session = lambda referer=None: Sess({})
out = p.run_ahah()
check("a file in .cache/ahah/ is read, cut to the scope and written with its version",
      out is not None and sorted(out["LSOA21CD"]) == sorted(SCOPE) and set(out["ahah_version"]) == {"5.1"}
      and (tmp / "data/environment" / p.AHAH_PARQUET).exists(), "" if out is None else out.head().to_string())
check("its deciles are England's, not the scope's", out is not None and out["ahah_decile"].nunique() < 6)
f.unlink()
kept = p.run_ahah()
check("with no new file and GeoDS unreachable, last run's figures are kept and the note says so",
      kept is not None and len(kept) == 6 and "stay as last written" in p._SOURCE_NOTE.get("ahah", ""))

tmp = fresh()
patch_lookups()
r = p.run_ahah()
check("with no file at all, the source is skipped (not failed), naming the ahah-source release and the page",
      r is None and "ahah-source" in p._SOURCE_SKIPPED.get("ahah", "") and p.AHAH_PAGE in p._SOURCE_SKIPPED["ahah"]
      and ".cache/ahah/" in p._SOURCE_SKIPPED["ahah"] and "ahah" not in p._SOURCE_ERROR)

tmp = fresh()
patch_lookups()
csv_bytes = tab.to_csv(index=False).encode() * 1
csv_bytes += b"\n" * 210_000                     # padded past the size check, as a real file is
record = {"success": True, "result": {"license_title": "UK Open Government Licence (OGL)", "version": "5.1",
                                      "resources": res}}
p.browser_session = lambda referer=None: Sess({p.AHAH_CKAN: Resp(js=record), res[0]["url"]: Resp(body=csv_bytes)})
out = p.run_ahah()
check("found through GeoDS's record and downloaded when the licence is open",
      out is not None and len(out) == 6 and (tmp / "cache/ahah/ahah_v5_1_lsoa21.csv").exists())

tmp = fresh()
patch_lookups()
login = b"<!DOCTYPE html><html><body><form>Log in to download</form></body></html>" + b" " * 300_000
p.browser_session = lambda referer=None: Sess({p.AHAH_CKAN: Resp(js=record), res[0]["url"]: Resp(body=login)})
r = p.run_ahah()
check("a login page in place of the file is no file: skipped, with the release to attach it to",
      r is None and "ahah-source" in p._SOURCE_SKIPPED.get("ahah", ""))

tmp = fresh()
patch_lookups()
closed = {"success": True, "result": {"license_title": "Other (Not Open)", "resources": res}}
p.browser_session = lambda referer=None: Sess({p.AHAH_CKAN: Resp(js=closed), res[0]["url"]: Resp(body=csv_bytes)})
r = p.run_ahah()
check("a record naming no open licence is not used: skipped, and nothing is downloaded",
      r is None and "no open licence" in p._SOURCE_SKIPPED.get("ahah", "") and not (tmp / "cache/ahah/ahah_v5_1_lsoa21.csv").exists())

tmp = fresh()
patch_lookups()
(tmp / "cache" / "ahah").mkdir(parents=True)
tab.to_csv(tmp / "cache" / "ahah" / "AHAH_V4.csv", index=False)
check("an older release, which measured distance rather than minutes, fails with what to use",
      raises(p.run_ahah, "version 5.1"))
(tmp / "cache" / "ahah" / "AHAH_V4.csv").unlink()
(tmp / "cache" / "ahah" / "ahah.csv").write_text("name,value\nx,1\n", encoding="utf-8")
check("a file that is not the AHAH table fails, rather than writing nothing quietly", raises(p.run_ahah, "English LSOA"))

print("=== AHAH: wards and LAs ===")
tmp = fresh()
patch_lookups()
lsoa = pd.DataFrame({"LSOA21CD": SCOPE, "ahah_index": [10.0, 20.0, 30.0, 5.0, 5.0, 50.0],
                     "ahah_health_services": [1.0] * 6, "ahah_retail": [2.0] * 6, "ahah_air_quality": [3.0] * 6,
                     "ahah_green_blue": [4.0] * 6, "ahah_fast_food_min": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
                     "ahah_pubs_min": [1.0] * 6, "ahah_off_licence_min": [1.0] * 6, "ahah_gambling_min": [1.0] * 6,
                     "ahah_tobacco_min": [1.0] * 6, "ahah_decile": [3, 2, 1, 5, 5, 1], "ahah_version": ["5.1"] * 6})
lsoa.to_parquet(tmp / "data/environment" / p.AHAH_PARQUET, index=False)
vals, notes, _c = p.places_figures(L2W, POP, what="ward")
check("a ward's index is its LSOAs' average weighted by residents ((10 + 60 + 30) / 5,000 = 20.0)",
      vals["W1"]["ahah_index"] == 20.0 and vals["W2"]["ahah_index"] == 14.0, str(vals))
check("so is a drive time ((10 x 2,000 + 20 x 2,000 + 30 x 1,000) / 5,000 = 18.0)", vals["W2"]["ahah_fast_food_min"] == 18.0)
check("a decile is not averaged into a ward", "ahah_decile" not in vals["W1"])
check("the notes carry the version", notes.get("ahah_version") == "5.1")
la, _n, _c = p.places_figures({c: "E09000001" for c in SCOPE}, POP, what="LA")
check("an LA's is all its LSOAs' average weighted by residents (170,000 / 10,000 = 17.0)",
      la["E09000001"]["ahah_index"] == 17.0, str(la))
pd.DataFrame({"LSOA21CD": SCOPE, "census_population": [POP[c] for c in SCOPE]}).to_parquet(
    tmp / "data/demographics/census2021.parquet", index=False)
pd.DataFrame({"LSOA21CD": SCOPE, "imd_score": [1.0] * 6, "imd_decile": [5] * 6}).to_parquet(
    tmp / "data/demographics/imd2025.parquet", index=False)
(tmp / "data/boundaries/wards.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"WD25CD": w, "WD25NM": f"Ward {w}", "LAD25CD": "E09000001",
                                                         "LAD25NM": "City of London"}} for w in ("W1", "W2")]}), encoding="utf-8")
try:
    wd = p.build_ward_data()
    check("the ward file carries them, and its metadata the version",
          wd["wards"]["W1"]["indicators"].get("ahah_index") == 20.0 and wd["metadata"]["places"].get("ahah_version") == "5.1",
          str(wd["metadata"].get("places")))
    ld = p.build_lsoa_data()
    check("an LSOA carries its own figures and its decile", ld[SCOPE[2]].get("ahah_index") == 30.0
          and ld[SCOPE[2]].get("ahah_decile") == 1 and ld[SCOPE[2]].get("ahah_fast_food_min") == 3.0, str(ld.get(SCOPE[2])))
    bd = p.build_borough_data()
    check("the LA file carries them", bd.get("E09000001", {}).get("ahah_index") == 17.0, str(bd.get("E09000001")))
    per = p.places_periods()
    check("the version is the period indicators.js carries", per.get("ahah_index") == "version 5.1"
          and per.get("ahah_decile") == "version 5.1", str(per))
except Exception as e:
    import traceback
    traceback.print_exc()
    check("the builders run on the scratch folder", False, repr(e))

print("=== registered as a source ===")
check("ahah is a source of its own", "ahah" in p.SOURCES and p.SOURCES["ahah"] is p.run_ahah)
check("its release is named ahah-source", p.places_release("ahah") == "ahah-source"
      and p.places_release("qof_msoa") == "qof-msoa-source")

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
