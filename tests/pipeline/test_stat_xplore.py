"""The DWP benefits from Stat-Xplore (fetch_all_data.py, "DWP BENEFITS, FROM
STAT-XPLORE"), against a fake Stat-Xplore. Offline: requests.get and
requests.post are replaced, so nothing leaves the machine.

The fake answers in the layout of the Stat-Xplore open data API as its
published documentation describes it, as remembered when this was written:

  GET  /schema and /schema/{id}  a node: id, label, location, type (FOLDER,
                                 DATABASE, COUNT, FIELD, VALUESET, VALUE) and
                                 its children as id, label, location, type
  POST /table                    {database, measures, recodes: {field: {map:
                                 [[value ids], ...], total}}, dimensions:
                                 [[field], ...]}, answered with fields (each
                                 a uri, a label and items holding labels and
                                 the uris each item groups) and cubes (for
                                 each measure, values nested one list per
                                 field)

That layout is UNVERIFIED here: the sandbox cannot reach Stat-Xplore. The
database labels follow the fact-check notes (People on Universal Credit,
Households on Universal Credit with Child Entitlement and Limited Capability
for Work Entitlement, PIP Cases with Entitlement, and so on). The ids in the
fake are made up, in the style of the documentation's examples, and the
client never relies on them: it finds everything by label, and this test
checks it picks the right node among decoys.

Covers: no key gives "skipped" with the note and no request; the schema walk
finds the right ids by label; batching under the cell cap; mapping to LSOA
2021, including a 2011-coded dataset through the ONS lookup; rounded and
withheld cells; ward and LA figures from summed counts; ages, the payment
filter, the LCW lag and PIP by condition; the CiLIF ward vintage check; and
an error body, a refused key and a server that needs a retry.
"""
import importlib.util
import itertools
import json
import os
import pathlib
import sys
import tempfile
import urllib.parse

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
        ok = contains.lower() in str(e).lower()
        if not ok:
            print("          raised: " + str(e)[:300])
        return ok
    return False


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


BASE = p.STAT_XPLORE_API

# ---------------------------------------------------------------------------
# The scope: six LSOAs of 2021 in two wards of LA A, and one more in a ward of
# LA B. E01033769 and E01033770 were drawn in 2021 from the 2011 LSOA
# E01000004, split; E01033771 merges the 2011 E01000005 and E01000006.
# ---------------------------------------------------------------------------
LA_A, LA_B = "E09000001", "E09000002"
L2W = {"E01000001": "E05000001", "E01000002": "E05000001", "E01000003": "E05000002",
       "E01033769": "E05000002", "E01033770": "E05000002", "E01033771": "E05000002",
       "E01000007": "E05000003"}
W2L = {"E05000001": LA_A, "E05000002": LA_A, "E05000003": LA_B}
LOOKUP = {l: (w, W2L[w]) for l, w in L2W.items()}
LSOAS = sorted(L2W)
ROWS11 = [("E01000001", "E01000001", "U"), ("E01000002", "E01000002", "U"), ("E01000003", "E01000003", "U"),
          ("E01000004", "E01033769", "S"), ("E01000004", "E01033770", "S"),
          ("E01000005", "E01033771", "M"), ("E01000006", "E01033771", "M"),
          ("E01000007", "E01000007", "U")]
POP = {"E01000001": 1500, "E01000002": 1000, "E01000003": 2000, "E01033769": 1500,
       "E01033770": 500, "E01033771": 1200, "E01000007": 1800}

p.get_lsoa_ward_lookup = lambda: dict(LOOKUP)
p.get_lsoa_to_ward = lambda: dict(L2W)
p.get_lsoa11_lsoa21_rows = lambda: list(ROWS11)
p._ward_to_lad = lambda: dict(W2L)
p._census_ward_inputs = lambda: (None, None, {})
p.get_oa_to_ward = lambda: {}
p.SCOPE_LADS = {LA_A, LA_B}
p.LAD_NAMES = {LA_A: "Alpha", LA_B: "Beta"}
p._sx_sleep = lambda s: SLEEPS.append(s)
SLEEPS = []

SOURCES = list(p.SX_PARQUETS)
# Households at the 2021 Census, and the mid-year estimates: all ages, 0 to
# 15, 16 to 64, 65 and over, 66 and over.
HH = {"E01000001": 600, "E01000002": 400, "E01000003": 800, "E01033769": 600, "E01033770": 200,
      "E01033771": 500, "E01000007": 700}
SAPE = {"E01000001": (1600, 300, 1000, 300, 280), "E01000002": (1050, 200, 700, 150, 140),
        "E01000003": (2100, 400, 1400, 300, 280), "E01033769": (1550, 300, 1000, 250, 230),
        "E01033770": (520, 100, 340, 80, 75), "E01033771": (1250, 250, 800, 200, 190),
        "E01000007": (1900, 350, 1250, 300, 280)}


def fresh_dirs():
    tmp = pathlib.Path(tempfile.mkdtemp())
    p.REPO_ROOT = tmp
    p.CACHE_DIR = tmp / "cache"
    p.DATA_DIR = tmp / "data"
    p.NATIONAL_DATA_DIR = tmp / "data"
    p.MANIFEST_PATH = tmp / "data" / "meta" / "manifest.json"
    for d in ("demographics", "economy", "meta", "boundaries"):
        (tmp / "data" / d).mkdir(parents=True, exist_ok=True)
    for k in SOURCES:
        p._SOURCE_ERROR.pop(k, None)
        p._SOURCE_PARTIAL.pop(k, None)
        p._SOURCE_NOTE.pop(k, None)
    p._SX_SCHEMA.clear()
    p._SX_CATALOGUE.clear()
    # Residents (Census 2021), households (the car or van table's base) and
    # the mid-year estimates by age, as the census and sape sources write them.
    pd.DataFrame({"LSOA21CD": LSOAS, "census_population": [POP[c] for c in LSOAS]}).to_parquet(
        tmp / "data/demographics/census2021.parquet", index=False)
    pd.DataFrame({"LSOA21CD": LSOAS, "census_no_car_pct__den": [HH[c] for c in LSOAS]}).to_parquet(
        tmp / "data/demographics/census2021_counts.parquet", index=False)
    cols = ("sape_population", "sape_age0_15", "sape_age16_64", "sape_age65plus", "sape_age66plus")
    pd.DataFrame({"LSOA21CD": LSOAS, **{k: [SAPE[c][i] for c in LSOAS] for i, k in enumerate(cols)}}).to_parquet(
        tmp / "data/demographics/sape_lsoa.parquet", index=False)
    return tmp


