"""The vaccination and screening series OHID publishes per LA.

Each one added in October 2026 is held to Fingertips' own name, compared
whole, because every vaccination series starts "Population vaccination
coverage" and the fourteen-letter check (_ft_name_mismatch) cannot tell one
from another. A download whose name does not match is not taken. Each has its
headline age, and a file with several sexes and no Persons row is refused
rather than read as whichever sex sorted last.

The ids and names are checked against data/outcomes/fingertips_profiles.parquet,
which holds the names the Fingertips API gave for them. Offline: run_fingertips
reads files written here, through a stand-in for the download.

    python3 tests/pipeline/test_ft_vaccines.py
"""
import pathlib
import sys
import tempfile

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import fetch_all_data as F  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


ids = {i: (short, desc) for i, short, desc in F.FINGERTIPS_INDICATORS}

print("=== the list ===")
check("every exact-name series is in FINGERTIPS_INDICATORS", F.FT_EXACT_NAME_IDS <= set(ids),
      str(sorted(F.FT_EXACT_NAME_IDS - set(ids))))
check("every exact-name series has its headline age", all(i in F.FT_HEADLINE_AGE for i in F.FT_EXACT_NAME_IDS),
      str(sorted(i for i in F.FT_EXACT_NAME_IDS if i not in F.FT_HEADLINE_AGE)))
shorts = [s for _, s, _ in F.FINGERTIPS_INDICATORS]
check("no two series share a short name", len(shorts) == len(set(shorts)))

prof = REPO / "data" / "outcomes" / "fingertips_profiles.parquet"
if prof.exists():
    pr = pd.read_parquet(prof)
    names = {}
    for i, n in zip(pr["indicator_id"], pr["indicator_name"]):
        names.setdefault(int(i), set()).add(F.ft_profile_name_key(n))
    bad = [i for i in F.FT_EXACT_NAME_IDS if i in names and F.ft_profile_name_key(ids[i][1]) not in names[i]]
    check("each exact name is the name Fingertips gives that id", not bad,
          "; ".join(f"{i}: {ids[i][1]!r}" for i in bad))
    ages = {}
    for i, a in zip(pr["indicator_id"], pr["age"]):
        if a:
            ages.setdefault(int(i), set()).add(str(a))
    bad = [i for i in F.FT_EXACT_NAME_IDS if i in ages and ages[i] != {F.FT_HEADLINE_AGE[i]}]
    check("each headline age is the age Fingertips gives that id", not bad,
          "; ".join(f"{i}: {sorted(ages[i])} not {F.FT_HEADLINE_AGE[i]!r}" for i in bad))
else:
    print("  (no fingertips_profiles.parquet here; name and age checks against it skipped)")

print("=== _ft_pick and sex ===")


def frame(sexes, value="80.0", age="1 yr"):
    return pd.DataFrame([{"Area Code": "E09000002", "Area Name": "Barking and Dagenham", "Value": value,
                          "Age": age, "Sex": s, "Category Type": "", "Time period": "2024/25",
                          "Time period Sortable": "20240000", "Value note": ""} for s in sexes])


try:
    F._ft_pick(frame(["Male", "Female"]), "hpv_1_dose_12_13", "1 yr")
    check("several sexes and no Persons row is refused", False)
except ValueError as e:
    check("several sexes and no Persons row is refused", "several sexes" in str(e), str(e))
p = F._ft_pick(frame(["Female"]), "cervical_screening_25_49", "1 yr")
check("a single-sex series is still read", len(p) == 1)
p = F._ft_pick(frame(["Male", "Female", "Persons"]), "pcv_age1", "1 yr")
check("Persons is taken where there is one", len(p) == 1 and p.iloc[0]["Sex"] == "Persons")

print("=== run_fingertips and the exact name ===")
tmp = pathlib.Path(tempfile.mkdtemp())
F.REPO_ROOT = tmp
F.CACHE_DIR = tmp / "cache"
F.DATA_DIR = tmp / "data"
F._ft_district_parents = lambda: {}   # no districts here, and no network
(F.DATA_DIR / "outcomes").mkdir(parents=True)
F.SCOPE_LADS = ["E09000002"]
F.LAD_NAMES = {"E09000002": "Barking and Dagenham"}
F.FINGERTIPS_INDICATORS = [
    (30306, "pcv_age1", "Population vaccination coverage: PCV"),
    (30308, "pcv_booster_age2", "Population vaccination coverage: PCV booster"),
]
served = {30306: "Population vaccination coverage: PCV",
          # A wrong id: another vaccination series under the booster's id.
          30308: "Population vaccination coverage: MMR for one dose (2 years old)"}


def fake_download(cache, ind_id, area_type, short, quiet=False):
    if area_type != 502:
        return None
    age = F.FT_HEADLINE_AGE[ind_id]
    df = frame(["Persons"], value="88.5", age=age)
    df["Indicator Name"] = served[ind_id]
    df["Lower CI 95.0 limit"] = None
    df["Upper CI 95.0 limit"] = None
    return df


F._ft_download = fake_download
F._SOURCE_ERROR.pop("fingertips", None)
out = F.run_fingertips()
got = set(out["indicator_short"]) if out is not None and not out.empty else set()
check("the series whose name matches is taken", "pcv_age1" in got, str(got))
check("the series whose name is another vaccination's is not", "pcv_booster_age2" not in got, str(got))
check("and the run says why", "pcv_booster_age2" in F._SOURCE_ERROR.get("fingertips", ""),
      F._SOURCE_ERROR.get("fingertips", ""))

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
