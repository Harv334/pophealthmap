"""Fingertips rows are the headline figure for the area they describe.

Three faults this guards against, all found in the October 2026 review:

  - adult smoking (92443) came through as its 18 to 64 breakdown, because
    rows were filtered on sex only and the last one sorted was kept;
  - every shire district showed its county's figure under its own name,
    because only area type 502 was fetched;
  - Barnsley and Sheffield had no figures at all after their 2024 code change.

And the Census age bands: under 16 and working age from single years (TS007),
checked against ONS's England shares, and the flags the map labels them by.
Offline: small frames only, no cache, no network.
"""
import sys
import pathlib as _pl

REPO = _pl.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import pandas as pd
import fetch_all_data as F

failures = 0


def check(ok, label, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures += 1


def row(code, name, value, age="18+ yrs", sex="Persons", cat="", period="2025",
        sortable="20250000", note="", lo=None, hi=None):
    return {"Area Code": code, "Area Name": name, "Value": value, "Age": age, "Sex": sex,
            "Category Type": cat, "Time period": period, "Time period Sortable": sortable,
            "Value note": note, "Lower CI 95.0 limit": lo, "Upper CI 95.0 limit": hi}


print("_ft_pick: headline age, no categories, latest period, withheld values")
df = pd.DataFrame([
    row("E09000002", "Barking and Dagenham", "5.3", age="18-64 yrs", sortable="20250000"),
    row("E09000002", "Barking and Dagenham", "16.0", age="18+ yrs", sortable="20250000"),
    row("E09000002", "Barking and Dagenham", "30.0", age="18+ yrs", cat="Deprivation decile",
        sortable="20250000"),
    row("E09000002", "Barking and Dagenham", "17.1", age="18+ yrs", sortable="20240000"),
    row("E09000004", "Bexley", "11.0", note="Value not published for data quality reasons"),
    row("E09000003", "Barnet", "", note="Value cannot be calculated as number of cases is too small"),
])
p = F._ft_pick(df, "smoking_prevalence_adults", "18+ yrs").set_index("Area Code")
check(p.loc["E09000002", "Value"] == "16.0", "18+ 2025 row kept over 18-64, the category row and 2024",
      str(p.loc["E09000002", "Value"]))
check(pd.isna(p.loc["E09000004", "Value"]), "a value OHID did not publish is blank")
check(pd.isna(p.loc["E09000003", "Value"]), "a value that cannot be calculated is blank")
try:
    F._ft_pick(df[df["Age"] == "18-64 yrs"], "smoking_prevalence_adults", "18+ yrs")
    check(False, "a file without the headline age is refused")
except ValueError:
    check(True, "a file without the headline age is refused")
# Ties on the sort key keep the order the file gave, whatever the platform.
tie = pd.DataFrame([row("E1", "A", "1"), row("E1", "A", "2")])
check(F._ft_pick(tie, "x", "18+ yrs").iloc[0]["Value"] == "2", "stable sort keeps the later row on a tie")
# With no age known, All ages, then the broadest N+ band.
multi = pd.DataFrame([row("E1", "A", "1", age="18-64 yrs"), row("E1", "A", "2", age="18+ yrs"),
                      row("E1", "A", "3", age="65+ yrs")])
check(F._ft_pick(multi, "x").iloc[0]["Value"] == "2", "unknown headline age picks the broadest N+ band")
males = pd.DataFrame([row("E1", "A", "80", sex="Male", age="All ages"),
                      row("E1", "A", "84", sex="Female", age="All ages")])
check(F._ft_pick(males, "life_expectancy_male", "All ages").iloc[0]["Value"] == "80",
      "a _male indicator keeps the Male row")

print("_ft_in_scope: the district's own figure first, then its county's, marked")
d501 = pd.DataFrame([row("E07000033", "Bolsover", "78.1"), row("E07000035", "Derbyshire Dales", None),
                     row("E06000015", "Derby", "77.0")])
d502 = pd.DataFrame([row("E10000007", "Derbyshire", "79.7"), row("E06000015", "Derby", "77.0"),
                     row("E08000016", "Barnsley", "78.5")])
lads = {"E07000033", "E07000035", "E06000015", "E08000038", "E07000999"}
parents = {"E07000033": "E10000007", "E07000035": "E10000007"}
got = F._ft_in_scope(F._ft_pick(d501, "x", "18+ yrs"), F._ft_pick(d502, "x", "18+ yrs"),
                     parents=parents, lads=lads).set_index("Area Code")
check(got.loc["E07000033", "Value"] == "78.1" and got.loc["E07000033", "source_kind"] == "own",
      "Bolsover keeps its own 501 figure")
check(got.loc["E07000035", "Value"] == "79.7" and got.loc["E07000035", "source_kind"] == "county"
      and got.loc["E07000035", "source_code"] == "E10000007"
      and got.loc["E07000035", "source_name"] == "Derbyshire",
      "Derbyshire Dales, with no figure of its own, takes Derbyshire's, named as Derbyshire's")
check(got.loc["E08000038", "Value"] == "78.5" and got.loc["E08000038", "source_kind"] == "predecessor",
      "Barnsley's new code finds the figure under its old one")
check("E07000999" not in got.index, "an LA with nothing anywhere has no row")

print("ft_sanity: wrong age or intervals too wide for a headline")
rows = [{"indicator_short": "s", "age": "18-64 yrs", "value": 5.0, "lower_ci": 0.0, "upper_ci": 11.0}] * 3
check(F.ft_sanity(rows, 92443, "s") is not None, "an 18-64 row for 92443 fails")
rows = [{"indicator_short": "s", "age": "18+ yrs", "value": 12.0, "lower_ci": 2.0, "upper_ci": 22.0}] * 3
check(F.ft_sanity(rows, 92443, "s") is not None, "a median half-width of 10 points fails")
rows = [{"indicator_short": "s", "age": "18+ yrs", "value": 12.0, "lower_ci": 9.0, "upper_ci": 15.0}] * 3
check(F.ft_sanity(rows, 92443, "s") is None, "18+ with 3 point half-widths passes")

print("Census: single years of age, checked against ONS")
cols = {"geography code": ["E01000001", "E01000002", "W01000001"], "Age: Total": [1000, 1000, 1000],
        "Age: Aged under 1 year": [0, 0, 0]}
for a in range(1, 100):
    cols[f"Age: Aged {a} year" + ("" if a == 1 else "s")] = [0, 0, 0]
cols["Age: Aged 100 years and over"] = [0, 0, 0]
t7 = pd.DataFrame(cols)
# Shares that match ONS: 17.4% aged 0 to 14, 1.2% aged 15, 18.4% aged 65+.
t7["Age: Aged 10 years"] = [174, 174, 0]
t7["Age: Aged 15 years"] = [12, 12, 0]
t7["Age: Aged 70 years"] = [184, 184, 0]
single = F._census_single_years(t7)
check(single is not None and len(single[1]) == 101, "every single year found, 0 to 100")
check(F._census_age_check(t7, "geography code", single[1], single[2]) is None,
      "England shares within tolerance of ONS pass")
t7b = t7.copy()
t7b["Age: Aged 15 years"] = [0, 0, 0]
check(F._census_age_check(t7b, "geography code", single[1], single[2]) is not None,
      "a 0 to 15 share that is really 0 to 14 fails")

print("England flags")
import build_england as BE
fl = BE.ind_flags([{"census_age_bands": "0-15", "ft_age_checked": True, "ft_age_mismatch": []},
                   {"census_age_bands": "0-14", "ft_age_checked": True, "ft_age_mismatch": ["ft_x"]}])
check(fl["census_age_bands"] == "0-14" and fl["ft_age_mismatch"] == ["ft_x"],
      "England takes the older bands and every series any region left out")
man = BE.merge_source_notes({"fingertips": {"status": "ok", "notes": ""}},
                            [("yorkshire-and-the-humber", {"sources": {"fingertips": {
                                "status": "failed", "error": "2 LA(s) have no Fingertips figure"}}})])
check(man["fingertips"]["status"] == "failed" and "yorkshire" in man["fingertips"]["notes"],
      "a region's failed source is not hidden behind London's ok")

print(f"\n{'OK' if not failures else str(failures) + ' FAILED'}")
sys.exit(1 if failures else 0)