# ---------------------------------------------------------------------------
# The fake Stat-Xplore
# ---------------------------------------------------------------------------
NODES: dict = {}
ROOT_ID = "str:folder:root"


def _brief(n):
    return {"id": n["id"], "label": n["label"], "location": BASE + "schema/" + n["id"], "type": n["type"]}


def node(ident, label, typ, children=()):
    n = {"id": ident, "label": label, "location": BASE + "schema/" + ident, "type": typ}
    if typ != "VALUE":
        n["children"] = [_brief(c) for c in children]
    NODES[ident] = n
    return n


LABEL: dict = {}
TRUTH: dict = {}          # db: {code: count}
SHARE: dict = {}          # db: {value id: share of the count}
WITHHELD: dict = {}       # db: {code: what the cell holds instead}
DATE_FACTOR: dict = {}    # value id: multiplier


def values(db, field, vs, items):
    """VALUE nodes for (label, tail) pairs under one value set."""
    out = []
    for lab, tail in items:
        vid = f"str:value:{db}:{field}:{vs}:{tail}"
        LABEL[vid] = lab
        out.append(node(vid, lab, "VALUE"))
    return out


def lsoa_set(db, codes, label="Lower Layer Super Output Areas", vs="V_C_LSOA"):
    vals = values(db, "GEO", vs, [(f"Area {c[-4:]}", c) for c in codes])
    return node(f"str:valueset:{db}:GEO:{vs}", label, "VALUESET", vals)


def geo_field(db, lsoa_vs, extra_vs=(), label="National - Regional - LA - OAs"):
    region = node(f"str:valueset:{db}:GEO:V_C_REGION", "Region", "VALUESET",
                  values(db, "GEO", "V_C_REGION", [("London", "E12000007")]))
    msoa = node(f"str:valueset:{db}:GEO:V_C_MSOA", "Middle Layer Super Output Areas", "VALUESET",
                values(db, "GEO", "V_C_MSOA", [("Area 001", "E02000001")]))
    f = node(f"str:field:{db}:GEO", label, "FIELD", [region, msoa, lsoa_vs] + list(extra_vs))
    LABEL[f["id"]] = label
    return f


def simple_field(db, name, label, items, vs_label=None):
    vs = node(f"str:valueset:{db}:{name}:V", vs_label or label, "VALUESET", values(db, name, "V", items))
    f = node(f"str:field:{db}:{name}", label, "FIELD", [vs])
    LABEL[f["id"]] = label
    return f


def count(db, label):
    m = node(f"str:count:{db}:COUNT", label, "COUNT")
    return m


def months(items):
    return [(lab, tail) for lab, tail in items]


L21 = ["E01000001", "E01000002", "E01000003", "E01000007", "E01033769", "E01033770", "E01033771", "W01000001"]
L11 = ["E01000001", "E01000002", "E01000003", "E01000004", "E01000005", "E01000006", "E01000007", "W01000001"]


