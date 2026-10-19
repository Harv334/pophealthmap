"""GP practices, pharmacies and dental practices a resident can use: the rules
in run_gp_practices, run_pharmacies and run_dentists. Offline.

    python3 tests/pipeline/test_service_sites.py
"""
import json
import sys
import pathlib as _pl

REPO = _pl.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import pandas as pd
import fetch_all_data as F

failures = 0


def check(ok, label, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures += 1


sites = F.load_service_sites()
v = lambda code, name: F.gp_practice_verdict(code, name, sites)

# GP practices
for name in ["Sel Special Allocation Practice", "Special Allocation Scheme (Nel)", "Croydon Sas",
             "Fiveways PCN Ea Hub", "Islington GP Federation", "Barnet Federated GPs Limited",
             "NCL Covid Medicines Delivery Unit", "QHS GP Care Home Service", "Larc Clinic (La)"]:
    check(v("Y99999", name)[0] == "drop", f"GP: {name} is not a practice")
for name in ["Kenton Clinic", "The Saluja Clinic", "Oakleigh Road Health Centre", "St Mary's Surgery",
             "Sasha Medical Centre", "Hubbard Practice"]:
    check(v("E99999", name)[0] == "keep", f"GP: {name} is kept")
check(v("Y07020", "One Care Lambeth")[0] == "drop", "GP: a scheme named only in the checked list is dropped")
check(v("E83666", "Homeless Action in Barnet") == ("keep", "Practice for people who are homeless"),
      "GP: the practice for people who are homeless is kept and labelled")

# A data test: nothing in the shipped GP files is named like a scheme, hub or office.
for p in [REPO / "data" / "map" / "gp_practices.js"] + sorted((REPO / "data" / "regions").glob("*/map/gp_practices.js")):
    s = p.read_text(encoding="utf-8")
    s = s[s.index("var GPS"):]
    recs = json.loads(s[s.index("["):s.rindex("]") + 1])
    bad = [r["n"] for r in recs if F.GP_NOT_A_PRACTICE.search(r["n"])]
    check(not bad, f"data: no GP record in {p.relative_to(REPO)} is named like a scheme, hub or office", ", ".join(bad[:5]))

# Pharmacies
pv = lambda name, addr="", role="RO182": F.pharmacy_verdict("FAAAA", name, addr, role, sites)
check(pv("Any Pharmacy", role="RO94") != "", "pharmacy: an RO94 appliance contractor is left out")
for name in ["Fittleworth Medical Limited", "Charles S Bullen Stomacare", "Salts Medilink",
             "Be Online Pharmacy", "Mychemistonline", "www.your-chemist.com", "Chemist 2 Home",
             "Phlo-Digital Pharmacy", "Netscripts Direct"]:
    check(pv(name) != "", f"pharmacy: {name} has no walk-in counter")
for name in ["Boots", "Fa Strange Chemist", "Day Lewis Pharmacy", "Directors Pharmacy"]:
    check(pv(name) == "", f"pharmacy: {name} is kept")
check(pv("Boots", "Airside, Terminal Three, London Heathrow Airport") != "", "pharmacy: an airside counter is left out")

cols = {c: "" for c in F.EPRACCUR_HEADER}
def row(**kw):
    r = dict(cols); r.update(kw); return r
df = pd.DataFrame([
    row(OrganisationCode="FCV30", Name="PYRAMID PHARMACY", AddressLine1="137 LAVENDER HILL", Postcode="SW11 5QJ", OpenDate="20100101", OrganisationSubTypeCode="RO182"),
    row(OrganisationCode="FYF59", Name="PYRAMID PHARMACY", AddressLine1="137 Lavender Hill", Postcode="SW115QJ", OpenDate="20230101", OrganisationSubTypeCode="RO182"),
    row(OrganisationCode="FAA01", Name="A PHARMACY", AddressLine1="", Postcode="E1 1AA", OpenDate="20200101", OrganisationSubTypeCode="RO182"),
    row(OrganisationCode="FAA02", Name="B PHARMACY", AddressLine1="", Postcode="E1 1AA", OpenDate="20210101", OrganisationSubTypeCode="RO182"),
    row(OrganisationCode="FAA03", Name="STOMA CARE LTD", AddressLine1="1 HIGH ST", Postcode="E1 1AB", OpenDate="20210101", OrganisationSubTypeCode="RO94"),
])
out = F.filter_pharmacies(df, sites)
check(sorted(out["OrganisationCode"]) == ["FAA01", "FAA02", "FYF59"],
      "pharmacy: two codes at one address keep the newer; no address is never a duplicate",
      ",".join(sorted(out["OrganisationCode"])))

# Dental
check(F.dental_verdict("HMP Brixton Dental Service") is None, "dental: a prison service is left out")
check(F.dental_verdict("Dental Suite", "H M Prison Frankland, Durham") is None, "dental: a prison address is left out")
check(F.dental_verdict("Smile Orthodontics") == "Orthodontics only, by referral", "dental: orthodontics labelled")
check(F.dental_verdict("Community Dental Services CIC") == "Community dental service, by referral", "dental: community service labelled")
check(F.dental_verdict("Smile Dental Care") == "", "dental: a practice has no label")
check(F.dental_display_name("DENTAL SURGERY", ["12 High Street", "Romford"]) == "Dental Surgery, 12 High Street",
      "dental: a practice named only Dental Surgery takes its street")
check(F.dental_display_name("SMILE DENTAL CARE", ["12 High Street"]) == "Smile Dental Care", "dental: a real name is kept")

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
