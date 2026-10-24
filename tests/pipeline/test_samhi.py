"""Small Area Mental Health Index (run_samhi): PLDR's yearly score of mental
ill health for each LSOA, the latest year kept.

Offline. The fact-check note says version 4.00 is a wide CSV (6.14 MB) and a
long one (30.18 MB), and that its LSOA vintage and column names were not
seen; copies of earlier versions are keyed to lsoa11. So the synthetic files
are laid out as earlier releases are, as best recalled: an "lsoa11" column,
then either a "year" column and "samhi_index" (long) or "samhi_index.2019"
and the like (wide), with decile and component columns beside them. Both are
unverified here. PLDR's page is answered by a fake session.

Covers both layouts, the latest year, the deciles (1 the worst tenth of
England), the 2011 to 2021 bridge (a split LSOA takes its parent's figure, a
merged one the average of its parents), ward and LA averages weighted by
residents, the licence gate (PLDR's page must name an open licence; a file
attached by hand is taken as checked), the skip with no file, and the
failure paths.
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
# The synthetic files hold 40 LSOAs, not England's 33,755.
p.PLACES_MIN_LSOAS = 20


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
        dct.pop("samhi", None)
    return tmp


class Resp:
    def __init__(self, status=200, body=b""):
        self.status_code, self.content = status, body
        self.ok = status < 400
        self.text = body.decode("utf-8", "replace")

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class Sess:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kw):
        for k, v in self.routes.items():
            if url.startswith(k):
                return v
        raise RuntimeError(f"no internet here ({url})")


# 2011 LSOAs: 40 in England (E01000001 to E01000040) and one in Wales. In
# 2021, E01000003 was split into E01033801 and E01033802, and E01000004 and
# E01000005 were merged into E01033803.
ENG11 = [f"E010000{i:02d}" for i in range(1, 41)]
SCOPE = ["E01000001", "E01000002", "E01033801", "E01033802", "E01033803", "E01000006"]
ROWS = [("E01000001", "E01000001", "U"), ("E01000002", "E01000002", "U"), ("E01000003", "E01033801", "S"),
        ("E01000003", "E01033802", "S"), ("E01000004", "E01033803", "M"), ("E01000005", "E01033803", "M"),
        ("E01000006", "E01000006", "U")]
L2W = {"E01000001": "W1", "E01000002": "W1", "E01033801": "W1", "E01033802": "W2", "E01033803": "W2", "E01000006": "W2"}
POP = {c: 1000 for c in SCOPE}
POP["E01000002"] = 3000


def patch_lookups():
    p.get_lsoa_ward_lookup = lambda: {l: (w, "E09000001") for l, w in L2W.items()}
    p.get_lsoa_to_ward = lambda: dict(L2W)
    p.get_lsoa_to_msoa = lambda: {l: "E02000001" for l in L2W}
    p.get_lsoa11_lsoa21_rows = lambda: list(ROWS)
    p._census_ward_inputs = lambda: (None, None, {})
    p.get_oa_to_ward = lambda: {}
    p._ward_to_lad = lambda: {"W1": "E09000001", "W2": "E09000001"}


def score(code, year):
    i = int(code[-2:])
    return round(-1.5 + 0.08 * i + 0.01 * (year - 2019), 3)


LONG = pd.DataFrame([{"lsoa11": c, "year": y, "samhi_index": score(c, y), "samhi_dec": 0,
                      "z_mh_rate": 0.1, "z_antidep_rate": 0.2, "z_est_qof_dep": 0.3, "z_ib_esa_rate": 0.4}
                     for y in (2019, 2020, 2021, 2022) for c in ENG11 + ["W01000001"]])
WIDE = pd.DataFrame({"lsoa11": ENG11 + ["W01000001"],
                     **{f"samhi_index.{y}": [score(c, y) for c in ENG11 + ["W01000001"]] for y in (2019, 2020, 2021, 2022)},
                     **{f"samhi_dec.{y}": [1] * 41 for y in (2019, 2020, 2021, 2022)}})

print("=== SAMHI: the two layouts ===")
nat, year, used = p.samhi_parse([("long", "", LONG.astype(str))])
check("long: the latest year is kept, for England's LSOAs", year == 2022 and len(nat) == 40
      and nat.set_index("LSOA_CODE").loc["E01000040", "samhi_index"] == score("E01000040", 2022), f"{year} {used}")
check("the score is samhi_index, not a decile or a component", used.endswith("samhi_index (year 2022)"), used)
d = nat.set_index("LSOA_CODE")["samhi_decile"]
check("decile 1 is the tenth with the worst mental health (the highest score)",
      d["E01000040"] == 1 and d["E01000001"] == 10 and sorted(d.value_counts().tolist()) == [4] * 10, str(d.to_dict()))
nat_w, year_w, used_w = p.samhi_parse([("wide", "", WIDE.astype(str))])
check("wide: the latest year's column is read", year_w == 2022 and used_w.endswith("samhi_index.2022")
      and nat_w.set_index("LSOA_CODE")["samhi_index"].equals(nat.set_index("LSOA_CODE")["samhi_index"]), used_w)
check("a table with no SAMHI index is refused, naming its columns",
      raises(lambda: p.samhi_parse([("x", "", LONG.drop(columns=["samhi_index"]).astype(str))]), "samhi_dec"))
flat = LONG.assign(samhi_index=1000)
check("a column that is not a score built from z-scores is refused",
      raises(lambda: p.samhi_parse([("x", "", flat.astype(str))]), "not a score"))

print("=== SAMHI: the 2011 LSOAs bridged to 2021 ===")
patch_lookups()
out, vintage = p.places_scope_frame(nat, set(SCOPE), cols=p.SAMHI_KEYS, source="samhi")
o = out.set_index("LSOA21CD")
check("a file on the 2011 LSOAs is bridged", vintage == "2011" and sorted(o.index) == sorted(SCOPE), str(o))
check("each part of a split LSOA takes its parent's score",
      o.loc["E01033801", "samhi_index"] == o.loc["E01033802", "samhi_index"] == score("E01000003", 2022))
check("a merged LSOA takes the average of its parents",
      abs(o.loc["E01033803", "samhi_index"] - (score("E01000004", 2022) + score("E01000005", 2022)) / 2) < 1e-9)
nat21 = nat.copy()
nat21["LSOA_CODE"] = nat21["LSOA_CODE"].replace({"E01000003": "E01033801", "E01000004": "E01033802", "E01000005": "E01033803"})
out21, v21 = p.places_scope_frame(nat21, set(SCOPE), cols=p.SAMHI_KEYS, source="samhi")
check("a file on the 2021 LSOAs is used as it is", v21 == "2021" and len(out21) == 6)

print("=== SAMHI: the source and its licence ===")
csv = LONG.to_csv(index=False).encode()
csv += b"\n" * 520_000
link = "https://pldr.org/download/2noyv/abc/SAMHI_v4_00.csv"
long_link = "https://pldr.org/download/2noyv/abd/SAMHI_v4_00_long.csv"
page_open = (f'<html><a href="{long_link}">long</a><a href="{link}">CSV</a>'
             '<p>Licence: Open Government Licence v3.0</p></html>').encode()
page_closed = f'<html><a href="{link}">CSV</a><p>All rights reserved.</p></html>'.encode()
check("the wide file is taken before the long one", p.samhi_pick([long_link, link, "https://pldr.org/about"])[0] == link)

tmp = fresh()
patch_lookups()
p.browser_session = lambda referer=None: Sess({p.SAMHI_PAGE: Resp(body=page_open), link: Resp(body=csv)})
out = p.run_samhi()
check("downloaded from PLDR's page when it names an open licence, bridged, with its year and licence",
      out is not None and sorted(out["LSOA21CD"]) == sorted(SCOPE) and set(out["samhi_year"]) == {2022}
      and set(out["samhi_vintage"]) == {"2011"} and set(out["samhi_licence"]) == {"Open Government Licence"}
      and set(out["samhi_version"]) == {"4.00"}, "" if out is None else out.to_string())
check("a merged LSOA's decile is a whole decile", out is not None and str(out["samhi_decile"].dtype) == "Int64"
      and out["samhi_decile"].notna().all())

tmp = fresh()
patch_lookups()
p.browser_session = lambda referer=None: Sess({p.SAMHI_PAGE: Resp(body=page_closed), link: Resp(body=csv)})
r = p.run_samhi()
check("a page naming no open licence: skipped, nothing downloaded, the note saying what to check",
      r is None and "names no open licence" in p._SOURCE_SKIPPED.get("samhi", "")
      and "samhi-source" in p._SOURCE_SKIPPED["samhi"] and not any((tmp / "cache/samhi").iterdir()))

tmp = fresh()
patch_lookups()
(tmp / "cache/samhi").mkdir(parents=True)
WIDE.to_csv(tmp / "cache/samhi/SAMHI_v4_00.csv", index=False)
p.browser_session = lambda referer=None: Sess({})
out = p.run_samhi()
check("a file attached by hand (the samhi-source release) is read, its licence taken as checked",
      out is not None and len(out) == 6 and "samhi-source" in set(out["samhi_licence"]).pop())

tmp = fresh()
patch_lookups()
r = p.run_samhi()
check("with no file and PLDR unreachable: skipped, not failed, naming the page and the release",
      r is None and p.SAMHI_PAGE in p._SOURCE_SKIPPED.get("samhi", "") and "samhi-source" in p._SOURCE_SKIPPED["samhi"]
      and "samhi" not in p._SOURCE_ERROR)

tmp = fresh()
patch_lookups()
(tmp / "cache/samhi").mkdir(parents=True)
(tmp / "cache/samhi/samhi.csv").write_text("lsoa11,other\nE01000001,1\n", encoding="utf-8")
check("a file that is not SAMHI fails", raises(p.run_samhi, "samhi"))

print("=== SAMHI: wards and LAs ===")
tmp = fresh()
patch_lookups()
pd.DataFrame({"LSOA21CD": SCOPE, "samhi_index": [1.0, -1.0, 0.5, 2.0, 0.0, -2.0], "samhi_decile": [2, 9, 4, 1, 5, 10],
              "samhi_year": [2022] * 6, "samhi_vintage": ["2011"] * 6, "samhi_version": ["4.00"] * 6,
              "samhi_licence": ["Open Government Licence"] * 6}).to_parquet(tmp / "data/outcomes" / p.SAMHI_PARQUET, index=False)
vals, notes, _c = p.places_figures(L2W, POP, what="ward")
check("a ward's index is its LSOAs' average weighted by residents ((1 - 3 + 0.5) / 5 = -0.3)",
      vals["W1"]["samhi_index"] == -0.3 and vals["W2"]["samhi_index"] == 0.0, str(vals))
check("deciles are not averaged into wards", "samhi_decile" not in vals["W1"])
check("the notes say the year, the LSOA vintage and the licence",
      notes.get("samhi_year") == 2022 and notes.get("samhi_vintage") == "2011" and notes.get("samhi_licence") == "Open Government Licence")
la, _n, _c = p.places_figures({c: "E09000001" for c in SCOPE}, POP, what="LA")
check("an LA's from all its LSOAs (-1,500 / 8,000 = -0.188)", la["E09000001"]["samhi_index"] == -0.188, str(la))
lv = p.places_lsoa_values()
check("an LSOA carries its index and decile", lv["E01000001"] == {"samhi_index": 1.0, "samhi_decile": 2}, str(lv.get("E01000001")))
check("the year is the period", p.places_periods().get("samhi_index") == "2022")

print("=== registered as a source ===")
check("samhi is a source of its own", p.SOURCES.get("samhi") is p.run_samhi)

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