def build_fake():
    NODES.clear(); LABEL.clear(); TRUTH.clear(); SHARE.clear(); WITHHELD.clear(); DATE_FACTOR.clear()
    # Universal Credit: people, households, a decoy of starts, and an older
    # copy of the people database on 2011 LSOAs that stops in 2025.
    db = "UC_Monthly"
    jcp = node(f"str:field:{db}:JCP", "Jobcentre Plus geography", "FIELD",
               [lsoa_set(db + "J", L21, "Lower Layer Super Output Areas (Jobcentre Plus)", vs="V_C_JCP")])
    geo_folder = node(f"str:folder:{db}:GEOGRAPHY", "Geography (residence-based)", "FOLDER",
                      [geo_field(db, lsoa_set(db, L21))])
    month = simple_field(db, "DATE", "Month", months([("January 2026", "202601"), ("April 2026 (r)", "202604"),
                                                       ("May 2026 (p)", "202605"), ("March 2026", "202603")]))
    uc_people = node(f"str:database:{db}", "People on Universal Credit", "DATABASE",
                     [count(db, "People on Universal Credit"), jcp, geo_folder, month])
    TRUTH[db] = {"E01000001": 120, "E01000002": 80, "E01000003": 200, "E01000007": 150,
                 "E01033769": 60, "E01033770": 30, "E01033771": 90}
    WITHHELD[db] = {"E01033770": None}          # an empty cell
    db = "UC_Monthly_old"
    old_people = node(f"str:database:{db}", "People on Universal Credit (2011 geographies, to September 2025)",
                      "DATABASE", [count(db, "People on Universal Credit"), geo_field(db, lsoa_set(db, L11)),
                                   simple_field(db, "DATE", "Month", [("September 2025", "202509")])])
    TRUTH[db] = {c: 1 for c in L11}
    db = "UC_Starts"
    starts = node(f"str:database:{db}", "Starts to Universal Credit", "DATABASE",
                  [count(db, "Starts"), geo_field(db, lsoa_set(db, L21)),
                   simple_field(db, "DATE", "Month", [("May 2026", "202605")])])
    db = "UC_Households"
    hh_month = simple_field(db, "DATE", "Month", [("February 2025", "202502"), ("May 2025", "202505"),
                                                   ("August 2025", "202508"), ("November 2025", "202511"),
                                                   ("February 2026", "202602"), ("May 2026", "202605")])
    child = simple_field(db, "CHILD", "Child Entitlement", [("Child Entitlement", "1"), ("No Child Entitlement", "0")])
    dchild = simple_field(db, "DCHILD", "Disabled Child Entitlement",
                          [("Disabled Child Entitlement", "1"), ("No Disabled Child Entitlement", "0")])
    lcw = simple_field(db, "LCW", "Limited Capability for Work Entitlement",
                       [("No LCW/LCWRA Entitlement", "0"), ("LCW Entitlement", "1"), ("LCWRA Entitlement", "2"),
                        ("Unknown or missing", "9")])
    pay = simple_field(db, "PAY", "Household Payment Status", [("In payment", "1"), ("Not in payment", "0")])
    uc_hh = node(f"str:database:{db}", "Households on Universal Credit", "DATABASE",
                 [count(db, "Households on Universal Credit"), geo_field(db, lsoa_set(db, L21)), hh_month,
                  dchild, child, lcw, pay])
    TRUTH[db] = {"E01000001": 100, "E01000002": 50, "E01000003": 200, "E01000007": 150,
                 "E01033769": 50, "E01033770": 100, "E01033771": 150}
    SHARE[db] = {f"str:value:{db}:CHILD:V:1": 0.4, f"str:value:{db}:CHILD:V:0": 0.6,
                 f"str:value:{db}:LCW:V:0": 0.6, f"str:value:{db}:LCW:V:1": 0.1,
                 f"str:value:{db}:LCW:V:2": 0.2, f"str:value:{db}:LCW:V:9": 0.1,
                 f"str:value:{db}:PAY:V:1": 0.9, f"str:value:{db}:PAY:V:0": 0.1}
    DATE_FACTOR[f"str:value:{db}:DATE:V:202505"] = 0.8
    uc = node("str:folder:fuc", "Universal Credit", "FOLDER", [starts, old_people, uc_people, uc_hh])

    # PIP: registrations (a decoy) and cases with entitlement, by condition.
    db = "PIP_Monthly_new"
    cond_specific = simple_field(db, "SPEC", "Main Disabling Condition (specific)",
                                 [("Psychiatric disorder: anxiety", "11"), ("Back pain", "21")])
    cond = simple_field(db, "COND", "Main Disabling Condition",
                        [("Psychiatric disorder", "1"), ("Musculoskeletal disease (general)", "2"),
                         ("Musculoskeletal disease (regional)", "3"), ("Neurological disease", "4"),
                         ("Respiratory disease", "5"), ("Other", "9")], vs_label="Disability Category")
    pip = node(f"str:database:{db}", "PIP Cases with Entitlement", "DATABASE",
               [count(db, "PIP Cases with Entitlement"), geo_field(db, lsoa_set(db, L21)),
                simple_field(db, "DATE", "Month", [("April 2026", "202604"), ("July 2026", "202607")]),
                cond_specific, cond])
    TRUTH[db] = {"E01000001": 100, "E01000002": 60, "E01000003": 150, "E01000007": 90,
                 "E01033769": 40, "E01033770": 20, "E01033771": 50}
    SHARE[db] = {f"str:value:{db}:COND:V:1": 0.4, f"str:value:{db}:COND:V:2": 0.15,
                 f"str:value:{db}:COND:V:3": 0.05, f"str:value:{db}:COND:V:4": 0.1,
                 f"str:value:{db}:COND:V:5": 0.1, f"str:value:{db}:COND:V:9": 0.2}
    WITHHELD[db] = {"E01000002": ".."}           # marked as too small to show
    reg = node("str:database:PIP_Registrations", "PIP Registrations", "DATABASE", [])
    pipf = node("str:folder:fpip", "Personal Independence Payment", "FOLDER", [reg, pip])

    # DLA, in payment and with entitlement: the entitled one is preferred.
    dla_dbs = []
    for db, lab in (("DLA_In_Payment_New", "DLA Cases in Payment"), ("DLA_Entitled_New", "DLA Cases with Entitlement")):
        dla_dbs.append(node(f"str:database:{db}", lab, "DATABASE",
                            [count(db, lab), geo_field(db, lsoa_set(db, L21)),
                             simple_field(db, "DATE", "Quarter", [("March 2025", "202503"), ("March 2026", "202603")]),
                             simple_field(db, "AGE", "Age of claimant (bands)",
                                          [("0-4", "a"), ("5-10", "b"), ("11-15", "c"), ("16-24", "d"),
                                           ("25-49", "e"), ("50-64", "f"), ("65 and over", "g"), ("Unknown age", "u")])]))
        TRUTH[db] = {c: 10 * (i + 1) for i, c in enumerate(LSOAS)}
        SHARE[db] = {f"str:value:{db}:AGE:V:{t}": s for t, s in
                     (("a", 0.05), ("b", 0.1), ("c", 0.1), ("d", 0.1), ("e", 0.3), ("f", 0.2), ("g", 0.1), ("u", 0.05))}
    dla = node("str:folder:fdla", "Disability Living Allowance", "FOLDER", dla_dbs)

    # Attendance Allowance on 2011 LSOAs, the label silent about the vintage,
    # dated in DWP's short style ("Mar-26").
    db = "AA_Entitled_New"
    aa = node(f"str:database:{db}", "Attendance Allowance Cases with Entitlement", "DATABASE",
              [count(db, "AA Cases with Entitlement"), geo_field(db, lsoa_set(db, L11)),
               simple_field(db, "DATE", "Quarter", [("Mar-25", "x1"), ("Mar-26", "x2")])])
    TRUTH[db] = {"E01000001": 50, "E01000002": 30, "E01000003": 60, "E01000004": 40, "E01000005": 12,
                 "E01000006": 8, "E01000007": 45, "W01000001": 5}
    aaf = node("str:folder:faa", "Attendance Allowance", "FOLDER", [aa])

    # Carer's Allowance: in payment and with entitlement; only in payment fits.
    ca_dbs = []
    for db, lab, n in (("CA_Entitled", "Carer's Allowance Cases with Entitlement", 99),
                       ("CA_In_Payment", "Carer's Allowance Cases in Payment", 1)):
        ca_dbs.append(node(f"str:database:{db}", lab, "DATABASE",
                           [count(db, lab), geo_field(db, lsoa_set(db, L21)),
                            simple_field(db, "DATE", "Quarter", [("March 2026", "202603")])]))
        TRUTH[db] = {c: n * 25.0 for c in LSOAS}
    TRUTH["CA_In_Payment"]["E01000003"] = "1,234"   # a count written with a comma
    caf = node("str:folder:fca", "Carer's Allowance", "FOLDER", ca_dbs)

    # Pension Credit, dated only in its value ids.
    db = "PC_New"
    pc = node(f"str:database:{db}", "Pension Credit Claimants", "DATABASE",
              [count(db, "Pension Credit Claimants"), geo_field(db, lsoa_set(db, L21)),
               simple_field(db, "DATE", "Month", [("Latest", "202603"), ("Earlier", "202503")])])
    TRUTH[db] = {"E01000001": 28, "E01000002": 14.0, "E01000003": 28, "E01000007": 30,
                 "E01033769": 23, "E01033770": 7, "E01033771": 19}
    pcf = node("str:folder:fpc", "Pension Credit", "FOLDER", [pc])

    # Children in low income families: relative and absolute, by ward.
    cil = []
    for db, lab in (("CILIF_REL", "Relative Low Income"), ("CILIF_ABS", "Absolute Low Income")):
        wards = values(db, "GEO", "V_C_WARD", [("Ward one", "E05000001"), ("Ward two", "E05000002"),
                                               ("Old ward three", "E05000099")])
        wvs = node(f"str:valueset:{db}:GEO:V_C_WARD", "Ward (2024)", "VALUESET", wards)
        la = node(f"str:valueset:{db}:GEO:V_C_LA", "Local Authority", "VALUESET",
                  values(db, "GEO", "V_C_LA", [("Alpha", LA_A)]))
        g = node(f"str:field:{db}:GEO", "Geography", "FIELD", [la, wvs])
        cil.append(node(f"str:database:{db}", lab, "DATABASE",
                        [count(db, "Number of children"), g,
                         simple_field(db, "DATE", "Year", [("FYE 2024", "2024"), ("FYE 2025 (p)", "2025")]),
                         simple_field(db, "AGE", "Age of Child (bands)",
                                      [("0-4", "a"), ("5-10", "b"), ("11-15", "c"), ("16-19", "d")])]))
        TRUTH[db] = {"E05000001": 100, "E05000002": 200, "E05000099": 50}
        SHARE[db] = {f"str:value:{db}:AGE:V:{t}": s for t, s in (("a", 0.3), ("b", 0.3), ("c", 0.2), ("d", 0.2))}
    cf = node("str:folder:fcilif", "Children in Low Income Families", "FOLDER", cil)

    NODES[ROOT_ID] = {"id": ROOT_ID, "label": "Root", "location": BASE + "schema", "type": "FOLDER",
                      "children": [_brief(x) for x in (uc, pipf, dla, aaf, caf, pcf, cf)]}


