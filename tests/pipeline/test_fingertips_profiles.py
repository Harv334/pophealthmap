"""Fingertips profiles are taken by name, not by the first ids in number order.

run_fingertips_profiles used to take sorted(ids)[:40] from each profile, so the
oldest ids came first whatever they were. It now takes the indicators named in
data/curated/fingertips_profile_indicators.csv, matched against the profile's
own metadata: an id whose name still matches, else the id under that name, and
a name found nowhere is logged and not guessed at. FINGERTIPS_PER_PROFILE stays
as a cap.

Offline. The metadata is a synthetic indicator_metadata/by_profile_id answer in
the API's shape ({id: {"Descriptive": {"Name": ...}}}, as _ft_profile_indicators
reads it), and the figures are synthetic CSVs in the all_data/csv layout the
other Fingertips tests use (Area Code, Sex, Age, Time period, Value, ...).
The checks on the shipped list compare it with fingertips_profiles.parquet,
which holds Fingertips' own names from the API.
"""
import csv
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
from contextlib import redirect_stdout

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


print("=== matching names ===")
check("a PHOF section code is set aside",
      p.ft_profile_name_key("A01b - Life expectancy at birth") == p.ft_profile_name_key("Life expectancy at birth"))
check("and so are case and punctuation",
      p.ft_profile_name_key("Smoking Prevalence in adults (aged 18 and over) - current smokers (APS)")
      == p.ft_profile_name_key("smoking prevalence in adults aged 18 and over current smokers APS"))
check("a name that merely starts like a code is left alone",
      p.ft_profile_name_key("E10 suicide rate") != p.ft_profile_name_key("Suicide rate"))

META = {1203: "Mortality rate from lung cancer, all ages", 90366: "A01b - Life expectancy at birth",
        90362: "A01a - Healthy life expectancy at birth", 93999: "Infant mortality rate",
        20101: "C04 - Low birth weight of term babies", 10301: "B03 - Pupil absence",
        40401: "E04a - Under 75 mortality rate from cardiovascular disease"}
WANT = [(90366, "Life expectancy at birth"),
        (92196, "Infant mortality rate"),              # renumbered: OHID now files it as 93999
        (99999, "Nonexistent indicator of something"),  # gone
        (20101, "Low birth weight of term babies"),
        (None, "B03 - Pupil absence"),                  # no id: found by its name
        (90366, "Life expectancy at birth")]            # listed twice: taken once
chosen, missing, moved = p.ft_pick_profile_indicators(META, WANT, 40)
check("taken in the order wanted, by name, not by number",
      chosen == [90366, 93999, 20101, 10301], str(chosen))
check("an indicator OHID renumbered is found under its name, and the move is reported",
      moved == [("Infant mortality rate", 92196, 93999)], str(moved))
check("a wanted name that is not in the profile is reported, not guessed",
      missing == ["Nonexistent indicator of something"], str(missing))
check("the oldest ids are not taken just for being first (1203 is not wanted)", 1203 not in chosen)
check("FINGERTIPS_PER_PROFILE is still a cap", p.ft_pick_profile_indicators(META, WANT, 2)[0] == [90366, 93999])
wrong = {90366: "Smoking prevalence in adults"}
check("a listed id whose name is now something else is not taken",
      p.ft_pick_profile_indicators(wrong, [(90366, "Life expectancy at birth")], 40)[0] == [])
two = {1: "Infant mortality rate", 2: "Infant mortality rate"}
c2, m2, _ = p.ft_pick_profile_indicators(two, [(None, "Infant mortality rate")], 40)
check("a name two ids share is reported rather than either taken", c2 == [] and m2 == ["Infant mortality rate"])

print("=== the shipped list ===")
wanted = p.ft_profile_wanted()
prof = pd.read_parquet(REPO / "data/outcomes/fingertips_profiles.parquet")
ids_of = prof.groupby("profile_id")["indicator_id"].apply(set).to_dict()
names = prof.groupby("indicator_id")["indicator_name"].first().to_dict()
check("every profile the pipeline reads has a list",
      all(pid in wanted and wanted[pid] for pid, _, _ in p.FINGERTIPS_PROFILES),
      str([pid for pid, _, _ in p.FINGERTIPS_PROFILES if not wanted.get(pid)]))
check("no list is longer than the cap, so nothing on it is cut silently",
      all(len(v) <= p.FINGERTIPS_PER_PROFILE for v in wanted.values()),
      str({k: len(v) for k, v in wanted.items() if len(v) > p.FINGERTIPS_PER_PROFILE}))
