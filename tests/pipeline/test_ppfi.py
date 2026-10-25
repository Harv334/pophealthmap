"""Priority Places for Food Index (run_ppfi): CDRC's ranking of each LSOA by
its risk of food insecurity, read from a file someone has downloaded with a
free CDRC login and attached to the ppfi-source release.

Offline. The fact-check note says version 2.1 is a CSV of ranks and deciles
for the combined index and its domains, on the 2021 LSOAs for England and
Wales, decile 1 the highest priority, and that its column names were not
seen. So the synthetic file is laid out as the first release is, as best
recalled: "geo_code" and "geo_name", then a rank and a decile for the
combined index and each of seven domains (supermarket proximity and
accessibility, e-commerce access, socio-demographic barriers, non-supermarket
food, food for families, fuel poverty). Unverified here. The rows cover the
UK, so the ranks are the UK's.

Covers the matching of domains (fuel poverty and socio-demographic barriers
left out), England deciles made again from UK ranks, the direction taken
from the published deciles, deciles used as published only when they split
England into tenths, the ward and LA share of residents in the
highest-priority fifth, the skip with no file (never a download, as the file
needs a login), kept figures and the failure paths.
"""
import importlib.util
import pathlib
import sys
import tempfile

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

FAILS = []
# The synthetic file holds 40 English LSOAs, not England's 33,755.
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
        dct.pop("ppfi", None)
    return tmp


ENG = [f"E0100{i:04d}" for i in range(1, 31)] + [f"E0103{3780 + i}" for i in range(10)]
OTHER = [f"W0100{i:04d}" for i in range(1, 11)] + ["S01006506", "95AA01S1"]
SCOPE = ENG[:6]
L2W = {SCOPE[0]: "W1", SCOPE[1]: "W1", SCOPE[2]: "W1", SCOPE[3]: "W2", SCOPE[4]: "W2", SCOPE[5]: "W2"}
POP = {SCOPE[0]: 1000, SCOPE[1]: 3000, SCOPE[2]: 1000, SCOPE[3]: 2000, SCOPE[4]: 2000, SCOPE[5]: 1000}
DOMS = ["supermarket_proximity", "supermarket_accessibility", "ecommerce_access", "socio_demographic",
        "nonsupermarket_proximity", "food_for_families", "fuel_poverty"]


def patch_lookups():
    p.get_lsoa_ward_lookup = lambda: {l: (w, "E09000001") for l, w in L2W.items()}
    p.get_lsoa_to_ward = lambda: dict(L2W)
    p.get_lsoa_to_msoa = lambda: {l: "E02000001" for l in L2W}
    p._census_ward_inputs = lambda: (None, None, {})
    p.get_oa_to_ward = lambda: {}
    p._ward_to_lad = lambda: {"W1": "E09000001", "W2": "E09000001"}


def table(reverse=False):
    """UK rows, ranked together: the Welsh and other rows sit among the
    English ones, so a UK rank is not an English one. Rank 1 is the highest
    priority (reverse: the lowest)."""
    codes = []
    o = list(OTHER)
    for i, c in enumerate(ENG):
        codes.append(c)
        if i % 4 == 3 and o:
            codes.append(o.pop(0))
    codes += o
    n = len(codes)
    rows = []
    for pos, c in enumerate(codes, start=1):
        rank = n + 1 - pos if reverse else pos
        dec = (pos - 1) * 10 // n + 1               # UK deciles, 1 the highest priority
        r = {"geo_code": c, "geo_name": f"Area {pos}", "rank_combined": rank, "pp_dec_combined": dec}
        for j, d in enumerate(DOMS):
            r[f"rank_domain_{d}"] = rank
            r[f"pp_dec_domain_{d}"] = dec
        rows.append(r)
    return pd.DataFrame(rows)


print("=== PPFI: the domains and the England deciles ===")
t = table()
nat, problems, basis = p.ppfi_parse([("ppfi", "", t.astype(str))])
n = nat.set_index("LSOA_CODE")
check("the combined index and five domains, not fuel poverty or socio-demographic barriers",
      sorted(c for c in nat.columns if c != "LSOA_CODE") == sorted(p.PPFI_LSOA_KEYS), f"{list(nat.columns)} {problems}")
check("England's LSOAs only", set(n.index) == set(ENG))
check("UK ranks are put in order again within England, so each English decile holds a tenth (4 of 40)",
      sorted(n["ppfi_decile"].value_counts().tolist()) == [4] * 10 and basis == "reranked", f"{basis} {n['ppfi_decile'].value_counts().to_dict()}")
check("decile 1 is the highest priority (the first English LSOAs by rank)",
      n.loc[ENG[0], "ppfi_decile"] == 1 and n.loc[ENG[-1], "ppfi_decile"] == 10)