class Resp:
    def __init__(self, code, body, headers=None, text=None):
        self.status_code = code
        self._body = body
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


LOG = []                 # every table request: db, field ids, groups per field, cells, date
GETS = []
CAP = [10 ** 9]          # the fake's own cell cap
FAULTS = {}              # "table": a list of responses to give first


def fake_get(url, headers=None, timeout=None, **kw):
    GETS.append(url)
    if (headers or {}).get("APIKey") != "test-key":
        return Resp(401, {"message": "Invalid API key"})
    rest = url[len(BASE):]
    ident = ROOT_ID if rest == "schema" else urllib.parse.unquote(rest[len("schema/"):])
    n = NODES.get(ident)
    if n is None:
        return Resp(404, {"message": f"No such schema item {ident}"})
    return Resp(200, n)


def fake_post(url, headers=None, json=None, timeout=None, **kw):
    if (headers or {}).get("APIKey") != "test-key":
        return Resp(401, {"message": "Invalid API key"})
    if FAULTS.get("table"):
        return FAULTS["table"].pop(0)
    q = json
    db = q["database"].split(":")[-1]
    dims = [d[0] for d in q["dimensions"]]
    groups = [q["recodes"][f]["map"] for f in dims]
    n_cells = 1
    for g in groups:
        n_cells *= len(g)
    LOG.append({"db": db, "dims": dims, "groups": [len(g) for g in groups], "cells": n_cells,
                "date": groups[1][0][0], "geo": len(groups[0])})
    if n_cells > CAP[0]:
        return Resp(400, {"message": f"The table would have {n_cells} cells, more than allowed"})

    def cell(idx):
        code = groups[0][idx[0]][0].rsplit(":", 1)[-1]
        if code in WITHHELD.get(db, {}):
            return WITHHELD[db][code]
        v = TRUTH[db].get(code)
        if isinstance(v, str):
            return v
        if v is None:
            return None
        for d in range(2, len(dims)):
            v *= sum(SHARE.get(db, {}).get(x, 1.0) for x in groups[d][idx[d]])
        return v * DATE_FACTOR.get(groups[1][idx[1]][0], 1.0)

    def nest(d, idx):
        if d == len(dims):
            return cell(idx)
        return [nest(d + 1, idx + [i]) for i in range(len(groups[d]))]
    fields = [{"uri": f, "label": LABEL.get(f, f),
               "items": [{"type": "RECODE", "labels": [LABEL.get(u, u) for u in g], "uris": g} for g in groups[d]]}
              for d, f in enumerate(dims)]
    m = q["measures"][0]
    return Resp(200, {"query": q, "database": {"id": q["database"]}, "measures": [{"id": m}],
                      "fields": fields, "cubes": {m: {"values": nest(0, [])}}})


def no_network(*a, **k):
    raise AssertionError("a request was made with no key")


# ---------------------------------------------------------------------------
print("=== no key: every source skipped, with the note, and nothing asked ===")
tmp = fresh_dirs()
os.environ.pop(p.STAT_XPLORE_KEY_ENV, None)
p.requests.get, p.requests.post = no_network, no_network
outs = {s: p.SOURCES[s]() for s in SOURCES}
check("all eight Stat-Xplore sources are registered", all(s in p.SOURCES for s in SOURCES), str(SOURCES))
check("with no key each returns no frame", all(v is None for v in outs.values()), str(outs))
check("and each says why, naming the secret to add",
      all(p.STAT_XPLORE_KEY_ENV in p._SOURCE_NOTE.get(s, "") and "HANDOVER.md" in p._SOURCE_NOTE.get(s, "")
          for s in SOURCES), str({s: p._SOURCE_NOTE.get(s) for s in SOURCES}))
