"""The checked hospital list (data/curated/hospital_sites.csv) and how
run_hospitals applies it. Offline, with no downloads.

    python3 tests/pipeline/test_hospital_sites.py
"""
import sys
import pathlib as _pl

REPO = _pl.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import fetch_all_data as F

failures = 0


def check(ok, label, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures += 1


sites = F.load_hospital_sites()
check(len(sites) >= 190, "the checked list loads", f"{len(sites)} rows")
kept = [v for v in sites.values() if v["keep"]]
check(all(v["type"] in F.HOSP_TYPES for v in kept), "every kept site has a known type")
check(all(v["ae"] in ("yes", "no", "eye", "") for v in sites.values()), "A&E is yes, no, eye or blank")
check(all(v["ae"] != "yes" or v["type"] == "NHS hospital" for v in kept),
      "only an NHS hospital is marked as having a 24-hour A&E")


def row(name, pc):
    return {"name": name, "postcode": pc, "lat": 51.5, "lng": -0.1, "type": "", "ae": ""}


out = F.apply_hospital_sites([
    row("Private Hospitals", "RM30PU"),                 # a service record: dropped
    row("Samaritan Hospital for Women", "NW1 5JD"),     # closed: dropped
    row("Hammersmith Hospital", "NW15QH"),              # renamed
    row("Queen Marys Hospital", "DA14 6LT"),            # town added
    row("Queen Mary's Hosp", "DA146LT"),                # respelt: matched by postcode
    row("Royal London Hospital", "E1 1BB"),             # A&E
    row("Spire Somewhere Hospital", "ZZ1 1ZZ"),         # unchecked region, private brand
    row("Anytown Hospital at Home", "ZZ1 1ZY"),         # unchecked region, a team
    row("Anytown Hospital", "ZZ1 1ZX"),                 # unchecked region
], sites)
names = [r["name"] for r in out]
check("Private Hospitals" not in names, "a service record is dropped")
check("Samaritan Hospital for Women" not in names, "a closed hospital is dropped")
check("Western Eye Hospital" in names, "the NW1 5QH record is the Western Eye Hospital")
check(names.count("Queen Mary's Hospital, Sidcup") == 2, "a name that repeats carries its town, matched on postcode")
rl = next(r for r in out if r["name"] == "Royal London Hospital")
check(rl["ae"] == "yes" and rl["type"] == "NHS hospital", "the Royal London has an A&E")
sp = next(r for r in out if r["name"] == "Spire Somewhere Hospital")
check(sp["type"] == "Private hospital", "a private brand outside the list is a private hospital")
check("Anytown Hospital at Home" not in names, "a hospital-at-home team outside the list is dropped")
an = next(r for r in out if r["name"] == "Anytown Hospital")
check(an["type"] == F.HOSP_UNCHECKED and an["ae"] == "", "an unchecked site keeps the register's claim and no A&E flag")

b = F.hosp_blob_row({"name": "X", "postcode": "E11BB", "lat": 1, "lng": 2, "type": "NHS hospital", "ae": "yes"})
check(b["a"] == "E1 1BB" and b["ae"] is True, "the map record spaces the postcode and carries ae true")
b = F.hosp_blob_row({"name": "X", "postcode": "E11BB", "lat": 1, "lng": 2, "type": F.HOSP_UNCHECKED, "ae": ""})
check("ae" not in b, "an unchecked site has no ae field")

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
