"""The ward crime total leaves out anti-social behaviour.

Anti-social behaviour is not a crime, and forces record it differently (a
fifth of Met records, almost none of the City of London Police's), so the
total, and the crime rate the page works out from it, leave it out. It keeps
its own indicator, crime_asb_12mo. Offline: a small frame, no cache needed.
"""
import sys
import pathlib as _pl

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


crime = pd.DataFrame({
    "WD25CD": ["W1", "W1", "W1", "W2", "W2", "W3"],
    "category": ["anti-social-behaviour", "burglary", "violent-crime",
                 "anti-social-behaviour", "anti-social-behaviour", "shoplifting"],
})
got = F.crime_total_by_ward(crime).to_dict()
print("=== crime_total_by_ward ===")
check(got.get("W1") == 2, "anti-social behaviour is not counted", str(got))
check(got.get("W2") == 0, "a ward with only anti-social behaviour has 0 crimes, not no figure", str(got))
check(got.get("W3") == 1, "other categories are counted", str(got))
nocat = F.crime_total_by_ward(pd.DataFrame({"WD25CD": ["W1", "W1"]})).to_dict()
check(nocat == {"W1": 2}, "a file with no categories counts every row", str(nocat))

print("\n=== the shipped ward files agree ===")
import json
core = json.loads((REPO / "data/ward_core.json").read_text(encoding="utf-8"))["wards"]
bad = [c for c, w in core.items()
       if w.get("crime_by_category") and "crime_total" in (w.get("indicators") or {})
       and w["indicators"]["crime_total"] != sum(v for k, v in w["crime_by_category"].items()
                                                 if k != "anti-social-behaviour")]
check(not bad, "ward_core.json crime_total = categories less anti-social behaviour", ", ".join(bad[:5]))

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