p.export_all = lambda: None
sys.argv = ["x", "--only"] + SOURCES
p.main()
man = json.loads(p.MANIFEST_PATH.read_text())["sources"]
check("the manifest marks every one skipped, not failed, so no pack is held up",
      all(man[s]["status"] == "skipped" and not man[s]["error"] for s in SOURCES),
      str({s: man[s]["status"] for s in SOURCES}))
check("and its notes say there is no Stat-Xplore API key",
      all("no Stat-Xplore API key" in man[s]["notes"] for s in SOURCES), man["dwp_uc_people"]["notes"])
check("nothing was written", not any((tmp / "data" / "economy").iterdir()))

# ---------------------------------------------------------------------------
print("=== small pieces: periods, cells, ages, codes, vintage ===")
P = p.statxplore_period
check("dates in DWP's styles are read",
      P("May 2026 (p)") == (2026, 5, "month") and P("Mar-26") == (2026, 3, "month") and P("202605") == (2026, 5, "month")
      and P("FYE 2025 (p)") == (2025, 3, "fy") and P("2024/25") == (2025, 3, "fy") and P("2026 Q1") == (2026, 3, "month")
      and P("Latest", "str:value:x:DATE:V:202603") == (2026, 3, "month") and P("Total") is None)
check("periods read as the map says them",
      p.statxplore_period_label((2026, 5, "month")) == "May 2026" and p.statxplore_period_label((2025, 3, "fy")) == "2024/25")
v, per = p.statxplore_pick_period([{"id": "a", "label": "February 2025"}, {"id": "b", "label": "May 2026"},
                                   {"id": "c", "label": "May 2025"}, {"id": "d", "label": "Total"}])
v12, per12 = p.statxplore_pick_period([{"id": "a", "label": "February 2025"}, {"id": "b", "label": "May 2026"},
                                       {"id": "c", "label": "May 2025"}], 12)
check("the newest month is taken, not the last listed", v["id"] == "b")
check("with a lag of 12 months, the newest month a year or more older", v12["id"] == "c" and per12[:2] == (2025, 5))
C = p.statxplore_cell
check("a cell is a count as published (rounded counts are used as they are)",
      C(12) == 12.0 and C(12.0) == 12.0 and C("15") == 15.0 and C("1,234") == 1234.0 and C({"value": 7}) == 7.0)
check("an empty or marked cell is no figure, never 0",
      C(None) is None and C("..") is None and C("*") is None and C("low") is None and C(float("nan")) is None
      and C(True) is None)
ages = [{"id": i, "label": l} for i, l in (("a", "0-4"), ("b", "5 to 10"), ("c", "11-15"), ("d", "16-24"),
                                            ("e", "65 and over"), ("u", "Unknown"))]
ids, prob = p.statxplore_ages_upto(ages, 15)
check("ages 0 to 15 from DWP's bands", ids == ["a", "b", "c"] and not prob, prob)
check("a band across 15 is refused, not cut",
      p.statxplore_ages_upto([{"id": "a", "label": "0-9"}, {"id": "b", "label": "10-19"}], 15)[0] == [])
check("bands that leave an age out are refused",
      p.statxplore_ages_upto([{"id": "a", "label": "0-4"}, {"id": "c", "label": "11-15"}], 15)[0] == [])
check("Under 16 is ages 0 to 15", p.statxplore_age_band("Under 16") == (0, 15))
check("the LSOA code is read from the end of the value id",
      p.statxplore_code({"id": "str:value:UC:GEO:V_C_LSOA:E01000001", "label": "City of London 001A"}, "lsoa") == "E01000001")
check("a set with a code first issued in 2021 is a 2021 set; one without is 2011",
      p.statxplore_lsoa_vintage("Lower Layer Super Output Areas", ["E01000001", "E01033769"]) == "2021"
      and p.statxplore_lsoa_vintage("Lower Layer Super Output Areas (2021)", ["E01000001", "E01000004"]) == "2011")
split = p.counts11_to_lsoa21({"E01000004": 40.0, "E01000005": 12.0, "E01000006": 8.0, "E01000001": 5.0},
                             ROWS11[:7], POP)
check("2011 counts move onto 2021 LSOAs: a split shared by residents, a merge added up, a missing one blank",
      approx(split["E01033769"], 30) and approx(split["E01033770"], 10) and approx(split["E01033771"], 20)
      and split["E01000001"] == 5.0 and split["E01000002"] is None, str(split))
check("a withheld 2011 cell leaves the merged 2021 LSOA blank, not part of a sum",
      p.counts11_to_lsoa21({"E01000005": 12.0, "E01000006": None}, ROWS11, POP)["E01033771"] is None)
check("an error body is told from an answer",
      p.statxplore_error_text({"message": "Invalid query"}) == "Invalid query"
      and p.statxplore_error_text({"errors": [{"code": 1}]}) != ""
      and p.statxplore_error_text({"id": "str:folder:root", "children": []}) == ""
      and p.statxplore_error_text({"cubes": {}, "fields": []}) == "")

# ---------------------------------------------------------------------------
print("=== the schema walk: the right ids by label ===")
tmp = fresh_dirs()
build_fake()
os.environ[p.STAT_XPLORE_KEY_ENV] = "test-key"
p.requests.get, p.requests.post = fake_get, fake_post
sx = p.StatXplore("test-key", source="dwp_uc_people")
dbs = p.statxplore_databases(sx)
check("the walk finds every database in every folder", len(dbs) == 14, str([d["label"] for d in dbs]))
ds = p.statxplore_dataset(sx, **p.SX_DATABASES["dwp_uc_people"])
check("people on Universal Credit: the current database, not the 2011 copy or the starts",
      ds["db"] == "str:database:UC_Monthly", ds["db"])
check("its count measure", ds["measure"] == "str:count:UC_Monthly:COUNT")
check("the newest month, May 2026, not the last one listed",
      ds["date_value"] == "str:value:UC_Monthly:DATE:V:202605" and ds["period_label"] == "May 2026", ds["period_label"])
