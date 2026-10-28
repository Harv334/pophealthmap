"""PCN membership from NHS South, Central and West CSU's list, and catchments.

The practices in each primary care network come from SCW's PCN footprints
download (data/curated/pcn_membership_scw.csv), which follows NHS England's
current list; a practice it does not list keeps its Fingertips network.
Fingertips names win over SCW's capitals, the "Not in a PCN" row goes to
Fingertips' U99999, and that group is never a catchment or a footprint.
run_pcn keeps working from SCW's list alone when Fingertips cannot be read.

Offline: Fingertips is a stand-in, the registrations are a small zip in the
layout NHS Digital publishes (an all-persons file beside the male and female
files), and the LSOAs are squares.

    python3 tests/pipeline/test_pcn_membership.py
"""
import json
import pathlib
import sys
import tempfile
import zipfile

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import fetch_all_data as F  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


tmp = pathlib.Path(tempfile.mkdtemp())

print("=== reading SCW's list ===")
csv = tmp / "scw.csv"
csv.write_text(
    "﻿pcn_name,majority_pcn_code,partner_gps_names,partner_gps_orgcodes,region_code,region_name,icb_code,"
    "icb_name,sub_icb_code,sub_icb_name,dataset_name,postgis_type\n"
    'APL BEXLEY PCN,U07140,"A,B,C","G83006,G83029,G83049",Y56,London,QKK,NHS South East London,72Q,X,PCN_Footprints,\n'
    'NEW NORTH PCN,U11111,"D","G83050",Y56,London,QKK,NHS South East London,72Q,X,PCN_Footprints,\n'
    'Not in a PCN,,"E,F","G83070,G83071",,,,,,,PCN_Footprints,\n',
    encoding="utf-8")
scw, names = F.pcn_membership_scw(csv)
check("each network's practices are read", scw.get("U07140") == ["G83006", "G83029", "G83049"], str(scw))
check("the row with no code goes to U99999", scw.get(F.PCN_UNALLOCATED) == ["G83070", "G83071"], str(scw))
check("names are kept as SCW gives them", names.get("U07140") == "APL BEXLEY PCN" and F.PCN_UNALLOCATED not in names, str(names))
check("no file gives no list", F.pcn_membership_scw(tmp / "none.csv") == ({}, {}))

print("=== merging with Fingertips ===")
ft = {"U07140": ["G83006", "G83029"], "U22222": ["G83029", "G83099"], F.PCN_UNALLOCATED: ["G83050"]}
m = F.merge_pcn_membership(scw, ft)
check("SCW's networks are taken as they are", m["U07140"] == ["G83006", "G83029", "G83049"])
check("a practice SCW places elsewhere is not also in its Fingertips network", "G83029" not in m.get("U22222", []), str(m))
check("a practice SCW does not list keeps its Fingertips network", m.get("U22222") == ["G83099"], str(m))
check("a practice SCW puts in a network leaves Fingertips' unallocated group", "G83050" not in m.get(F.PCN_UNALLOCATED, []), str(m))
every = [p for ps in m.values() for p in ps]
check("each practice is in one network only", len(every) == len(set(every)))

print("=== run_pcn: SCW's list, Fingertips' names, catchments ===")
F.REPO_ROOT = tmp
F.DATA_DIR = tmp / "data"
F.CACHE_DIR = tmp / "cache"
F.MAP_DIR = tmp / "data" / "map"
F.PCN_MEMBERSHIP_CSV = csv
(F.DATA_DIR / "healthcare").mkdir(parents=True)
(F.DATA_DIR / "boundaries").mkdir(parents=True)
pd.DataFrame({"code": ["G83006", "G83029", "G83049", "G83050", "G83070"]}).to_parquet(
    F.DATA_DIR / "healthcare" / "gp_practices.parquet", index=False)
