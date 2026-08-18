import json, sys
import pathlib as _pl, sys as _sys
# The repo root, so fetch_all_data imports and the data paths resolve
# wherever this is run from. Derived rather than written down: these tests
# used to carry one developer's absolute path and ran nowhere else.
REPO = _pl.Path(__file__).resolve().parents[2]
_sys.path.insert(0, str(REPO))
import fetch_all_data as F


def boom():
    raise RuntimeError("simulated upstream outage")


F.SOURCES = dict(F.SOURCES)
F.SOURCES["ptal"] = boom
sys.argv = ["x", "--only", "imd", "ptal"]
F.main()

m = json.load(open(REPO / "data/meta/manifest.json"))
print("\n--- manifest ---")
print("last_run:", m["last_run"])
print("keys:", sorted(m["sources"]))
for k, v in sorted(m["sources"].items()):
    print(f"  {k:6} status={v['status']:7} rows={v['rows_written']:>7,} "
          f"path={v['output_path'] or '-'} err={v['error'][:40]}")
failed = sorted(k for k, v in m["sources"].items() if v.get("status") == "failed")
print("failing sources the workflow would report:", failed)