check("the LSOA level of the residence-based geography, not the Jobcentre Plus one",
      ds["geo_field"] == "str:field:UC_Monthly:GEO" and ds["geo_valueset"]["id"] == "str:valueset:UC_Monthly:GEO:V_C_LSOA",
      ds["geo_field"] + " " + ds["geo_valueset"]["id"])
picks = {}
for s in SOURCES:
    d = p.statxplore_dataset(p.StatXplore("test-key", source=s), **p.SX_DATABASES[s])
    picks[s] = d["db"].split(":")[-1]
check("each benefit finds its own database",
      picks == {"dwp_uc_people": "UC_Monthly", "dwp_uc_households": "UC_Households", "dwp_pip": "PIP_Monthly_new",
                "dwp_dla_children": "DLA_Entitled_New", "dwp_attendance_allowance": "AA_Entitled_New",
                "dwp_carers_allowance": "CA_In_Payment", "dwp_pension_credit": "PC_New", "dwp_cilif": "CILIF_REL"},
      str(picks))
n_gets = len(GETS)
p.statxplore_dataset(p.StatXplore("test-key", source="dwp_pip"), **p.SX_DATABASES["dwp_pip"])
check("the catalogue and schema answers are fetched once a run, for every source", len(GETS) == n_gets,
      f"{len(GETS) - n_gets} more")
check("a database nobody publishes is a clear failure naming the nearest",
      raises(lambda: p.statxplore_dataset(sx, what="Universal Credit sanctions", want=(r"universal credit", r"sanction")),
             contains="no Stat-Xplore database is labelled as Universal Credit sanctions"))

# ---------------------------------------------------------------------------
print("=== Universal Credit people: batches, LSOAs, withheld cells ===")
p.SX_GEO_BATCH, p.SX_MAX_CELLS = 3, 4
LOG.clear()
df = p.run_dwp_uc_people()
reqs = [r for r in LOG if r["db"] == "UC_Monthly"]
check("the scope's seven LSOAs go in batches of at most three", [r["geo"] for r in reqs] == [3, 3, 1],
      str([r["geo"] for r in reqs]))
check("every request stays under the cell cap", all(r["cells"] <= 4 for r in reqs))
check("one row per LSOA of 2021 in scope", sorted(df["LSOA21CD"]) == LSOAS, str(list(df["LSOA21CD"])))
r1 = df.set_index("LSOA21CD")
check("counts as Stat-Xplore gave them", r1.loc["E01000001", "uc_people"] == 120 and r1.loc["E01033769", "uc_people"] == 60)
check("the withheld cell is no figure (NaN), not 0", pd.isna(r1.loc["E01033770", "uc_people"]))
check("the period is written with the counts", set(df["uc_people_period"]) == {"May 2026"})
check("the vintage is 2021", set(df["lsoa_vintage"]) == {"2021"})
check("written to data/economy", (tmp / "data/economy" / p.SX_PARQUETS["dwp_uc_people"]).exists())

# ---------------------------------------------------------------------------
print("=== Universal Credit households: payment filter, children, LCW a year back ===")
p.SX_GEO_BATCH, p.SX_MAX_CELLS = 1000, 5000
LOG.clear()
dh = p.run_dwp_uc_households().set_index("LSOA21CD")
hh_reqs = [r for r in LOG if r["db"] == "UC_Households"]
check("two tables: children at the newest month, LCW twelve months earlier",
      [r["date"].rsplit(":", 1)[-1] for r in hh_reqs] == ["202605", "202505"], str([r["date"] for r in hh_reqs]))
check("both filtered to households in payment, and split in two (the group and the rest)",
      all(r["groups"][2] == 1 and r["groups"][3] == 2 for r in hh_reqs), str([r["groups"] for r in hh_reqs]))
check("households in payment: 90% of 100", approx(dh.loc["E01000001", "uc_hh"], 90))
check("with children: 40% of them", approx(dh.loc["E01000001", "uc_hh_children"], 36))
check("LCW and LCWRA, not 'No LCW/LCWRA' or unknown: 30% of May 2025's 72",
      approx(dh.loc["E01000001", "uc_hh_lcw"], 0.9 * 100 * 0.8 * 0.3), str(dh.loc["E01000001", "uc_hh_lcw"]))
check("the LCW period says which month", set(dh["uc_hh_lcw_period"]) == {"May 2025"} and set(dh["uc_hh_period"]) == {"May 2026"})
check("ok, with nothing missing", "dwp_uc_households" not in p._SOURCE_ERROR, p._SOURCE_ERROR.get("dwp_uc_households", ""))

# ---------------------------------------------------------------------------
print("=== PIP, and PIP by main condition ===")
p.SX_MAX_CELLS = 8
LOG.clear()
dp = p.run_dwp_pip().set_index("LSOA21CD")
cond_reqs = [r for r in LOG if r["db"] == "PIP_Monthly_new" and len(r["groups"]) == 3]
check("the condition table is split four ways (three conditions and the rest), two LSOAs a request",
      cond_reqs and all(r["groups"][2] == 4 and r["geo"] <= 2 and r["cells"] <= 8 for r in cond_reqs),
      str([(r["groups"], r["geo"]) for r in cond_reqs]))
check("PIP at July 2026", set(dp["pip_period"]) == {"July 2026"} and dp.loc["E01000001", "pip"] == 100)
check("psychiatric, both musculoskeletal groups together, and neurological",
      approx(dp.loc["E01000001", "pip_psych"], 40) and approx(dp.loc["E01000001", "pip_msk"], 20)
      and approx(dp.loc["E01000001", "pip_neuro"], 10))
check("the cell marked '..' is no figure", pd.isna(dp.loc["E01000002", "pip"]) and pd.isna(dp.loc["E01000002", "pip_psych"]))
check("the 'specific' condition field was passed over", "pip_psych" in dp.columns)
p.SX_MAX_CELLS = 5000

