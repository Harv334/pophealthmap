"""OHID's own LA figures for the Local Health indicators (review item H1).

The map's LA figure for a Local Health indicator was a population-weighted
mean of ward figures, which disagreed with OHID's LA figure. The pipeline now
fetches the LA series and writes it into borough_data.json under the same
ft_<id> key, so boroughOvValue prefers it. Offline: built from small frames.
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


def frame(rows):
    cols = ["Area Code", "Area Name", "Area Type", "Sex", "Time period", "Time period Sortable",
            "Value", "Lower CI 95.0 limit", "Upper CI 95.0 limit"]
    return pd.DataFrame(rows, columns=cols, dtype=str)


print("=== life expectancy: women keep the id, men take FT_MALE_ID ===")
le = frame([
    ["E09000025", "Newham", "Districts & UAs", "Female", "2019 - 23", "20190000", "83.0", "82.6", "83.4"],
    ["E09000025", "Newham", "Districts & UAs", "Male", "2019 - 23", "20190000", "80.1", "79.7", "80.5"],
    ["E09000025", "Newham", "Districts & UAs", "Male", "2014 - 18", "20140000", "79.0", "78.6", "79.4"],
    # OHID's 'cannot be calculated' placeholder: upper limit below the lower.
    ["E09000001", "City of London", "Districts & UAs", "Male", "2019 - 23", "20190000", "0", "0", "-1"],
    ["E92000001", "England", "England", "Male", "2019 - 23", "20190000", "79.3", "79.2", "79.4"],
])
rows = F._ft_la_latest(le, 93283)
by = {(r["LAD25CD"], r["indicator_id"]): r for r in rows}
check(by[("E09000025", 93283)]["value"] == 83.0, "women under 93283")
check(by[("E09000025", F.FT_MALE_ID(93283))]["value"] == 80.1, "men under FT_MALE_ID, latest period")
check(by[("E09000001", F.FT_MALE_ID(93283))]["value"] is None, "City placeholder is missing, not zero")
check(not any(r["LAD25CD"] == "E92000001" for r in rows), "England row dropped")

print("\n=== persons series, and the borough_data values ===")
adm = frame([
    ["E09000030", "Tower Hamlets", "Districts & UAs", "Persons", "2016/17 - 20/21", "20160000", "104.2", "102", "106"],
])
rows += F._ft_la_latest(adm, 93227)
vals = F.local_health_la_values(pd.DataFrame(rows))
check(vals["E09000025"]["ft_932831"] == 80.1 if F.FT_MALE_ID(93283) == 932831 else True, "ft_932831 for Newham")
check(vals["E09000030"]["ft_93227"] == 104.2, "ft_93227 for Tower Hamlets")
check("ft_932831" not in vals.get("E09000001", {}), "the City's incalculable figure is not written")

print("\n=== Barnsley and Sheffield found under their old codes ===")
old = frame([
    ["E08000016", "Barnsley", "Districts & UAs", "Persons", "2016/17 - 20/21", "20160000", "110.5", "108", "113"],
    ["E08000019", "Sheffield", "Districts & UAs", "Persons", "2016/17 - 20/21", "20160000", "98.1", "96", "100"],
    ["E08000039", "Sheffield", "Districts & UAs", "Persons", "2016/17 - 20/21", "20160000", "97.0", "95", "99"],
])
moved = F.ft_codes_to_current(old)
codes = list(moved["Area Code"])
check("E08000038" in codes and "E08000016" not in codes, "Barnsley's old code is filed under its new one")
check(codes.count("E08000039") == 1 and "E08000019" in codes, "Sheffield's own new-code row is kept and the old one left alone")

print("\n=== it is a source the pipeline runs ===")
check(F.SOURCES.get("fingertips_msoa_la") is F.run_fingertips_msoa_la, "SOURCES has fingertips_msoa_la")

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
