"""Wards whose LSOAs hold noticeably more or fewer people than the ward.

The best-fit lookup gives each LSOA to one ward, so for a few wards the LSOAs
that drive deprivation and health figures hold under 80% or over 125% of the
ward's own Census residents. Those carry lsoa_fit. Offline: small inputs, then
the shipped ward_core.json.
"""
import json
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


wards = {
    "W1": {"indicators": {"census_population": 1000}},
    "W2": {"indicators": {"census_population": 1000}, "lsoa_fit": 0.5},
    "W3": {"indicators": {"census_population": 1000}},
    "W4": {"indicators": {"census_population": 500}},
    "W5": {"indicators": {}},
}
l2w = {"A": "W1", "B": "W2", "C": "W3", "D": "W3"}
pop = {"A": 650, "B": 1000, "C": 900, "D": 400}
fit = F.ward_lsoa_fit(wards, l2w, pop)
print("=== ward_lsoa_fit ===")
check(fit == {"W1": 0.65, "W2": 1.0, "W3": 1.3, "W4": 0.0}, "ratio of LSOA residents to Census residents", str(fit))
n = F.record_lsoa_fit(wards, fit)
check(n == 2 and wards["W1"].get("lsoa_fit") == 0.65 and wards["W3"].get("lsoa_fit") == 1.3,
      "wards outside 80% to 125% are marked", str(n))
check("lsoa_fit" not in wards["W2"], "a ward back inside the band loses its mark")
check("lsoa_fit" not in wards["W4"], "a ward with no LSOA at all is not marked")

print("\n=== the shipped ward file ===")
core = json.loads((REPO / "data/ward_core.json").read_text(encoding="utf-8"))["wards"]
marked = {w["name"]: w["lsoa_fit"] for w in core.values() if "lsoa_fit" in w}
check(marked.get("Farringdon Without") == 3.45 and marked.get("Marlborough") == 0.65,
      "Farringdon Without and Marlborough are marked", str(sorted(marked.items())[:6]))
check(all(F.ward_fit_is_off(v) for v in marked.values()), "only wards outside the band are marked")

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