# ---------------------------------------------------------------------------
print("=== DLA children, Attendance Allowance (2011 LSOAs), Carer's Allowance, Pension Credit ===")
LOG.clear()
dd = p.run_dwp_dla_children().set_index("LSOA21CD")
dla_req = [r for r in LOG if r["db"] == "DLA_Entitled_New"][0]
check("DLA: one age filter making 0 to 15 (three bands in one group)", dla_req["groups"][2:] == [1])
check("children aged 0 to 15 are 25% of the count", approx(dd.loc["E01000001", "dla_child"], 10 * 0.25))
da = p.run_dwp_attendance_allowance().set_index("LSOA21CD")
check("Attendance Allowance is read as a 2011 set from its codes", set(da["lsoa_vintage"]) == {"2011"})
check("and moved onto 2021 LSOAs: the split by residents, the merge added up, unchanged ones as they are",
      approx(da.loc["E01033769", "aa"], 30) and approx(da.loc["E01033770", "aa"], 10)
      and approx(da.loc["E01033771", "aa"], 20) and da.loc["E01000001", "aa"] == 50, str(da["aa"].to_dict()))
check("dated from 'Mar-26'", set(da["aa_period"]) == {"March 2026"})
aa_req = [r for r in LOG if r["db"] == "AA_Entitled_New"][0]
check("the 2011 set is asked for the 2011 LSOAs the scope's are drawn from, not Wales", aa_req["geo"] == 7)
dc = p.run_dwp_carers_allowance().set_index("LSOA21CD")
check("Carer's Allowance in payment, not with entitlement", dc.loc["E01000001", "ca"] == 25.0)
check("a count written with a comma is read", dc.loc["E01000003", "ca"] == 1234.0)
dpc = p.run_dwp_pension_credit().set_index("LSOA21CD")
check("Pension Credit dated from its value ids", set(dpc["pc_period"]) == {"March 2026"} and dpc.loc["E01000002", "pc"] == 14.0)

# ---------------------------------------------------------------------------
print("=== children in relative low income families: wards, if they are the map's ===")
dw = p.run_dwp_cilif()
check("the wards of LA A, whose 2025 wards are all in DWP's list", sorted(dw["WD25CD"]) == ["E05000001", "E05000002"])
check("ages 0 to 15 only: 80% of the count", approx(dw.set_index("WD25CD").loc["E05000001", "cilif_rel_0_15"], 80))
check("LA B, whose ward is not in DWP's list, is left out and named",
      "Beta" in p._SOURCE_NOTE.get("dwp_cilif", ""), p._SOURCE_NOTE.get("dwp_cilif", ""))
check("the financial year", set(dw["cilif_period"]) == {"2024/25"})

# ---------------------------------------------------------------------------
print("=== ward, LA and LSOA figures ===")
vw, nw = p.small_area_figures(L2W, POP, what="ward")
to_la = {lc: lad for lc, (_w, lad) in LOOKUP.items()}
vl, _ = p.small_area_figures(to_la, POP, what="borough")
w1, w2, la = vw.get("E05000001", {}), vw.get("E05000002", {}), vl.get(LA_A, {})
check("UC people for a ward: its LSOAs' people over their residents aged 16 to 64 (200 / 1,700)",
      w1.get("dwp_uc_people_pct") == round(100 * 200 / 1700, 1), str(w1))
check("a ward with a withheld LSOA leaves it out of count and base, when the rest hold four in five of the base",
      w2.get("dwp_uc_people_pct") == round(100 * (200 + 60 + 90) / (1400 + 1000 + 800), 1), str(w2))
check("UC households over Census 2021 households", w1.get("dwp_uc_hh_pct") == round(100 * (90 + 45) / 1000, 1))
check("UC households with children over UC households", w1.get("dwp_uc_hh_children_pct") == 40.0)
check("LCW households over all households", w1.get("dwp_uc_hh_lcw_pct") == round(100 * 0.72 * 0.3 * 150 / 1000, 1),
      str(w1.get("dwp_uc_hh_lcw_pct")))
check("PIP: a ward whose LSOAs with a count hold under four in five of its base gets no figure",
      "dwp_pip_pct" not in w1, str(w1))
check("PIP for ward two (all LSOAs counted)",
      w2.get("dwp_pip_pct") == round(100 * (150 + 40 + 20 + 50) / (1700 + 1250 + 420 + 1000), 1), str(w2))
check("DLA children over children aged 0 to 15",
      w2.get("dwp_dla_child_pct") == round(100 * 0.25 * (30 + 50 + 60 + 70) / (400 + 300 + 100 + 250), 1), str(w2))
check("Attendance Allowance over residents aged 66 and over, after the 2011 to 2021 move",
      w2.get("dwp_aa_pct") == round(100 * (60 + 30 + 10 + 20) / (280 + 230 + 75 + 190), 1), str(w2))
check("Pension Credit claims per 100 residents aged 66 and over",
      w1.get("dwp_pension_credit_per_100") == round(100 * 42 / 420, 1), str(w1))
check("children in low income families for a ward: DWP's count over the children of its LSOAs",
      w1.get("dwp_cilif_pct") == round(100 * 80 / 500, 1) and w2.get("dwp_cilif_pct") == round(100 * 160 / 1050, 1))
check("an LA from all its LSOAs, counts added up before dividing",
      la.get("dwp_uc_people_pct") == round(100 * (120 + 80 + 200 + 60 + 90) / (1000 + 700 + 1400 + 1000 + 800), 1)
      and la.get("dwp_cilif_pct") == round(100 * 240 / 1550, 1), str(la))
check("no low income figure for LA B, whose wards are another vintage", "dwp_cilif_pct" not in vl.get(LA_B, {}))
check("the ward metadata names each DWP source", set(nw.get("dwp", {})) == set(SOURCES), str(nw.get("dwp")))
lv = p.small_area_lsoa_values(POP)
check("Universal Credit for LSOAs", lv["E01000001"].get("dwp_uc_people_pct") == 12.0
      and lv["E01000001"].get("dwp_uc_hh_pct") == 15.0 and lv["E01000001"].get("dwp_uc_hh_children_pct") == 40.0)
check("but not a withheld one, nor the benefits shown for wards and LAs only",
      "dwp_uc_people_pct" not in lv.get("E01033770", {}) and not any(
          k in lv["E01000001"] for k in ("dwp_pip_pct", "dwp_aa_pct", "dwp_dla_child_pct", "dwp_cilif_pct")))
