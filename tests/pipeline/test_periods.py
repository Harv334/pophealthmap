"""indicators.js carries each indicator's real period, and the pipeline writes it.

The map shows the generated period beside the legend in place of the
hand-typed one, so the shipped file and the pipeline have to agree: rebuilt
from the shipped payloads, build_indicator_stats must reproduce indicators.js
exactly, apart from its timestamp.
"""
import json, sys
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


ld = lambda p: json.loads((REPO / p).read_text(encoding="utf-8"))
src = (REPO / "data/map/indicators.js").read_text(encoding="utf-8")
shipped = json.loads(src[src.index("var PH_IND_STATS = ") + len("var PH_IND_STATS = "):src.rindex(";")])
built = F.build_indicator_stats(ld("ward_data.json"), ld("lsoa_data.json"),
                                ld("msoa_data.json"), ld("borough_data.json"))
built["generated"] = shipped["generated"]

print("=== the pipeline reproduces the shipped file ===")
check(built == shipped, "build_indicator_stats == data/map/indicators.js",
      str(sorted(k for k in set(built["stats"]) | set(shipped["stats"])
                 if built["stats"].get(k) != shipped["stats"].get(k))[:10]))

print("\n=== periods ===")
st = shipped["stats"]
want = {
    "ft_93227": "2016/17 - 20/21",   # emergency admissions, a five-year pool
    "ft_93480": "2019 - 23",         # preventable mortality
    "ft_93283": "2019 - 23",         # life expectancy, MSOA
    "ft_hypertension_qof": "2024/25",
}
for k, v in want.items():
    check(st.get(k, {}).get("yr") == v, f"{k} is {v}", repr(st.get(k, {}).get("yr")))
vague = sorted(k for k, s in st.items() if str(s.get("yr", "")).lower().startswith("latest"))
check(not vague, "no generated period says 'latest'", str(vague))
undated = sorted(k for k in st if k.startswith("ft_") and "yr" not in st[k])
check(not undated, "every Fingertips indicator has a period", str(undated))

print()
print("FAILED" if failures else "ALL PASS", f"({failures} failure(s))" if failures else "")
sys.exit(1 if failures else 0)
