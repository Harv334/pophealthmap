"""A source that fails softly (warn + empty frame) must show as failed."""
import json, sys
import pandas as pd
import pathlib as _pl, sys as _sys
# The repo root, so fetch_all_data imports and the data paths resolve
# wherever this is run from. Derived rather than written down: these tests
# used to carry one developer's absolute path and ran nowhere else.
REPO = _pl.Path(__file__).resolve().parents[2]
_sys.path.insert(0, str(REPO))
import fetch_all_data as F

def dwp_like():                      # what run_dwp_benefits actually does
    F.warn("dwp: no datasets returned any data - skipping write")
    return pd.DataFrame()

def hospitals_like():                # optional source with nothing to do
    return None

def healthy():
    return pd.DataFrame([{"a": 1}])

def raiser():
    raise RuntimeError("upstream 500")

F.SOURCES = {"dwp": dwp_like, "hospitals": hospitals_like,
             "gp": healthy, "crime": raiser}

# Write the manifest somewhere disposable.
#
# This test replaces SOURCES with four fakes and then runs the real main(),
# which ends by writing the real data/meta/manifest.json. That writer drops
# entries whose key is not in SOURCES, so running this test against the live
# file deleted the other seventeen sources' records and left the public
# methodology page reporting two failures and a skip that had not happened.
# The file is committed, so it looked like a data change rather than a test.
# Under .cache/ rather than the system temp directory: write_manifest logs the
# path relative to the repo root, which raises on anything outside it.
_tmp = REPO / ".cache" / "test_manifest.json"
_tmp.parent.mkdir(parents=True, exist_ok=True)
_tmp.unlink(missing_ok=True)
F.MANIFEST_PATH = _tmp

sys.argv = ["x", "--only", "dwp", "hospitals", "gp", "crime"]
F.main()

m = json.load(open(_tmp))
print("\n--- manifest ---")
for k, v in sorted(m["sources"].items()):
    print(f"  {k:10} status={v['status']:8} rows={v['rows_written']:>3}  {v['error'][:52]}")
failed = sorted(k for k, v in m["sources"].items() if v.get("status") == "failed")
print("\nworkflow would open an issue naming:", failed)
expected = ["crime", "dwp"]
print("PASS" if failed == expected else f"FAIL (expected {expected})")
