"""Practice QOF columns are named for the measure their Fingertips id holds.

A wrong id returns a full set of plausible percentages under our own label,
so nothing downstream notices. These checks hold the table, the shipped files
and the fetcher's own name check to Fingertips' names.
"""
import json, re, shutil, sys
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


print("=== QOF_INDICATORS against Fingertips' own names ===")
prof = pd.read_parquet(REPO / "data/outcomes/fingertips_profiles.parquet")
names = prof.groupby("indicator_id")["indicator_name"].first().to_dict()
for ind_id, short, label in F.QOF_INDICATORS:
    actual = names.get(ind_id)
    if actual is None:
        check(False, f"{ind_id} {short}", "id not in fingertips_profiles.parquet, so its name is unconfirmed")
        continue
    check(not F._ft_name_mismatch(label, actual), f"{ind_id} is {actual!r}, filed as {short}",
          f"label {label!r}")
    check("QOF prevalence" in actual, f"{ind_id} is a QOF prevalence", actual)

print("\n=== the name check catches a wrong id ===")
check(F._ft_name_mismatch("Hypertension: QOF prevalence", "Diabetes: QOF prevalence"),
      "hypertension label over diabetes data is flagged")
check(not F._ft_name_mismatch("Heart Failure: QOF prevalence", "Heart failure: QOF prevalence (all ages)"),
      "a change of case and a trailing qualifier is not flagged")

print("\n=== shipped files ===")
shorts = {s for _, s, _ in F.QOF_INDICATORS}
q = pd.read_parquet(REPO / "data/healthcare/qof_prevalence.parquet")
cols = [c for c in q.columns if c != "code"]
check(set(cols) <= shorts, "every parquet column is a QOF_INDICATORS short name",
      str(sorted(set(cols) - shorts)))
check(float(q[cols].median().max()) < 30, "every column's median is a plausible prevalence",
      q[cols].median().round(1).to_dict().__repr__())
check(float(q[cols].max().max()) <= 100, "no value above 100%")

src = (REPO / "data/map/gp_practices.js").read_text(encoding="utf-8")
gps = json.loads(src[src.index("var GPS = ") + len("var GPS = "):src.rindex(";")])
keys = set()
for g in gps:
    keys |= set((g.get("qof") or {}).keys())
check(keys <= {s[4:-4] for s in shorts}, "gp_practices.js qof keys match the table",
      str(sorted(keys)))

print("\n=== run_qof skips a download whose name does not match ===")
tmp = REPO / ".cache" / "test_qof_labels"
shutil.rmtree(tmp, ignore_errors=True)
(tmp / "qof_fingertips").mkdir(parents=True)
codes = list(pd.read_parquet(REPO / "data/healthcare/gp_practices.parquet")["code"].astype(str)[:40])


def fake_csv(name, value):
    rows = [{"Indicator Name": name, "Area Code": c, "Value": value,
             "Time period": "2024/25", "Time period Sortable": "20240000"} for c in codes]
    return pd.DataFrame(rows).to_csv(index=False)


(tmp / "qof_fingertips" / "ind_219_practice.csv").write_text(
    fake_csv("Hypertension: QOF prevalence", 12.5), encoding="utf-8")
(tmp / "qof_fingertips" / "ind_241_practice.csv").write_text(
    fake_csv("Smoking: QOF prevalence", 15.0), encoding="utf-8")
written = {}
orig = (F.CACHE_DIR, F.QOF_INDICATORS, F.write_parquet_guarded)
F.CACHE_DIR = tmp
F.QOF_INDICATORS = [(219, "qof_hypertension_pct", "Hypertension: QOF prevalence"),
                    (241, "qof_diabetes_pct", "Diabetes: QOF prevalence")]
F.write_parquet_guarded = lambda path, df, source: written.setdefault("df", df)
try:
    F.run_qof()
finally:
    F.CACHE_DIR, F.QOF_INDICATORS, F.write_parquet_guarded = orig
    shutil.rmtree(tmp, ignore_errors=True)
out = written.get("df")
check(out is not None and "qof_hypertension_pct" in out.columns, "the matching id is written")
check(out is not None and "qof_diabetes_pct" not in out.columns,
      "smoking data offered as diabetes is not written")

print()
print("FAILED" if failures else "ALL PASS", f"({failures} failure(s))" if failures else "")
sys.exit(1 if failures else 0)