per = p.small_area_periods()
check("the periods for indicators.js", per.get("dwp_uc_people_pct") == "May 2026" and per.get("dwp_uc_hh_lcw_pct") == "May 2025"
      and per.get("dwp_pip_pct") == "July 2026" and per.get("dwp_aa_pct") == "March 2026"
      and per.get("dwp_cilif_pct") == "2024/25", str(per))
(tmp / "data/demographics/sape_lsoa.parquet").unlink()
vw2, _ = p.small_area_figures(L2W, POP, what="ward")
check("without the mid-year estimates the age-based rates are left out, the household ones kept",
      "dwp_uc_people_pct" not in vw2["E05000001"] and "dwp_aa_pct" not in vw2["E05000001"]
      and vw2["E05000001"].get("dwp_uc_hh_pct") == w1.get("dwp_uc_hh_pct"))

# ---------------------------------------------------------------------------
print("=== what DWP may not publish ===")
tmp = fresh_dirs()
build_fake()
NODES["str:database:UC_Households"]["children"] = [c for c in NODES["str:database:UC_Households"]["children"]
                                                    if "LCW" not in c["id"]]
dh2 = p.run_dwp_uc_households()
check("UC households without the LCW field: written, and the source marked failed saying so",
      "uc_hh" in dh2.columns and "uc_hh_lcw" not in dh2.columns
      and "Limited Capability for Work" in p._SOURCE_ERROR.get("dwp_uc_households", ""))
NODES["str:database:PIP_Monthly_new"]["children"] = [c for c in NODES["str:database:PIP_Monthly_new"]["children"]
                                                     if "COND" not in c["id"] and "SPEC" not in c["id"]]
p._SX_SCHEMA.clear()
dp2 = p.run_dwp_pip()
check("PIP without a condition field: PIP written, a note rather than a failure",
      "pip" in dp2.columns and "pip_psych" not in dp2.columns and "dwp_pip" not in p._SOURCE_ERROR
      and "condition" in p._SOURCE_NOTE.get("dwp_pip", ""))
for db in ("CILIF_REL",):
    vs = NODES[f"str:valueset:{db}:GEO:V_C_WARD"]
    vs["children"] = [c for c in vs["children"] if c["id"].endswith("E05000099")]
p._SX_SCHEMA.clear()
check("CiLIF on an older ward vintage than the map's: skipped with the reason, not forced",
      p.run_dwp_cilif() is None and "older ward vintage" in p._SOURCE_NOTE.get("dwp_cilif", ""),
      p._SOURCE_NOTE.get("dwp_cilif", ""))
NODES["str:database:DLA_Entitled_New"]["children"] = [c for c in NODES["str:database:DLA_Entitled_New"]["children"]
                                                      if "AGE" not in c["id"]]
NODES["str:database:DLA_In_Payment_New"]["children"] = [c for c in NODES["str:database:DLA_In_Payment_New"]["children"]
                                                        if "AGE" not in c["id"]]
p._SX_SCHEMA.clear()
check("DLA with no age field fails, rather than counting adults as children",
      raises(p.run_dwp_dla_children, contains="no age field"))

# ---------------------------------------------------------------------------
print("=== errors ===")
tmp = fresh_dirs()
build_fake()
FAULTS["table"] = [Resp(200, {"message": "Query is not valid: unknown field"})]
check("an error body with HTTP 200 is a clear failure, with its message",
      raises(p.run_dwp_uc_people, contains="Query is not valid"))
FAULTS["table"] = [Resp(400, {"errors": [{"message": "Too many cells"}]})]
check("an HTTP 400 is a failure with its message", raises(p.run_dwp_uc_people, contains="Too many cells"))
FAULTS["table"] = [Resp(200, None, text="<html>maintenance</html>")]
check("a body that is not JSON is a failure", raises(p.run_dwp_uc_people, contains="not JSON"))
FAULTS["table"] = [Resp(200, {"fields": [], "query": {}})]
check("an answer without cubes is a failure", raises(p.run_dwp_uc_people, contains="no cubes"))
SLEEPS.clear()
FAULTS["table"] = [Resp(429, {"message": "Too many requests"}, headers={"Retry-After": "7"}),
                   Resp(503, {"message": "busy"})]
out = p.run_dwp_uc_people()
check("a 429 and a 503 are retried, waiting as asked, and the run goes on",
      out is not None and len(out) == 7 and 7.0 in SLEEPS, str(SLEEPS[:6]))
FAULTS["table"] = [Resp(503, {})] * p.SX_ATTEMPTS
check("a server that never answers fails after the attempts", raises(p.run_dwp_uc_people, contains="did not answer"))
FAULTS.clear()
os.environ[p.STAT_XPLORE_KEY_ENV] = "wrong-key"
p._SX_SCHEMA.clear(); p._SX_CATALOGUE.clear()
check("a refused key is a failure naming the secret", raises(p.run_dwp_uc_people, contains="STAT_XPLORE_API_KEY"))
os.environ[p.STAT_XPLORE_KEY_ENV] = "test-key"
sys.argv = ["x", "--only", "dwp_uc_people"]
FAULTS["table"] = [Resp(200, {"message": "Query is not valid"})]
p.main()
m = json.loads(p.MANIFEST_PATH.read_text())["sources"]["dwp_uc_people"]
check("in the manifest an error body is 'failed' with the message", m["status"] == "failed" and "Query is not valid" in m["error"],
      str(m))
FAULTS.clear()

# ---------------------------------------------------------------------------
print("=== registered, and the workflows pass the key ===")
order = list(p.SOURCES)
check("each DWP source has its own manifest entry, after sape",
      all(order.index(s) > order.index("sape") for s in SOURCES), str(order))
check("SAPE adds the 66 and over band the pension-age bases use", p.SAPE_BANDS.get("sape_age66plus") == (66, None))
for wf in ("refresh-data.yml", "build-region.yml"):
    t = (REPO / ".github" / "workflows" / wf).read_text()
    check(f"{wf} passes the secret to the pipeline step",
          "STAT_XPLORE_API_KEY: ${{ secrets.STAT_XPLORE_API_KEY }}" in t)

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