sq = lambda x: {"type": "Polygon", "coordinates": [[[x, 51.5], [x + .01, 51.5], [x + .01, 51.51], [x, 51.51], [x, 51.5]]]}
lsoas = ["E01000001", "E01000002", "E01000003"]
(F.DATA_DIR / "boundaries" / "lsoa.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"LSOA21CD": c}, "geometry": sq(0.01 * i)} for i, c in enumerate(lsoas)]}))
F.get_lsoa_to_msoa = lambda: {c: None for c in lsoas}
reg_dir = tmp / "gp_reg"
reg_dir.mkdir()
rows = [("G83006", "E01000001", 80), ("G83029", "E01000001", 10), ("G83049", "E01000002", 60),
        ("G83050", "E01000002", 30), ("G83070", "E01000003", 100), ("G83050", "E01000003", 20),
        ("G83070", "E01000001", 3)]
head = "PUBLICATION,EXTRACT_DATE,PRACTICE_CODE,PRACTICE_NAME,LSOA_CODE,SEX,NUMBER_OF_PATIENTS\n"
with zipfile.ZipFile(reg_dir / "gp-reg-pat-prac-lsoa-male-female-Oct-26.zip", "w") as z:
    z.writestr("gp-reg-pat-prac-lsoa-all.csv",
               head + "".join(f"GP_PRAC_PAT_LIST,2026-10-01,{p},X,{l},ALL,{n}\n" for p, l, n in rows))
    # The male file would double every count if it were read with the all-persons one.
    z.writestr("gp-reg-pat-prac-lsoa-male.csv",
               head + "".join(f"GP_PRAC_PAT_LIST,2026-10-01,{p},X,{l},MALE,{n}\n" for p, l, n in rows))
F.GP_REG_DIR = reg_dir


def ft_get(url, timeout=0):
    class R:
        def __init__(self, js): self._js, self.text = js, json.dumps(js)
        def json(self): return self._js
    if "parent_to_child_areas" in url:
        return R({"U07140": ["G83006"], F.PCN_UNALLOCATED: ["G83050"]})
    return R([{"Code": "U07140", "Name": "APL Bexley PCN"}, {"Code": F.PCN_UNALLOCATED, "Name": "GPs not allocated to a PCN"}])


F._ft_get = ft_get
F.run_pcn()
d = json.loads((F.MAP_DIR / "pcn.json").read_text())
by = {p["code"]: p for p in d["pcns"]}
check("the membership says where it is from", "South, Central and West" in d.get("membership", "") and "Fingertips" in d["membership"],
      d.get("membership"))
check("Fingertips' name is kept over SCW's capitals", by["U07140"]["name"] == "APL Bexley PCN", by["U07140"]["name"])
check("a network only SCW lists is named in title case", by["U11111"]["name"] == "New North PCN", by["U11111"]["name"])
check("the registrations are the all-persons file's", by["U07140"]["lsoas"]["E01000001"][0] == 90, str(by["U07140"].get("lsoas")))
check("each share is of all registered residents of the LSOA (90 of 93)", by["U07140"]["lsoas"]["E01000001"][1] == 96.8,
      str(by["U07140"]["lsoas"]["E01000001"]))
check("the unallocated group has no catchment", not by.get(F.PCN_UNALLOCATED, {}).get("lsoas"), str(by.get(F.PCN_UNALLOCATED)))
fp = json.loads((F.MAP_DIR / "pcn_footprints.json").read_text())
codes = sorted(f["properties"]["code"] for f in fp["features"])
# E01000003: 100 residents use practices in no network and 20 use New North,
# so the LSOA joins New North's footprint, not a footprint of the unallocated.
check("footprints are drawn for networks, never for the unallocated group, even where it has the most patients",
      codes == ["U07140", "U11111"], str(codes))

print("=== run_pcn when Fingertips cannot be read ===")


def no_ft(url, timeout=0):
    raise RuntimeError("no internet here")


F._ft_get = no_ft
F.CACHE_DIR = tmp / "cache2"
F.run_pcn()
d = json.loads((F.MAP_DIR / "pcn.json").read_text())
check("SCW's list alone still builds the networks and catchments",
      d["has_catchments"] and {p["code"] for p in d["pcns"]} >= {"U07140", "U11111"} and "Fingertips" not in d["membership"],
      d.get("membership"))

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