nr, _pr, _b = p.ppfi_parse([("ppfi", "", table(reverse=True).astype(str))])
check("ranks that run the other way are read the right way round, by the published deciles",
      nr.set_index("LSOA_CODE").loc[ENG[0], "ppfi_decile"] == 1 and nr.set_index("LSOA_CODE").loc[ENG[-1], "ppfi_decile"] == 10)
dec_only = t[[c for c in t.columns if not c.startswith("rank_")]]
check("UK deciles with no ranks are refused: they do not split England into tenths, and there is nothing to re-rank",
      raises(lambda: p.ppfi_parse([("ppfi", "", dec_only.astype(str))]), "whole UK"))
eng_only = t[t["geo_code"].str.startswith("E01")].copy()
eng_only["pp_dec_combined"] = [(i * 10) // len(eng_only) + 1 for i in range(len(eng_only))]
eng_only = eng_only[["geo_code", "geo_name", "pp_dec_combined"]]
ne, _pe, be = p.ppfi_parse([("ppfi", "", eng_only.astype(str))])
check("England deciles with no ranks are used as published", be == "published" and ne["ppfi_decile"].notna().all())
check("a file with no combined index is refused, naming its columns",
      raises(lambda: p.ppfi_parse([("ppfi", "", t.drop(columns=["rank_combined", "pp_dec_combined"]).astype(str))]), "geo_code"))

print("=== PPFI: the source ===")
tmp = fresh()
patch_lookups()
p.browser_session = lambda referer=None: (_ for _ in ()).throw(AssertionError("PPFI must never be fetched"))
r = p.run_ppfi()
check("with no file: skipped, not failed, naming the ppfi-source release, CDRC's page and the login",
      r is None and "ppfi-source" in p._SOURCE_SKIPPED.get("ppfi", "") and p.PPFI_PAGE in p._SOURCE_SKIPPED["ppfi"]
      and "login" in p._SOURCE_SKIPPED["ppfi"] and "ppfi" not in p._SOURCE_ERROR)
(tmp / "cache/ppfi").mkdir(parents=True, exist_ok=True)
t.to_csv(tmp / "cache/ppfi/PPFI_v2.1.csv", index=False)
out = p.run_ppfi()
check("a file in .cache/ppfi/ is read, cut to the scope, with its version and how the deciles were made",
      out is not None and sorted(out["LSOA21CD"]) == sorted(SCOPE) and set(out["ppfi_version"]) == {"2.1"}
      and set(out["ppfi_basis"]) == {"reranked"} and str(out["ppfi_decile"].dtype) == "Int64", "" if out is None else out.to_string())
(tmp / "cache/ppfi/PPFI_v2.1.csv").unlink()
kept = p.run_ppfi()
check("with no new file, last run's figures are kept", kept is not None and len(kept) == 6
      and "stay as last written" in p._SOURCE_NOTE.get("ppfi", ""))
(tmp / "cache/ppfi/other.csv").write_text("geo_code,value\nE01000001,3\n", encoding="utf-8")
check("a file that is not PPFI fails", raises(p.run_ppfi, "combined index"))

print("=== PPFI: wards and LAs ===")
tmp = fresh()
patch_lookups()
pd.DataFrame({"LSOA21CD": SCOPE, "ppfi_decile": pd.array([1, 2, 5, 3, 10, None], dtype="Int64"),
              **{k: pd.array([1] * 6, dtype="Int64") for k in p.PPFI_LSOA_KEYS[1:]},
              "ppfi_version": ["2.1"] * 6, "ppfi_basis": ["reranked"] * 6}).to_parquet(
    tmp / "data/environment" / p.PPFI_PARQUET, index=False)
vals, notes, _c = p.places_figures(L2W, POP, what="ward")
check("a ward's share of residents in the highest-priority fifth (deciles 1 and 2): 4,000 of 5,000 = 80.0%",
      vals["W1"]["ppfi_priority_pct"] == 80.0, str(vals))
check("an LSOA with no decile is left out of the base as well (W2: 0 of 4,000)", vals["W2"]["ppfi_priority_pct"] == 0.0)
check("deciles are not averaged into wards", "ppfi_decile" not in vals["W1"])
check("the notes say how the deciles were made", notes.get("ppfi_basis") == "reranked" and notes.get("ppfi_version") == "2.1")
la, _n, _c = p.places_figures({c: "E09000001" for c in SCOPE}, POP, what="LA")
check("an LA's from all its LSOAs (4,000 of 9,000 = 44.4%)", la["E09000001"]["ppfi_priority_pct"] == 44.4, str(la))
lv = p.places_lsoa_values()
check("an LSOA carries its deciles, as whole numbers", lv[SCOPE[0]]["ppfi_decile"] == 1
      and isinstance(lv[SCOPE[0]]["ppfi_online_decile"], int) and "ppfi_decile" not in lv.get(SCOPE[5], {}))
check("the version is the period", p.places_periods().get("ppfi_priority_pct") == "version 2.1")

print("=== registered as a source ===")
check("ppfi is a source of its own", p.SOURCES.get("ppfi") is p.run_ppfi)

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
