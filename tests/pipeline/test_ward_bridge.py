"""Ward figures built from LSOAs and MSOAs, PTAL's 2011 codes, OHID's value
notes, the Custom area counts and the charities the Near list shows
(review items H2, H3, H4, H5, H7, H9 and H15). Offline: small hand-built
inputs only.
"""
import sys
import pathlib as _pl
from datetime import date

import pandas as pd

REPO = _pl.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import fetch_all_data as F

failures = 0


def check(ok, label, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures += 1


print("=== bridge_to_wards: weighted by residents, not one per LSOA ===")
l2w = {"A1": "W1", "A2": "W1", "B1": "W2", "B2": "W2", "B3": "W2"}
pop = {"A1": 1000, "A2": 3000, "B1": 1000, "B2": 1000, "B3": 8000}
vals, cover = F.bridge_to_wards({"A1": 10.0, "A2": 20.0}, l2w, pop)
check(abs(vals["W1"] - 17.5) < 1e-9, "W1 is the residents-weighted mean (17.5, not 15)", str(vals.get("W1")))
check(cover["W1"] == 1.0, "W1 fully covered")

print("=== bridge_to_wards: a ward under 80% of its residents gets no figure ===")
vals, cover = F.bridge_to_wards({"B1": 5.0, "B2": 7.0}, l2w, pop)
check("W2" not in vals, "W2 left blank (20% of residents)")
check(abs(cover["W2"] - 0.2) < 1e-9, "its coverage is still recorded", str(cover.get("W2")))
vals, cover = F.bridge_to_wards({"B1": 5.0, "B3": 7.0}, l2w, pop)
check("W2" in vals and abs(cover["W2"] - 0.9) < 1e-9, "W2 kept at 90% of residents")

print("=== bridge_to_wards: no populations is an error, not a silent weight of 1 ===")
try:
    F.bridge_to_wards({"A1": 1.0}, l2w, {})
    check(False, "raises without populations")
except RuntimeError:
    check(True, "raises without populations")
try:
    F.census_pop_by_lsoa(pd.DataFrame({"LSOA21CD": ["A1"], "imd_score": [3.0]}))
    check(False, "census_pop_by_lsoa raises when there is no population column")
except RuntimeError:
    check(True, "census_pop_by_lsoa raises when there is no population column")

print("=== record_cover: only a figure on part of the ward is marked ===")
wards = {"W1": {}, "W2": {}}
n = F.record_cover(wards, {"W1": 1.0, "W2": 0.874}, "ft_1", {"W1": 1, "W2": 2})
check(n == 1 and wards["W2"]["cover"]["ft_1"] == 87 and "cover" not in wards["W1"],
      "W2 marked 87%, W1 not marked", str(wards))
F.record_cover(wards, {"W2": 0.5}, "ft_1", {})
check("ft_1" not in wards["W2"].get("cover", {}), "a ward left blank carries no share")
F.record_cover(wards, {"W1": 0.996}, "ft_2", {"W1": 1})
check("ft_2" not in wards["W1"].get("cover", {}), "a share that rounds to 100% is not marked")

print("=== lsoa11_to_lsoa21_values: PTAL's 2011 codes reach the 2021 LSOAs ===")
rows = [("E01000001", "E01000001", "U"),
        ("E01000002", "E01035001", "S"), ("E01000002", "E01035002", "S"),
        ("E01000003", "E01035003", "M"), ("E01000004", "E01035003", "M"),
        ("E01000005", "E01035004", "X"), ("E01000006", "E01035004", "X"),
        ("E01000009", "E01035009", "U")]
v11 = {"E01000001": 4.0, "E01000002": 6.0, "E01000003": 2.0, "E01000004": 4.0,
       "E01000005": 1.0, "E01000006": 5.0}
out = F.lsoa11_to_lsoa21_values(v11, rows)
check(out.get("E01000001") == 4.0, "unchanged keeps its figure")
check(out.get("E01035001") == 6.0 and out.get("E01035002") == 6.0, "both parts of a split take the parent's")
check(out.get("E01035003") == 3.0, "a merge takes the mean of its parts", str(out.get("E01035003")))
check(out.get("E01035004") == 3.0, "a complex redraw takes the mean of its parents")
check("E01035009" not in out, "an LSOA whose parent has no figure is left out")

print("=== OHID value notes ===")
check(F._ft_note(float("nan")) == "" and F._ft_note(" Value for Hackney and City of London combined ")
      == "Value for Hackney and City of London combined", "_ft_note cleans blanks and spaces")
try:
    F.ft_note_check("E09000001", "life_expectancy_male",
                    "Value cannot be calculated as number of cases is too small")
    check(False, "a withheld value reaching the LA file fails the build")
except RuntimeError:
    check(True, "a withheld value reaching the LA file fails the build")
try:
    F.ft_note_check("E09000002", "life_expectancy_male",
                    "Value based on provisional mortality and population data")
    check(True, "a provisional note passes")
except RuntimeError:
    check(False, "a provisional note passes")

print("=== lsoa_census_counts: numerators then denominators, in scope only ===")
cnt = pd.DataFrame({"LSOA21CD": ["A1", "Z9"],
                    "census_no_car_pct__num": [30.0, 1.0], "census_no_car_pct__den": [100.0, 2.0],
                    "census_owned_pct__num": [40.0, 1.0], "census_owned_pct__den": [99.0, 2.0]})
out = F.lsoa_census_counts(cnt, {"A1": {}})
check(out["keys"] == ["census_no_car_pct", "census_owned_pct"], "keys in order", str(out.get("keys")))
check(out["rows"] == {"A1": [30, 40, 100, 99]}, "one in-scope row, nums then dens", str(out.get("rows")))

print("=== charities: the Near list's rule ===")
check(F.charity_serves_area("BRENT CARERS CENTRE", 250000, ["services"]), "a carers' centre giving services is kept")
check(not F.charity_serves_area("THE PAROCHIAL CHURCH COUNCIL OF ST MARY", 30000, ["services"]), "a PCC is not")
check(not F.charity_serves_area("ST JOHN'S PRIMARY SCHOOL PTA", 9000, ["advocacy_info"]), "a school PTA is not")
check(not F.charity_serves_area("THE GRANT FUND", 90000, ["grants_individuals"]), "a grant fund is not")
check(not F.charity_serves_area("HELP CENTRE", 0, ["services"]), "no income is not")
check(not F.charity_serves_area("HELP CENTRE", 5000, ["services"], status="Submission Overdue"),
      "overdue accounts are not")
check(F.charity_accounts_overdue("", "2020-03-31", today=date(2026, 10, 1)), "accounts five years old are late")
check(not F.charity_accounts_overdue("Submission Received", "2025-03-31", today=date(2026, 10, 1)),
      "last year's accounts are not")
check(F.charity_no_pin("WATERLOW PARK", "LONDON BOROUGH OF CAMDEN, 7TH FLOOR, TOWN HALL, ARGYLE STREET",
                       ["buildings_facilities"]), "a park run from the town hall is not pinned")
check(F.charity_no_pin("KING GEORGE'S FIELD", "", ["buildings_facilities"]), "a land-only field is not pinned")
check(not F.charity_no_pin("SOUTHALL COMMUNITY ALLIANCE", "SOUTHALL TOWN HALL, 1 HIGH STREET",
                           ["services"]), "a charity based in an old town hall keeps its pin")
rec = {"n": "BRENT CARERS CENTRE", "inc": 250000.0, "ht": ["services"], "a": "1 HIGH ROAD"}
F.apply_charity_flags(rec)
check(rec["nr"] is True and "np" not in rec, "apply_charity_flags sets nr and leaves np off")

print()
if failures:
    print(f"{failures} check(s) failed")
    sys.exit(1)
print("All checks passed")
