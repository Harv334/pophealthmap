"""Wards the LSOA lookup gives no LSOA (fill_wards_without_lsoa): each takes
the figures of the LSOA holding most of its residents, found through the
output areas, as published and with no counts or Census figures. Offline, on
small inputs.

    python3 tests/pipeline/test_ward_fill.py
"""
import json
import pathlib as _pl
import sys

REPO = _pl.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import fetch_all_data as F

failures = 0


def check(ok, label, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures += 1


def inputs():
    # W1 holds LSOAs A and B; W2 is small and holds none, though its output
    # areas sit in A (60 residents) and B (200); W3 has no residents; W4 has
    # residents but no output areas in the lookup.
    wards = {
        "W1": {"indicators": {"census_population": 3000, "imd_score": 30.0, "ft_93283": 80.0,
                              "claimant_count": 90, "claimant_rate_pct": 4.0, "crime_violence_12mo": 40,
                              "census_over65_pct": 12.0, "dwp_cilif_pct": 20.0}},
        "W2": {"indicators": {"census_population": 260, "census_over65_pct": 18.0}},
        "W3": {"indicators": {}},
        "W4": {"indicators": {"census_population": 500}},
    }
    lsoa = {"A": {"census_population": 1500, "imd_score": 20.0, "claimant_count": 40, "claimant_rate_pct": 3.0,
                  "census_over65_pct": 10.0, "imd_decile": 5},
            "B": {"census_population": 1500, "imd_score": 40.0, "claimant_count": 50, "claimant_rate_pct": 5.0,
                  "census_over65_pct": 14.0, "imd_decile": 2}}
    msoa = {"M1": {"ft_93283": 79.5, "ft_93283_lci": 78.0}, "M2": {"ft_93283": 82.0}}
    l2w = {"A": "W1", "B": "W1"}
    oa2w = {"o1": "W1", "o2": "W1", "o3": "W2", "o4": "W2", "o5": "W2", "o6": "W3"}
    oa2l = {"o1": "A", "o2": "B", "o3": "A", "o4": "B", "o5": "B", "o6": "A"}
    oapop = {"o1": 1440, "o2": 1300, "o3": 60, "o4": 120, "o5": 80, "o6": 0}
    l2m = {"A": "M1", "B": "M2"}
    return wards, lsoa, msoa, l2w, oa2w, oa2l, oapop, l2m


print("=== a ward with no LSOA ===")
wards, lsoa, msoa, l2w, oa2w, oa2l, oapop, l2m = inputs()
out = F.fill_wards_without_lsoa(wards, lsoa, msoa, l2w, oa2w, oa2l, oapop, l2m)
w2 = wards["W2"]
check(out == {"W2": "B"}, "only W2 is filled, from B, which holds 200 of its 260 residents", str(out))
check(w2["indicators"].get("imd_score") == 40.0 and w2["indicators"].get("claimant_rate_pct") == 5.0,
      "it takes B's own figures as published, not an average of A and B", str(w2))
check(w2["indicators"].get("ft_93283") == 82.0, "and the MSOA figure B carries", str(w2))
check("claimant_count" not in w2["indicators"] and "crime_violence_12mo" not in w2["indicators"],
      "no count, which W1 already counts", str(w2))
check(w2["indicators"]["census_over65_pct"] == 18.0, "its own Census figure from its output areas stays", str(w2))
check("imd_decile" not in w2["indicators"] and "ft_93283_lci" not in w2["indicators"]
      and "dwp_cilif_pct" not in w2["indicators"],
      "nothing the other wards do not have, and nothing B or its MSOA lacks", str(w2))
check(w2.get("lsoa_from") == "B" and w2.get("lsoa_fit") == 5.77,
      "it is marked with its LSOA and how many more residents that LSOA holds", str(w2))
check("lsoa_from" not in wards["W1"] and wards["W1"]["indicators"]["imd_score"] == 30.0,
      "a ward with LSOAs is untouched", str(wards["W1"]))
check(wards["W3"] == {"indicators": {}} and wards["W4"] == {"indicators": {"census_population": 500}},
      "a ward with no residents, or no output areas, is left as it was", str([wards["W3"], wards["W4"]]))

print("=== ties and order ===")
wards, lsoa, msoa, l2w, oa2w, oa2l, oapop, l2m = inputs()
oapop.update({"o3": 100, "o4": 50, "o5": 50})
F.fill_wards_without_lsoa(wards, lsoa, msoa, l2w, oa2w, oa2l, oapop, l2m)
check(wards["W2"].get("lsoa_from") == "A", "a tie goes to the first LSOA by code, the same on every run",
      str(wards["W2"]))

print("=== what is skipped ===")
check(all(F.fill_skips(k) for k in ("census_population", "claimant_count", "claimant_yoy_change",
                                    "crime_violence_12mo", "gb_total_uprn", "core20_lsoa_count")),
      "the Census and counts")
check(not any(F.fill_skips(k) for k in ("imd_score", "claimant_rate_pct", "ft_93283", "bus_stops_800m",
                                        "fuel_poverty_pct", "qof_est_diabetes_pct")),
      "but not scores, rates, shares or averages")

print("=== the export ===")
src = (REPO / "fetch_all_data.py").read_text(encoding="utf-8")
check("    fill_wards_from_oas(ward_data, lsoa_data, msoa_data)\n    drop_retired(" in src,
      "export_all fills the wards before the files are written")
check('write_parquet_guarded(DATA_DIR / "demographics" / "oa21_lsoa21.parquet"' in src,
      "the output area run keeps each output area's LSOA for it")

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