# Against the parquet, which holds Fingertips' own names. A row whose id a
# later refresh did not find is not in it, and the refresh log names it, so
# only the ids it holds are compared here.
bad = [(pid, i, n) for pid, rows in wanted.items() for i, n in rows
       if i is not None and i in names
       and p._ft_name_mismatch(p._FT_NAME_CODE.sub("", n), p._FT_NAME_CODE.sub("", names[i]))]
check("every row with an id the parquet holds carries Fingertips' own name for it", not bad, str(bad[:5]))
check("every row has an id and a name", all(i is not None and n for rows in wanted.values() for i, n in rows))
unseen = sorted({i for rows in wanted.values() for i, _ in rows if i not in names})
print(f"  ..  {len(unseen)} listed id(s) not in the parquet yet" + (f": {unseen[:8]}" if unseen else ""))
listed = {i for rows in wanted.values() for i, _ in rows}
qof = {i for i, _, _ in p.QOF_INDICATORS}
check("every QOF prevalence the QOF name check reads is still taken (test_qof_labels.py)",
      qof <= listed, str(sorted(qof - listed)))
la_ids = {i for i, _, _ in p.FINGERTIPS_INDICATORS} & set(names)
check("every LA series of FINGERTIPS_INDICATORS that the parquet held is still taken",
      la_ids <= listed, str(sorted(la_ids - listed)))
check("PHOF now leads with life expectancy, which numerical order never reached",
      [i for i, _ in wanted[19][:2]] == [90366, 90362], str(wanted[19][:3]))
kept_now = {(pid, i) for pid, rows in wanted.items() for i, _ in rows}
gone = [(pid, i, names.get(i)) for pid, s in ids_of.items() for i in s if (pid, i) not in kept_now]
print(f"  ..  {len(gone)} profile entries the old numerical cut took are no longer wanted (none is read by the map)")

print("=== run_fingertips_profiles, end to end, offline ===")
tmp = pathlib.Path(tempfile.mkdtemp())
p.REPO_ROOT = tmp
p.CACHE_DIR = tmp / "cache"
p.DATA_DIR = tmp / "data"
(p.DATA_DIR / "outcomes").mkdir(parents=True)
cache = p.CACHE_DIR / "fingertips_profiles"
cache.mkdir(parents=True)
p.FINGERTIPS_PROFILES = [(19, "phof", "Public Health Outcomes Framework")]
(cache / "profile_19_meta.json").write_text(json.dumps(
    {str(i): {"Descriptive": {"Name": n}} for i, n in META.items()}), encoding="utf-8")
lads = sorted(p.SCOPE_LADS)[:3]
p.SCOPE_LADS = set(lads)
p.LAD_NAMES = {l: f"LA {l}" for l in lads}
p._ft_district_parents = lambda: {}
for i in META:
    rows = [{"Indicator ID": i, "Indicator Name": META[i], "Area Code": l, "Area Name": f"LA {l}",
             "Area Type": "Counties & UAs (from Apr 2023)", "Sex": "Persons", "Age": "All ages",
             "Category Type": "", "Time period": "2022 - 24", "Time period Sortable": "20220000",
             "Value": 50 + k, "Value note": ""} for k, l in enumerate(lads)]
    pd.DataFrame(rows).to_csv(cache / f"ind_{i}.csv", index=False)
wfile = tmp / "wanted.csv"
with open(wfile, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["profile_id", "indicator_id", "indicator_name"])
    for i, n in WANT:
        w.writerow([19, "" if i is None else i, n])
p.FINGERTIPS_PROFILE_WANTED = wfile
p._ft_get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network in this test"))
log = io.StringIO()
with redirect_stdout(log):
    out = p.run_fingertips_profiles()
text = log.getvalue()
got = sorted(out["indicator_id"].unique()) if len(out) else []
check("the parquet holds the wanted indicators and nothing else", got == sorted([90366, 93999, 20101, 10301]), str(got))
check("each keeps Fingertips' own name", set(out.loc[out.indicator_id == 90366, "indicator_name"]) == {META[90366]})
check("a wanted name not found is a clear log line naming it",
      "wanted indicator not found in profile 19's metadata: 'Nonexistent indicator of something'" in text, text[-800:])
check("a renumbered indicator says so", "'Infant mortality rate' is now id 93999 (listed as 92196)" in text)
check("the summary says how many were found by name", "4 of 6 wanted indicators found by name" in text, text[-600:])
check("the parquet is written", (p.DATA_DIR / "outcomes" / "fingertips_profiles.parquet").exists())

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
